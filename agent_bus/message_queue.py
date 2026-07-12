"""持久化消息队列 —— 基于 Redis List 的消息暂存与重放。

优化说明 (P0):
  - Worker 离线时消息暂存到 Redis List，恢复后自动消费积压
  - 消息自带 TTL，过期自动丢弃避免无限积压
  - provide queue depth monitoring for load-aware scheduling
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from loguru import logger

from config import REDIS_CONFIG

QUEUE_PREFIX = "agent:queue"


class MessageQueue:
    """基于 Redis List 的持久化消息队列。

    使用模式:
        await queue.enqueue("worker.weather", msg)   # 消息入队
        msg = await queue.dequeue("worker.weather")   # 阻塞取出
        await queue.requeue("worker.weather", msg)    # 失败重新入队
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None

    async def start(self) -> None:
        """连接 Redis。"""
        redis_url = (
            f"redis://{REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}/{REDIS_CONFIG['db']}"
        )
        self._redis = await aioredis.from_url(
            redis_url,
            password=REDIS_CONFIG["password"] or None,
            decode_responses=True,
            socket_connect_timeout=5,
        )

    async def stop(self) -> None:
        if self._redis:
            await self._redis.close()

    def _queue_key(self, target_agent: str) -> str:
        return f"{QUEUE_PREFIX}:{target_agent}"

    async def enqueue(self, target_agent: str, message_json: str, ttl: int = 30) -> None:
        """将消息入队（LPUSH，头部插入）。

        Args:
            target_agent: 目标 Agent 名称
            message_json: 序列化后的消息 JSON
            ttl: 队列中消息的 TTL（秒），过期自动丢弃
        """
        if not self._redis:
            return
        key = self._queue_key(target_agent)
        # 包装消息，附加入队时间和 TTL
        envelope = json.dumps({
            "message": message_json,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "ttl": ttl,
        }, ensure_ascii=False)
        await self._redis.lpush(key, envelope)
        logger.debug(f"MessageQueue: enqueued to '{target_agent}' (ttl={ttl}s)")

    async def dequeue(self, target_agent: str, timeout: float = 2.0) -> Optional[str]:
        """阻塞取出消息（BRPOP，尾部取出）。

        Args:
            target_agent: 目标 Agent 名称
            timeout: 阻塞等待超时（秒），0 表示无限等待

        Returns:
            原始消息 JSON 字符串，或 None（超时/队列空）
        """
        if not self._redis:
            return None
        key = self._queue_key(target_agent)
        result = await self._redis.brpop(key, timeout=max(1, int(timeout)))
        if result is None:
            return None
        _, envelope = result
        try:
            data = json.loads(envelope)
            # 检查 TTL 过期
            enqueued_at = datetime.fromisoformat(data["enqueued_at"])
            ttl = data.get("ttl", 30)
            elapsed = (datetime.now(timezone.utc) - enqueued_at).total_seconds()
            if elapsed > ttl:
                logger.debug(f"MessageQueue: discarded expired message for '{target_agent}' "
                            f"(elapsed={elapsed:.0f}s > ttl={ttl}s)")
                return None
            return data["message"]
        except (json.JSONDecodeError, KeyError, ValueError) as e:
            logger.error(f"MessageQueue: bad envelope for '{target_agent}': {e}")
            return None

    async def requeue(self, target_agent: str, message_json: str) -> None:
        """失败消息重新入队（RPUSH，尾部插入，保持顺序）。"""
        if not self._redis:
            return
        key = self._queue_key(target_agent)
        envelope = json.dumps({
            "message": message_json,
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "ttl": 30,
        }, ensure_ascii=False)
        await self._redis.rpush(key, envelope)
        logger.debug(f"MessageQueue: requeued to '{target_agent}'")

    async def get_queue_depth(self, target_agent: str) -> int:
        """获取队列积压长度（用于负载感知调度）。"""
        if not self._redis:
            return 0
        return await self._redis.llen(self._queue_key(target_agent))

    async def drain_queue(self, target_agent: str) -> List[str]:
        """消费队列中所有消息（Worker 启动后批量处理积压）。"""
        messages = []
        if not self._redis:
            return messages
        key = self._queue_key(target_agent)
        # 非阻塞方式批量取出，避免 BRPOP 0 阻塞无限
        while True:
            envelope = await self._redis.rpop(key)
            if envelope is None:
                break
            try:
                data = json.loads(envelope)
                enqueued_at = datetime.fromisoformat(data["enqueued_at"])
                ttl = data.get("ttl", 30)
                elapsed = (datetime.now(timezone.utc) - enqueued_at).total_seconds()
                if elapsed > ttl:
                    continue
                messages.append(data["message"])
            except (json.JSONDecodeError, KeyError, ValueError):
                continue
        if messages:
            logger.info(f"MessageQueue: drained {len(messages)} messages for '{target_agent}'")
        return messages

    async def purge(self, target_agent: str) -> None:
        """清空指定 Agent 的消息队列。"""
        if not self._redis:
            return
        await self._redis.delete(self._queue_key(target_agent))
        logger.info(f"MessageQueue: purged queue for '{target_agent}'")
