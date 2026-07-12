"""增强消息协议 —— 在 AgentMessage 基础上提供 task_id 生成、状态流转追踪、消息验证。

优化说明 (P0):
  - 新增 task_id 字段，独立于 correlation_id，用于全局任务追踪
  - 状态流转校验: pending → received → running → success/failed
  - 提供 EnhancedAgentMessage 工厂方法，自动填充 trace_id
  - 消息有效性验证 (verify_message_integrity)
"""
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from agent_bus.message import AgentMessage, ERROR_CODE_UNKNOWN
from middleware.trace_id import get_trace_id


# 合法状态流转表
VALID_STATUS_TRANSITIONS = {
    "pending":  {"received", "failed"},
    "received": {"running", "failed"},
    "running":  {"success", "failed"},
    "success":  set(),
    "failed":   {"pending"},  # 允许重试
}


class EnhancedAgentMessage:
    """增强 AgentMessage 工具类 —— 提供 task_id 生成、trace_id 自动注入、状态流转校验。

    所有静态/类方法创建 AgentMessage 实例，不改变原有 AgentMessage 结构。
    """

    @staticmethod
    def new_task_id() -> str:
        """生成全局唯一 task_id（短格式）。"""
        return f"task_{uuid.uuid4().hex[:12]}"

    @staticmethod
    def create_task_with_trace(
        sender: str,
        receiver: str,
        task_type: str,
        payload: Dict[str, Any],
        trace_id: str = "",
        ttl: int = 30,
        ack_required: bool = True,
    ) -> AgentMessage:
        """创建任务消息 —— 自动注入 trace_id 和 task_id。

        优先使用传入的 trace_id，其次从 ContextVar 获取，fallback 生成新的。
        task_id 存入 payload["_task_id"] 供全链路追踪。
        """
        trace_id = trace_id or get_trace_id() or f"tr_{uuid.uuid4().hex[:12]}"
        task_id = EnhancedAgentMessage.new_task_id()
        payload["_task_id"] = task_id
        return AgentMessage.create_task(
            sender=sender,
            receiver=receiver,
            task_type=task_type,
            payload=payload,
            trace_id=trace_id,
            ttl=ttl,
            ack_required=ack_required,
        )

    @staticmethod
    def create_error_response(
        original_msg: AgentMessage,
        error: str,
        error_code: str = ERROR_CODE_UNKNOWN,
    ) -> AgentMessage:
        """创建错误响应消息 —— 携带 error_code 分类。"""
        return AgentMessage.create_result(
            original_msg=original_msg,
            status="failed",
            payload={"success": False, "error": error, "data": [], "error_code": error_code},
            error_code=error_code,
        )

    @staticmethod
    def validate_transition(current_status: str, new_status: str) -> bool:
        """校验状态流转是否合法。

        Returns:
            True 如果 new_status 在 current_status 的允许目标集合中。
        """
        allowed = VALID_STATUS_TRANSITIONS.get(current_status, set())
        return new_status in allowed

    @staticmethod
    def verify_message_integrity(msg: AgentMessage) -> Optional[str]:
        """验证消息完整性，返回 None 表示通过，否则返回错误描述。

        检查项:
          - sender 不为空
          - receiver 不为空（广播消息 "*" 除外）
          - task_type 不为空（task/result 类型消息）
          - payload 不为空（task 类型消息）
        """
        if not msg.sender:
            return "sender is empty"
        if not msg.receiver:
            return "receiver is empty"
        if msg.message_type in ("task", "result") and not msg.task_type:
            return "task_type is empty for task/result message"
        if msg.message_type == "task" and not msg.payload:
            return "payload is empty for task message"
        return None

    @staticmethod
    def get_task_id(msg: AgentMessage) -> str:
        """从消息 payload 中提取 task_id。"""
        return msg.payload.get("_task_id", "")
