"""OpenAI 客户端池 —— 启动时创建，全模块共享，统一 timeout + max_retries。"""
from typing import Dict
from openai import OpenAI
from loguru import logger
from config import LLM_CONFIG, INTENT_LLM_CONFIG


class LLMClientManager:
    """管理多个 OpenAI 客户端实例，按配置键复用。"""

    def __init__(self):
        self._clients: Dict[str, OpenAI] = {}

    def get_client(self, config_key: str = "default") -> OpenAI:
        """获取或创建客户端实例。

        config_key:
          - "default"  → LLM_CONFIG (回答生成、工具查询、A2A SQL)
          - "intent"   → INTENT_LLM_CONFIG (意图识别)
          - "embedding" → 复用 default 的 api_key/base_url
        """
        if config_key in self._clients:
            return self._clients[config_key]

        if config_key == "intent":
            cfg = INTENT_LLM_CONFIG
        else:
            cfg = LLM_CONFIG

        client = OpenAI(
            api_key=cfg["api_key"],
            base_url=cfg["base_url"],
            timeout=30.0,
            max_retries=1,
        )
        self._clients[config_key] = client
        logger.info(f"LLM client created: key={config_key}, model={cfg.get('model', '?')}")
        return client


llm_manager = LLMClientManager()
