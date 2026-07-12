"""意图路由节点 —— 位于意图识别之后，根据解析结果分流到对应 Worker Agent。

职责：
  - 单意图 → 直连对应领域 Worker Agent（通过 MCP 总线）
  - 多意图 → 并行分发到多个 Worker Agent
  - 缺槽位/out_of_scope → 透传至 final_answer（不执行工具调用）
  - MCP 不可用时 → 回退到 tool_execution_node（旧直接调用路径）

此节点作为架构对齐的关键新增节点，位于 intent_slot_node 和 tool_execution_node 之间。
"""

from typing import List, Optional

from loguru import logger
from graph.state import TourismStateDict


async def intent_router_node(state: TourismStateDict) -> TourismStateDict:
    """意图路由节点：读取意图识别结果，生成 worker_targets 路由信息。

    路由规则：
    1. 缺槽位 (missing_slots 非空) → next_step="end"，交由 final_answer 生成追问
    2. out_of_scope → next_step="end"
    3. 单意图 → next_step="dispatch_to_worker"，记录单个 worker_target
    4. 多意图 → next_step="dispatch_to_worker"，记录多个 worker_targets
    """
    intent = state.get("intent", "")
    sub_tasks = state.get("sub_tasks", [])
    missing_slots = state.get("missing_slots", [])

    # 缺槽位 → 追问（不执行工具）
    if missing_slots:
        logger.info(f"IntentRouter: missing_slots={missing_slots}, routing to follow-up")
        state["next_step"] = "end"
        return state

    # out_of_scope → 直接回复
    if intent == "out_of_scope" or not intent:
        logger.info(f"IntentRouter: intent='{intent}', routing to end")
        state["next_step"] = "end"
        return state

    # 构建 worker 路由目标列表
    from agents.worker import get_worker_name

    worker_targets: List[dict] = []

    if len(sub_tasks) > 1:
        # 多意图并行分发
        logger.info(f"IntentRouter: multi-task detected ({len(sub_tasks)} sub-tasks)")
        for task in sub_tasks:
            task_intent = task.get("intent", "")
            if not task_intent:
                continue
            worker_name = get_worker_name(task_intent)
            worker_targets.append({
                "intent": task_intent,
                "worker_name": worker_name,
                "slots": task.get("slots", {}),
                "priority": task.get("priority", 0),
                "depends_on": task.get("depends_on", []),
            })
        state["need_planning"] = True
    else:
        # 单意图直连分发
        worker_name = get_worker_name(intent)
        worker_targets.append({
            "intent": intent,
            "worker_name": worker_name,
            "slots": state.get("slots", {}),
            "priority": 0,
            "depends_on": [],
        })
        state["need_planning"] = False

    state["worker_targets"] = worker_targets
    state["next_step"] = "dispatch_to_worker"

    logger.info(
        f"IntentRouter: {len(worker_targets)} target(s) → "
        + ", ".join(t["worker_name"] for t in worker_targets)
    )
    return state


def resolve_worker_targets(state: TourismStateDict) -> Optional[List[dict]]:
    """便捷方法：从 state 中提取已解析的 worker 路由目标列表。"""
    return state.get("worker_targets")
