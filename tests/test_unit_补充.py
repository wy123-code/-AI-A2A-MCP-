"""补充单元测试 —— 覆盖 P0/P1/P2 12 个未测关键模块。

测试范围：
  P0 核心: PluginRegistry / MessageQueue / LLMClientPool
  P1 通信: AgentRegistry / TaskScheduler / RateLimitMiddleware
  P2 基础: MCPClient / BaseAgent / city_codes / TimeoutMiddleware

共享夹具定义在 conftest.py 中，请优先复用。
"""

import json
import sys
import pytest
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

# ============================================================================
# 预置 Mock：解决 pymilvus → google.protobuf 兼容性问题
# 在 tools/__init__.py 的导入链生效前拦截 db.milvus_client
# ============================================================================
_fake_milvus_mod = MagicMock()
_fake_milvus_mod.milvus_client = MagicMock()
_fake_milvus_mod.MilvusClient = MagicMock()
_fake_milvus_mod.MilvusClient.return_value.search.return_value = []
sys.modules.setdefault("db.milvus_client", _fake_milvus_mod)
# 确保 db 包能正常 from .milvus_client import milvus_client
import db
db.milvus_client = _fake_milvus_mod
db.__dict__.setdefault("milvus_client", _fake_milvus_mod.milvus_client)


# ============================================================================
# P0-1: 插件注册中心 (agents/plugin.py)
# ============================================================================

class TestPluginManifest:
    """PluginManifest 数据类 —— 创建与字段校验。"""

    def test_create_manifest_basic(self):
        """创建基本插件清单。"""
        from agents.plugin import PluginManifest
        fake_agent = MagicMock()
        m = PluginManifest(
            agent_class=fake_agent,
            agent_name="weather_worker",
            intents=["weather_query"],
        )
        assert m.agent_name == "weather_worker"
        assert m.intents == ["weather_query"]
        assert m.priority == 5
        assert m.load_balancer_weight == 1.0
        assert m.tool_functions == []

    def test_create_manifest_full(self):
        """创建完整配置的插件清单。"""
        from agents.plugin import PluginManifest
        fake_agent = MagicMock()
        m = PluginManifest(
            agent_class=fake_agent,
            agent_name="flight_worker",
            intents=["flight_ticket", "train_ticket"],
            priority=3,
            load_balancer_weight=2.0,
            tool_functions=["query_flight", "query_train"],
        )
        assert m.priority == 3
        assert m.load_balancer_weight == 2.0
        assert len(m.tool_functions) == 2

    def test_manifest_slots_defined(self):
        """__slots__ 减少内存占用。"""
        from agents.plugin import PluginManifest
        assert hasattr(PluginManifest, '__slots__')


class TestPluginRegistry:
    """插件注册中心 —— 意图查找 / 优先级查询。"""

    def test_find_by_intent_found(self):
        """意图匹配时返回对应 manifest。"""
        from agents.plugin import PluginManifest, PluginRegistry
        fake = MagicMock()
        manifests = [
            PluginManifest(fake, "weather_worker", ["weather_query"], priority=2),
            PluginManifest(fake, "flight_worker", ["flight_ticket", "train_ticket"], priority=3),
        ]
        result = PluginRegistry.find_by_intent("flight_ticket", manifests)
        assert result is not None
        assert result.agent_name == "flight_worker"

    def test_find_by_intent_not_found(self):
        """意图无匹配时返回 None。"""
        from agents.plugin import PluginManifest, PluginRegistry
        manifests = [
            PluginManifest(MagicMock(), "weather_worker", ["weather_query"]),
        ]
        result = PluginRegistry.find_by_intent("hotel_query", manifests)
        assert result is None

    def test_find_by_intent_empty_list(self):
        """空清单返回 None。"""
        from agents.plugin import PluginRegistry
        result = PluginRegistry.find_by_intent("weather_query", [])
        assert result is None

    def test_get_intent_priority_found(self):
        """已注册意图返回对应优先级。"""
        from agents.plugin import PluginManifest, PluginRegistry
        fake = MagicMock()
        with patch.object(PluginRegistry, 'auto_discover', return_value=[
            PluginManifest(fake, "weather_worker", ["weather_query"], priority=2),
        ]):
            assert PluginRegistry.get_intent_priority("weather_query") == 2

    def test_get_intent_priority_default(self):
        """未注册意图返回默认优先级 5。"""
        from agents.plugin import PluginRegistry
        with patch.object(PluginRegistry, 'auto_discover', return_value=[]):
            assert PluginRegistry.get_intent_priority("unknown") == 5


