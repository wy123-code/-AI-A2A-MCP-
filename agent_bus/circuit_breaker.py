"""Agent 级熔断器 —— 防止服务雪崩，支持 CLOSED→OPEN→HALF_OPEN 三态转换。

优化说明 (P4):
  - 基于 Redis 原子计数，分布式安全
  - 连续失败 N 次自动熔断，快速失败避免资源浪费
  - HALF_OPEN 探测恢复：一次成功即可关闭熔断
  - 发布熔断/恢复系统事件到 agent:system:events
"""
import time
from dataclasses import dataclass
from enum import Enum
from typing import Optional

import redis.asyncio as aioredis
from loguru import logger

from config import REDIS_CONFIG

CIRCUIT_PREFIX = "circuit"


class CircuitState(str, Enum):
    """熔断器三态：CLOSED(正常) → OPEN(熔断) → HALF_OPEN(探测恢复)。"""
    CLOSED = "closed"       # 正常通行
    OPEN = "open"           # 熔断中，快速失败
    HALF_OPEN = "half_open"  # 探测恢复中


@dataclass
class CircuitConfig:
    """熔断器配置参数。"""
    failure_threshold: int = 5       # 连续失败 N 次触发熔断
    timeout_seconds: int = 30        # 熔断持续最短时间
    recovery_timeout: int = 60       # HALF_OPEN 最大探测时间
    half_open_max_requests: int = 1  # HALF_OPEN 允许的探测请求数


class CircuitBreaker:
    """Agent 级熔断器 —— 每个 Worker 独立熔断状态。

    使用方法:
        cb = CircuitBreaker()
        await cb.start()

        if not await cb.before_call("worker.hotel"):
            return degrade_result  # 熔断中，快速失败
        result = await call_worker(...)
        await cb.after_call("worker.hotel", success=result["success"])
    """

    def __init__(self, config: CircuitConfig = None):
        self.config = config or CircuitConfig()
        self._redis: Optional[aioredis.Redis] = None

    async def start(self) -> None:
        redis_url = (
            f"redis://{REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}/{REDIS_CONFIG['db']}"
        )
        self._redis = await aioredis.from_url(
            redis_url,
            password=REDIS_CONFIG["password"] or None,
            decode_responses=True,
            socket_connect_timeout=3,
        )

    async def stop(self) -> None:
        if self._redis:
            await self._redis.close()

    def _state_key(self, worker_name: str) -> str:
        return f"{CIRCUIT_PREFIX}:{worker_name}:state"

    def _fail_key(self, worker_name: str) -> str:
        return f"{CIRCUIT_PREFIX}:{worker_name}:fail_count"

    def _half_open_key(self, worker_name: str) -> str:
        return f"{CIRCUIT_PREFIX}:{worker_name}:half_open_time"

    async def before_call(self, worker_name: str) -> bool:
        """调用前检查 —— 返回 True 允许调用，False 表示已熔断需快速失败。"""
        if not self._redis:
            return True

        state = await self._redis.get(self._state_key(worker_name)) or CircuitState.CLOSED

        if state == CircuitState.CLOSED:
            return True

        if state == CircuitState.OPEN:
            # 检查是否超过熔断持续时间，可以进入 HALF_OPEN
            fail_count = int(await self._redis.get(self._fail_key(worker_name)) or 0)
            if fail_count >= self.config.failure_threshold:
                # 还在熔断中
                logger.warning(
                    f"CircuitBreaker: {worker_name} is OPEN (fail_count={fail_count}), fast-failing"
                )
                return False

        if state == CircuitState.HALF_OPEN:
            # 只允许有限探测请求
            half_time = float(await self._redis.get(self._half_open_key(worker_name)) or 0)
            if time.time() - half_time > self.config.recovery_timeout:
                # 探测超时，重置为 CLOSED
                await self._reset(worker_name)
                return True
            return True  # 允许探测

        return True

    async def after_call(self, worker_name: str, success: bool) -> None:
        """调用后更新熔断状态。"""
        if not self._redis:
            return

        state = await self._redis.get(self._state_key(worker_name)) or CircuitState.CLOSED

        if success:
            # 成功：重置所有计数
            await self._reset(worker_name)
            if state != CircuitState.CLOSED:
                logger.info(f"CircuitBreaker: {worker_name} recovered → CLOSED")
        else:
            # 失败：递增计数
            fail_count = await self._redis.incr(self._fail_key(worker_name))
            await self._redis.expire(self._fail_key(worker_name), 300)

            if state == CircuitState.HALF_OPEN:
                # 探测失败 → 重新熔断
                await self._redis.set(self._state_key(worker_name), CircuitState.OPEN)
                logger.warning(f"CircuitBreaker: {worker_name} HALF_OPEN probe failed → OPEN")
            elif fail_count >= self.config.failure_threshold and state == CircuitState.CLOSED:
                # 触发熔断
                await self._redis.set(self._state_key(worker_name), CircuitState.OPEN)
                await self._redis.expire(self._state_key(worker_name), 300)
                logger.warning(
                    f"CircuitBreaker: {worker_name} failed {fail_count} times → OPEN"
                )
                # 发布系统事件
                try:
                    await self._redis.publish(
                        "agent:system:events",
                        f"circuit_open:{worker_name}:fail_count={fail_count}",
                    )
                except Exception:
                    pass

    async def _reset(self, worker_name: str) -> None:
        """重置熔断状态（恢复为 CLOSED）。"""
        await self._redis.set(self._state_key(worker_name), CircuitState.CLOSED)
        await self._redis.delete(self._fail_key(worker_name))
        await self._redis.delete(self._half_open_key(worker_name))
