"""测试共享夹具 —— Mock LLM、DB、Redis、Celery 等外部依赖。"""
import sys
from unittest.mock import MagicMock

# ============================================================================
# 预置 Mock：解决 pymilvus → google.protobuf 版本不兼容
# 必须在任何 db.milvus_client 导入之前注入 sys.modules
# ============================================================================
_fake_milvus_mod = MagicMock()
_fake_milvus_mod.milvus_client = MagicMock()
_fake_milvus_mod.MilvusClient = MagicMock()
_fake_milvus_mod.MilvusClient.return_value.search.return_value = []
sys.modules.setdefault("db.milvus_client", _fake_milvus_mod)

import json
import pytest
from unittest.mock import AsyncMock, patch


def _build_default_response(content=None, json_content=None):
    """构造一个模拟的 OpenAI chat completion 响应。"""
    mock = MagicMock()
    if json_content is not None:
        content = json.dumps(json_content, ensure_ascii=False)
    mock.choices = [MagicMock()]
    mock.choices[0].message = MagicMock()
    mock.choices[0].message.content = content
    return mock


@pytest.fixture
def mock_openai_client():
    """Mock LLM 客户端池 —— 拦截所有 get_client() 调用。

    项目已迁移到 llm.client_pool.llm_manager，各模块不再直接 import OpenAI，
    而是通过 llm_manager.get_client() 获取客户端。只需 patch get_client 即可。
    """
    shared_instance = MagicMock()
    shared_instance.chat.completions.create.return_value = _build_default_response(
        json_content={"intent": "weather_query", "confidence": 0.95, "in_scope": True}
    )
    shared_instance.embeddings.create.return_value = MagicMock(
        data=[MagicMock(embedding=[0.1] * 256)]
    )

    with patch("llm.client_pool.llm_manager.get_client", return_value=shared_instance):
        yield shared_instance


@pytest.fixture
def mock_query_cache():
    """Mock Redis 查询缓存，避免测试时真实 Redis 连接。"""
    with patch("cache.query_cache.get_query_cache") as mock_get:
        cache = MagicMock()
        cache.get = AsyncMock(return_value=None)
        cache.set = AsyncMock()
        mock_get.return_value = cache
        yield cache


@pytest.fixture
def mock_celery_tasks():
    """Mock Celery 异步任务，防止测试中真实入队。

    由于 Celery 可能未安装，通过 sys.modules 注入 mock module，
    避免 import celery_tasks.memory 触发真实的 Celery 导入。
    """
    import sys
    from unittest.mock import MagicMock

    fake_memory = MagicMock()
    fake_memory.save_long_term_memory = MagicMock()
    fake_memory.save_conversation_history = MagicMock()
    fake_memory.extract_and_save_preferences = MagicMock()
    fake_memory.compress_single_session = MagicMock()
    fake_memory.save_long_term_memory.delay = MagicMock()
    fake_memory.save_conversation_history.delay = MagicMock()
    fake_memory.extract_and_save_preferences.delay = MagicMock()
    fake_memory.compress_single_session.delay = MagicMock()

    old_memory = sys.modules.get("celery_tasks.memory")
    old_celery_app = sys.modules.get("celery_app")
    sys.modules["celery_tasks.memory"] = fake_memory
    sys.modules["celery_app"] = MagicMock()

    yield {
        "save_long_term_memory": fake_memory.save_long_term_memory,
        "save_conversation_history": fake_memory.save_conversation_history,
        "extract_and_save_preferences": fake_memory.extract_and_save_preferences,
        "compress_single_session": fake_memory.compress_single_session,
    }

    if old_memory is not None:
        sys.modules["celery_tasks.memory"] = old_memory
    else:
        sys.modules.pop("celery_tasks.memory", None)
    if old_celery_app is not None:
        sys.modules["celery_app"] = old_celery_app
    else:
        sys.modules.pop("celery_app", None)


@pytest.fixture
def mock_mysql():
    """Mock MySQL 客户端单例，返回预定义数据。"""
    with patch("db.mysql_client.mysql_client") as mock_instance:
        mock_instance.execute_query.return_value = [
            {"id": 1, "city": "北京", "temperature_high": 25, "temperature_low": 15}
        ]
        mock_instance.health_check.return_value = True
        yield mock_instance


@pytest.fixture
def mock_milvus():
    """Mock Milvus 客户端，返回预定义向量检索结果。"""
    with patch("db.milvus_client.MilvusClient") as mock_cls:
        instance = MagicMock()
        instance.search.return_value = [
            {"id": "1", "score": 0.95, "name": "故宫", "description": "明清皇宫", "tags": "历史", "city": "北京"}
        ]
        instance.health_check.return_value = True
        mock_cls.return_value = instance
        yield instance


@pytest.fixture
def mock_redis():
    """Mock Redis，短期记忆使用内存字典。"""
    with patch("redis.asyncio.Redis") as mock_cls:
        redis_instance = MagicMock()
        redis_instance.ping = AsyncMock(return_value=True)
        redis_instance.lrange = AsyncMock(return_value=[])
        redis_instance.lpush = AsyncMock()
        redis_instance.ltrim = AsyncMock()
        redis_instance.expire = AsyncMock()
        redis_instance.set = AsyncMock()
        redis_instance.get = AsyncMock(return_value=None)
        redis_instance.delete = AsyncMock()
        mock_cls.from_url = AsyncMock(return_value=redis_instance)
        yield redis_instance


@pytest.fixture
def sample_state():
    """返回一个基准 TourismStateDict，供各节点测试使用。"""
    return {
        "query": "北京明天天气怎么样",
        "session_id": "test_session",
        "history": [],
        "intent": "",
        "intent_in_scope": True,
        "need_planning": False,
        "sub_tasks": [],
        "slots": {},
        "missing_slots": [],
        "follow_up_question": "",
        "tool_name": "",
        "tool_input": {},
        "tool_result": None,
        "summary": "",
        "final_answer": "",
        "next_step": "intent_recognition",
        "error": "",
    }
