"""Orchestrator 子包 —— 编排调度核心模块。

优化 (P2): 从单文件 agent.py 拆分为:
  - agent.py: 核心管线 (process/process_stream)
  - scheduler.py: 任务调度与健康预检分发
  - aggregator.py: 多任务结果聚合与去重
  - context_manager.py: 上下文管理与 Token 控制
"""
from .agent import OrchestratorAgent
from .scheduler import TaskScheduler, TaskDependency
from .aggregator import aggregate_results, resolve_conflicts, rank_results
from .context_manager import ContextManager

__all__ = [
    "OrchestratorAgent",
    "TaskScheduler", "TaskDependency",
    "aggregate_results", "resolve_conflicts", "rank_results",
    "ContextManager",
]
