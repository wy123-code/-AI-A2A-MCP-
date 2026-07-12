"""结果聚合器 —— 多任务结果去重、冲突解决、排序、摘要生成。

优化说明 (P3):
  - aggregate_results 自动调用 resolve_conflicts + rank_results
  - 追踪 success_count / failure_count，支持局部失败降级
  - 新增 build_summary_prompt() 生成摘要注入 final_answer 上下文
"""
from typing import Any, Dict, List, Set, Tuple

from loguru import logger
from graph.state import TourismStateDict


def aggregate_results(
    state: TourismStateDict,
    results: List[Tuple],
) -> TourismStateDict:
    """聚合多个并行任务的执行结果。

    将 asyncio.gather 返回的 (intent, tool_name, tool_result) 元组列表
    合并为统一的 tool_result 结构，自动去重、排序。
    """
    all_results: Dict[str, Any] = {}
    tool_names: Set[str] = set()
    intent_counts: Dict[str, int] = {}
    success_count = 0
    failure_count = 0

    for intent_name, tool_name, tool_result in results:
        if intent_name is None:
            continue
        if tool_name:
            tool_names.add(tool_name)
        cnt = intent_counts.get(intent_name, 0) + 1
        intent_counts[intent_name] = cnt
        key = intent_name if cnt == 1 else f"{intent_name}_{cnt}"

        if tool_result.get("success"):
            success_count += 1
            data = tool_result.get("data", [])
            # 自动去重（列表类数据）
            if isinstance(data, list) and len(data) > 1:
                data = resolve_conflicts(data)
                data = rank_results(data)
            all_results[key] = data
        else:
            failure_count += 1
            all_results[key] = {"error": tool_result.get("error", "查询失败")}

    # 局部失败标记
    partial_failure = failure_count > 0 and success_count > 0

    state["tool_name"] = "+".join(sorted(tool_names))
    state["tool_result"] = {
        "success": success_count > 0,
        "data": all_results,
        "is_multi": True,
        "partial_failure": partial_failure,
        "success_count": success_count,
        "failure_count": failure_count,
    }
    state["sub_tasks"] = [{"intent": k} for k in all_results.keys()]
    state["next_step"] = "result_summary"

    # 生成摘要提示
    summary = build_summary_prompt(
        intents=list(intent_counts.keys()),
        success_count=success_count,
        failure_count=failure_count,
        total_results=sum(
            len(v) if isinstance(v, list) else 0 for v in all_results.values()
        ),
    )
    state["summary"] = summary

    logger.info(
        f"Aggregator: merged {len(all_results)} results "
        f"(success={success_count}, fail={failure_count})"
    )
    return state


def build_summary_prompt(
    intents: List[str],
    success_count: int = 0,
    failure_count: int = 0,
    total_results: int = 0,
) -> str:
    """生成结果摘要 —— 注入到 final_answer_node 上下文，精简 LLM 输入。

    摘要格式：已查询 N 类信息，返回 M 条结果（X 项成功，Y 项暂不可用）。
    """
    from config import INTENT_CN_MAP

    intent_cn = [INTENT_CN_MAP.get(i, i) for i in intents if i]
    parts = [f"已查询{'、'.join(intent_cn[:3])}" if intent_cn else ""]
    if total_results > 0:
        parts.append(f"返回 {total_results} 条结果")
    if success_count > 0:
        parts.append(f"{success_count} 项成功")
    if failure_count > 0:
        parts.append(f"{failure_count} 项暂不可用")

    return "，".join(p for p in parts if p)


def resolve_conflicts(data_list: List[Dict], key_field: str = "name") -> List[Dict]:
    """检测并解决多数据源的冲突数据。

    当多个数据源返回同一实体的冲突信息时（如同一酒店的不同价格），
    保留信息最完整的条目。
    """
    if not data_list:
        return []

    merged: Dict[str, Dict] = {}
    for item in data_list:
        key = str(item.get(key_field, ""))
        if not key:
            continue
        if key in merged:
            existing = merged[key]
            if len(item) > len(existing):
                item["_sources"] = existing.get("_sources", ["source_1"]) + ["source_2"]
                merged[key] = item
            else:
                existing["_sources"] = existing.get("_sources", ["source_1"]) + ["source_2"]
        else:
            item["_sources"] = ["source_1"]
            merged[key] = item

    return list(merged.values())


def rank_results(data_list: List[Dict], user_prefs: Dict[str, Any] = None,
                 sort_key: str = "price") -> List[Dict]:
    """基于用户偏好对结果排序（低价/高价/默认）。"""
    if not data_list:
        return []

    user_prefs = user_prefs or {}

    # 按价格排序
    try:
        data_list = sorted(
            data_list,
            key=lambda x: float(
                str(x.get(sort_key, 999999)).replace("元", "").replace(",", "")
            ),
        )
    except (ValueError, TypeError):
        pass

    # 高预算偏好：反转排序
    if user_prefs.get("budget") == "high":
        data_list = list(reversed(data_list))

    return data_list
