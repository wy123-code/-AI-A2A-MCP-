"""租车查询工具 —— 通过 A2A Server 将槽位转为 SQL 查询 car_rental 表。"""

from typing import Any, Dict
from loguru import logger
from mcp.mcp_server import MCPServer
from a2a.a2a_server import A2AServer

CAR_RENTAL_TABLE_SCHEMA = """
CREATE TABLE `car_rental` (
    id INT AUTO_INCREMENT PRIMARY KEY,
    city VARCHAR(50) NOT NULL,
    car_model VARCHAR(100),
    car_type VARCHAR(50),
    price_per_day DECIMAL(10,2),
    available_from DATE,
    available_to DATE,
    company VARCHAR(100),
    INDEX idx_city_date (city, available_from)
);
"""

car_rental_mcp_server = MCPServer(name="car_rental")
car_rental_a2a = A2AServer(
    tool_name="car_rental_query",
    mcp_server=car_rental_mcp_server,
    table_name="car_rental",
    table_schema=CAR_RENTAL_TABLE_SCHEMA,
)


async def car_rental_tool(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"CarRentalTool: querying with slots={slots}")
    result = await car_rental_a2a.query(intent=intent, slots=slots)
    logger.info(f"CarRentalTool: success={result.get('success')}")
    return result
