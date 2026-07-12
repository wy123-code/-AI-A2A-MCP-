"""性能指标收集器 —— 基于 Redis Sorted Set 的轻量级指标存储。

优化说明 (P5):
  - 记录每次请求/Worker调用的耗时与成功率
  - 提供统计查询 (P95/P99、平均耗时、成功率)
  - 自动识别慢请求并记录
"""
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis.asyncio as aioredis
from loguru import logger

from config import REDIS_CONFIG

METRICS_PREFIX = "metrics"


class MetricsCollector:
    """轻量级性能指标收集器。

    存储结构：
      metrics:requests:{time_bucket}  → Sorted Set (score=timestamp, member=json)
      metrics:workers:{time_bucket}   → Sorted Set
      metrics:cache_hits:{time_bucket} → String (counter)
      metrics:slow_requests           → List (最近 100 条慢请求)
    """

    def __init__(self):
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

    def _bucket(self, prefix: str) -> str:
        """生成 10 分钟粒度的 metric key bucket。"""
        now = int(time.time())
        bucket_ts = (now // 600) * 600  # 10 分钟窗口
        return f"{METRICS_PREFIX}:{prefix}:{bucket_ts}"

    # ---- 请求级指标 ----

    async def record_request(self, intent: str, duration_ms: int, success: bool) -> None:
        """记录一次 API 请求的耗时和结果。"""
        if not self._redis:
            return
        bucket = self._bucket(f"requests:{intent}")
        member = f"{time.time()}_{duration_ms}_{1 if success else 0}"
        await self._redis.zadd(bucket, {member: time.time()})
        await self._redis.expire(bucket, 7200)  # 保留 2 小时

        # 慢请求记录（> 5 秒）
        if duration_ms > 5000:
            await self._redis.lpush(
                f"{METRICS_PREFIX}:slow_requests",
                f"{datetime.now(timezone.utc).isoformat()} | {intent} | {duration_ms}ms | success={success}"
            )
            await self._redis.ltrim(f"{METRICS_PREFIX}:slow_requests", 0, 99)

    async def record_worker_call(self, worker_name: str, duration_ms: int,
                                 success: bool) -> None:
        """记录一次 Worker 调用的耗时和结果。"""
        if not self._redis:
            return
        bucket = self._bucket(f"workers:{worker_name}")
        member = f"{time.time()}_{duration_ms}_{1 if success else 0}"
        await self._redis.zadd(bucket, {member: time.time()})
        await self._redis.expire(bucket, 7200)

    async def record_cache_hit(self, worker_name: str) -> None:
        """记录一次缓存命中。"""
        if not self._redis:
            return
        bucket = self._bucket(f"cache_hits:{worker_name}")
        await self._redis.incr(bucket)
        await self._redis.expire(bucket, 7200)

    # ---- 统计查询 ----

    async def get_stats(self, window_seconds: int = 3600) -> Dict[str, Any]:
        """获取最近时间窗口的聚合统计。

        Returns:
            {"total_requests": N, "success_rate": 0.95, "avg_duration_ms": 320, ...}
        """
        if not self._redis:
            return {}
        cutoff = time.time() - window_seconds
        total = 0
        total_success = 0
        durations = []

        # 扫描所有请求 bucket
        cursor = 0
        while True:
            cursor, keys = await self._redis.scan(
                cursor, match=f"{METRICS_PREFIX}:requests:*", count=100
            )
            for key in keys:
                members = await self._redis.zrangebyscore(key, cutoff, "+inf")
                for m in members:
                    parts = m.split("_", 2)
                    if len(parts) == 3:
                        _, dur_str, ok_str = parts
                        dur = int(dur_str)
                        durations.append(dur)
                        total += 1
                        if ok_str == "1":
                            total_success += 1
            if cursor == 0:
                break

        if not total:
            return {"total_requests": 0, "success_rate": 0, "avg_duration_ms": 0}

        durations.sort()
        p95_idx = int(len(durations) * 0.95)
        p99_idx = int(len(durations) * 0.99)

        return {
            "total_requests": total,
            "success_rate": round(total_success / total, 4),
            "avg_duration_ms": round(sum(durations) / len(durations)),
            "p50_duration_ms": durations[len(durations) // 2],
            "p95_duration_ms": durations[min(p95_idx, len(durations) - 1)],
            "p99_duration_ms": durations[min(p99_idx, len(durations) - 1)],
        }

    async def get_slow_requests(self, limit: int = 20) -> List[str]:
        """获取最近慢请求列表。"""
        if not self._redis:
            return []
        return await self._redis.lrange(
            f"{METRICS_PREFIX}:slow_requests", 0, limit - 1
        )


# 全局单例
metrics_collector = MetricsCollector()
