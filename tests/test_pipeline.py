"""Agent 管线节点测试 —— 测试槽位填充、工具执行、回答生成各节点。"""
import json
import pytest
from unittest.mock import MagicMock, patch, AsyncMock

from graph.nodes.tool_execution import tool_execution_node
from graph.nodes.response_generation import final_answer_node


class TestToolExecution:
    """工具执行节点测试"""

    @pytest.mark.asyncio
    async def test_routes_to_correct_tool(self, sample_state, mock_query_cache):
        sample_state["intent"] = "weather_query"
        sample_state["slots"] = {"city": "北京", "date": "2026-05-16"}

        mock_tool = AsyncMock(return_value={
            "success": True, "error": None,
            "data": [{"city": "北京", "temperature_high": 25}]
        })

        with patch.dict("tools.TOOL_REGISTRY", {"weather_query": mock_tool}):
            result = await tool_execution_node(sample_state)

        assert result["tool_name"] == "weather_query"
        assert result["tool_result"]["success"] is True
        assert result["next_step"] == "result_summary"

    @pytest.mark.asyncio
    async def test_handles_unknown_intent(self, sample_state, mock_query_cache):
        sample_state["intent"] = "unknown_intent"
        result = await tool_execution_node(sample_state)
        assert result["tool_result"]["success"] is False
        assert "No tool" in result["error"]

    @pytest.mark.asyncio
    async def test_handles_tool_exception(self, sample_state, mock_query_cache):
        sample_state["intent"] = "weather_query"
        sample_state["slots"] = {"city": "北京"}

        mock_tool = AsyncMock(side_effect=Exception("Connection refused"))

        with patch.dict("tools.TOOL_REGISTRY", {"weather_query": mock_tool}):
            result = await tool_execution_node(sample_state)

        assert result["tool_result"]["success"] is False
        assert "Connection refused" in result["tool_result"]["error"]


class TestResponseGeneration:
    """回答生成节点测试"""

    @pytest.mark.asyncio
    async def test_generates_final_answer(self, sample_state, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content="根据查询，北京明天天气晴朗，最高温25°C，建议做好防晒。"
            ))]
        )
        sample_state["intent"] = "weather_query"
        sample_state["tool_result"] = {
            "success": True,
            "data": [{"city": "北京", "temperature_high": 25, "temperature_low": 15}],
        }
        result = await final_answer_node(sample_state)
        assert len(result["final_answer"]) > 0
        assert "天气" in result["final_answer"]
        assert result["next_step"] == "end"

    @pytest.mark.asyncio
    async def test_handles_tool_failure(self, sample_state):
        sample_state["tool_result"] = {"success": False, "error": "查询超时"}
        result = await final_answer_node(sample_state)
        assert "查询失败" in result["final_answer"]
        assert result["next_step"] == "end"
