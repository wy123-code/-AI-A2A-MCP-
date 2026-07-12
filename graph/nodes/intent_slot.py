"""合并意图识别 + 槽位填充节点 —— 一次 LLM 调用完成意图分类、槽位提取与多任务拆分。"""
import asyncio
import json
import re
from datetime import date, timedelta
from loguru import logger
from config import INTENT_LLM_CONFIG
from prompts import INTENT_SLOT_PROMPT
from llm.client_pool import llm_manager
from graph.state import TourismStateDict

CHINESE_CITIES = (
    r'(北京|上海|广州|深圳|杭州|成都|重庆|武汉|西安|南京|天津|苏州|长沙|郑州|'
    r'青岛|大连|厦门|昆明|三亚|桂林|丽江|哈尔滨|长春|沈阳|济南|合肥|福州|南昌|'
    r'南宁|贵阳|兰州|银川|西宁|拉萨|乌鲁木齐|呼和浩特|海口|澳门|香港|台北|'
    # 黑龙江
    r'齐齐哈尔|牡丹江|佳木斯|大庆|漠河|'
    # 吉林
    r'吉林|延吉|四平|长白山|延边|'
    # 辽宁
    r'鞍山|抚顺|丹东|锦州|'
    # 内蒙古
    r'包头|鄂尔多斯|呼伦贝尔|满洲里|海拉尔|'
    # 河北
    r'石家庄|保定|唐山|秦皇岛|邯郸|承德|张家口|'
    # 山西
    r'太原|大同|平遥|五台山|运城|'
    # 山东
    r'淄博|烟台|潍坊|威海|泰安|泰山|日照|曲阜|临沂|'
    # 河南
    r'开封|洛阳|安阳|少林寺|龙门石窟|'
    # 陕西
    r'咸阳|延安|华山|兵马俑|'
    # 甘肃
    r'敦煌|嘉峪关|天水|张掖|'
    # 青海
    r'青海湖|'
    # 新疆
    r'吐鲁番|喀什|伊犁|天山|天池|'
    # 西藏
    r'日喀则|林芝|珠峰|纳木错|'
    # 四川
    r'绵阳|乐山|峨眉山|九寨沟|稻城|稻城亚丁|都江堰|宜宾|'
    # 贵州
    r'遵义|黄果树|黔东南|安顺|'
    # 云南
    r'大理|西双版纳|景洪|香格里拉|玉龙雪山|洱海|腾冲|'
    # 湖北
    r'宜昌|襄阳|武当山|恩施|神农架|'
    # 湖南
    r'张家界|凤凰|岳阳|衡阳|衡山|株洲|'
    # 广东
    r'珠海|汕头|佛山|东莞|中山|惠州|湛江|潮州|'
    # 广西
    r'阳朔|北海|柳州|'
    # 海南
    r'三沙|'
    # 福建
    r'泉州|武夷山|鼓浪屿|漳州|'
    # 浙江
    r'宁波|温州|嘉兴|绍兴|舟山|普陀山|乌镇|千岛湖|'
    # 江苏
    r'无锡|常州|扬州|镇江|徐州|周庄|同里|'
    # 安徽
    r'黄山|宏村|芜湖|安庆|'
    # 江西
    r'九江|庐山|景德镇|婺源|井冈山|'
    # 台湾
    r'高雄|台中|花莲|垦丁|阿里山|日月潭)'
)

SLOT_CN_NAMES = {
    "departure_city": "出发城市", "arrival_city": "到达城市", "departure_date": "出发日期",
    "return_date": "返程日期", "departure_port": "出发港口", "arrival_port": "到达港口",
    "city": "城市", "date": "日期", "date_range": "日期", "concert_name": "演唱会名称",
    "address": "地点", "time": "时间", "pickup_date": "取车日期",
    "car_type": "车型", "insurance_type": "保险类型", "travel_date": "出行日期",
    "destination": "目的地", "days": "游玩天数",
}