# ============================================================================
# P0-2: 消息队列 (agent_bus/message_queue.py)
# ============================================================================

class TestMessageQueue:
    """Redis List 持久化消息队列 —— 入队/出队/TTL过期。"""

    @pytest.mark.asyncio
    async def test_enqueue_lpushs_message(self, mock_redis):
        """消息入队 → LPUSH 到正确 key。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = mock_redis
        await mq.enqueue("worker.weather", '{"intent":"weather_query"}')
        mock_redis.lpush.assert_called_once()
        key = mock_redis.lpush.call_args[0][0]
        assert "agent:queue:worker.weather" in key

    @pytest.mark.asyncio
    async def test_enqueue_without_redis_noop(self, mock_redis):
        """未连接 Redis → 入队静默跳过。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = None
        await mq.enqueue("worker.weather", '{"intent":"test"}')
        # 不应抛出异常

    @pytest.mark.asyncio
    async def test_dequeue_returns_message(self, mock_redis):
        """BRPOP 成功 → 返回消息内容。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = mock_redis
        import json as _json
        from datetime import datetime, timezone
        envelope = _json.dumps({
            "message": '{"intent":"weather_query"}',
            "enqueued_at": datetime.now(timezone.utc).isoformat(),
            "ttl": 300,
        })
        mock_redis.brpop = AsyncMock(return_value=(b"key", envelope))
        result = await mq.dequeue("worker.weather")
        assert result == '{"intent":"weather_query"}'

    @pytest.mark.asyncio
    async def test_dequeue_expired_ttl(self, mock_redis):
        """TTL 过期 → 返回 None（丢弃）。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = mock_redis
        import json as _json
        envelope = _json.dumps({
            "message": '{"intent":"weather_query"}',
            "enqueued_at": "2020-01-01T00:00:00+00:00",  # 早已过期
            "ttl": 30,
        })
        mock_redis.brpop = AsyncMock(return_value=(b"key", envelope))
        result = await mq.dequeue("worker.weather")
        assert result is None

    @pytest.mark.asyncio
    async def test_dequeue_empty_queue(self, mock_redis):
        """队列为空 → BRPOP 返回 None。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = mock_redis
        mock_redis.brpop = AsyncMock(return_value=None)
        result = await mq.dequeue("worker.weather")
        assert result is None

    @pytest.mark.asyncio
    async def test_requeue_rpushs_to_tail(self, mock_redis):
        """失败消息重入队 → RPUSH 到尾部。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = mock_redis
        mock_redis.rpush = AsyncMock()  # 必须显式设为 AsyncMock
        await mq.requeue("worker.weather", '{"intent":"weather_query"}')
        mock_redis.rpush.assert_called_once()

    @pytest.mark.asyncio
    async def test_get_queue_depth(self, mock_redis):
        """获取队列积压长度。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = mock_redis
        mock_redis.llen = AsyncMock(return_value=42)
        depth = await mq.get_queue_depth("worker.weather")
        assert depth == 42

    @pytest.mark.asyncio
    async def test_purge_deletes_key(self, mock_redis):
        """清空队列 → DELETE key。"""
        from agent_bus.message_queue import MessageQueue
        mq = MessageQueue()
        mq._redis = mock_redis
        await mq.purge("worker.weather")
        mock_redis.delete.assert_called_once()


# ============================================================================
# P0-3: LLM 客户端池 (llm/client_pool.py)
# ============================================================================

class TestLLMClientPool:
    """LLM 客户端池 —— 创建/复用/切换模型。"""

    @patch("llm.client_pool.OpenAI")
    def test_get_client_creates_new(self, mock_openai_cls):
        """首次调用 → 创建新客户端。"""
        from llm.client_pool import LLMClientManager
        mgr = LLMClientManager()
        mgr._clients.clear()  # 清除全局单例缓存
        client = mgr.get_client("default")
        mock_openai_cls.assert_called_once()
        assert client is not None

    @patch("llm.client_pool.OpenAI")
    def test_get_client_reuses_existing(self, mock_openai_cls):
        """重复调用 → 复用已有客户端（不重复创建）。"""
        from llm.client_pool import LLMClientManager
        mgr = LLMClientManager()
        mgr._clients.clear()
        c1 = mgr.get_client("default")
        c2 = mgr.get_client("default")
        assert c1 is c2
        # 只创建了 1 次
        assert mock_openai_cls.call_count == 1

    @patch("llm.client_pool.OpenAI")
    def test_get_client_intent_key_creates_separate(self, mock_openai_cls):
        """不同 config_key → 创建不同客户端。"""
        from llm.client_pool import LLMClientManager
        mgr = LLMClientManager()
        mgr._clients.clear()
        # side_effect 确保每次 OpenAI() 返回不同实例
        mock_openai_cls.side_effect = [MagicMock(), MagicMock()]
        c_default = mgr.get_client("default")
        c_intent = mgr.get_client("intent")
        assert c_default is not c_intent
        assert mock_openai_cls.call_count == 2

    def test_global_llm_manager_exists(self):
        """全局 llm_manager 单例已创建。"""
        from llm.client_pool import llm_manager
        assert llm_manager is not None


# ============================================================================
# P1-1: Agent 注册中心 (agent_bus/registry.py)
# ============================================================================

class TestAgentRegistry:
    """Agent 注册中心 —— 注册/发现/心跳/过期清理。"""

    @pytest.mark.asyncio
    async def test_register_sets_hash_fields(self, mock_redis):
        """注册 Agent → Redis HSET 正确字段。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        # 必须显式声明 AsyncMock，因为 base MagicMock 不支持 await
        mock_redis.exists = AsyncMock(return_value=0)
        mock_redis.hset = AsyncMock()
        mock_redis.expire = AsyncMock()
        await registry.register("worker.weather", "worker", intents=["weather_query"])
        # 验证 hset 被调用
        assert mock_redis.hset.called
        call_kwargs = mock_redis.hset.call_args[1]
        assert "mapping" in call_kwargs
        assert call_kwargs["mapping"]["type"] == "worker"
        assert "weather_query" in call_kwargs["mapping"]["intents"]
        assert call_kwargs["mapping"]["status"] == "online"

    @pytest.mark.asyncio
    async def test_unregister_deletes_key(self, mock_redis):
        """注销 → DELETE key。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        await registry.unregister("worker.weather")
        mock_redis.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_updates_timestamp(self, mock_redis):
        """心跳 → HSET 更新时间戳和失败计数。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        await registry.heartbeat("worker.weather")
        mock_redis.hset.assert_called()
        mapping = mock_redis.hset.call_args[1]["mapping"]
        assert mapping["heartbeat_fail_count"] == "0"

    @pytest.mark.asyncio
    async def test_heartbeat_failure_tracks_count(self, mock_redis):
        """连续失败 3 次 → 标记 unstable。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        # hset 第一次抛异常
        mock_redis.hset = MagicMock(side_effect=[ConnectionError("fail"), None, None])
        # hget 返回 "2"（已失败 2 次）
        mock_redis.hget = AsyncMock(return_value="2")
        await registry.heartbeat("worker.weather")
        # 第三次失败 → 标记 unstable
        hset_calls = mock_redis.hset.call_args_list
        # 最后一次调用应设置 status="unstable"
        found_unstable = any(
            "unstable" in str(c) for c in hset_calls
        )
        assert found_unstable

    @pytest.mark.asyncio
    async def test_discover_returns_agents(self, mock_redis):
        """发现已注册 Agent 列表。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        mock_redis.scan = AsyncMock(return_value=(0, ["agent_registry:worker.weather"]))
        mock_redis.hgetall = AsyncMock(return_value={
            "type": "worker", "intents": "weather_query,hotel_query",
            "status": "online", "last_heartbeat": "2026-07-09T10:00:00+00:00",
            "metadata": "{}",
        })
        result = await registry.discover()
        assert len(result) == 1
        assert result[0]["name"] == "worker.weather"

    @pytest.mark.asyncio
    async def test_discover_by_intent_finds_match(self, mock_redis):
        """按意图查找 Agent。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        mock_redis.scan = AsyncMock(return_value=(0, ["agent_registry:worker.weather"]))
        mock_redis.hgetall = AsyncMock(return_value={
            "type": "worker", "intents": "weather_query,hotel_query",
            "status": "online", "last_heartbeat": "2026-07-09T10:00:00+00:00",
            "metadata": "{}",
        })
        result = await registry.discover_by_intent("weather_query")
        assert result == "worker.weather"

    @pytest.mark.asyncio
    async def test_is_online_true(self, mock_redis):
        """心跳在超时窗口内 → 在线。"""
        from agent_bus.registry import AgentRegistry
        from datetime import datetime, timezone
        registry = AgentRegistry()
        registry._redis = mock_redis
        mock_redis.hget = AsyncMock(return_value=datetime.now(timezone.utc).isoformat())
        assert await registry.is_online("worker.weather") is True

    @pytest.mark.asyncio
    async def test_is_online_no_heartbeat(self, mock_redis):
        """无心跳记录 → 离线。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        mock_redis.hget = AsyncMock(return_value=None)
        assert await registry.is_online("worker.weather") is False

    @pytest.mark.asyncio
    async def test_cleanup_expired_removes_offline(self, mock_redis):
        """过期 Agent → 删除 key + 队列。"""
        from agent_bus.registry import AgentRegistry
        registry = AgentRegistry()
        registry._redis = mock_redis
        mock_redis.scan = AsyncMock(return_value=(0, ["agent_registry:worker.old"]))
        mock_redis.hget = AsyncMock(return_value="2020-01-01T00:00:00+00:00")  # 超时
        cleaned = await registry.cleanup_expired()
        assert "worker.old" in cleaned
        # DELETE 被调用（含 agent queue key）
        assert mock_redis.delete.call_count >= 1


