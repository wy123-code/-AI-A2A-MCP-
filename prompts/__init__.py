"""Prompt 模板集合 —— 为 Agent 管线的各个节点提供 LLM 提示词。

所有模板定义在 prompts/templates.py 中，此处仅做重导出。
"""

from .templates import (
    INTENT_SLOT_PROMPT,
    SQL_GENERATION_PROMPT,
    FINAL_ANSWER_PROMPT,
    RECOMMENDATION_PROMPT,
    TICKET_QUERY_PROMPT,
)
