"""MCP Server —— 对外暴露 `execute_sql` 工具，仅允许 SELECT 查询，限定允许的表。

安全防护四层机制：
  1. 白名单校验：仅允许 ALLOWED_TABLES 中的表
  2. 仅放行 SELECT：禁止 INSERT/UPDATE/DELETE/DDL
  3. 多语句拦截：禁止分号注入
  4. 结果集行数限制 + 敏感字段过滤（本次新增）
"""

import re
from typing import Any, Dict, List
from loguru import logger
from db.mysql_client import mysql_client

ALLOWED_TABLES = {"weather", "tour_group", "hotel", "car_rental", "insurance", "flight"}

# === 新增：结果集安全限制 ===
MAX_RESULT_ROWS = 50  # 单次查询全局最大返回行数

# 各表的推荐返回行数 —— 防止 LLM 处理过多结果导致响应变慢
TABLE_ROW_LIMITS = {
    "hotel": 5,        # 酒店：推荐5家
    "tour_group": 10,  # 旅行团：推荐10个
    "car_rental": 5,   # 租车：推荐5家
    "insurance": 5,    # 保险：推荐5个
    "flight": 10,      # 航班：推荐10班
    "weather": 1,      # 天气：只需1条
}

# 敏感字段黑名单 —— 即使表中存在这些列也不会返回给 LLM
SENSITIVE_COLUMNS = {
    "password", "password_hash", "secret", "token", "api_key",
    "private_key", "credit_card", "ssn", "id_number", "phone_number",
    "email_address", "real_name", "address_detail",
}


def _filter_sensitive_columns(rows: List[dict]) -> List[dict]:
    """过滤结果集中的敏感字段，防止敏感信息泄露给 LLM。

    Args:
        rows: 数据库查询返回的行列表

    Returns:
        过滤后的行列表（敏感列已移除）
    """
    if not rows:
        return rows
    filtered = []
    for row in rows:
        clean_row = {
            k: v for k, v in row.items()
            if k.lower() not in SENSITIVE_COLUMNS
        }
        filtered.append(clean_row)

    removed_cols = set(rows[0].keys()) - set(filtered[0].keys()) if rows else set()
    if removed_cols:
        logger.info(f"MCP Server: filtered sensitive columns: {removed_cols}")
    return filtered


def _append_limit_if_missing(sql: str, max_rows: int = MAX_RESULT_ROWS) -> str:
    """如果 SQL 中没有 LIMIT 子句，自动追加行数限制。

    这确保 LLM 生成的 SQL 即使忘记加 LIMIT，也不会返回海量数据。

    Args:
        sql: 原始 SQL 语句
        max_rows: 最大返回行数

    Returns:
        追加了 LIMIT 的 SQL（如果原来没有 LIMIT）
    """
    sql_upper = sql.upper().rstrip(";").strip()
    # 已有 LIMIT 则不重复追加
    if re.search(r'\bLIMIT\s+\d+', sql_upper):
        # 但如果限制超过最大值，则替换为安全上限
        existing_limit = int(re.search(r'\bLIMIT\s+(\d+)', sql_upper).group(1))
        if existing_limit > max_rows:
            sql = re.sub(
                r'\bLIMIT\s+\d+', f'LIMIT {max_rows}', sql, count=1, flags=re.IGNORECASE
            )
        return sql
    # 检查是否有子查询（含子查询不追加 LIMIT，避免语法错误）
    if sql_upper.count("SELECT") > 1:
        return sql
    return f"{sql.rstrip(';').strip()} LIMIT {max_rows}"


class MCPServer:

    """MCP 服务端：管理工具注册并执行 MySQL 查询，四层安全防护。

    安全机制：
      1. 仅放行 SELECT 语句（禁止 INSERT/UPDATE/DELETE/DDL）
      2. 多语句拦截（禁止分号注入）
      3. 表白名单校验（只允许 ALLOWED_TABLES 中的表）
      4. 结果集行数限制 + 敏感字段过滤（本次新增）
    """

    def __init__(self, name: str = "default"):
        self.name = name

    async def execute_sql(self, sql: str) -> Dict[str, Any]:
        sql_clean = sql.strip().rstrip(";").strip()
        sql_upper = sql_clean.upper()

        # === 第1层：仅放行 SELECT ===
        if not sql_upper.startswith("SELECT"):
            logger.warning(f"MCP Server [{self.name}]: blocked non-SELECT: {sql[:80]}")
            return {"success": False, "error": "Only SELECT statements are allowed", "data": []}

        # === 第2层：多语句拦截 ===
        if ";" in sql_clean[:-1]:
            logger.warning(f"MCP Server [{self.name}]: blocked multi-statement SQL")
            return {"success": False, "error": "Multiple statements not allowed", "data": []}

        # === 第3层：表白名单校验 ===
        tables = {t.lower() for t in re.findall(r'\b(?:FROM|JOIN)\s+`?(\w+)`?', sql_upper)}
        if not tables.issubset(ALLOWED_TABLES):
            unknown = tables - ALLOWED_TABLES
            logger.warning(f"MCP Server [{self.name}]: blocked tables {unknown}")
            return {"success": False, "error": f"Table not allowed: {', '.join(unknown)}", "data": []}

        # === 第4层：自动追加行数限制（按表控制，防止海量数据导致响应慢） ===
        table_limit = MAX_RESULT_ROWS
        for tbl in tables:
            if tbl in TABLE_ROW_LIMITS:
                table_limit = min(table_limit, TABLE_ROW_LIMITS[tbl])
        sql = _append_limit_if_missing(sql, max_rows=table_limit)

        logger.info(f"MCP Server [{self.name}]: executing SQL: {sql[:150]}")
        try:
            result = await mysql_client.execute_query(sql)
            # === 敏感字段过滤 ===
            result = _filter_sensitive_columns(result)
            logger.info(f"MCP Server [{self.name}]: returned {len(result)} rows (sensitive columns filtered)")
            return {"success": True, "error": None, "data": result}
        except Exception as e:
            logger.error(f"MCP Server [{self.name}]: query failed: {e}")
            return {"success": False, "error": str(e), "data": []}