async def _extract_slots_from_history(history: list, required_slots: list, existing: dict = None) -> dict:
    """从对话历史中提取已有槽位值 —— 仅填充当前查询中缺失的槽位。"""
    found = {}
    if not history:
        return found
    existing = existing or {}
    for slot in required_slots:
        # 当前查询已有该槽位则不搜历史
        if slot in existing and existing[slot] and existing[slot] != "null":
            continue
        for msg in reversed(history):
            content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
            slots_in_msg = msg.get("slots", {}) if isinstance(msg, dict) else {}
            if slot in slots_in_msg and slots_in_msg[slot]:
                found[slot] = slots_in_msg[slot]
                break
            if slot in ("city", "departure_city", "arrival_city") and not found.get(slot):
                if slot == "departure_city":
                    cities = re.findall(r'(?:从|出发地?|离开)' + CHINESE_CITIES, content)
                    if not cities:
                        cities = re.findall(CHINESE_CITIES, content)
                elif slot == "arrival_city":
                    cities = re.findall(r'(?:到|去|往|飞|抵达|目的地?)' + CHINESE_CITIES, content)
                else:
                    cities = re.findall(CHINESE_CITIES, content)
                if cities:
                    found[slot] = cities[-1]
    return found


def _build_follow_up(missing_slots: list, intent: str) -> str:
    """当 LLM 未生成追问时的后备自然追问。"""
    names = [SLOT_CN_NAMES.get(s, s) for s in missing_slots]
    if not names:
        return "能再详细说说你的需求吗？"
    if len(names) == 1:
        return f"好的，还需要确认一下{names[0]}，方便告诉我吗？"
    return f"好的，还需要确认{'、'.join(names[:-1])}和{names[-1]}，方便一起告诉我吗？"


def _pre_extract_slots(query: str) -> dict:
    """LLM 调用前用正则预先提取明显槽位，返回标准化的 yyyy-MM-dd 日期。"""
    slots = {}

    today = date.today()

    # 日期：明天/后天/今天/具体日期 → 统一转 yyyy-MM-dd
    import re as _re
    date_patterns = [
        (r'(大后天)', today + timedelta(days=3)),
        (r'(后天)', today + timedelta(days=2)),
        (r'(明天)', today + timedelta(days=1)),
        (r'(今天)', today),
    ]
    for pat, d in date_patterns:
        m = _re.search(pat, query)
        if m:
            slots["date"] = d.strftime("%Y-%m-%d")
            slots["departure_date"] = d.strftime("%Y-%m-%d")
            break

    if "date" not in slots:
        m = _re.search(r'(\d{4})[-/](\d{1,2})[-/](\d{1,2})', query)
        if m:
            val = f"{m.group(1)}-{int(m.group(2)):02d}-{int(m.group(3)):02d}"
            slots["date"] = val
            slots["departure_date"] = val
        else:
            m = _re.search(r'(\d{1,2})\s*月\s*(\d{1,2})\s*[日号]?', query)
            if m:
                val = f"{today.year}-{int(m.group(1)):02d}-{int(m.group(2)):02d}"
                slots["date"] = val
                slots["departure_date"] = val

    # 城市匹配
    cities = _re.findall(CHINESE_CITIES, query)
    if cities:
        slots["city"] = cities[-1]

    # 出发/到达城市
    dep = _re.findall(r'(?:从|出发地?)(北京|上海|广州|深圳|杭州|成都|重庆|武汉|西安|南京|天津|苏州|长沙|郑州|青岛|大连|厦门|昆明|三亚|桂林|丽江|哈尔滨|长春|沈阳|济南|合肥|福州|南昌|南宁|贵阳|兰州|银川|西宁|拉萨|乌鲁木齐|呼和浩特|海口|澳门|香港|台北)', query)
    arr = _re.findall(r'(?:到|去|往|飞|抵达|目的地?)(北京|上海|广州|深圳|杭州|成都|重庆|武汉|西安|南京|天津|苏州|长沙|郑州|青岛|大连|厦门|昆明|三亚|桂林|丽江|哈尔滨|长春|沈阳|济南|合肥|福州|南昌|南宁|贵阳|兰州|银川|西宁|拉萨|乌鲁木齐|呼和浩特|海口|澳门|香港|台北)', query)
    if dep:
        slots["departure_city"] = dep[-1]
    # 不再从 cities 默认推断 departure_city —— 避免非出行类意图（酒店/天气/景点）被错误填入
    if arr:
        slots["arrival_city"] = arr[-1]
    elif dep and len(cities) >= 2:
        # 有出发地 + 多个城市时，第二个城市作为到达地
        slots["arrival_city"] = cities[1]

    # 天数
    days = _re.findall(r'(\d+)\s*天', query)
    if days:
        slots["days"] = days[0]

    return slots