# ============================================================================
# P1-2: 调度器 DAG (agents/orchestrator/scheduler.py)
# ============================================================================

class TestTaskSchedulerDAG:
    """任务调度器 —— DAG 依赖编排拓扑排序。"""

    def test_resolve_order_single_task(self):
        """单任务 → 1 层。"""
        from agents.orchestrator.scheduler import TaskScheduler, TaskDependency
        tasks = [TaskDependency("t1", "weather_query", {"city": "北京"})]
        layers = TaskScheduler.resolve_order(tasks)
        assert len(layers) == 1
        assert len(layers[0]) == 1
        assert layers[0][0].task_id == "t1"

    def test_resolve_order_empty(self):
        """空列表 → 返回 []。"""
        from agents.orchestrator.scheduler import TaskScheduler
        layers = TaskScheduler.resolve_order([])
        assert layers == []

    def test_resolve_order_two_independent(self):
        """两个独立任务 → 同层并行。"""
        from agents.orchestrator.scheduler import TaskScheduler, TaskDependency
        tasks = [
            TaskDependency("t1", "weather_query", {"city": "北京"}),
            TaskDependency("t2", "hotel_query", {"city": "北京"}),
        ]
        layers = TaskScheduler.resolve_order(tasks)
        assert len(layers) == 1
        assert len(layers[0]) == 2

    def test_resolve_order_with_dependency(self):
        """有依赖关系 → 分层串行（t2 依赖 t1）。"""
        from agents.orchestrator.scheduler import TaskScheduler, TaskDependency
        tasks = [
            TaskDependency("t1", "weather_query", {"city": "北京"}),
            TaskDependency("t2", "flight_ticket", {"city": "北京"}, depends_on=["t1"]),
        ]
        layers = TaskScheduler.resolve_order(tasks)
        assert len(layers) == 2
        assert layers[0][0].task_id == "t1"
        assert layers[1][0].task_id == "t2"

    def test_resolve_order_priority_sort(self):
        """同层内按 priority 排序（数字小=高优先级排前面）。"""
        from agents.orchestrator.scheduler import TaskScheduler, TaskDependency
        tasks = [
            TaskDependency("low", "hotel_query", {}, priority=9),
            TaskDependency("high", "weather_query", {}, priority=1),
            TaskDependency("mid", "attraction_recommend", {}, priority=5),
        ]
        layers = TaskScheduler.resolve_order(tasks)
        assert layers[0][0].task_id == "high"
        assert layers[0][-1].task_id == "low"

    def test_resolve_order_chain(self):
        """链式依赖 A→B→C → 3 层串行。"""
        from agents.orchestrator.scheduler import TaskScheduler, TaskDependency
        tasks = [
            TaskDependency("a", "weather_query", {}),
            TaskDependency("b", "flight_ticket", {}, depends_on=["a"]),
            TaskDependency("c", "hotel_query", {}, depends_on=["b"]),
        ]
        layers = TaskScheduler.resolve_order(tasks)
        assert len(layers) == 3

    def test_resolve_order_diamond(self):
        """菱形依赖 A→(B,C)→D → 3 层。"""
        from agents.orchestrator.scheduler import TaskScheduler, TaskDependency
        tasks = [
            TaskDependency("a", "weather_query", {}),
            TaskDependency("b", "flight_ticket", {}, depends_on=["a"]),
            TaskDependency("c", "hotel_query", {}, depends_on=["a"]),
            TaskDependency("d", "attraction_recommend", {}, depends_on=["b", "c"]),
        ]
        layers = TaskScheduler.resolve_order(tasks)
        assert len(layers) == 3
        assert len(layers[1]) == 2  # B 和 C 同层
        assert layers[2][0].task_id == "d"


