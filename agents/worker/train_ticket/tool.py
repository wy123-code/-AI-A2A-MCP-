"""火车票查询工具 —— 通过 12306 MCP 服务查询真实火车票信息。

查询流程：
1. 并行调用 get-station-code-of-citys + get-current-date
2. 调用 get-tickets 获取车票列表
3. 解析原始文本为结构化 JSON，确保车次/时间等字段稳定传递
"""

import asyncio
import re
from typing import Any, Dict, List

from loguru import logger

from config import MCP_SERVERS
from mcp.mcp_client import get_mcp_client

# 车站编码缓存：{城市名: {"station_code": "BJP", "station_name": "北京"}}
_station_cache: Dict[str, Dict[str, str]] = {}

# 电报码反查表：{telecode: station_name}，由 _update_station_cache 自动构建
_telecode_to_name: Dict[str, str] = {}

# 车次号正则：G/D/C/K/Z/T/S + 数字
_TRAIN_NO_PATTERN = re.compile(r"^[GDCKZTS]\d+")

# 层次化格式：匹配车次行
# 例: G547 高速(telecode:VNP) -> 上海虹桥(telecode:AOH) 06:18 -> 12:11 历时：05:53
_TRAIN_LINE_RE = re.compile(
    r"^([GDCKZTS]\d+)\s+\S+\(telecode:(\w+)\)\s*->\s*(.+?)\(telecode:(\w+)\)"
    r"\s*(\d{1,2}:\d{2})\s*->\s*(\d{1,2}:\d{2})\s*历时[：:]\s*(\d{1,2}:\d{2})"
)

# 匹配座位详情行: - 商务座: 剩余13张票 2318元
_SEAT_RE = re.compile(r"-\s*(.+?)[：:]\s*(.+?)\s+(\d+)元")

# 列车类型关键词（用于区分站名和车型）
_TRAIN_TYPE_NAMES = {"高速", "动车", "城际", "快速", "直达", "特快", "普快", "临客"}


def _get_cached_station(city: str) -> dict | None:
    """从缓存中查找城市车站信息。"""
    # 精确匹配
    if city in _station_cache:
        return _station_cache[city]
    # 模糊匹配：缓存 key 包含城市名
    for key, info in _station_cache.items():
        if city in key or key in city:
            return info
    return None


def _update_station_cache(data: dict) -> None:
    """将 MCP 返回的车站数据写入缓存，同时构建电报码反查表。"""
    for key, value in data.items():
        if isinstance(value, dict) and value.get("station_code"):
            code = value.get("station_code", "")
            name = value.get("station_name", key)
            _station_cache[key] = {
                "station_code": code,
                "station_name": name,
            }
            if code and code not in _telecode_to_name:
                _telecode_to_name[code] = name


def _get_station_name_by_code(telecode: str) -> str:
    """通过电报码反查车站名称（优先查反向表，再遍历缓存）。"""
    if name := _telecode_to_name.get(telecode):
        return name
    for info in _station_cache.values():
        if info.get("station_code") == telecode:
            return info.get("station_name", "")
    return ""


def _detect_format(text: str) -> str:
    """检测 MCP 返回数据的格式类型。

    Returns:
        "hierarchical" — 管道符分隔的层次化格式（车次行 + 缩进座位行）
        "space_table"  — 空格分隔的二维表格
    """
    first_line = text.strip().split("\n")[0].strip() if text.strip() else ""
    if "|" in first_line and "->" in first_line:
        return "hierarchical"
    return "space_table"


