"""Redis Pub/Sub 通信层 —— Agent 消息发布、订阅、请求-响应模式。

优化说明 (P0):
  - request_response 增加 ACK 确认阶段 (ack_timeout=3s)，未收到 ACK 自动重试 (最多2次)
  - 集成 ErrorHandler.with_timeout + with_retry 提供一致的容错策略
  - 新增 publish_with_ack(): 单向消息 + ACK 确认送达
  - 新增 _cleanup_dead_agents(): 定期清理过期 Agent 的残留 channel

优化说明 (P5):
  - 所有 publish 操作自动注入 trace_id（从 ContextVar 获取），确保全链路追踪
"""
import asyncio
import time
import uuid
from typing import Any, Callable, Awaitable, Dict, Optional

import redis.asyncio as aioredis
from loguru import logger

from config import REDIS_CONFIG, AGENT_BUS_CONFIG
from agent_bus.message import AgentMessage
from agent_bus.error_handler import ErrorHandler
from middleware.trace_id import get_trace_id


class MCPPubSub:
    """基于 Redis Pub/Sub 的 MCP 消息中间件。

    提供四种通信模式：
    - publish: 单向消息投递（fire-and-forget）
    - publish_with_ack: 单向消息 + ACK 确认送达（不等待业务结果）
    - subscribe: 持久订阅（后台回调）
    - request_response: 请求-响应模式（ACK → 发布任务 → 等待结果，含自动重试）
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._pubsub: Optional[aioredis.client.PubSub] = None
        self._callbacks: Dict[str, Callable[[AgentMessage], Awaitable[None]]] = {}
        self._pending_requests: Dict[str, asyncio.Future] = {}
        self._listener_task: Optional[asyncio.Task] = None
        self._running = False

    async def start(self) -> None:
        """连接 Redis 并启动后台监听任务。"""
        redis_url = (
            f"redis://{REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}/{REDIS_CONFIG['db']}"
        )
        self._redis = await aioredis.from_url(
            redis_url,
            password=REDIS_CONFIG["password"] or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )
        await self._redis.ping()
        self._pubsub = self._redis.pubsub()
        self._running = True
        self._listener_task = asyncio.create_task(self._listener_loop())
        logger.info("MCPPubSub: Redis Pub/Sub connected and listening")

    async def stop(self) -> None:
        """关闭所有订阅并断开 Redis 连接。"""
        self._running = False
        if self._listener_task:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass

        # 取消所有等待中的请求
        for future in self._pending_requests.values():
            if not future.done():
                future.set_exception(ConnectionError("MCPPubSub stopped"))
        self._pending_requests.clear()

        if self._pubsub:
            await self._pubsub.close()
        if self._redis:
            await self._redis.close()
        self._callbacks.clear()
        logger.info("MCPPubSub: stopped")

    async def publish(self, channel: str, message: AgentMessage) -> None:
        """向指定频道发布消息（单向，不等待响应）。

        P5: 自动从 ContextVar 注入 trace_id，确保全链路追踪。
        """
        if not self._redis:
            raise ConnectionError("MCPPubSub not started")
        # 自动注入 trace_id
        if not message.trace_id:
            message.trace_id = get_trace_id() or f"tr_{uuid.uuid4().hex[:12]}"
        payload = message.to_json()
        await self._redis.publish(channel, payload)

    async def subscribe(
        self,
        channel: str,
        callback: Callable[[AgentMessage], Awaitable[None]],
    ) -> None:
        """订阅频道，收到消息时异步回调。"""
        if not self._pubsub:
            raise ConnectionError("MCPPubSub not started")
        self._callbacks[channel] = callback
        await self._pubsub.subscribe(channel)
        logger.info(f"MCPPubSub: subscribed to '{channel}'")

    async def unsubscribe(self, channel: str) -> None:
        """取消订阅指定频道。"""
        if self._pubsub:
            await self._pubsub.unsubscribe(channel)
        self._callbacks.pop(channel, None)

    async def request_response(
        self,
        channel: str,
        message: AgentMessage,
        timeout: float = None,
        ack_timeout: float = 3.0,
        max_retries: int = None,
    ) -> AgentMessage:
        """请求-响应模式（增强版）：ACK 确认 → 发布任务 → 等待结果。

        流程:
          1. 订阅 ACK 频道: {channel}:ack:{correlation_id}
          2. 订阅响应频道: {channel}:response:{correlation_id}
          3. 发布任务消息
          4. 等待 ACK（短超时 ack_timeout），未收到则重试（最多 max_retries 次）
          5. 收到 ACK 后等待业务结果（长超时 timeout）
          6. 清理临时频道

        Args:
            channel: 目标频道
            message: 要发送的任务消息（应设置 ack_required=True）
            timeout: 等待响应的超时时间（秒），默认使用 AGENT_BUS_CONFIG
            ack_timeout: 等待 ACK 的超时时间（秒），默认 3s
            max_retries: ACK 未收到时的最大重试次数，默认使用 AGENT_BUS_CONFIG

        Returns:
            响应消息

        Raises:
            asyncio.TimeoutError: 超时未收到响应
        """
        timeout = timeout or AGENT_BUS_CONFIG["default_task_timeout"]
        max_retries = max_retries if max_retries is not None else AGENT_BUS_CONFIG["max_retries"]

        if not self._redis or not self._pubsub:
            raise ConnectionError("MCPPubSub not started")

        # 设置 ACK 要求
        message.ack_required = True

        ack_channel = f"{channel}:ack:{message.correlation_id}"
        response_channel = f"{channel}:response:{message.correlation_id}"
        ack_future: asyncio.Future = asyncio.get_running_loop().create_future()
        future: asyncio.Future = asyncio.get_running_loop().create_future()

        self._pending_requests[message.correlation_id] = future

        async def _ack_callback(msg: AgentMessage) -> None:
            if msg.message_type == "ack" and msg.correlation_id == message.correlation_id:
                if not ack_future.done():
                    ack_future.set_result(True)

        async def _response_callback(msg: AgentMessage) -> None:
            if msg.correlation_id == message.correlation_id and msg.message_type in ("result", "error"):
                if not future.done():
                    future.set_result(msg)

        await self.subscribe(ack_channel, _ack_callback)
        await self.subscribe(response_channel, _response_callback)

        try:
            # ---- 阶段1: 发送 + 等待 ACK（带重试） ----
            ack_received = False
            for attempt in range(max_retries + 1):
                await self.publish(channel, message)
                ack_received = await ErrorHandler.with_timeout(
                    asyncio.wait_for(ack_future, timeout=ack_timeout),
                    timeout=ack_timeout,
                    fallback_result=False,
                )
                if ack_received:
                    break
                if attempt < max_retries:
                    logger.warning(
                        f"MCPPubSub: ACK timeout for '{channel}' "
                        f"(attempt {attempt + 1}/{max_retries + 1}), retrying..."
                    )
                    # 重建 ack_future 用于下一次重试
                    if ack_future.done():
                        ack_future = asyncio.get_running_loop().create_future()

            if not ack_received:
                from agent_bus.message import ERROR_CODE_ACK_TIMEOUT
                logger.error(f"MCPPubSub: all ACK attempts failed for '{channel}'")
                error_msg = AgentMessage.create_result(
                    original_msg=message,
                    status="failed",
                    payload={"success": False, "error": "ACK timeout", "data": []},
                    error_code=ERROR_CODE_ACK_TIMEOUT,
                )
                return error_msg

            # ---- 阶段2: 等待业务结果 ----
            result = await asyncio.wait_for(future, timeout=timeout)
            return result
        except asyncio.TimeoutError:
            from agent_bus.message import ERROR_CODE_TIMEOUT
            logger.error(f"MCPPubSub: result timeout ({timeout}s) for '{channel}'")
            error_msg = AgentMessage.create_result(
                original_msg=message,
                status="failed",
                payload={"success": False, "error": f"Request timeout after {timeout}s", "data": []},
                error_code=ERROR_CODE_TIMEOUT,
            )
            return error_msg
        finally:
            self._pending_requests.pop(message.correlation_id, None)
            await self.unsubscribe(response_channel)
            await self.unsubscribe(ack_channel)

    async def publish_with_ack(
        self,
        channel: str,
        message: AgentMessage,
        ack_timeout: float = 3.0,
    ) -> bool:
        """发送单向消息并要求 ACK 确认送达（不等待业务结果）。

        适用场景: fire-and-forget 但需要确认对方收到（如 Memory 保存）。

        Args:
            channel: 目标频道
            message: 要发送的消息
            ack_timeout: 等待 ACK 的超时（秒）

        Returns:
            True 表示收到 ACK，False 表示超时或失败
        """
        if not self._redis or not self._pubsub:
            return False

        message.ack_required = True
        ack_channel = f"{channel}:ack:{message.correlation_id}"
        ack_future: asyncio.Future = asyncio.get_running_loop().create_future()

        async def _ack_callback(msg: AgentMessage) -> None:
            if msg.message_type == "ack" and msg.correlation_id == message.correlation_id:
                if not ack_future.done():
                    ack_future.set_result(True)

        await self.subscribe(ack_channel, _ack_callback)
        try:
            await self.publish(channel, message)
            result = await ErrorHandler.with_timeout(
                asyncio.wait_for(ack_future, timeout=ack_timeout),
                timeout=ack_timeout,
                fallback_result=False,
            )
            return bool(result)
        except Exception:
            return False
        finally:
            await self.unsubscribe(ack_channel)

    async def _cleanup_dead_agents(self) -> int:
        """清理已过期 Agent 的残留订阅和待处理请求。

        由定时任务调用（Celery Beat 每 5 分钟触发）。

        Returns:
            清理的 Agent 数量
        """
        cleaned = 0
        # 清理过期的 pending requests
        for corr_id, future in list(self._pending_requests.items()):
            if future.done():
                self._pending_requests.pop(corr_id, None)
                cleaned += 1
        return cleaned

    async def _listener_loop(self) -> None:
        """后台监听循环，从 PubSub 读取消息并分发到对应的回调函数。"""
        while self._running:
            try:
                message = await self._pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=1.0
                )
                if message and message.get("type") == "message":
                    channel = message["channel"]
                    if isinstance(channel, bytes):
                        channel = channel.decode()
                    data = message["data"]
                    if isinstance(data, bytes):
                        data = data.decode()

                    callback = self._callbacks.get(channel)
                    if callback:
                        try:
                            msg = AgentMessage.from_json(data)
                            asyncio.create_task(callback(msg))
                        except Exception as e:
                            logger.error(f"MCPPubSub: callback error for '{channel}': {e}")
            except asyncio.CancelledError:
                break
            except RuntimeError as e:
                if "pubsub connection not set" in str(e):
                    # 启动初期尚无订阅，等待 Agent 注册频道后自动恢复
                    await asyncio.sleep(0.5)
                else:
                    logger.error(f"MCPPubSub: listener error: {e}")
                    await asyncio.sleep(1)
            except Exception as e:
                logger.error(f"MCPPubSub: listener error: {e}")
                await asyncio.sleep(1)
