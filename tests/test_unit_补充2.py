"""单元测试补充（第三轮） —— 覆盖 P0/P1 核心模块。

覆盖范围：
  P0: intent_slot / mcp_server / intent_router
  P1: AgentMessage / CircuitBreaker / ErrorHandler / ResultAggregator

复用 conftest.py 夹具。
"""

import json
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch


# ============================================================================
# P0-1: 意图识别 + 槽位填充 (graph/nodes/intent_slot.py)
# ============================================================================

class TestIntentSlot:
    """意图槽位识别 —— 核心业务逻辑。"""

    @pytest.mark.asyncio
    async def test_valid_intent_goes_to_tool_execution(self, mock_openai_client):
        """LLM 正常返回意图+槽位 → next_step=tool_execution。"""
        from graph.nodes.intent_slot import intent_slot_node
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps({
                "intents": [{"intent": "weather_query", "slots": {"city": "北京", "date": "2026-05-16"}, "missing_slots": []}],
                "is_multi": False, "follow_up_question": "",
            })))]
        )
        state = {
            "query": "北京明天天气", "session_id": "t1", "history": [],
            "intent": "", "intent_in_scope": True, "need_planning": False,
            "sub_tasks": [], "slots": {}, "missing_slots": [],
            "follow_up_question": "", "follow_up_count": 0,
            "tool_name": "", "tool_input": {}, "tool_result": None,
            "summary": "", "final_answer": "", "next_step": "intent_slot", "error": "",
        }
        result = await intent_slot_node(state)
        assert result["intent"] == "weather_query"
        assert result["slots"]["city"] == "北京"
        assert result["next_step"] == "tool_execution"

    @pytest.mark.asyncio
    async def test_out_of_scope_rejected(self, mock_openai_client):
        """越界查询 → next_step=end。"""
        from graph.nodes.intent_slot import intent_slot_node
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps({
                "intents": [{"intent": "out_of_scope", "slots": {}, "missing_slots": []}],
                "is_multi": False, "follow_up_question": "",
            })))]
        )
        state = {
            "query": "今天股市", "session_id": "t2", "history": [],
            "intent": "", "intent_in_scope": True, "need_planning": False,
            "sub_tasks": [], "slots": {}, "missing_slots": [],
            "follow_up_question": "", "follow_up_count": 0,
            "tool_name": "", "tool_input": {}, "tool_result": None,
            "summary": "", "final_answer": "", "next_step": "intent_slot", "error": "",
        }
        result = await intent_slot_node(state)
        assert result["next_step"] == "end"
        assert result["intent_in_scope"] is False

    @pytest.mark.asyncio
    async def test_missing_slots_generates_followup(self, mock_openai_client):
        """槽位缺失 → 生成追问。"""
        from graph.nodes.intent_slot import intent_slot_node
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content=json.dumps({
                "intents": [{"intent": "weather_query", "slots": {"city": None}, "missing_slots": ["city"]}],
                "is_multi": False, "follow_up_question": "请问哪个城市？",
            })))]
        )
        state = {
            "query": "天气", "session_id": "t3", "history": [],
            "intent": "", "intent_in_scope": True, "need_planning": False,
            "sub_tasks": [], "slots": {}, "missing_slots": [],
            "follow_up_question": "", "follow_up_count": 0,
            "tool_name": "", "tool_input": {}, "tool_result": None,
            "summary": "", "final_answer": "", "next_step": "intent_slot", "error": "",
        }
        result = await intent_slot_node(state)
        assert result["next_step"] == "end"
        assert len(result["final_answer"]) > 0

    def test_pre_extract_date_tomorrow(self):
        """正则预提取 '明天' → yyyy-MM-dd。"""
        from graph.nodes.intent_slot import _pre_extract_slots
        result = _pre_extract_slots("明天北京天气")
        assert "date" in result
        assert result["date"].startswith("20")

    def test_pre_extract_cities(self):
        """正则预提取城市对。"""
        from graph.nodes.intent_slot import _pre_extract_slots
        result = _pre_extract_slots("从北京到上海的机票")
        assert result.get("departure_city") == "北京"
        assert result.get("arrival_city") == "上海"


