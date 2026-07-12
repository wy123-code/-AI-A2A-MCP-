"""数据模型定义 - Pydantic schemas for API requests/responses and Agent state"""
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field


class ChatRequest(BaseModel):
    """对话请求模型

    Attributes:
        query: 用户输入的查询文本
        session_id: 会话标识符，用于跟踪对话上下文
        history: 历史对话记录列表，包含角色和内容
    """
    query: str
    session_id: str = "default"
    history: Optional[List[Dict[str, Any]]] = None


class ChatResponse(BaseModel):
    """对话响应模型

    Attributes:
        session_id: 会话标识符
        answer: AI助手的回答内容
        intent: 识别出的用户意图
        follow_up_needed: 是否需要进一步追问以获取更多信息
        follow_up_question: 如果需要追问，具体的追问问题
        duration_ms: 处理耗时（毫秒）
    """
    session_id: str
    answer: str
    intent: str = ""
    follow_up_needed: bool = False
    follow_up_question: str = ""
    duration_ms: int = 0

