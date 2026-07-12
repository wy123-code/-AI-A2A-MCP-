"""配置模块测试 —— 验证配置结构和意图体系完整性。"""
import pytest
from config import (
    LLM_CONFIG, MYSQL_CONFIG, MILVUS_CONFIG, REDIS_CONFIG, MEMORY_CONFIG,
    SUPPORTED_INTENTS, INTENT_CN_MAP, INTENT_SLOTS, TOOL_ROUTING,
)


class TestLLMConfig:
    """LLM 配置测试"""

    def test_llm_config_has_required_keys(self):
        assert "model" in LLM_CONFIG
        assert "api_key" in LLM_CONFIG
        assert "base_url" in LLM_CONFIG
        assert "temperature" in LLM_CONFIG
        assert "max_tokens" in LLM_CONFIG

    def test_temperature_in_range(self):
        assert 0 <= LLM_CONFIG["temperature"] <= 1


class TestDatabaseConfig:
    """数据库配置测试"""

    def test_mysql_config_has_required_keys(self):
        for key in ("host", "port", "user", "password", "database", "charset"):
            assert key in MYSQL_CONFIG, f"Missing key: {key}"

    def test_milvus_config_has_required_keys(self):
        for key in ("host", "port", "database_name", "collection_name"):
            assert key in MILVUS_CONFIG, f"Missing key: {key}"

    def test_redis_config_has_required_keys(self):
        for key in ("host", "port", "db", "session_ttl", "max_history_turns"):
            assert key in REDIS_CONFIG, f"Missing key: {key}"

    def test_memory_config_values(self):
        assert MEMORY_CONFIG["max_preferences"] > 0
        assert MEMORY_CONFIG["max_history_queries"] > 0
        assert 0 < MEMORY_CONFIG["relevance_threshold"] <= 1


class TestIntentSystem:
    """意图体系测试"""

    def test_all_intents_have_cn_name(self):
        for intent in SUPPORTED_INTENTS:
            assert intent in INTENT_CN_MAP, f"Missing CN name for: {intent}"

    def test_all_intents_have_slots(self):
        for intent in SUPPORTED_INTENTS:
            assert intent in INTENT_SLOTS, f"Missing slots for: {intent}"

    def test_all_intents_have_tool_routing(self):
        for intent in SUPPORTED_INTENTS:
            assert intent in TOOL_ROUTING, f"Missing tool routing for: {intent}"

    def test_intent_count_consistency(self):
        assert len(SUPPORTED_INTENTS) == 10
        assert len(INTENT_CN_MAP) == 10
        assert len(INTENT_SLOTS) == 10
        assert len(TOOL_ROUTING) == 10

    def test_tool_names_in_registry(self):
        """验证所有路由到的工具都在 TOOL_REGISTRY 中注册。"""
        from tools import TOOL_REGISTRY
        for intent, tool_name in TOOL_ROUTING.items():
            assert tool_name in TOOL_REGISTRY, f"Tool not found: {tool_name} for intent={intent}"
