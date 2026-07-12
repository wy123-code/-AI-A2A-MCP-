"""Milvus 向量数据库客户端 —— 提供向量检索与健康检查。"""

import asyncio
import time
from pymilvus import connections, Collection
from loguru import logger
from config import MILVUS_CONFIG


class MilvusClient:
    """Milvus 向量检索客户端，支持 embedding 相似搜索与标量过滤。"""

    def __init__(self):
        self.config = MILVUS_CONFIG
        self._connected = None  # None=未尝试, True=已连接, False=最近失败
        self._collection = None
        self._last_attempt = 0.0

    def _connect_sync(self):
        now = time.time()
        if self._connected is True:
            return
        if self._connected is False and now - self._last_attempt < 30:
            return
        self._last_attempt = now
        try:
            connections.connect(
                alias="default",
                host=self.config["host"],
                port=self.config["port"],
                db_name=self.config["database_name"],
            )
            self._collection = Collection(self.config["collection_name"])
            self._collection.load()
            self._connected = True
            logger.info(f"Milvus connected: {self.config['host']}:{self.config['port']}")
        except Exception as e:
            logger.error(f"Milvus connection failed: {e}")
            self._connected = False

    async def connect(self):
        if self._connected is True:
            return
        await asyncio.to_thread(self._connect_sync)

    def _search_sync(self, query_vector: list[float], top_k: int = 5, filter_expr: str = None) -> list[dict]:
        if self._connected is not True:
            self._connect_sync()
        if self._connected is not True:
            logger.warning("Milvus not available, returning empty results")
            return []

        try:
            search_params = {
                "metric_type": MILVUS_CONFIG["metric_type"],
                "params": {"nprobe": MILVUS_CONFIG["nprobe"]},
            }
            results = self._collection.search(
                data=[query_vector],
                anns_field="embedding",
                param=search_params,
                limit=top_k,
                expr=filter_expr,
                output_fields=["name", "description", "tags", "city"],
            )
            hits = []
            for hits_list in results:
                for hit in hits_list:
                    hits.append({
                        "id": hit.id,
                        "score": hit.score,
                        **hit.entity.to_dict(),
                    })
            return hits
        except Exception as e:
            logger.error(f"Milvus search failed: {e}")
            return []

    async def search(self, query_vector: list[float], top_k: int = 5, filter_expr: str = None) -> list[dict]:
        return await asyncio.to_thread(self._search_sync, query_vector, top_k, filter_expr)

    def health_check(self) -> bool:
        try:
            self._connect_sync()
            return self._connected
        except Exception:
            return False


milvus_client = MilvusClient()
