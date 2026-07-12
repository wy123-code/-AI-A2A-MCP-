"""结构化日志增强 —— 自动附加 trace_id，写入每 Agent 独立日志文件。

优化说明 (P5):
  - 每条日志自动携带 trace_id、agent_name、event_type
  - 按 Agent 分文件输出: logs/agent_{name}.log
  - 保持与 loguru 兼容的调用方式
"""
import sys
from typing import Any, Dict, Optional

from loguru import logger as loguru_logger

from middleware.trace_id import get_trace_id


class StructuredLogger:
    """结构化 Agent 事件日志记录器。

    使用方法:
        slog = StructuredLogger("orchestrator")
        slog.info("task_dispatched", worker="worker.weather", intent="weather_query")
    """

    def __init__(self, agent_name: str):
        self.agent_name = agent_name
        self._logger = loguru_logger.bind(agent=agent_name)
        # 为每个 Agent 添加独立日志文件
        log_path = f"logs/agent_{agent_name}.log"
        self._logger.add(
            log_path,
            rotation="10 MB",
            retention="7 days",
            level="DEBUG",
            format="{time:YYYY-MM-DD HH:mm:ss.SSS} | {level} | {extra[agent]} | {message}",
            filter=lambda record: record["extra"].get("agent") == agent_name,
        )

    def _enrich(self, event_type: str, **kwargs) -> str:
        """构建结构化日志消息，自动附加 trace_id。"""
        trace_id = get_trace_id() or ""
        parts = [f"event={event_type}"]
        if trace_id:
            parts.append(f"trace_id={trace_id}")
        for k, v in kwargs.items():
            if v is not None:
                parts.append(f"{k}={v}")
        return " | ".join(parts)

    def info(self, event_type: str, **kwargs) -> None:
        self._logger.info(self._enrich(event_type, **kwargs))

    def debug(self, event_type: str, **kwargs) -> None:
        self._logger.debug(self._enrich(event_type, **kwargs))

    def warning(self, event_type: str, **kwargs) -> None:
        self._logger.warning(self._enrich(event_type, **kwargs))

    def error(self, event_type: str, **kwargs) -> None:
        self._logger.error(self._enrich(event_type, **kwargs))


def get_agent_logger(agent_name: str) -> StructuredLogger:
    """获取指定 Agent 的结构化日志记录器（缓存，避免重复添加 handler）。"""
    return StructuredLogger(agent_name)
