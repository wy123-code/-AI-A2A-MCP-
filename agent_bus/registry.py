"""Agent 注册中心 —— 基于 Redis Hash 的服务发现机制。

优化说明 (P1):
  - 新增 cleanup_expired(): 自动清理心跳超时的 Agent
  - 上线/下线通知: register/unregister 时向 agent:system:events 频道发布事件
  - register() 幂等性: key 已存在时刷新 TTL 而非覆盖
  - heartbeat() 失败追踪: 连续 3 次失败标记 status="unstable"
"""
import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from loguru import logger

from config import REDIS_CONFIG, AGENT_BUS_CONFIG


REGISTRY_PREFIX = "agent_registry"
SYSTEM_EVENTS_CHANNEL = "agent:system:events"
HEARTBEAT_FAIL_THRESHOLD = 3  # 连续心跳失败阈值


class AgentRegistry:
    """Agent 注册中心：管理所有 Agent 的注册、发现、心跳。

    使用 Redis Hash 存储 Agent 信息：
    - Key: agent_registry:{agent_name}
    - Fields: type, intents, status, last_heartbeat, heartbeat_fail_count, metadata
    """

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._event_pubsub: Any = None  # 可选: MCPPubSub 实例（用于系统事件通知）

    def set_event_pubsub(self, pubsub: Any) -> None:
        """注入 MCPPubSub 实例，用于发布 Agent 上线/下线系统事件。"""
        self._event_pubsub = pubsub

    async def start(self) -> None:
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

    async def stop(self) -> None:
        if self._redis:
            await self._redis.close()

    def _key(self, name: str) -> str:
        return f"{REGISTRY_PREFIX}:{name}"

    async def register(
        self,
        name: str,
        agent_type: str,
        intents: List[str] = None,
        metadata: Dict[str, Any] = None,
    ) -> None:
        """注册一个 Agent 到注册中心（幂等 —— key 已存在时刷新 TTL）。

        同时向 agent:system:events 频道发布 agent_online 事件。
        """
        if not self._redis:
            return
        key = self._key(name)
        existed = await self._redis.exists(key)
        await self._redis.hset(key, mapping={
            "type": agent_type,
            "intents": ",".join(intents or []),
            "status": "online",
            "last_heartbeat": datetime.now(timezone.utc).isoformat(),
            "heartbeat_fail_count": "0",
            "metadata": json.dumps(metadata or {}, ensure_ascii=False),
        })
        await self._redis.expire(
            key, AGENT_BUS_CONFIG["agent_heartbeat_interval"] * 3
        )
        action = "reconnected" if existed else "registered"
        logger.info(f"AgentRegistry: {action} '{name}' (type={agent_type}, intents={intents})")

        # 系统事件通知
        await self._publish_event("agent_online", name=name, agent_type=agent_type)

    async def unregister(self, name: str) -> None:
        """从注册中心移除一个 Agent，发布 agent_offline 事件。"""
        if not self._redis:
            return
        await self._redis.delete(self._key(name))
        logger.info(f"AgentRegistry: unregistered '{name}'")

        # 系统事件通知
        await self._publish_event("agent_offline", name=name)

    async def heartbeat(self, name: str) -> None:
        """更新 Agent 心跳时间戳并续期，追踪连续失败次数。

        连续 3 次心跳失败 → 标记 status="unstable"。
        """
        if not self._redis:
            return
        key = self._key(name)
        now_iso = datetime.now(timezone.utc).isoformat()
        try:
            await self._redis.hset(key, mapping={
                "last_heartbeat": now_iso,
                "heartbeat_fail_count": "0",
            })
            await self._redis.expire(
                key, AGENT_BUS_CONFIG["agent_heartbeat_interval"] * 3
            )
        except Exception as e:
            logger.warning(f"AgentRegistry: heartbeat failed for '{name}': {e}")
            # 追踪连续失败次数
            try:
                fail_count_str = await self._redis.hget(key, "heartbeat_fail_count")
                fail_count = int(fail_count_str or "0") + 1
                if fail_count >= HEARTBEAT_FAIL_THRESHOLD:
                    await self._redis.hset(key, "status", "unstable")
                    logger.warning(
                        f"AgentRegistry: '{name}' marked unstable "
                        f"({fail_count} consecutive heartbeat failures)"
                    )
                    await self._publish_event("agent_unstable", name=name,
                                            fail_count=str(fail_count))
                await self._redis.hset(key, "heartbeat_fail_count", str(fail_count))
            except Exception:
                pass

    async def discover(self, agent_type: str = None) -> List[Dict[str, Any]]:
        """发现已注册的 Agent 列表，可按类型筛选。"""
        if not self._redis:
            return []
        result = []
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=f"{REGISTRY_PREFIX}:*", count=100
            )
            for key in keys:
                data = await self._redis.hgetall(key)
                if not data:
                    continue
                name = key.replace(f"{REGISTRY_PREFIX}:", "")
                agent_type_val = data.get("type", "")
                if agent_type and agent_type_val != agent_type:
                    continue
                result.append({
                    "name": name,
                    "type": agent_type_val,
                    "intents": data.get("intents", "").split(",") if data.get("intents") else [],
                    "status": data.get("status", "offline"),
                    "last_heartbeat": data.get("last_heartbeat", ""),
                    "metadata": json.loads(data.get("metadata", "{}")),
                })
            if cursor == 0:
                break
        return result

    async def discover_by_intent(self, intent: str) -> Optional[str]:
        """根据意图查找能处理该意图的 Agent 名称。"""
        if not self._redis:
            return None
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=f"{REGISTRY_PREFIX}:*", count=100
            )
            for key in keys:
                data = await self._redis.hgetall(key)
                intents_str = data.get("intents", "")
                if intent in intents_str.split(","):
                    return key.replace(f"{REGISTRY_PREFIX}:", "")
            if cursor == 0:
                break
        return None

    async def is_online(self, name: str) -> bool:
        """检查 Agent 是否在线（心跳是否在允许的过期时间内）。"""
        if not self._redis:
            return False
        key = self._key(name)
        heartbeat_str = await self._redis.hget(key, "last_heartbeat")
        if not heartbeat_str:
            return False
        try:
            last_hb = datetime.fromisoformat(heartbeat_str)
            elapsed = (datetime.now(timezone.utc) - last_hb).total_seconds()
            return elapsed < AGENT_BUS_CONFIG["agent_heartbeat_timeout"]
        except (ValueError, TypeError):
            return False

    async def cleanup_expired(self) -> List[str]:
        """清理所有心跳超时的 Agent，返回被清理的 Agent 名称列表。

        扫描所有 agent_registry:* key，is_online() == False 的执行:
          1. DELETE Redis key
          2. 发布 agent_offline 事件
          3. 清理消息队列 key (agent:queue:{name})

        由 Celery Beat 定时任务每 5 分钟触发。
        """
        if not self._redis:
            return []
        cleaned = []
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=f"{REGISTRY_PREFIX}:*", count=100
            )
            for key in keys:
                name = key.replace(f"{REGISTRY_PREFIX}:", "")
                if not await self.is_online(name):
                    await self._redis.delete(key)
                    # 清理消息队列
                    await self._redis.delete(f"agent:queue:{name}")
                    cleaned.append(name)
                    logger.warning(
                        f"AgentRegistry: cleanup_expired removed '{name}' "
                        f"(heartbeat timeout > {AGENT_BUS_CONFIG['agent_heartbeat_timeout']}s)"
                    )
                    await self._publish_event("agent_offline", name=name,
                                            reason="heartbeat_timeout")
            if cursor == 0:
                break
        if cleaned:
            logger.info(f"AgentRegistry: cleanup_expired removed {len(cleaned)} agents: {cleaned}")
        return cleaned

    async def _publish_event(self, event: str, **kwargs) -> None:
        """向 agent:system:events 频道发布系统事件。"""
        if not self._event_pubsub or not self._redis:
            return
        try:
            event_data = json.dumps({
                "event": event,
                "timestamp": datetime.now(timezone.utc).isoformat(),
                **kwargs,
            }, ensure_ascii=False)
            await self._redis.publish(SYSTEM_EVENTS_CHANNEL, event_data)
        except Exception as e:
            logger.error(f"AgentRegistry: failed to publish event '{event}': {e}")
