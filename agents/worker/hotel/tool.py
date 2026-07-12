"""酒店查询工具 —— 通过 A2A Server 将槽位转为 SQL 查询 hotel 表。"""

from typing import Any, Dict
from loguru import logger
from mcp.mcp_server import MCPServer
from a2a.a2a_server import A2AServer

HOTEL_TABLE_SCHEMA = """
CREATE TABLE `hotel` (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    city VARCHAR(50) NOT NULL,
    district VARCHAR(100),
    star_rating INT,
    price_per_night DECIMAL(10,2),
    available_rooms INT,
    amenities TEXT,
    address VARCHAR(200),
    INDEX idx_city (city)
);
"""

hotel_mcp_server = MCPServer(name="hotel")
hotel_a2a = A2AServer(
    tool_name="hotel_query",
    mcp_server=hotel_mcp_server,
    table_name="hotel",
    table_schema=HOTEL_TABLE_SCHEMA,
)


async def hotel_tool(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"HotelTool: querying with slots={slots}")
    result = await hotel_a2a.query(intent=intent, slots=slots)
    logger.info(f"HotelTool: success={result.get('success')}")
    return result