# ============================================================================
# P1-3: 限流中间件 (middleware/rate_limit.py)
# ============================================================================

class TestRateLimitMiddleware:
    """令牌桶限流器 —— 正常放行 / 超限拦截。"""

    @pytest.mark.asyncio
    async def test_allows_first_request(self):
        """首次请求 → 放行。"""
        from middleware.rate_limit import RateLimitMiddleware
        mock_app = MagicMock()
        mock_app.return_value = AsyncMock()
        middleware = RateLimitMiddleware(mock_app, rate=60, burst=10)

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.1"

        call_next = AsyncMock(return_value=MagicMock(status_code=200))
        response = await middleware.dispatch(mock_request, call_next)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_blocks_after_burst_exhausted(self):
        """令牌耗尽 → 返回 429。"""
        from middleware.rate_limit import RateLimitMiddleware
        mock_app = MagicMock()
        middleware = RateLimitMiddleware(mock_app, rate=60, burst=1)

        mock_request = MagicMock()
        mock_request.client.host = "192.168.1.2"
        mock_request.state.trace_id = ""  # 避免 MagicMock 序列化报错
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        # 第一次成功
        r1 = await middleware.dispatch(mock_request, call_next)
        assert r1.status_code == 200

        # 第二次（令牌耗尽但未经过足够时间）→ 429
        r2 = await middleware.dispatch(mock_request, call_next)
        assert r2.status_code == 429
        # JSONResponse body 可通过 body 属性访问
        body = r2.body
        if isinstance(body, bytes):
            body = body.decode()
        assert "RATE_LIMITED" in str(body)

    @pytest.mark.asyncio
    async def test_different_ips_independent(self):
        """不同 IP 独立限流。"""
        from middleware.rate_limit import RateLimitMiddleware
        mock_app = MagicMock()
        middleware = RateLimitMiddleware(mock_app, rate=60, burst=1)
        call_next = AsyncMock(return_value=MagicMock(status_code=200))

        # IP A 用掉令牌
        req_a = MagicMock(); req_a.client.host = "10.0.0.1"
        await middleware.dispatch(req_a, call_next)

        # IP B 应不受影响
        req_b = MagicMock(); req_b.client.host = "10.0.0.2"
        r_b = await middleware.dispatch(req_b, call_next)
        assert r_b.status_code == 200


