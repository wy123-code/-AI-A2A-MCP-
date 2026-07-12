"""A2A (Agent-to-Agent) 服务端 —— 接收槽位参数，通过 LLM 生成 SQL，委托 MCP Server 执行。"""

import asyncio
import json
import re
from typing import Any, Dict
from loguru import logger
from config import LLM_CONFIG
from prompts import SQL_GENERATION_PROMPT
from mcp.mcp_server import MCPServer
from llm.client_pool import llm_manager


class A2AServer:
    """A2A 服务：将意图+槽位转为自然语言 SQL 查询，由 MCP Server 直接执行。"""

    def __init__(self, tool_name: str, mcp_server: MCPServer, table_name: str, table_schema: str):
        self.tool_name = tool_name
        self.table_name = table_name
        self.table_schema = table_schema
        self.mcp_server = mcp_server

    def _extract_sql(self, text: str) -> str:
        """从LLM输出文本中提取SQL语句
        
        采用多种策略提取SQL，按优先级依次尝试：
        1. 匹配 ```sql ... ``` 代码块格式
        2. 匹配 ``` ... ``` 通用代码块格式
        3. 提取以SELECT开头的连续行
        
        Args:
            text: LLM输出的原始文本
            
        Returns:
            提取出的SQL语句字符串，如果无法提取则返回原始文本
        """
        text = text.strip()
        
        # 策略1：匹配 ```sql ... ``` 格式的代码块
        sql_match = re.search(r"```sql\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
        if sql_match:
            return sql_match.group(1).strip()
        
        # 策略2：匹配 ``` ... ``` 通用代码块格式
        sql_match = re.search(r"```\s*(.*?)\s*```", text, re.DOTALL)
        if sql_match:
            return sql_match.group(1).strip()
        
        # 策略3：提取以SELECT开头的连续行
        lines = text.split("\n")
        sql_lines = []
        started = False
        for line in lines:
            if line.strip().upper().startswith("SELECT"):
                started = True
            if started:
                sql_lines.append(line)
        if sql_lines:
            return "\n".join(sql_lines)
        return text

    async def query(self, intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
        """通过LLM生成SQL并执行查询
        
        工作流程：
        1. 构建SQL生成提示词
        2. 调用LLM生成SQL语句
        3. 从LLM输出中提取SQL
        4. 通过MCP Server执行SQL
        5. 返回查询结果
        
        Args:
            intent: 用户意图标识（如 weather_query, hotel_query 等）
            slots: 槽位参数字典，包含查询所需的具体参数
            
        Returns:
            查询结果字典，包含success、error、data字段
        """
        # 构建SQL生成提示词
        prompt = SQL_GENERATION_PROMPT.format(
            table_name=self.table_name,
            table_schema=self.table_schema,
            intent=intent,
            slots=json.dumps(slots, ensure_ascii=False, indent=2),
        )

        logger.info(f"A2A Server [{self.tool_name}]: generating SQL via LLM")

        try:
            response = await asyncio.to_thread(
                lambda: llm_manager.get_client("default").chat.completions.create(
                    model=LLM_CONFIG["model"],
                    messages=[{"role": "user", "content": prompt}],
                    temperature=LLM_CONFIG["temperature"],
                    max_tokens=LLM_CONFIG["max_tokens"],
                    timeout=30.0,
                )
            )
            raw_sql = response.choices[0].message.content
            sql = self._extract_sql(raw_sql)
            logger.info(f"A2A Server [{self.tool_name}]: generated SQL: {sql[:150]}")
        except Exception as e:
            logger.error(f"A2A Server [{self.tool_name}]: LLM SQL generation failed: {e}")
            return {"success": False, "error": f"SQL generation failed: {e}", "data": []}

        # 通过MCP Server执行SQL查询
        result = await self.mcp_server.execute_sql(sql)
        return result
