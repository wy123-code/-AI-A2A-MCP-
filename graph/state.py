"""Agent 管线状态定义 —— TypedDict 用于节点间传递上下文。"""

from typing import Any, Dict, List, TypedDict


class TourismStateDict(TypedDict, total=False):
    """旅游助手 Agent 管线状态字典，贯穿意图识别→规划→槽位填充→工具执行→回答生成全流程。"""
    query: str
    original_query: str  # 用户原始输入，不含上下文增强
    session_id: str
    history: List[Dict[str, str]]

    intent: str
    intent_in_scope: bool

    need_planning: bool
    sub_tasks: List[Dict[str, Any]]

    slots: Dict[str, Any]
    missing_slots: List[str]
    follow_up_question: str

    tool_name: str
    tool_input: Dict[str, Any]
    tool_result: Any

    worker_targets: List[Dict[str, Any]]  # intent_router 输出的 Worker 路由目标列表

    summary: str

    final_answer: str

    follow_up_count: int  # 追问轮次计数器，达到 max_turns 时触发安全终止

    next_step: str
    error: str
