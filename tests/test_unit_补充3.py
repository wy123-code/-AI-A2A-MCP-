"""单元测试补充（第四轮） —— P2 基础设施模块。

覆盖范围：
  enhanced_message / error_handler 中间件 / trace_id / schemas / prompts
"""

import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


# ============================================================================
# P2-1: 增强消息体 (mcp/enhanced_message.py)
# ============================================================================

class TestEnhancedMessage:
    """增强消息协议 —— task_id / 状态流转 / 消息验证。"""

    def test_new_task_id_format(self):
        """生成的 task_id 格式为 task_xxx。"""
        from mcp.enhanced_message import EnhancedAgentMessage
        tid = EnhancedAgentMessage.new_task_id()
        assert tid.startswith("task_")
        assert len(tid) == 17  # "task_" + 12 hex chars

    def test_new_task_id_unique(self):
        """连续生成的 task_id 不重复。"""
        from mcp.enhanced_message import EnhancedAgentMessage
        ids = {EnhancedAgentMessage.new_task_id() for _ in range(100)}
        assert len(ids) == 100

    def test_create_task_with_trace_injects_ids(self):
        """自动注入 trace_id 和 task_id。"""
        from mcp.enhanced_message import EnhancedAgentMessage
        msg = EnhancedAgentMessage.create_task_with_trace(
            sender="orch", receiver="worker.weather",
            task_type="weather_query", payload={"city": "北京"},
        )
        assert msg.sender == "orch"
        assert msg.receiver == "worker.weather"
        assert msg.trace_id != ""
        assert msg.ack_required is True

    def test_create_task_with_explicit_trace(self):
        """使用显式传入的 trace_id。"""
        from mcp.enhanced_message import EnhancedAgentMessage
        msg = EnhancedAgentMessage.create_task_with_trace(
            sender="orch", receiver="worker.weather",
            task_type="test", payload={}, trace_id="tr_my_trace",
        )
        assert msg.trace_id == "tr_my_trace"

    def test_valid_transitions_defined(self):
        """合法状态流转表已定义。"""
        from mcp.enhanced_message import VALID_STATUS_TRANSITIONS
        assert "pending" in VALID_STATUS_TRANSITIONS
        assert "running" in VALID_STATUS_TRANSITIONS
        assert "success" in VALID_STATUS_TRANSITIONS
        assert "failed" in VALID_STATUS_TRANSITIONS

    def test_verify_message_integrity_valid(self):
        """完整的消息 → 验证通过（返回 None）。"""
        from mcp.enhanced_message import EnhancedAgentMessage
        msg = EnhancedAgentMessage.create_task_with_trace(
            sender="orch", receiver="worker", task_type="test", payload={"key": "v"},
        )
        result = EnhancedAgentMessage.verify_message_integrity(msg)
        assert result is None

    def test_verify_message_integrity_no_sender(self):
        """缺少 sender → 返回错误描述字符串。"""
        from mcp.enhanced_message import EnhancedAgentMessage
        from agent_bus.message import AgentMessage
        msg = AgentMessage.create_task("", "worker", "test", {"k": "v"})
        result = EnhancedAgentMessage.verify_message_integrity(msg)
        assert result is not None
        assert "sender" in result

    def test_verify_message_integrity_no_payload(self):
        """task 消息缺 payload → 验证失败。"""
        from mcp.enhanced_message import EnhancedAgentMessage
        from agent_bus.message import AgentMessage
        msg = AgentMessage(sender="a", receiver="b", message_type="task", task_type="t")
        result = EnhancedAgentMessage.verify_message_integrity(msg)
        assert result is not None
        assert "payload" in result


# ============================================================================
# P2-2: HTTP 错误处理中间件 (middleware/error_handler.py)
# ============================================================================