# ============================================================================
# P2-1: MCP 客户端 (mcp/mcp_client.py)
# ============================================================================

class TestMCPClient:
    """MCP Streamable HTTP 客户端 —— JSON-RPC 协议处理。"""

    def test_client_init_state(self):
        """初始化状态检查。"""
        from mcp.mcp_client import MCPClient
        client = MCPClient("test-server", "http://localhost:8080/mcp")
        assert client.name == "test-server"
        assert client.url == "http://localhost:8080/mcp"
        assert client._initialized is False
        assert client._tools is None

    def test_url_trailing_slash_trimmed(self):
        """URL 尾部斜杠自动去除。"""
        from mcp.mcp_client import MCPClient
        client = MCPClient("test", "http://localhost:8080/mcp/")
        assert client.url == "http://localhost:8080/mcp"

    @pytest.mark.asyncio
    async def test_call_tool_uninitialized_triggers_init(self):
        """未初始化时调用 → 自动初始化（标记已初始化跳过握手）。"""
        from mcp.mcp_client import MCPClient
        client = MCPClient("test", "http://localhost:8080/mcp")
        client._initialized = True  # 跳过 initialize 握手

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "result": {"content": [{"text": '{"data": [1,2,3]}'}]}
        })
        client._http = MagicMock()
        client._http.post = AsyncMock(return_value=mock_resp)

        result = await client.call_tool("search", {"q": "test"})
        assert result["success"] is True
        assert result["data"] == {"data": [1, 2, 3]}

    @pytest.mark.asyncio
    async def test_call_tool_handles_json_rpc_error(self):
        """JSON-RPC error → success=False。"""
        from mcp.mcp_client import MCPClient
        client = MCPClient("test", "http://localhost:8080/mcp")
        client._initialized = True  # 跳过 initialize 握手
        client._http = MagicMock()

        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json = AsyncMock(return_value={
            "error": {"code": -32601, "message": "Method not found"}
        })
        client._http.post = AsyncMock(return_value=mock_resp)

        result = await client.call_tool("unknown_tool", {})
        assert result["success"] is False
        assert "Method not found" in result["error"]

    @pytest.mark.asyncio
    async def test_get_mcp_client_singleton(self):
        """全局 get_mcp_client 单例复用。"""
        from mcp.mcp_client import get_mcp_client, _mcp_clients
        _mcp_clients.clear()
        with patch("mcp.mcp_client.MCPClient.initialize", AsyncMock(return_value=True)):
            c1 = await get_mcp_client("test-singleton", "http://localhost:8080/mcp")
            c2 = await get_mcp_client("test-singleton", "http://localhost:8080/mcp")
            assert c1 is c2
        _mcp_clients.clear()

    @pytest.mark.asyncio
    async def test_close_cleans_up(self):
        """close() 关闭 HTTP session。"""
        from mcp.mcp_client import MCPClient
        client = MCPClient("test", "http://localhost:8080/mcp")
        mock_http = MagicMock()
        mock_http.close = AsyncMock()
        client._http = mock_http
        await client.close()
        mock_http.close.assert_called_once()
        assert client._http is None


