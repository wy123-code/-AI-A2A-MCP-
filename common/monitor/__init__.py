"""common/monitor —— 全链路可观测性基础设施。

提供:
  - MetricsCollector: 性能指标收集 (P50/P95/P99、成功率、慢请求)
  - StructuredLogger: 结构化 Agent 日志 (自动附加 trace_id)
"""
from .metrics import MetricsCollector, metrics_collector
from .logger import StructuredLogger, get_agent_logger

__all__ = [
    "MetricsCollector", "metrics_collector",
    "StructuredLogger", "get_agent_logger",
]
