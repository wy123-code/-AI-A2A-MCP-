"""Redis 查询结果缓存 —— 三档分层 TTL（static/short/realtime），减少第三方 API 重复调用。

优化说明 (P4):
  - 引入 CACHE_TIERS 配置：static(30min) / short(5min) / realtime(no-cache)
  - 每个意图/工具自动匹配对应缓存档位
  - 新增 batch_preload() 支持 Celery 热点数据预加载
"""
import hashlib
import json
from typing import Any, Dict, List, Optional
from loguru import logger
import redis.asyncio as aioredis
from config import REDIS_CONFIG, CACHE_TIERS, CACHE_TIER_TTL


class QueryCache:
    """基于 Redis 的三档分层结果缓存层。

    TTL 策略（统一由 CACHE_TIERS 驱动）:
      - static:  景点推荐/酒店/旅行团 → 1800s (30min)
      - short:   天气查询 → 300s (5min)
      - realtime: 机票/火车票/租车 → 0 (不缓存)
    """

    CACHE_PREFIX = "cache:tool:"
    DEFAULT_TTL = 300

    # 由 config.CACHE_TIER_TTL 动态推导，保留旧 _TTL_MAP 兼容
    _TTL_MAP = {
        k: CACHE_TIER_TTL.get(CACHE_TIERS.get(k, "short"), 300)
        for k in CACHE_TIERS
    }

    def __init__(self):
        self._redis: Optional[aioredis.Redis] = None
        self._available = False

    async def _ensure(self):
        if self._redis is not None:
            return
        try:
            self._redis = await aioredis.from_url(
                f"redis://{REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}/{REDIS_CONFIG['db']}",
                password=REDIS_CONFIG["password"] or None,
                decode_responses=True,
                socket_connect_timeout=2,
            )
            await self._redis.ping()
            self._available = True
            logger.info("QueryCache: Redis connected")
        except Exception as e:
            logger.warning(f"QueryCache: Redis unavailable, caching disabled: {e}")
            self._available = False

    def _cache_key(self, tool_name: str, params: Dict) -> str:
        canonical = json.dumps(params, sort_keys=True, ensure_ascii=False, default=str)
        digest = hashlib.md5(canonical.encode()).hexdigest()
        return f"{self.CACHE_PREFIX}{tool_name}:{digest}"

    async def get(self, tool_name: str, params: Dict) -> Optional[Dict]:
        await self._ensure()
        if not self._available:
            return None
        try:
            key = self._cache_key(tool_name, params)
            raw = await self._redis.get(key)
            if raw:
                logger.debug("Cache HIT", tool=tool_name)
                return json.loads(raw)
        except Exception:
            pass
        return None

    async def set(self, tool_name: str, params: Dict, result: Dict, ttl: int = None):
        await self._ensure()
        if not self._available:
            return
        # realtime 档位不缓存
        tier = self.get_tier(tool_name)
        if tier == "realtime":
            return
        try:
            key = self._cache_key(tool_name, params)
            ttl = ttl or self._TTL_MAP.get(tool_name, self.DEFAULT_TTL)
            if ttl <= 0:
                return
            await self._redis.setex(key, ttl, json.dumps(result, ensure_ascii=False, default=str))
        except Exception as e:
            logger.warning(f"QueryCache: write failed: {e}")

    @staticmethod
    def get_tier(tool_name: str) -> str:
        """返回工具对应的缓存档位 (static/short/realtime)。"""
        intent = tool_name.replace("_query", "")
        return CACHE_TIERS.get(tool_name, CACHE_TIERS.get(intent, "short"))

    async def batch_preload(self, entries: List[Dict]) -> int:
        """批量预加载缓存（供 Celery 定时任务调用）。

        entries: [{"tool_name": "...", "params": {...}, "result": {...}}, ...]
        返回成功写入条数。
        """
        await self._ensure()
        if not self._available:
            return 0
        count = 0
        for entry in entries:
            try:
                await self.set(
                    tool_name=entry["tool_name"],
                    params=entry["params"],
                    result=entry["result"],
                )
                count += 1
            except Exception as e:
                logger.warning(f"QueryCache: batch_preload failed for {entry.get('tool_name')}: {e}")
        logger.info(f"QueryCache: batch_preload wrote {count}/{len(entries)} entries")
        return count


_query_cache: Optional[QueryCache] = None


async def get_query_cache() -> QueryCache:
    global _query_cache
    if _query_cache is None:
        _query_cache = QueryCache()
    return _query_cache
