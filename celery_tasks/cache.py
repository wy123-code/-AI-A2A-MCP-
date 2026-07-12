"""缓存预热 Celery 任务 —— 定时拉取热门城市/景点数据填充 Redis 缓存。"""
from celery_app import app
from loguru import logger


@app.task(name="celery_tasks.cache.preload_hot_data")
def preload_hot_data():
    """预热高频查询数据到 Redis 缓存（每 5 分钟执行）。

    当前策略：预热热门城市景点、天气数据减少冷启动。
    """
    logger.info("Cache preload: starting hot data warmup")
    # 异步调用需要 asyncio 事件循环，Celery 同步任务内创建
    import asyncio

    async def _warmup():
        from cache.query_cache import get_query_cache
        cache = await get_query_cache()

        hot_cities = ["北京", "上海", "广州", "深圳", "杭州", "成都", "重庆", "西安", "南京", "武汉"]

        preloaded = 0
        for city in hot_cities:
            try:
                # 预热天气缓存（short 档位，5min）
                from agents.worker.weather.tool import weather_tool
                weather_result = await weather_tool(intent="weather_query", slots={"city": city})
                if weather_result and weather_result.get("success"):
                    await cache.set("weather_query", {"city": city}, weather_result)
                    preloaded += 1
            except Exception as e:
                logger.warning(f"Cache preload: weather/{city} skipped: {e}")

            try:
                # 预热景点缓存（static 档位，30min）
                from agents.worker.attraction.tool import recommend_attractions
                attraction_result = await recommend_attractions(intent="attraction_recommend", slots={"city": city, "days": 3})
                if attraction_result and attraction_result.get("success"):
                    await cache.set("attraction_recommend", {"city": city, "days": 3}, attraction_result)
                    preloaded += 1
            except Exception as e:
                logger.warning(f"Cache preload: attraction/{city} skipped: {e}")

        logger.info(f"Cache preload: warmed {preloaded} entries for {len(hot_cities)} hot cities")

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        loop.run_until_complete(_warmup())
    except Exception as e:
        logger.error(f"Cache preload failed: {e}")


@app.task(name="celery_tasks.cache.invalidate_stale_cache")
def invalidate_stale_cache():
    """清理过期缓存条目（每天凌晨执行）。"""
    logger.info("Cache invalidation: checking stale entries")
    # Redis key 自带 TTL 过期，本任务记录缓存统计
    import asyncio

    async def _check():
        from cache.query_cache import get_query_cache
        cache = await get_query_cache()
        await cache._ensure()
        if cache._available:
            keys = await cache._redis.keys(f"{cache.CACHE_PREFIX}*")
            logger.info(f"Cache invalidation: {len(keys)} active cache keys")
        return len(keys) if cache._available else 0

    try:
        loop = asyncio.get_event_loop()
        if loop.is_closed():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
        count = loop.run_until_complete(_check())
        logger.info(f"Cache invalidation complete: {count} keys active")
    except Exception as e:
        logger.error(f"Cache invalidation failed: {e}")