def _fallback_parse(raw: str) -> dict:
    """当 LLM 返回的 JSON 格式有误时，用正则尽力提取意图和槽位。"""
    import re as _re

    result = {}
    if not raw or not isinstance(raw, str):
        return result

    # 提取 intent
    m = _re.search(r'"intent"\s*:\s*"(\w+)"', raw)
    if m:
        result["intent"] = m.group(1)

    # 尝试找 intents 数组
    intents_raw = _re.findall(r'"intent"\s*:\s*"(\w+)"', raw)
    if intents_raw:
        result["intents"] = [{"intent": i} for i in intents_raw]
        result["is_multi"] = len(intents_raw) > 1

    # 提取 slots：找 "slot_name": "value" 或 "slot_name":"value" 模式
    slot_patterns = [
        r'"slots"\s*:\s*\{([^}]+)\}',
        r'"slots"\s*:\s*\{',
    ]
    slots = {}
    slot_pairs = _re.findall(r'"(\w+)"\s*:\s*"([^"]*)"', raw)
    known_slots = {
        "departure_city", "arrival_city", "departure_date", "return_date",
        "departure_port", "arrival_port", "city", "date", "date_range",
        "concert_name", "address", "time", "pickup_date", "car_type",
        "insurance_type", "travel_date", "destination", "days",
    }
    for key, value in slot_pairs:
        if key in known_slots and value and value != "null":
            slots[key] = value

    # 计算 missing_slots
    intent_name = result.get("intent", "")
    if not intent_name and result.get("intents"):
        intent_name = result["intents"][0].get("intent", "")

    from config import INTENT_SLOTS
    required = INTENT_SLOTS.get(intent_name, [])
    missing = [s for s in required if s not in slots]

    if result.get("intents"):
        result["intents"][0]["slots"] = slots
        result["intents"][0]["missing_slots"] = missing
    if "slots" not in result:
        result["slots"] = slots
    if "missing_slots" not in result:
        result["missing_slots"] = missing
    if "is_multi" not in result:
        result["is_multi"] = False
    if "follow_up_question" not in result:
        result["follow_up_question"] = ""

    logger.info(f"FallbackParse: extracted intent={intent_name}, slots={slots}, missing={missing}")
    return result


def _override_intent_from_slots(slots: dict, query: str = "") -> str:
    """当 LLM 误判 out_of_scope 时，根据预提取槽位 + 查询关键词推测正确意图。"""
    query_lower = query.lower() if query else ""

    # 关键词 → 意图映射（优先级高于槽位推测）
    KEYWORD_INTENT = [
        (["酒店", "住宿", "宾馆", "民宿", "旅馆"], "hotel_query"),
        (["天气", "气温", "下雨", "下雪", "晴天", "阴天"], "weather_query"),
        (["机票", "飞机", "航班", "飞行"], "flight_ticket"),
        (["火车", "高铁", "动车", "列车"], "train_ticket"),
        (["景点", "旅游", "游玩", "旅行", "推荐"], "attraction_recommend"),
        (["租车", "自驾"], "car_rental_query"),
        (["旅行团", "跟团", "团游"], "tour_group_query"),
        (["保险"], "insurance_query"),
        (["演唱会", "音乐会", "演出", "票"], "concert_ticket"),
        (["船票", "轮船", "游轮"], "ship_ticket"),
    ]

    for keywords, intent in KEYWORD_INTENT:
        for kw in keywords:
            if kw in query:
                return intent

    # 槽位推测（回退逻辑）
    if not slots:
        return ""

    has_dep = bool(slots.get("departure_city"))
    has_arr = bool(slots.get("arrival_city"))
    has_date = bool(slots.get("departure_date") or slots.get("date"))
    has_city = bool(slots.get("city"))
    has_days = bool(slots.get("days"))
    has_port_dep = bool(slots.get("departure_port"))
    has_port_arr = bool(slots.get("arrival_port"))

    if has_dep and has_arr:
        return "train_ticket"

    if has_city and has_date:
        return "weather_query"

    if has_port_dep and has_port_arr:
        return "ship_ticket"

    if has_city and has_days:
        return "attraction_recommend"

    if has_city:
        return "attraction_recommend"

    return ""