# ============================================================================
# P0-2: MCP Server SQL 安全 (mcp/mcp_server.py)
# ============================================================================

class TestMCPServerSecurity:
    """MCP Server —— SQL 四层安全防护。"""

    @pytest.mark.asyncio
    async def test_select_allowed(self):
        """SELECT 合法通过。"""
        from mcp.mcp_server import MCPServer
        server = MCPServer("test")
        with patch("mcp.mcp_server.mysql_client") as mock_db:
            mock_db.execute_query = AsyncMock(return_value=[{"id": 1}])
            result = await server.execute_sql("SELECT * FROM weather WHERE city='北京'")
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_delete_blocked(self):
        """DELETE 被拦截。"""
        from mcp.mcp_server import MCPServer
        server = MCPServer("test")
        result = await server.execute_sql("DELETE FROM weather")
        assert result["success"] is False
        assert "Only SELECT" in result["error"]

    @pytest.mark.asyncio
    async def test_insert_blocked(self):
        """INSERT 被拦截。"""
        from mcp.mcp_server import MCPServer
        server = MCPServer("test")
        result = await server.execute_sql("INSERT INTO weather VALUES (1)")
        assert result["success"] is False

    @pytest.mark.asyncio
    async def test_multi_statement_blocked(self):
        """多语句注入被拦截。"""
        from mcp.mcp_server import MCPServer
        server = MCPServer("test")
        result = await server.execute_sql("SELECT * FROM weather; DROP TABLE weather")
        assert result["success"] is False
        assert "Multiple statements" in result["error"]

    @pytest.mark.asyncio
    async def test_unauthorized_table_blocked(self):
        """未授权表被拦截。"""
        from mcp.mcp_server import MCPServer
        server = MCPServer("test")
        result = await server.execute_sql("SELECT * FROM users WHERE id=1")
        assert result["success"] is False
        assert "Table not allowed" in result["error"]

    @pytest.mark.asyncio
    async def test_limit_auto_appended(self):
        """无 LIMIT → 自动追加。"""
        from mcp.mcp_server import _append_limit_if_missing
        result = _append_limit_if_missing("SELECT * FROM weather")
        assert "LIMIT 50" in result


# ============================================================================
# P0-3: 意图路由 (graph/nodes/intent_router.py)
# ============================================================================

