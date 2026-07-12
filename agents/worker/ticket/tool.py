"""票务查询工具 —— 覆盖机票、火车票、船票、演唱会票，由 LLM 生成模拟票务数据。"""

import asyncio
import json
from typing import Any, Dict
from loguru import logger
from config import LLM_CONFIG
from llm.client_pool import llm_manager
from prompts import TICKET_QUERY_PROMPT

# 国内实际运营的客运航线白名单（bidirectional: frozenset 忽略顺序）
VALID_FERRY_ROUTES: set[frozenset[str]] = {
    # 珠三角
    frozenset({"深圳", "珠海"}),
    frozenset({"深圳", "香港"}),
    frozenset({"深圳", "澳门"}),
    frozenset({"珠海", "香港"}),
    frozenset({"珠海", "澳门"}),
    frozenset({"香港", "澳门"}),
    # 渤海湾
    frozenset({"大连", "烟台"}),
    frozenset({"大连", "威海"}),
    frozenset({"烟台", "大连"}),
    frozenset({"烟台", "威海"}),
    # 琼州海峡
    frozenset({"海口", "北海"}),
    frozenset({"海口", "湛江"}),
    frozenset({"海口", "徐闻"}),
    frozenset({"海口", "海安"}),
    # 长三角
    frozenset({"上海", "舟山"}),
    frozenset({"上海", "普陀山"}),
    frozenset({"舟山", "普陀山"}),
    # 厦门
    frozenset({"厦门", "鼓浪屿"}),
}


def _validate_ferry_route(departure: str, arrival: str) -> str | None:
    """校验船票航线是否合法。返回 None 表示合法，否则返回提示信息。"""
    route = frozenset({departure.strip(), arrival.strip()})
    if route in VALID_FERRY_ROUTES:
        return None
    # 收集白名单中与出发地/到达地相关的航线作为建议
    related = set()
    for r in VALID_FERRY_ROUTES:
        if departure.strip() in r:
            related.update(r)
    related.discard(departure.strip())
    suggestions = "、".join(sorted(related)[:5]) if related else "临近港口城市"
    return (
        f"抱歉，{departure.strip()} 到 {arrival.strip()} 之间暂无客运航线。\n\n"
        f"目前国内客运船票主要覆盖以下航线：珠三角（深圳/珠海/香港/澳门之间）、"
        f"渤海湾（大连/烟台/威海之间）、琼州海峡（海口/北海/湛江/徐闻之间）、"
        f"长三角（上海/舟山/普陀山之间），以及厦门至鼓浪屿。\n\n"
        f"从 {departure.strip()} 出发的航线有：{suggestions}。"
        f"如需从 {departure.strip()} 到 {arrival.strip()}，建议选择飞机或火车。"
    )


async def query_ticket(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    # 船票航线校验：不合理航线直接返回，不调用 LLM
    if intent == "ship_ticket":
        departure = slots.get("departure_port") or slots.get("departure_city", "")
        arrival = slots.get("arrival_port") or slots.get("arrival_city", "")
        if departure and arrival:
            invalid_msg = _validate_ferry_route(departure, arrival)
            if invalid_msg is not None:
                logger.info(f"TicketQuery: ferry route rejected {departure} -> {arrival}")
                return {"success": True, "error": None, "data": invalid_msg}

    ticket_type_map = {
        "flight_ticket": "飞机票",
        "train_ticket": "火车票",
        "ship_ticket": "船票",
        "concert_ticket": "演唱会票",
    }
    ticket_type = ticket_type_map.get(intent, "票务")

    from datetime import date
    prompt = TICKET_QUERY_PROMPT.format(
        ticket_type=ticket_type,
        slots=json.dumps(slots, ensure_ascii=False, indent=2),
        today=date.today().strftime("%Y-%m-%d"),
    )

    logger.info(f"TicketQuery: querying {ticket_type} with slots={slots}")
    client = llm_manager.get_client("default")

    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=LLM_CONFIG["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_CONFIG["temperature"],
                max_tokens=LLM_CONFIG["max_tokens"],
                response_format={"type": "json_object"},
                timeout=30.0,
            )
        )
        result = json.loads(response.choices[0].message.content)
        result["_disclaimer"] = "以上票务数据为AI模拟生成，仅供参考，实际价格和班次请以官方渠道为准"
        logger.info(f"TicketQuery: got {len(result.get('results', []))} results")
        return {"success": True, "error": None, "data": result}
    except Exception as e:
        logger.error(f"TicketQuery failed: {e}")
        return {"success": False, "error": str(e), "data": {}}
