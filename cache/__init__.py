"""缓存层 —— Redis 工具结果缓存，减少重复外部 API 调用。"""
from .query_cache import QueryCache, get_query_cache

__all__ = ["QueryCache", "get_query_cache"]