class TestIntentRouter:
    """意图路由节点 —— 单/多任务分发。"""

    @pytest.mark.asyncio
    async def test_single_intent_creates_one_worker_target(self):
        """单意图 → 1 个 worker_target。"""
        from graph.nodes.intent_router import intent_router_node
        state = {
            "query": "北京天气", "session_id": "t1",
            "intent": "weather_query", "intent_in_scope": True,
            "slots": {"city": "北京"},
            "sub_tasks": [{"intent": "weather_query", "slots": {"city": "北京"}, "missing_slots": []}],
            "missing_slots": [], "need_planning": False, "worker_targets": [],
            "next_step": "", "history": [], "follow_up_count": 0,
            "tool_name": "", "tool_input": {}, "tool_result": None,
            "follow_up_question": "", "summary": "", "final_answer": "", "error": "",
        }
        result = await intent_router_node(state)
        assert result["next_step"] == "dispatch_to_worker"
        assert len(result["worker_targets"]) == 1

    @pytest.mark.asyncio
    async def test_multi_intent_creates_multi_worker_targets(self):
        """多意图 → 多个 worker_target。"""
        from graph.nodes.intent_router import intent_router_node
        state = {
            "query": "天气和机票", "session_id": "t2",
            "intent": "weather_query", "intent_in_scope": True,
            "slots": {"city": "北京"},
            "sub_tasks": [
                {"intent": "weather_query", "slots": {"city": "北京"}, "missing_slots": []},
                {"intent": "flight_ticket", "slots": {"departure_city": "北京"}, "missing_slots": []},
            ],
            "missing_slots": [], "need_planning": False, "worker_targets": [],
            "next_step": "", "history": [], "follow_up_count": 0,
            "tool_name": "", "tool_input": {}, "tool_result": None,
            "follow_up_question": "", "summary": "", "final_answer": "", "error": "",
        }
        result = await intent_router_node(state)
        assert len(result["worker_targets"]) == 2

    @pytest.mark.asyncio
    async def test_missing_slots_routes_to_end(self):
        """缺槽位 → end。"""
        from graph.nodes.intent_router import intent_router_node
        state = {
            "query": "天气", "session_id": "t3",
            "intent": "weather_query", "intent_in_scope": True,
            "slots": {}, "sub_tasks": [],
            "missing_slots": ["city"], "need_planning": False, "worker_targets": [],
            "next_step": "", "history": [], "follow_up_count": 0,
            "tool_name": "", "tool_input": {}, "tool_result": None,
            "follow_up_question": "", "summary": "", "final_answer": "", "error": "",
        }
        result = await intent_router_node(state)
        assert result["next_step"] == "end"

    @pytest.mark.asyncio
    async def test_out_of_scope_routes_to_end(self):
        """out_of_scope → end。"""
        from graph.nodes.intent_router import intent_router_node
        state = {
            "query": "股市", "session_id": "t4",
            "intent": "out_of_scope", "intent_in_scope": False,
            "slots": {}, "sub_tasks": [],
            "missing_slots": [], "need_planning": False, "worker_targets": [],
            "next_step": "", "history": [], "follow_up_count": 0,
            "tool_name": "", "tool_input": {}, "tool_result": None,
            "follow_up_question": "", "summary": "", "final_answer": "", "error": "",
        }
        result = await intent_router_node(state)
        assert result["next_step"] == "end"


# ============================================================================
# P1-1: Agent 消息体 (agent_bus/message.py)
# ============================================================================

class TestAgentMessage:
    """AgentMessage 标准消息体 —— 创建/序列化/ACK/状态转换。"""

    def test_create_task_message_defaults(self):
        """创建 task 消息 —— 默认值检查。"""
        from agent_bus.message import AgentMessage
        msg = AgentMessage.create_task(
            sender="orchestrator", receiver="worker.weather",
            task_type="weather_query", payload={"city": "北京"},
        )
        assert msg.message_type == "task"
        assert msg.sender == "orchestrator"
        assert msg.receiver == "worker.weather"
        assert msg.status == "pending"
        assert msg.correlation_id != ""
        assert len(msg.correlation_id) == 12

    def test_create_result_reverses_sender_receiver(self):
        """result 消息交换 sender/receiver。"""
        from agent_bus.message import AgentMessage
        task = AgentMessage.create_task("orch", "worker", "test", {})
        result = AgentMessage.create_result(task, "success", {"data": [1, 2]})
        assert result.message_type == "result"
        assert result.sender == task.receiver
        assert result.receiver == task.sender
        assert result.correlation_id == task.correlation_id

    def test_create_ack_ties_to_original(self):
        """ACK 消息关联原始 task。"""
        from agent_bus.message import AgentMessage
        task = AgentMessage.create_task("orch", "worker", "test", {})
        ack = AgentMessage.create_ack(task)
        assert ack.message_type == "ack"
        assert ack.correlation_id == task.correlation_id

    def test_json_roundtrip(self):
        """序列化 → 反序列化 数据一致。"""
        from agent_bus.message import AgentMessage
        original = AgentMessage.create_task(
            "orch", "worker.weather", "weather_query",
            {"city": "北京", "date": "2026-05-16"},
            trace_id="tr_001",
        )
        parsed = AgentMessage.from_json(original.to_json())
        assert parsed.sender == original.sender
        assert parsed.receiver == original.receiver
        assert parsed.correlation_id == original.correlation_id
        assert parsed.trace_id == "tr_001"

    def test_mark_running_changes_status(self):
        """mark_running → status='running'。"""
        from agent_bus.message import AgentMessage
        msg = AgentMessage.create_task("orch", "worker", "test", {})
        msg.mark_running()
        assert msg.status == "running"
        assert msg.ack_time is not None

    def test_mark_completed_stores_result(self):
        """mark_completed → status='success'。"""
        from agent_bus.message import AgentMessage
        msg = AgentMessage.create_task("orch", "worker", "test", {})
        msg.mark_completed({"success": True, "data": [1]})
        assert msg.status == "success"
        assert msg.payload["success"] is True

    def test_mark_failed_sets_error(self):
        """mark_failed → status='failed' + error_code。"""
        from agent_bus.message import AgentMessage
        msg = AgentMessage.create_task("orch", "worker", "test", {})
        msg.mark_failed("timeout", error_code="TIMEOUT")
        assert msg.status == "failed"
        assert msg.error_code == "TIMEOUT"