def _parse_hierarchical_format(text: str) -> List[Dict[str, str]]:
    """解析 MCP 层次化格式：车次行 + 缩进座位详情行。

    输入示例:
        车次|出发站 -> 到达站|出发时间 -> 到达时间|历时
        G547 高速(telecode:VNP) -> 上海虹桥(telecode:AOH) 06:18 -> 12:11 历时：05:53
        - 商务座: 剩余13张票 2318元
        - 一等座: 有票 1035元

    只返回至少有一个座位类型有余票的车次。
    """
    lines = text.strip().split("\n")
    trains: List[Dict[str, str]] = []
    current: dict | None = None

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue

        # 跳过表头行（含 | 但不是车次行）
        if "|" in stripped and not _TRAIN_NO_PATTERN.match(stripped.split()[0] if stripped.split() else ""):
            continue

        # 匹配车次行
        train_match = _TRAIN_LINE_RE.match(stripped)
        if train_match:
            # 保存上一趟车次
            if current and current.get("_seats"):
                trains.append(_flatten_train_record(current))

            dep_telecode = train_match.group(2)
            dep_name = _get_station_name_by_code(dep_telecode)
            dep_display = f"{dep_name}({dep_telecode})" if dep_name else dep_telecode

            arr_name = train_match.group(3).strip()
            arr_telecode = train_match.group(4)
            arr_display = f"{arr_name}({arr_telecode})"

            current = {
                "车次": train_match.group(1),
                "出发站": dep_display,
                "到达站": arr_display,
                "出发时间": train_match.group(5),
                "到达时间": train_match.group(6),
                "历时": train_match.group(7),
                "_seats": [],
            }
            continue

        # 匹配座位行
        if stripped.startswith("- ") and current is not None:
            seat_match = _SEAT_RE.match(stripped)
            if seat_match:
                seat_type = seat_match.group(1)
                availability = seat_match.group(2).strip()
                price = seat_match.group(3)

                # 跳过无票/售罄的座位类型
                if "无票" in availability or "售罄" in availability:
                    continue

                # 格式化座位值：有票仅显示价格，限票显示余量
                if "有票" in availability:
                    seat_value = f"{price}元"
                else:
                    seat_value = f"{price}元 ({availability})"

                current["_seats"].append((seat_type, seat_value))

    # 最后一趟车次
    if current and current.get("_seats"):
        trains.append(_flatten_train_record(current))

    return trains


def _flatten_train_record(train: dict) -> Dict[str, str]:
    """将内部 _seats 列表展开为扁平的列名→值映射。"""
    seats = train.pop("_seats", [])
    result: Dict[str, str] = {k: v for k, v in train.items()}
    for seat_type, seat_value in seats:
        result[seat_type] = seat_value
    return result


