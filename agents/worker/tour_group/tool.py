"""旅行团查询工具 —— 通过 A2A Server 将槽位转为 SQL 查询 tour_group 表。"""

from typing import Any, Dict
from loguru import logger
from mcp.mcp_server import MCPServer
from a2a.a2a_server import A2AServer

TOUR_GROUP_TABLE_SCHEMA = """
CREATE TABLE `tour_group` (
    id INT AUTO_INCREMENT PRIMARY KEY,
    name VARCHAR(100) NOT NULL,
    destination VARCHAR(100) NOT NULL,
    departure_city VARCHAR(50),
    start_date DATE,
    end_date DATE,
    duration_days INT,
    price DECIMAL(10,2),
    max_participants INT,
    current_participants INT,
    description TEXT,
    INDEX idx_dest_date (destination, start_date)
);
"""

tour_group_mcp_server = MCPServer(name="tour_group")
tour_group_a2a = A2AServer(
    tool_name="tour_group_query",
    mcp_server=tour_group_mcp_server,
    table_name="tour_group",
    table_schema=TOUR_GROUP_TABLE_SCHEMA,
)


async def tour_group_tool(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"TourGroupTool: querying with slots={slots}")
    result = await tour_group_a2a.query(intent=intent, slots=slots)
    logger.info(f"TourGroupTool: success={result.get('success')}")
    return result
