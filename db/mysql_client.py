"""MySQL 数据库客户端 —— 仅允许 SELECT 查询，提供健康检查。"""

import asyncio
import pymysql
from pymysql.cursors import DictCursor
from loguru import logger
from config import MYSQL_CONFIG


class MySQLClient:
    """MySQL 连接与查询客户端，仅放行 SELECT 语句，结果以字典列表返回。"""

    def __init__(self):
        self.config = MYSQL_CONFIG

    def _get_connection(self):
        return pymysql.connect(
            host=self.config["host"],
            port=self.config["port"],
            user=self.config["user"],
            password=self.config["password"],
            database=self.config["database"],
            charset=self.config["charset"],
            cursorclass=DictCursor,
        )

    def _execute_sync(self, sql: str) -> list[dict]:
        sql_upper = sql.strip().upper()
        if not sql_upper.startswith("SELECT"):
            raise ValueError("Only SELECT statements are allowed")

        conn = self._get_connection()
        try:
            with conn.cursor() as cursor:
                cursor.execute(sql)
                result = cursor.fetchall()
                return result
        finally:
            conn.close()

    async def execute_query(self, sql: str) -> list[dict]:
        return await asyncio.to_thread(self._execute_sync, sql)

    def health_check(self) -> bool:
        try:
            conn = self._get_connection()
            conn.ping()
            conn.close()
            return True
        except Exception as e:
            logger.error(f"MySQL health check failed: {e}")
            return False


mysql_client = MySQLClient()