def _optimize_history(history: list, max_chars: int = 2000) -> list:
    """对话历史去重与截断，防止上下文溢出。

    去重：移除连续重复的用户消息（内容完全相同的相邻 user 消息）。
    截断：从旧到新累计字符数，超过 max_chars 时丢弃最早的消息。
    """
    if not history:
        return history

    deduped = []
    last_user_content = None
    for msg in history:
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        role = msg.get("role", "") if isinstance(msg, dict) else ""
        if role == "user":
            if content == last_user_content:
                continue
            last_user_content = content
        else:
            last_user_content = None
        deduped.append(msg)

    total = 0
    truncated = []
    for msg in reversed(deduped):
        content = msg.get("content", "") if isinstance(msg, dict) else str(msg)
        total += len(content)
        if total > max_chars and truncated:
            break
        truncated.append(msg)
    truncated.reverse()

    if len(truncated) < len(deduped):
        logger.info(f"OptimizeHistory: {len(deduped)} -> {len(truncated)} items, {total} chars")
    return truncated


async def intent_slot_node(state: TourismStateDict) -> TourismStateDict:
    query = state.get("query", "")
    history = _optimize_history(state.get("history", []))
    existing_slots = state.get("slots", {})
    previous_intent = state.get("intent", "")

    # 预提取：正则抓取明显槽位
    pre_slots = _pre_extract_slots(query)
    existing_slots = {**existing_slots, **pre_slots}

    # === 追问轮次上限检查 (max_turns=3) ===
    follow_up_count = state.get("follow_up_count", 0)
    MAX_FOLLOW_UP_TURNS = 3

    # 检测本轮是否也是追问（缺槽位）—— 如果历史中上一轮也是追问，则递增计数
    previous_missing = state.get("missing_slots", [])
    if previous_missing:
        follow_up_count += 1
    else:
        follow_up_count = 0  # 新话题重置计数

    if follow_up_count > MAX_FOLLOW_UP_TURNS:
        logger.warning(
            f"IntentSlot: follow_up_count={follow_up_count} exceeds max={MAX_FOLLOW_UP_TURNS}, "
            f"triggering safe termination (Level 3)"
        )
        state["follow_up_count"] = follow_up_count
        # Level 3 安全终止：友好引导用户，基于已有信息尝试推理
        if existing_slots:
            # 基于已有槽位 + Memory Agent 用户偏好进行智能补全
            state["intent_in_scope"] = True
            state["slots"] = existing_slots
            state["missing_slots"] = []
            state["final_answer"] = (
                "抱歉，我暂时无法完全确定您的需求。"
                "为了给您更精准的推荐，请您在下一次询问时，"
                "尽可能详细地告诉我目的地、时间和预算等信息哦～"
            )
        else:
            state["intent_in_scope"] = False
            state["final_answer"] = (
                "看起来我还不太理解您的需求。"
                "您可以试试这样问我：「帮我查一下明天北京到上海的机票」"
                "或者「推荐几个杭州的景点」～"
            )
        state["next_step"] = "end"
        return state

    state["follow_up_count"] = follow_up_count

    history_str = json.dumps(history[-6:] if history else [], ensure_ascii=False)

    # 构建上一轮上下文
    ctx_parts = []
    if previous_intent:
        from config import INTENT_CN_MAP
        ctx_parts.append(f"上一轮意图：{INTENT_CN_MAP.get(previous_intent, previous_intent)}")
    if existing_slots:
        slot_str = "、".join(f"{k}={v}" for k, v in existing_slots.items())
        ctx_parts.append(f"已填槽位：{slot_str}")
    previous_context = "\n".join(ctx_parts)

    prompt = INTENT_SLOT_PROMPT.format(
        query=query,
        history=history_str,
        today=date.today().strftime("%Y-%m-%d"),
        previous_context=f"\n{previous_context}" if ctx_parts else "",
    )

    logger.info(f"IntentSlot: processing query='{query[:80]}' prev_intent={previous_intent} slots={existing_slots}")
    client = llm_manager.get_client("intent")

    raw_content = ""
    result = {}
    llm_errors = []

    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=INTENT_LLM_CONFIG["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=INTENT_LLM_CONFIG["temperature"],
                max_tokens=INTENT_LLM_CONFIG["max_tokens"],
                response_format={"type": "json_object"},
                timeout=30.0,
            )
        )
        raw_content = response.choices[0].message.content
        result = json.loads(raw_content)
    except json.JSONDecodeError as e:
        llm_errors.append(f"json_parse: {e}")
        logger.warning(f"IntentSlot JSON parse failed: {e}, raw={raw_content[:200]}")
        result = _fallback_parse(raw_content)
        if result:
            logger.info("IntentSlot: used fallback parser")
    except Exception as e:
        llm_errors.append(f"api: {e}")
        logger.error(f"IntentSlot LLM call failed: {e}")

    if not result:
        logger.error(f"IntentSlot all attempts failed: {'; '.join(llm_errors)}")
        corrected = _override_intent_from_slots(existing_slots, query)
        if corrected:
            logger.info(f"IntentSlot: LLM failed but slots suggest {corrected}, auto-routing")
            state["intent"] = corrected
            state["intent_in_scope"] = True
            state["slots"] = existing_slots
            state["sub_tasks"] = [{"intent": corrected, "slots": existing_slots, "missing_slots": []}]
            state["next_step"] = "tool_execution"
            return state
        state["intent"] = "out_of_scope"
        state["intent_in_scope"] = False
        state["final_answer"] = "抱歉，系统暂时无法处理您的请求，请稍后重试。"
        state["next_step"] = "end"
        return state

    # 解析 LLM 返回结果（容错处理）
    try:
        intents = result.get("intents", [])
        is_multi = result.get("is_multi", False)
        follow_up = result.get("follow_up_question", "")

        if not isinstance(intents, list):
            intents = []

        # 向后兼容：旧格式单 intent
        if not intents and result.get("intent"):
            slots_val = result.get("slots", {})
            if not isinstance(slots_val, dict):
                slots_val = {}
            intents = [{
                "intent": result.get("intent", "out_of_scope"),
                "slots": slots_val,
                "missing_slots": result.get("missing_slots", []),
            }]

        intents = [i for i in intents if isinstance(i, dict) and i.get("intent")]

        if not intents:
            corrected = _override_intent_from_slots(existing_slots, query)
            if corrected:
                logger.info(f"IntentSlot: empty intents but slots suggest {corrected}, auto-routing")
                state["intent"] = corrected
                state["intent_in_scope"] = True
                state["slots"] = existing_slots
                state["sub_tasks"] = [{"intent": corrected, "slots": existing_slots, "missing_slots": []}]
                state["next_step"] = "tool_execution"
                return state
            state["intent"] = "out_of_scope"
            state["intent_in_scope"] = False
            state["final_answer"] = "抱歉，我不太理解您的需求，能换个方式说说吗？"
            state["next_step"] = "end"
            return state

        # 计算 in_scope：只要有一个意图不是 out_of_scope 就算在范围内
        in_scope = any(i.get("intent") != "out_of_scope" for i in intents)

        logger.info(f"IntentSlot: intents={[i.get('intent', '?') for i in intents]}, is_multi={is_multi}, in_scope={in_scope}")

        if not in_scope:
            # 如果 LLM 误判为 out_of_scope，但预提取槽位明显匹配某个意图，则自动纠偏
            corrected = _override_intent_from_slots(existing_slots, query)
            if corrected:
                logger.info(f"IntentSlot: LLM returned out_of_scope but slots suggest {corrected}, overriding")
                intents[0]["intent"] = corrected
                in_scope = True
            else:
                state["intent"] = intents[0].get("intent", "out_of_scope")
                state["intent_in_scope"] = False
                state["final_answer"] = "本系统为专业的旅游内容查询，你询问的内容不在范围内。"
                state["next_step"] = "end"
                return state

        # 收集所有需要的槽位
        all_required_slots = set()
        for item in intents:
            item_missing = item.get("missing_slots", [])
            if isinstance(item_missing, list):
                all_required_slots.update(item_missing)
            item_slots = item.get("slots", {})
            if isinstance(item_slots, dict):
                all_required_slots.update(item_slots.keys())

        history_slots = await _extract_slots_from_history(history, list(all_required_slots), existing_slots)

        # 三层合并：历史提取 → 当前查询预提取 → LLM 提取
        # 优先级：LLM > 当前查询 > 历史（越靠近当前查询越可信）
        merged_slots = {**history_slots, **existing_slots}
        for item in intents:
            item_slots = item.get("slots", {})
            if not isinstance(item_slots, dict):
                continue
            for k, v in item_slots.items():
                if v is not None and v != "" and v != "null" and isinstance(v, (str, int, float)):
                    # LLM 对当前查询的理解优先于历史/正则，无条件覆盖
                    merged_slots[k] = v

        # 重新计算每个意图的缺失槽位（可选槽位不阻塞追问）
        from config import INTENT_SLOTS, INTENT_OPTIONAL_SLOTS

        all_missing = []
        sub_tasks = []
        for item in intents:
            intent_name = item.get("intent", "")
            required = INTENT_SLOTS.get(intent_name, [])
            optional = INTENT_OPTIONAL_SLOTS.get(intent_name, [])
            all_allowed = required + optional
            item_slots = {}
            for s in all_allowed:
                val = merged_slots.get(s)
                if val and val != "null":
                    item_slots[s] = val
            # 只把必填槽位算作缺失，可选槽位不阻塞
            item_missing = [s for s in required if not merged_slots.get(s) or merged_slots[s] == "null"]
            all_missing.extend(item_missing)
            sub_tasks.append({
                "intent": intent_name,
                "slots": item_slots,
                "missing_slots": item_missing,
            })

        all_missing = list(dict.fromkeys(all_missing))
        logger.info(f"IntentSlot: merged_slots={merged_slots}, all_missing={all_missing}")

        # 智能默认填充：日期/天数等软槽位缺失时自动补全，减少追问
        from datetime import date as dt_date
        _today = dt_date.today().strftime("%Y-%m-%d")
        _tomorrow = (dt_date.today() + __import__("datetime").timedelta(days=1)).strftime("%Y-%m-%d")
        _soft_defaults = {
            "date": _today, "departure_date": _tomorrow, "time": _today,
            "days": 1, "pickup_date": _tomorrow, "travel_date": _today,
            "return_date": _tomorrow,
        }
        for slot_name in list(all_missing):
            if slot_name in _soft_defaults:
                merged_slots[slot_name] = _soft_defaults[slot_name]
                # 同步更新 sub_tasks 中的 slots 和 missing_slots
                for task in sub_tasks:
                    if slot_name in task["missing_slots"]:
                        task["slots"][slot_name] = _soft_defaults[slot_name]
                        task["missing_slots"].remove(slot_name)
                all_missing.remove(slot_name)

        primary_intent = intents[0].get("intent", "")
        state["intent"] = primary_intent
        state["intent_in_scope"] = True
        state["slots"] = merged_slots
        state["missing_slots"] = all_missing
        state["follow_up_question"] = follow_up
        state["sub_tasks"] = sub_tasks
        state["need_planning"] = is_multi or len(intents) > 1

        if all_missing:
            state["final_answer"] = follow_up or _build_follow_up(all_missing, primary_intent)
            state["next_step"] = "end"
        else:
            state["next_step"] = "tool_execution"

        return state

    except Exception as e:
        logger.error(f"IntentSlot result parsing failed: {e}", exc_info=True)
        state["intent"] = "out_of_scope"
        state["intent_in_scope"] = False
        state["final_answer"] = "抱歉，系统暂时无法处理您的请求，请稍后重试。"
        state["next_step"] = "end"
        return state