async def query_train_ticket(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """通过 12306 MCP 查询火车票。

    Args:
        intent: 意图标识（固定为 "train_ticket"）
        slots: 槽位字典，包含 departure_city, arrival_city, departure_date
    """
    departure_city = str(slots.get("departure_city", "")).strip()
    arrival_city = str(slots.get("arrival_city", "")).strip()
    departure_date = str(slots.get("departure_date", "")).strip()

    if not departure_city or not arrival_city:
        return {
            "success": False,
            "error": "缺少出发城市或到达城市",
            "data": [],
        }

    logger.info(
        f"TrainTicketQuery: {departure_city} -> {arrival_city}, date={departure_date}"
    )

    mcp_config = MCP_SERVERS.get("12306-mcp")
    if not mcp_config:
        logger.info("TrainTicketQuery: 12306 MCP not configured, using LLM fallback")
        return await _query_via_llm(intent, slots)

    # 尝试 MCP，失败则用 LLM 兜底
    try:
        return await _query_via_mcp(intent, slots, mcp_config)
    except Exception as e:
        logger.warning(f"TrainTicketQuery: MCP failed ({e}), falling back to LLM")
        return await _query_via_llm(intent, slots)


async def _query_via_mcp(intent: str, slots: Dict[str, Any],
                          mcp_config: dict) -> Dict[str, Any]:
    """通过 12306 MCP 查询火车票。"""
    departure_city = str(slots.get("departure_city", "")).strip()
    arrival_city = str(slots.get("arrival_city", "")).strip()
    departure_date = str(slots.get("departure_date", "")).strip()
    client = await get_mcp_client("12306-mcp", mcp_config["url"])

    # Step 1: 查车站编码（优先缓存）+ 日期（并行）
    from_cached = _get_cached_station(departure_city)
    to_cached = _get_cached_station(arrival_city)

    if from_cached and to_cached:
        # 缓存命中，只需查日期
        from_code, from_name = from_cached["station_code"], from_cached["station_name"]
        to_code, to_name = to_cached["station_code"], to_cached["station_name"]
        station_data = None
        date_result = await client.call_tool("get-current-date", {})
        logger.info(f"TrainTicketQuery: station cache HIT for {departure_city}/{arrival_city}")
    else:
        # 缓存未命中，并行查车站编码 + 日期
        station_coro = client.call_tool(
            "get-station-code-of-citys",
            {"citys": f"{departure_city}|{arrival_city}"},
        )
        date_coro = client.call_tool("get-current-date", {})
        station_result, date_result = await asyncio.gather(station_coro, date_coro)

        if not station_result.get("success"):
            return {
                "success": False,
                "error": f"车站编码查询失败: {station_result.get('error', '')}",
                "data": [],
            }

        station_data = station_result["data"]
        if not isinstance(station_data, dict):
            return {
                "success": False,
                "error": "车站编码返回格式异常",
                "data": [],
            }

        # 写入缓存
        _update_station_cache(station_data)

        from_code = _extract_station_code(station_data, departure_city)
        to_code = _extract_station_code(station_data, arrival_city)
        from_name = _extract_station_name(station_data, departure_city)
        to_name = _extract_station_name(station_data, arrival_city)

    if not from_code:
        return {
            "success": False,
            "error": f"未找到出发城市 '{departure_city}' 的车站编码",
            "data": [],
        }
    if not to_code:
        return {
            "success": False,
            "error": f"未找到到达城市 '{arrival_city}' 的车站编码",
            "data": [],
        }

    logger.info(
        f"TrainTicketQuery: stations {from_name}({from_code}) -> {to_name}({to_code})"
    )

    # Step 2: 用服务器日期标准化用户输入的日期
    from datetime import date as dt_date
    real_today = dt_date.today()
    if date_result.get("success"):
        raw = str(date_result["data"]).strip()
        import re
        m = re.search(r"(\d{4})[-/](\d{1,2})[-/](\d{1,2})", raw)
        if m:
            real_today = dt_date(int(m.group(1)), int(m.group(2)), int(m.group(3)))

    date_str = _normalize_date(departure_date, real_today)

    # Step 3: 查询车票
    ticket_result = await client.call_tool(
        "get-tickets",
        {
            "date": date_str,
            "fromStation": from_code,
            "toStation": to_code,
        },
    )

    # Step 4: 过滤无票车次 → 解析为结构化数据
    ticket_result = _filter_available_tickets(ticket_result)

    raw_data = ticket_result.get("data", "")
    logger.info(f"TrainTicketQuery raw_data type={type(raw_data).__name__}, "
                f"preview={str(raw_data)[:300]}")

    # 尝试解析：文本格式（空格表）或 JSON 格式
    if isinstance(raw_data, str):
        trains = _parse_ticket_text(raw_data)
    elif isinstance(raw_data, list):
        # MCP 直接返回了列表
        trains = raw_data
    elif isinstance(raw_data, dict):
        # MCP 返回了字典，可能是 {"trains": [...]} 或其他结构
        trains = raw_data.get("trains", raw_data.get("result", []))
        if isinstance(trains, dict):
            trains = [trains]
    else:
        trains = []

    logger.info(
        f"TrainTicketQuery: parsed {len(trains)} trains, "
        f"filtered={ticket_result.get('_filtered', 0)}"
    )

    disclaimer_text = "以上火车票数据来自12306官方渠道，实际票价和余票请以12306官网 (www.12306.cn) 为准"
    station_info = f"查询路线：{from_name}({from_code}) → {to_name}({to_code})，日期：{date_str}"

    if len(trains) == 0:
        # 结构化解析失败或确实无车次
        filtered_count = ticket_result.get("_filtered", 0)
        raw_preview = str(raw_data)[:2000]
        logger.warning(f"TrainTicketQuery: parsed 0 trains, fallback to raw text. "
                       f"raw_type={type(raw_data).__name__}, len={len(str(raw_data))}, "
                       f"filtered_out={filtered_count}")

        if filtered_count > 0 and not raw_preview.strip():
            return {
                "success": True,
                "error": None,
                "data": (
                    f"{station_info}\n\n"
                    f"该路线当日共 {filtered_count} 趟车次，但均已售罄。"
                    f"建议尝试其他日期或相邻城市。\n\n"
                    f"> {disclaimer_text}"
                ),
            }

        # 回退：把原始数据交给 LLM 解析
        return {
            "success": True,
            "error": None,
            "data": (
                f"请从以下12306返回数据中提取车票信息，生成Markdown表格展示：\n\n"
                f"{raw_preview or '(12306返回为空，该路线可能暂未开通或日期超出预售期)'}\n\n"
                f"> {disclaimer_text}"
            ),
        }

    # 列名统一归一化 → 代码层直接生成 Markdown 表格，LLM 只需原样呈现
    trains = _normalize_columns(trains)
    formatted = _format_ticket_table(trains, station_info, disclaimer_text)
    return {
        "success": True,
        "error": None,
        "data": formatted,
    }


def _extract_station_code(data: dict, city: str) -> str:
    """从 get-station-code-of-citys 返回数据中提取车站编码。"""
    for key, value in data.items():
        if city in key or key in city:
            if isinstance(value, dict):
                return value.get("station_code", "")
    # 遍历所有城市，找名称匹配的
    for city_key, city_data in data.items():
        if isinstance(city_data, dict):
            code = city_data.get("station_code", "")
            name = city_data.get("station_name", "")
            if city in name or city in city_key:
                return code
    return ""


def _extract_station_name(data: dict, city: str) -> str:
    """从 get-station-code-of-citys 返回数据中提取车站名称。"""
    for key, value in data.items():
        if city in key or key in city:
            if isinstance(value, dict):
                return value.get("station_name", key)
    for city_key, city_data in data.items():
        if isinstance(city_data, dict):
            name = city_data.get("station_name", "")
            if city in name or city in city_key:
                return name
    return city


def _normalize_date(date_str: str, today) -> str:
    """标准化日期格式为 yyyy-MM-dd（12306 MCP 要求固定 10 位）。

    Args:
        date_str: 用户输入的日期字符串
        today: 从 12306 MCP get-current-date 获取的真实日期 (datetime.date)
    """
    if not date_str:
        return today.strftime("%Y-%m-%d")

    date_str = date_str.strip()

    # 已经是标准格式 yyyy-MM-dd
    if len(date_str) == 10 and date_str[4] == "-":
        return date_str

    from datetime import timedelta
    import re

    # 相对日期：今天/明天/后天
    relative = {
        "今天": today, "今日": today,
        "明天": today + timedelta(days=1), "明日": today + timedelta(days=1),
        "后天": today + timedelta(days=2), "後天": today + timedelta(days=2),
        "大后天": today + timedelta(days=3),
    }
    if date_str in relative:
        return relative[date_str].strftime("%Y-%m-%d")

    # yyyy年MM月dd日 / yyyy年MM月dd号
    m = re.search(r"(\d{4})\s*年\s*(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    # MM月dd日 / MM月dd号 (使用真实年份)
    m = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?", date_str)
    if m:
        return f"{today.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"

    # yyyy/MM/dd
    m = re.search(r"(\d{4})\s*/\s*(\d{1,2})\s*/\s*(\d{1,2})", date_str)
    if m:
        return f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"

    return today.strftime("%Y-%m-%d")


def _filter_available_tickets(result: dict) -> dict:
    """过滤无票车次 —— 只保留至少有一个座位类型有余票的行。

    对层次化格式（管道符分隔）直接透传，由 _parse_hierarchical_format 统一处理。
    对空格分隔的表格格式，检查每行第7列起是否有 "有" 或正整数的余票标记。
    """
    data = result.get("data")
    if not isinstance(data, str):
        return result

    # 层次化格式：过滤逻辑已内置在 _parse_hierarchical_format 中
    if _detect_format(data) == "hierarchical":
        return result

    lines = data.strip().split("\n")
    if len(lines) < 2:
        return result

    import re
    train_pattern = re.compile(r"^[GDCKZTS]\d+")

    kept_header = []
    kept_rows = []
    removed = 0

    for line in lines:
        stripped = line.strip()
        if not stripped:
            kept_rows.append(line)
            continue

        if not train_pattern.match(stripped):
            kept_header.append(line)
            continue

        parts = stripped.split()
        if len(parts) >= 7:
            seat_cols = parts[6:]
            has_available = any(
                c == "有" or (c.isdigit() and int(c) > 0)
                for c in seat_cols
            )
            if has_available:
                kept_rows.append(line)
            else:
                removed += 1
        else:
            kept_rows.append(line)

    if removed > 0:
        logger.info(f"TrainTicketFilter: removed {removed} sold-out trains, kept {len(kept_rows)}")
        result["data"] = "\n".join(kept_header + kept_rows)
        result["_filtered"] = removed

    return result


# MCP 可能返回英文/拼音列名 → 统一映射为中文，保证后续格式化一致
_COLUMN_NAME_MAP = {
    # 车次
    "train_no": "车次", "trainno": "车次", "train_code": "车次", "code": "车次",
    "车次": "车次", "train": "车次", "no": "车次",
    # 出发站
    "from_station": "出发站", "fromstation": "出发站", "from": "出发站",
    "departure_station": "出发站", "start_station": "出发站", "start": "出发站",
    "出发站": "出发站", "出发地": "出发站",
    # 到达站
    "to_station": "到达站", "tostation": "到达站", "to": "到达站",
    "arrival_station": "到达站", "arrive_station": "到达站", "end_station": "到达站",
    "到达站": "到达站", "目的地": "到达站",
    # 出发时间
    "departure_time": "出发时间", "departuretime": "出发时间", "dept_time": "出发时间",
    "start_time": "出发时间", "leave_time": "出发时间", "depart_time": "出发时间",
    "出发时间": "出发时间", "发车时间": "出发时间", "开车时间": "出发时间",
    # 到达时间
    "arrival_time": "到达时间", "arrivaltime": "到达时间", "arr_time": "到达时间",
    "arrive_time": "到达时间", "end_time": "到达时间",
    "到达时间": "到达时间", "到站时间": "到达时间",
    # 历时
    "duration": "历时", "cost_time": "历时", "travel_time": "历时",
    "历时": "历时", "耗时": "历时", "运行时间": "历时", "take_time": "历时",
    # 日期
    "date": "日期", "departure_date": "日期", "train_date": "日期",
    "日期": "日期", "出发日期": "日期",
}


def _normalize_columns(trains: List[Dict[str, str]]) -> List[Dict[str, str]]:
    """将 MCP 返回的各种列名统一映射为中文标准列名。"""
    if not trains:
        return trains
    normalized = []
    for train in trains:
        new_train = {}
        for k, v in train.items():
            key_lower = k.lower().replace(" ", "").replace("_", "")
            std_key = _COLUMN_NAME_MAP.get(k) or _COLUMN_NAME_MAP.get(key_lower) or k
            new_train[std_key] = v
        normalized.append(new_train)
    return normalized


def _format_ticket_table(trains: List[Dict[str, str]], station_info: str, disclaimer: str) -> str:
    """将结构化车票数据渲染为 Markdown 表格 —— 保证列完整、格式一致。

    输出:
        查询日期：2026-05-18
        查询路线：北京南(VNP) → 上海虹桥(AOH)

        | 车次 | 出发站 | 到达站 | 出发时间 | 到达时间 | 历时 | 商务座 | 一等座 | 二等座 |
        |------|--------|--------|----------|----------|------|--------|--------|--------|
        | G123 | 北京南 | 上海虹桥 | 08:00 | 12:30 | 04:30 | 有 | 有 | 20 |
    """
    if not trains:
        return f"{station_info}\n\n暂无可用车次。\n\n> {disclaimer}"

    # 收集所有列名，保持首次出现的顺序，且确保关键列在最前面
    key_cols = ["车次", "出发站", "到达站", "出发时间", "到达时间", "历时"]
    seen = set(key_cols)
    all_headers = list(key_cols)

    # 添加座位类型列（不在关键列中的）
    for train in trains:
        for k in train:
            if k not in seen:
                seen.add(k)
                all_headers.append(k)

    # 构建表头
    header_line = "| " + " | ".join(all_headers) + " |"
    sep_line = "|" + "|".join("------" for _ in all_headers) + "|"

    # 构建数据行
    data_lines = []
    for train in trains:
        cells = []
        for h in all_headers:
            val = train.get(h, "")
            cells.append(val if val else "--")
        data_lines.append("| " + " | ".join(cells) + " |")

    table = "\n".join([header_line, sep_line] + data_lines)

    return f"{station_info}\n\n{table}\n\n> {disclaimer}"


def _parse_ticket_text(text: str) -> List[Dict[str, str]]:
    """将 MCP 返回的文本解析为结构化车次列表。

    自动检测格式并分派到对应解析器：
    - 层次化格式（管道符+缩进） → _parse_hierarchical_format
    - 空格分隔表格              → 沿用原有解析逻辑

    输出：[{"车次":"G123", "出发站":"北京南", "出发时间":"08:00", ...}, ...]
    """
    if _detect_format(text) == "hierarchical":
        trains = _parse_hierarchical_format(text)
        logger.info(f"TrainTicketParser (hierarchical): parsed {len(trains)} trains")
        return trains

    # 原有空格分隔表格解析逻辑
    lines = [l.strip() for l in text.strip().split("\n") if l.strip()]
    if not lines:
        return []

    # 分离表头行与数据行
    header_line = None
    data_lines = []
    for line in lines:
        if _TRAIN_NO_PATTERN.match(line.split()[0] if line.split() else ""):
            data_lines.append(line)
        elif not header_line:
            header_line = line

    # 如果所有行都是数据行（无独立表头），用默认列名
    if not header_line:
        if not data_lines:
            return []
        header_line = "车次 出发站 到达站 出发时间 到达时间 历时"
        logger.info("TrainTicketParser: no header found, using default column names")

    headers = header_line.split()
    trains = []
    for line in data_lines:
        parts = line.split()
        if not parts:
            continue
        train = {}
        for i, h in enumerate(headers):
            train[h] = parts[i] if i < len(parts) else ""
        trains.append(train)
    return trains


# ============================================================
# LLM 兜底：12306 MCP 不可用时，用 DeepSeek 生成火车票数据
# ============================================================

async def _query_via_llm(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    """当 12306 MCP 不可用时，用 LLM 生成火车票模拟数据作为兜底。"""
    departure_city = str(slots.get("departure_city", "")).strip()
    arrival_city = str(slots.get("arrival_city", "")).strip()
    departure_date = str(slots.get("departure_date", "")).strip()

    logger.info(f"TrainTicketQuery(LLM): {departure_city} -> {arrival_city}, date={departure_date}")

    from datetime import date, timedelta
    # 如果日期为空或不是标准格式，默认用明天
    try:
        target_date = date.fromisoformat(departure_date)
    except (ValueError, TypeError):
        target_date = date.today() + timedelta(days=1)
        departure_date = target_date.strftime("%Y-%m-%d")

    prompt = f"""你是一个火车票查询系统。请根据以下条件生成3-5条合理的火车票信息，以JSON格式返回。

出发站：{departure_city}
到达站：{arrival_city}
出发日期：{departure_date}

要求：
1. 生成中国真实存在的车次号（如G高铁/D动车/K快速/Z直达/T特快）
2. 价格合理（高铁二等座0.4-0.6元/km，动车0.3-0.4元/km，普速0.1-0.2元/km）
3. 出发时间分布均匀（早中晚都有）
4. 耗时合理（高铁300km/h，动车200km/h，普速120km/h估算）

返回纯JSON数组，不要包含其他文字：
[{{"train_no":"G101","type":"高铁","depart_time":"08:00","arrive_time":"12:30","duration":"4h30min","price":553,"seats":"有票"}}]
"""
    try:
        from llm.client_pool import llm_manager
        from config import LLM_CONFIG
        client = llm_manager.get_client("default")
        resp = client.chat.completions.create(
            model=LLM_CONFIG["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=1024,
        )
        content = resp.choices[0].message.content
        # 提取 JSON 数组
        import re
        match = re.search(r'\[.*\]', content, re.DOTALL)
        if match:
            trains = json.loads(match.group())
        else:
            trains = []
    except Exception as e:
        logger.error(f"TrainTicketQuery(LLM): generation failed: {e}")
        trains = []

    if not trains:
        return {
            "success": False,
            "error": f"未找到 {departure_city} 到 {arrival_city} 的火车票信息",
            "data": [],
        }

    # 格式化为表格
    rows = []
    for t in trains:
        rows.append(
            f"| {t.get('train_no','')} | {t.get('type','')} | {t.get('depart_time','')} | "
            f"{t.get('arrive_time','')} | {t.get('duration','')} | ¥{t.get('price',0)} | "
            f"{t.get('seats','有票')} |"
        )
    header = "| 车次 | 类型 | 出发 | 到达 | 耗时 | 票价 | 余票 |\n|------|------|------|------|------|------|------|"
    table = "\n".join([header] + rows)

    disclaimer = "以上火车票为AI生成参考数据，实际车次和票价请以12306官网为准"
    return {
        "success": True,
        "error": None,
        "data": f"查询路线：{departure_city} → {arrival_city}，日期：{departure_date}\n\n{table}\n\n> {disclaimer}",
    }