# ============================================================================
# P1-2: 熔断器 (agent_bus/circuit_breaker.py)
# ============================================================================

class TestCircuitBreaker:
    """Agent 熔断器 —— 三态转换。"""

    @pytest.mark.asyncio
    async def test_starts_closed(self, mock_redis):
        """初始状态 CLOSED → 允许调用。"""
        from agent_bus.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        cb._redis = mock_redis
        mock_redis.get = AsyncMock(return_value=None)
        assert await cb.before_call("worker.test") is True

    @pytest.mark.asyncio
    async def test_open_blocks_calls(self, mock_redis):
        """OPEN 状态 → 快速失败。"""
        from agent_bus.circuit_breaker import CircuitBreaker, CircuitState
        cb = CircuitBreaker()
        cb._redis = mock_redis
        # before_call: ①查state → OPEN ②查fail_count → 6(>=threshold=5) → 快速失败
        mock_redis.get = AsyncMock(side_effect=[CircuitState.OPEN, "6"])
        assert await cb.before_call("worker.test") is False

    @pytest.mark.asyncio
    async def test_success_resets_circuit(self, mock_redis):
        """成功调用 → 重置为 CLOSED。"""
        from agent_bus.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        cb._redis = mock_redis
        mock_redis.get = AsyncMock(return_value="open")
        mock_redis.incr = AsyncMock(return_value=1)
        mock_redis.set = AsyncMock()
        mock_redis.delete = AsyncMock()
        await cb.after_call("worker.test", success=True)
        set_calls = [c[1] for c in mock_redis.set.mock_calls if len(c[1]) >= 2]
        assert any("closed" in str(c).lower() for c in set_calls)

    @pytest.mark.asyncio
    async def test_fail_increments_count(self, mock_redis):
        """失败 → 递增计数。"""
        from agent_bus.circuit_breaker import CircuitBreaker
        cb = CircuitBreaker()
        cb._redis = mock_redis
        mock_redis.get = AsyncMock(return_value="closed")
        mock_redis.incr = AsyncMock(return_value=3)
        mock_redis.expire = AsyncMock()
        await cb.after_call("worker.test", success=False)
        mock_redis.incr.assert_called()


# ============================================================================
# P1-3: 错误处理 (agent_bus/error_handler.py)
# ============================================================================

