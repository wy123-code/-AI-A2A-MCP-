"""Agent 标准消息结构体 —— 所有智能体间通信的唯一数据格式。

优化说明 (P0):
  - 新增 error_code 字段，用于分类错误类型 (TIMEOUT / DEGRADED / WORKER_ERROR)
  - 新增 ack_required / ack_time 字段，实现 ACK 确认机制
  - 新增 create_ack() / mark_running() / mark_completed() / mark_failed() 便捷方法
"""
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, Optional

# 错误码常量 —— 用于分类 MCP 通信中的错误类型
ERROR_CODE_TIMEOUT = "TIMEOUT"
ERROR_CODE_DEGRADED = "DEGRADED"
ERROR_CODE_WORKER_ERROR = "WORKER_ERROR"
ERROR_CODE_ACK_TIMEOUT = "ACK_TIMEOUT"
ERROR_CODE_UNKNOWN = "UNKNOWN"


def _json_dumps_safe(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


@dataclass
class AgentMessage:
    """A2A 标准消息体，贯穿 MCP 通信全链路。

    Attributes:
        trace_id: 全局唯一链路追踪 ID（从 TraceIDMiddleware 继承）
        sender: 发送方角色名（如 "orchestrator", "worker.weather", "memory"）
        receiver: 接收方角色名（或 "*" 表示广播）
        message_type: "task" | "result" | "error" | "heartbeat" | "register" | "ack"
        task_type: 任务类型（即意图标识，如 "weather_query"）
        payload: 载荷数据（任务参数或结果数据）
        status: "pending" | "received" | "running" | "success" | "failed"
        correlation_id: 关联 ID，用于请求-响应匹配
        timestamp: ISO 8601 UTC 时间戳
        ttl: 消息存活时间（秒），默认 30
        error_code: 错误分类码 (TIMEOUT / DEGRADED / WORKER_ERROR / ACK_TIMEOUT / UNKNOWN)
        ack_required: 是否需要 ACK 确认（默认 False）
        ack_time: ACK 确认时间戳（ISO 8601 UTC）
    """
    trace_id: str = ""
    sender: str = ""
    receiver: str = ""
    message_type: str = "task"
    task_type: str = ""
    payload: Dict[str, Any] = field(default_factory=dict)
    status: str = "pending"
    correlation_id: str = ""
    timestamp: str = ""
    ttl: int = 30
    error_code: str = ""
    ack_required: bool = False
    ack_time: str = ""

    def __post_init__(self):
        if not self.correlation_id:
            self.correlation_id = str(uuid.uuid4())[:12]
        if not self.timestamp:
            self.timestamp = datetime.now(timezone.utc).isoformat()

    # ---- 序列化 ----

    def to_json(self) -> str:
        """序列化消息为 JSON 字符串，用于 MCP 通信传输。"""
        return _json_dumps_safe({
            "trace_id": self.trace_id,
            "sender": self.sender,
            "receiver": self.receiver,
            "message_type": self.message_type,
            "task_type": self.task_type,
            "payload": self.payload,
            "status": self.status,
            "correlation_id": self.correlation_id,
            "timestamp": self.timestamp,
            "ttl": self.ttl,
            "error_code": self.error_code,
            "ack_required": self.ack_required,
            "ack_time": self.ack_time,
        })

    @classmethod
    def from_json(cls, json_str: str) -> "AgentMessage":
        """从 JSON 字符串反序列化为 AgentMessage 对象。"""
        data = json.loads(json_str) if isinstance(json_str, str) else json_str
        return cls(
            trace_id=data.get("trace_id", ""),
            sender=data.get("sender", ""),
            receiver=data.get("receiver", ""),
            message_type=data.get("message_type", "task"),
            task_type=data.get("task_type", ""),
            payload=data.get("payload", {}),
            status=data.get("status", "pending"),
            correlation_id=data.get("correlation_id", ""),
            timestamp=data.get("timestamp", ""),
            ttl=data.get("ttl", 30),
            error_code=data.get("error_code", ""),
            ack_required=data.get("ack_required", False),
            ack_time=data.get("ack_time", ""),
        )

    # ---- 工厂方法 ----

    @classmethod
    def create_task(
        cls,
        sender: str,
        receiver: str,
        task_type: str,
        payload: Dict[str, Any],
        trace_id: str = "",
        ttl: int = 30,
        ack_required: bool = False,
    ) -> "AgentMessage":
        """创建任务消息（status=pending）。"""
        return cls(
            trace_id=trace_id,
            sender=sender,
            receiver=receiver,
            message_type="task",
            task_type=task_type,
            payload=payload,
            status="pending",
            ttl=ttl,
            ack_required=ack_required,
        )

    @classmethod
    def create_result(
        cls,
        original_msg: "AgentMessage",
        status: str,
        payload: Dict[str, Any],
        error_code: str = "",
    ) -> "AgentMessage":
        """创建结果消息 —— 交换 sender/receiver，继承 correlation_id 和 trace_id。"""
        return cls(
            trace_id=original_msg.trace_id,
            sender=original_msg.receiver,
            receiver=original_msg.sender,
            message_type="result",
            task_type=original_msg.task_type,
            payload=payload,
            status=status,
            correlation_id=original_msg.correlation_id,
            ttl=original_msg.ttl,
            error_code=error_code,
        )

    @classmethod
    def create_ack(cls, original_msg: "AgentMessage") -> "AgentMessage":
        """创建 ACK 确认消息 —— 表示已收到任务，正在处理中。

        接收方收到 task 后应立即返回 ACK（status="received"），
        发送方在短超时内未收到 ACK 则触发重试。
        """
        return cls(
            trace_id=original_msg.trace_id,
            sender=original_msg.receiver,
            receiver=original_msg.sender,
            message_type="ack",
            task_type=original_msg.task_type,
            payload={"acknowledged": True},
            status="received",
            correlation_id=original_msg.correlation_id,
            ttl=original_msg.ttl,
            ack_time=datetime.now(timezone.utc).isoformat(),
        )

    # ---- 状态转换便捷方法 ----

    def mark_running(self) -> None:
        """标记消息为处理中状态。"""
        self.status = "running"
        self.message_type = "task"

    def mark_completed(self, result_payload: Dict[str, Any]) -> None:
        """标记消息为成功完成，填充结果载荷。"""
        self.status = "success"
        self.message_type = "result"
        self.payload = result_payload

    def mark_failed(self, error: str, error_code: str = ERROR_CODE_WORKER_ERROR) -> None:
        """标记消息为失败，填充错误信息和错误码。"""
        self.status = "failed"
        self.message_type = "error"
        self.error_code = error_code
        self.payload = {"success": False, "error": error, "data": [], "error_code": error_code}
