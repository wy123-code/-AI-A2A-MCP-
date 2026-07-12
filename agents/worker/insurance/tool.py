"""保险查询工具 —— 通过 A2A Server 将槽位转为 SQL 查询 insurance 表。"""

from typing import Any, Dict
from loguru import logger
from mcp.mcp_server import MCPServer
from a2a.a2a_server import A2AServer

INSURANCE_TABLE_SCHEMA = """
CREATE TABLE `insurance` (
    id INT AUTO_INCREMENT PRIMARY KEY,
    insurance_type VARCHAR(50) NOT NULL,
    name VARCHAR(100) NOT NULL,
    coverage TEXT,
    price DECIMAL(10,2),
    duration_days INT,
    provider VARCHAR(100),
    INDEX idx_type (insurance_type)
);
"""

insurance_mcp_server = MCPServer(name="insurance")
insurance_a2a = A2AServer(
    tool_name="insurance_query",
    mcp_server=insurance_mcp_server,
    table_name="insurance",
    table_schema=INSURANCE_TABLE_SCHEMA,
)


async def insurance_tool(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"InsuranceTool: querying with slots={slots}")
    result = await insurance_a2a.query(intent=intent, slots=slots)
    logger.info(f"InsuranceTool: success={result.get('success')}")
    return result