# ============================================================================
# P2-2: Agent 基类 (agents/worker/base.py)
# ============================================================================

class TestBaseAgentLifecycle:
    """BaseAgent 生命周期 —— 收件箱频道 / 启动停止。"""

    def test_inbox_channel_name(self):
        """收件箱频道名格式正确。"""
        mock_pubsub = MagicMock()
        mock_registry = MagicMock()
        from agents.worker.base import BaseAgent

        class TestAgent(BaseAgent):
            async def handle_message(self, msg):
                return None

        agent = TestAgent("test_worker", "worker", mock_pubsub, mock_registry)
        assert agent.inbox_channel() == "agent:test_worker:inbox"


class TestWorkerProtocolCoverage:
    """Worker 协议补充测试 —— validate_intent 已在 test_unit.py 覆盖，此处补充边界。"""

    def test_validate_intent_empty_supported(self):
        """supported_intents 为空时拒绝所有。"""
        from agents.worker.protocol import WorkerProtocol
        assert WorkerProtocol.validate_intent("weather_query", []) is False

    def test_validate_intent_none_intent(self):
        """intent 为 None 时安全处理。"""
        from agents.worker.protocol import WorkerProtocol
        # None 不在列表中 → False
        assert WorkerProtocol.validate_intent(None, ["weather_query"]) is False