class TestErrorHandler:
    """通信容错工具 —— 超时/重试/降级。"""

    def test_degrade_factory_returns_structured_error(self):
        """降级工厂返回标准错误结构。"""
        from agent_bus.error_handler import ErrorHandler
        result = ErrorHandler.degrade_factory("weather_query")
        assert result["success"] is False
        assert "weather_query" in result["error"]
        assert result["degraded"] is True

    @pytest.mark.asyncio
    async def test_with_retry_no_retry_on_success(self):
        """首次成功 → 不重试。"""
        from agent_bus.error_handler import ErrorHandler
        count = 0

        async def work():
            nonlocal count
            count += 1
            return "ok"

        result = await ErrorHandler.with_retry(work, max_retries=2)
        assert result == "ok"
        assert count == 1

    @pytest.mark.asyncio
    async def test_with_retry_retries_on_failure(self):
        """失败后重试 → 最终成功。"""
        from agent_bus.error_handler import ErrorHandler
        count = 0

        async def work():
            nonlocal count
            count += 1
            if count < 3:
                raise ConnectionError("fail")
            return "recovered"

        result = await ErrorHandler.with_retry(work, max_retries=3)
        assert result == "recovered"
        assert count == 3

    @pytest.mark.asyncio
    async def test_with_retry_exhausted_raises(self):
        """重试耗尽 → 抛出异常。"""
        from agent_bus.error_handler import ErrorHandler

        async def work():
            raise ConnectionError("always fail")

        with pytest.raises(ConnectionError):
            await ErrorHandler.with_retry(work, max_retries=2)

    @pytest.mark.asyncio
    async def test_with_timeout_returns_fallback(self):
        """超时 → 返回降级结果。"""
        from agent_bus.error_handler import ErrorHandler

        async def slow():
            await asyncio.sleep(999)
            return "too late"

        result = await ErrorHandler.with_timeout(
            slow(), timeout=0.01, fallback_result="timeout_fallback"
        )
        assert result == "timeout_fallback"


# ============================================================================
# P1-4: 结果聚合器 (agents/orchestrator/aggregator.py)
# ============================================================================

class TestResultAggregator:
    """多源结果融合 —— 去重/排序/聚合。"""

    def test_resolve_conflicts_keeps_most_complete(self):
        """同名实体 → 去重保留 1 条。"""
        from agents.orchestrator.aggregator import resolve_conflicts
        data = [
            {"name": "如家酒店", "price": 200},
            {"name": "如家酒店", "price": 180, "address": "朝阳区"},
        ]
        result = resolve_conflicts(data, key_field="name")
        assert len(result) == 1
        assert result[0]["name"] == "如家酒店"

    def test_resolve_conflicts_different_names_all_kept(self):
        """不同名 → 全部保留。"""
        from agents.orchestrator.aggregator import resolve_conflicts
        data = [
            {"name": "如家酒店", "price": 200},
            {"name": "汉庭酒店", "price": 180},
        ]
        assert len(resolve_conflicts(data, "name")) == 2

    def test_rank_results_price_asc(self):
        """按价格升序排列。"""
        from agents.orchestrator.aggregator import rank_results
        data = [{"name": "A", "price": 500}, {"name": "B", "price": 200}, {"name": "C", "price": 800}]
        result = rank_results(data)
        assert result[0]["price"] == 200
        assert result[-1]["price"] == 800

    def test_build_summary_prompt(self):
        """摘要包含意图和结果数。"""
        from agents.orchestrator.aggregator import build_summary_prompt
        s = build_summary_prompt(["weather_query", "flight_ticket"], success_count=2, failure_count=0, total_results=5)
        assert "5 条" in s
        assert "2 项成功" in s

    def test_build_summary_with_failure(self):
        """含失败服务的摘要。"""
        from agents.orchestrator.aggregator import build_summary_prompt
        s = build_summary_prompt(["weather_query"], success_count=1, failure_count=1, total_results=3)
        assert "1 项暂不可用" in s

    def test_aggregate_results_multi_task(self):
        """多任务结果合并。"""
        from agents.orchestrator.aggregator import aggregate_results
        state = {"sub_tasks": [], "tool_name": "", "tool_result": None, "next_step": ""}
        results = [
            ("weather_query", "worker.weather", {"success": True, "data": [{"city": "北京"}]}),
            ("flight_ticket", "worker.flight", {"success": True, "data": [{"flight_no": "CA1234"}]}),
        ]
        result = aggregate_results(state, results)
        assert result["tool_result"]["is_multi"] is True
        assert result["tool_result"]["success"] is True