class TestErrorHandlerMiddleware:
    """全局异常处理中间件 —— 捕获异常 + 标准化响应。"""

    @pytest.mark.asyncio
    async def test_normal_request_passes(self):
        """正常请求 → 透传响应。"""
        from middleware.error_handler import ErrorHandlerMiddleware
        middleware = ErrorHandlerMiddleware(MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_request.state.trace_id = "tr_test"
        mock_response = MagicMock(status_code=200)
        result = await middleware.dispatch(mock_request, AsyncMock(return_value=mock_response))
        assert result.status_code == 200

    @pytest.mark.asyncio
    async def test_exception_returns_500(self):
        """异常 → 500 + JSON 错误体。"""
        from middleware.error_handler import ErrorHandlerMiddleware
        middleware = ErrorHandlerMiddleware(MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/chat"
        mock_request.state.trace_id = "tr_err"

        async def failing(*args, **kwargs):
            raise ValueError("something broke")

        with patch("common.monitor.metrics.metrics_collector") as mock_m:
            mock_m.start = AsyncMock()
            mock_m.record_request = AsyncMock()
            result = await middleware.dispatch(mock_request, failing)
        assert result.status_code == 500
        body = result.body if isinstance(result.body, str) else result.body.decode()
        data = json.loads(body)
        assert data["error"]["code"] == "INTERNAL_ERROR"
        assert data["error"]["trace_id"] == "tr_err"

    @pytest.mark.asyncio
    async def test_exception_contains_trace_id(self):
        """异常响应包含 trace_id。"""
        from middleware.error_handler import ErrorHandlerMiddleware
        middleware = ErrorHandlerMiddleware(MagicMock())
        mock_request = MagicMock()
        mock_request.url.path = "/api"
        mock_request.state.trace_id = "trace_abc123"

        async def failing(*args, **kwargs):
            raise RuntimeError("boom")

        with patch("common.monitor.metrics.metrics_collector") as mock_m:
            mock_m.start = AsyncMock()
            mock_m.record_request = AsyncMock()
            result = await middleware.dispatch(mock_request, failing)
        body = result.body if isinstance(result.body, str) else result.body.decode()
        assert "trace_abc123" in body


# ============================================================================
# P2-3: TraceID 中间件 (middleware/trace_id.py)
# ============================================================================

class TestTraceID:
    """链路追踪 —— ContextVar / get_trace_id / 中间件。"""

    def test_get_trace_id_default_empty(self):
        """无 trace 时返回空字符串。"""
        from middleware.trace_id import get_trace_id
        assert get_trace_id() == ""

    @pytest.mark.asyncio
    async def test_middleware_sets_trace_id_on_request(self):
        """中间件把 trace_id 注入 request.state。"""
        from middleware.trace_id import TraceIDMiddleware
        middleware = TraceIDMiddleware(MagicMock())
        mock_request = MagicMock()
        mock_request.headers.get.return_value = ""
        mock_request.state = MagicMock()
        mock_response = MagicMock()
        mock_response.headers = {}

        result = await middleware.dispatch(mock_request, AsyncMock(return_value=mock_response))
        assert mock_request.state.trace_id != ""
        assert len(mock_request.state.trace_id) == 12
        assert "X-Trace-ID" in result.headers

    @pytest.mark.asyncio
    async def test_middleware_uses_existing_trace_id(self):
        """使用请求头中已有的 X-Trace-ID。"""
        from middleware.trace_id import TraceIDMiddleware
        middleware = TraceIDMiddleware(MagicMock())
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "existing_trace"
        mock_request.state = MagicMock()
        mock_response = MagicMock()
        mock_response.headers = {}

        result = await middleware.dispatch(mock_request, AsyncMock(return_value=mock_response))
        assert mock_request.state.trace_id == "existing_trace"
        assert result.headers["X-Trace-ID"] == "existing_trace"


# ============================================================================
# P2-4: Pydantic 数据模型 (models/schemas.py)
# ============================================================================

class TestChatRequestSchema:
    """ChatRequest 校验 —— 必填/可选/默认值。"""

    def test_minimal_request(self):
        """仅 query → 校验通过，session_id 默认值。"""
        from models.schemas import ChatRequest
        req = ChatRequest(query="北京天气")
        assert req.query == "北京天气"
        assert req.session_id == "default"
        assert req.history is None

    def test_full_request(self):
        """全部字段填充。"""
        from models.schemas import ChatRequest
        req = ChatRequest(
            query="北京天气", session_id="s1",
            history=[{"role": "user", "content": "你好"}],
        )
        assert req.session_id == "s1"
        assert len(req.history) == 1

    def test_missing_query_raises(self):
        """缺少 query → ValidationError。"""
        from pydantic import ValidationError
        from models.schemas import ChatRequest
        with pytest.raises(ValidationError):
            ChatRequest()

    def test_empty_query_allowed(self):
        """空字符串 query → 校验通过（业务层拦截）。"""
        from models.schemas import ChatRequest
        req = ChatRequest(query="")
        assert req.query == ""


class TestChatResponseSchema:
    """ChatResponse 结构 —— 字段完整性。"""

    def test_minimal_response(self):
        """最少字段 → 默认值检查。"""
        from models.schemas import ChatResponse
        resp = ChatResponse(session_id="s1", answer="北京明天晴")
        assert resp.answer == "北京明天晴"
        assert resp.intent == ""
        assert resp.follow_up_needed is False
        assert resp.follow_up_question == ""
        assert resp.duration_ms == 0

    def test_full_response(self):
        """全部字段填充 → 序列化正确。"""
        from models.schemas import ChatResponse
        resp = ChatResponse(
            session_id="s2", answer="北京明天晴，25°C",
            intent="weather_query", follow_up_needed=False,
            duration_ms=150,
        )
        d = resp.model_dump()
        assert d["intent"] == "weather_query"
        assert d["duration_ms"] == 150


# ============================================================================
# P2-5: Prompt 模板 (prompts/templates.py)
# ============================================================================

class TestPromptTemplates:
    """Prompt 模板 —— 非空 / 格式化正确。"""

    def test_intent_slot_prompt_non_empty(self):
        """意图槽位模板非空。"""
        from prompts.templates import INTENT_SLOT_PROMPT
        assert len(INTENT_SLOT_PROMPT) > 100
        assert "旅游助手" in INTENT_SLOT_PROMPT

    def test_intent_slot_prompt_format(self):
        """模板可正确格式化。"""
        from prompts.templates import INTENT_SLOT_PROMPT
        formatted = INTENT_SLOT_PROMPT.format(
            today="2026-07-09", query="北京天气",
            history="无", previous_context="",
        )
        assert "北京天气" in formatted
        assert "2026-07-09" in formatted

    def test_sql_generation_prompt_non_empty(self):
        """SQL 生成模板非空。"""
        from prompts.templates import SQL_GENERATION_PROMPT
        assert len(SQL_GENERATION_PROMPT) > 100

    def test_final_answer_prompt_non_empty(self):
        """最终回答模板非空。"""
        from prompts.templates import FINAL_ANSWER_PROMPT
        assert len(FINAL_ANSWER_PROMPT) > 100

    def test_all_template_keys_present(self):
        """所有模板 key 已定义。"""
        import prompts.templates as t
        expected = ["INTENT_SLOT_PROMPT", "SQL_GENERATION_PROMPT",
                     "FINAL_ANSWER_PROMPT", "RECOMMENDATION_PROMPT",
                     "TICKET_QUERY_PROMPT"]
        for key in expected:
            assert hasattr(t, key), f"Missing template: {key}"
            assert len(getattr(t, key)) > 0, f"Empty template: {key}"