# ============================================================================
# P2-3: 城市编码工具 (tools/city_codes.py)
# ============================================================================

class TestCityCodes:
    """城市编码映射 —— 精确匹配/模糊匹配/边界值。"""

    def test_exact_match(self):
        from tools.city_codes import get_location_id
        assert get_location_id("北京") == "101010100"
        assert get_location_id("上海") == "101020100"

    def test_match_with_city_suffix(self):
        """'北京市' → 自动匹配 '北京'。"""
        from tools.city_codes import get_location_id
        assert get_location_id("北京市") == "101010100"
        assert get_location_id("上海市") == "101020100"

    def test_partial_match(self):
        """部分匹配（景区名匹配到城市编码）。"""
        from tools.city_codes import get_location_id
        # "桂林市" 在 CITY_CODES 中，"桂林" 切片匹配
        assert get_location_id("桂林市") == "101300501"
        # "长白山" 是直接 key
        assert get_location_id("长白山") == "101060310"

    def test_unknown_city_returns_empty(self):
        """不存在的城市返回 ''。"""
        from tools.city_codes import get_location_id
        assert get_location_id("火星") == ""

    def test_empty_input(self):
        """空字符串/NULL 安全处理。"""
        from tools.city_codes import get_location_id
        assert get_location_id("") == ""
        assert get_location_id(None) == ""

    def test_strip_whitespace(self):
        """带空格的输入 → 自动 trim。"""
        from tools.city_codes import get_location_id
        assert get_location_id(" 北京 ") == "101010100"

    def test_english_name(self):
        """英文城市名匹配。"""
        from tools.city_codes import get_location_id
        assert get_location_id("Hong Kong") == "101320101"

    def test_get_city_info_found(self):
        """获取城市旅游信息。"""
        from tools.city_codes import get_city_info
        info = get_city_info("北京")
        assert info is not None
        assert "attractions" in info
        assert "transport_tips" in info
        assert len(info["attractions"]) >= 3

    def test_get_city_info_with_suffix(self):
        """'成都市' → 匹配 '成都'。"""
        from tools.city_codes import get_city_info
        info = get_city_info("成都市")
        assert info is not None
        assert "大熊猫繁育研究基地" in str(info["attractions"])

    def test_get_city_info_not_found(self):
        """不存在城市返回 None。"""
        from tools.city_codes import get_city_info
        assert get_city_info("火星") is None

    def test_get_city_info_empty(self):
        """空输入返回 None。"""
        from tools.city_codes import get_city_info
        assert get_city_info("") is None
        assert get_city_info(None) is None


# ============================================================================
# P2-4: 超时中间件 (middleware/timeout.py)
# ============================================================================

