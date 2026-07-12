"""Worker 标准化协议 —— 定义每个领域 Agent 必须遵循的五步闭环。

五步闭环：
  1. 子意图校验 (validate_intent)    → 确认意图匹配 supported_intents
  2. 私有槽位补全 (_preprocess)      → 填入领域默认值（如天气默认当天）
  3. 领域工具调用 (execute)           → 调用具体业务工具
  4. 本地数据预处理 (_postprocess)    → 标准化/过滤/排序结果
  5. 标准结构化输出                   → {"success": bool, "error": str, "data": Any, "metadata": dict}
"""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional


@dataclass
class WorkerResult:
    """Worker Agent 标准输出结构 —— 所有领域 Agent 统一返回格式。"""

    success: bool
    error: Optional[str] = None
    data: Any = None
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """将 WorkerResult 转为标准字典格式，用于 MCP 响应序列化。"""
        return {
            "success": self.success,
            "error": self.error,
            "data": self.data if self.data is not None else [],
            "metadata": self.metadata,
        }


class WorkerProtocol:
    """Worker Agent 标准化协议 —— 定义 execute 管线的标准执行流程。

    子类覆盖 _preprocess() 和 _postprocess() 来定制领域行为，
    无需改动 execute() 核心逻辑即可接入标准化闭环。
    """

    @staticmethod
    def validate_intent(intent: str, supported_intents: List[str]) -> bool:
        """校验意图是否在当前 Worker 的支持范围内。"""
        return intent in supported_intents

    @staticmethod
    def build_metadata(worker_name: str, intent: str, duration_ms: int = 0,
                       result_count: int = 0, cache_hit: bool = False,
                       degraded: bool = False) -> Dict[str, Any]:
        """构建标准化 metadata 块。"""
        return {
            "worker": worker_name,
            "intent": intent,
            "duration_ms": duration_ms,
            "result_count": result_count,
            "cache_hit": cache_hit,
            "degraded": degraded,
            "source": "a2a+mcp",
        }
