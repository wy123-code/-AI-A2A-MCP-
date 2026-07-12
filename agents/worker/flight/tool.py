"""航班查询工具 —— 通过 A2A Server 将槽位转为 SQL 查询 flight 表。"""

from typing import Any, Dict, List
from loguru import logger
from mcp.mcp_server import MCPServer
from a2a.a2a_server import A2AServer

FLIGHT_TABLE_SCHEMA = """
CREATE TABLE `flight` (
    id INT AUTO_INCREMENT PRIMARY KEY,
    flight_no VARCHAR(10) NOT NULL,
    airline VARCHAR(50) NOT NULL,
    departure_city VARCHAR(50) NOT NULL,
    departure_airport VARCHAR(100) NOT NULL,
    arrival_city VARCHAR(50) NOT NULL,
    arrival_airport VARCHAR(100) NOT NULL,
    departure_time VARCHAR(5) NOT NULL,
    arrival_time VARCHAR(5) NOT NULL,
    duration INT NOT NULL,
    price DECIMAL(10,0) NOT NULL,
    aircraft_type VARCHAR(50) NOT NULL,
    flight_date DATE NOT NULL,
    on_time TINYINT DEFAULT 1,
    INDEX idx_flight_date (flight_date),
    INDEX idx_dep_city (departure_city),
    INDEX idx_arr_city (arrival_city),
    INDEX idx_flight_no (flight_no),
    INDEX idx_dep_arr (departure_city, arrival_city)
);
"""

# 列名映射：MySQL 英文字段 → 中文
_FLIGHT_COLUMN_MAP = {
    "flight_no": "航班号",
    "airline": "航空公司",
    "departure_city": "出发城市",
    "departure_airport": "出发机场",
    "arrival_city": "到达城市",
    "arrival_airport": "到达机场",
    "departure_time": "出发时间",
    "arrival_time": "到达时间",
    "duration": "历时(分钟)",
    "price": "票价(元)",
    "aircraft_type": "机型",
    "flight_date": "航班日期",
    "on_time": "准点率",
    "id": "ID",
}

flight_mcp_server = MCPServer(name="flight")
flight_a2a = A2AServer(
    tool_name="flight_query",
    mcp_server=flight_mcp_server,
    table_name="flight",
    table_schema=FLIGHT_TABLE_SCHEMA,
)


def _normalize_columns(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """将 MySQL 返回的英文字段名映射为中文列名。"""
    if not rows:
        return rows
    return [
        {_FLIGHT_COLUMN_MAP.get(k, k): v for k, v in row.items()}
        for row in rows
    ]


async def flight_tool(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    logger.info(f"FlightTool: querying with slots={slots}")
    result = await flight_a2a.query(intent=intent, slots=slots)
    if result.get("success") and isinstance(result.get("data"), list):
        result["data"] = _normalize_columns(result["data"])
    logger.info(f"FlightTool: success={result.get('success')}")
    return result