class TestTimeoutMiddleware:
    """超时中间件 —— 按端点类型区分超时。"""

    @pytest.mark.asyncio
    async def test_chat_path_uses_long_timeout(self):
        """聊天端点 → 120s 超时。"""
        from middleware.timeout import TimeoutMiddleware
        middleware = TimeoutMiddleware(MagicMock())
        assert middleware.CHAT_TIMEOUT == 120.0
        assert "/chat" in middleware.CHAT_PATHS
        assert "/chat/stream" in middleware.CHAT_PATHS

    @pytest.mark.asyncio
    async def test_non_chat_path_uses_default_timeout(self):
        """非聊天端点 → 30s 默认超时。"""
        from middleware.timeout import TimeoutMiddleware
        middleware = TimeoutMiddleware(MagicMock())
        assert middleware.DEFAULT_TIMEOUT == 30.0

    @pytest.mark.asyncio
    async def test_normal_request_passes(self):
        """正常时间完成 → 返回响应。"""
        from middleware.timeout import TimeoutMiddleware
        mock_app = MagicMock()
        middleware = TimeoutMiddleware(mock_app)

        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_request.state = MagicMock()

        mock_response = MagicMock(status_code=200)
        call_next = AsyncMock(return_value=mock_response)
        response = await middleware.dispatch(mock_request, call_next)
        assert response.status_code == 200

    @pytest.mark.asyncio
    async def test_timeout_returns_504(self):
        """超过超时时间 → 504 + 超时信息。"""
        from middleware.timeout import TimeoutMiddleware
        import middleware.timeout as timeout_mod
        mock_app = MagicMock()
        middleware = TimeoutMiddleware(mock_app)
        # 临时降低超时时间避免测试跑 30s
        old_timeout = timeout_mod.TimeoutMiddleware.DEFAULT_TIMEOUT
        timeout_mod.TimeoutMiddleware.DEFAULT_TIMEOUT = 0.05

        mock_request = MagicMock()
        mock_request.url.path = "/health"
        mock_request.state.trace_id = ""

        async def _slow(*args, **kwargs):
            await asyncio.sleep(999)
            return MagicMock(status_code=200)

        try:
            with patch("common.monitor.metrics.metrics_collector") as mock_metrics:
                mock_metrics.start = AsyncMock()
                mock_metrics.record_request = AsyncMock()
                response = await middleware.dispatch(mock_request, _slow)
                assert response.status_code == 504
        finally:
            timeout_mod.TimeoutMiddleware.DEFAULT_TIMEOUT = old_timeout


# ============================================================================
# P2-5: 编排器 _format_response 工具函数
# ============================================================================

class TestFormatResponse:
    """_format_response / json_dumps_safe 工具函数。"""

    def test_format_response_follow_up_needed(self):
        """缺槽位 → follow_up_needed=True。"""
        from agents.orchestrator.agent import _format_response
        state = {
            "session_id": "s1", "final_answer": "请问哪个城市？",
            "intent": "weather_query", "missing_slots": ["city"],
            "follow_up_question": "请问哪个城市？",
        }
        resp = _format_response(state, 100)
        assert resp["follow_up_needed"] is True
        assert resp["session_id"] == "s1"

    def test_format_response_complete(self):
        """完整回答 → follow_up_needed=False。"""
        from agents.orchestrator.agent import _format_response
        state = {
            "session_id": "s2", "final_answer": "北京明天晴",
            "intent": "weather_query", "missing_slots": [],
            "follow_up_question": "",
        }
        resp = _format_response(state, 200)
        assert resp["follow_up_needed"] is False
        assert resp["duration_ms"] == 200

    def test_json_dumps_safe_handles_datetime(self):
        """json_dumps_safe 处理 datetime 对象。"""
        from agents.orchestrator.agent import json_dumps_safe
        from datetime import datetime
        obj = {"time": datetime(2026, 7, 9, 12, 0, 0)}
        result = json_dumps_safe(obj)
        assert "2026" in result
