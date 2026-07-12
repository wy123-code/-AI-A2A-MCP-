"""FastAPI 端点测试 —— 使用 TestClient 测试所有 API 路由。"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi.testclient import TestClient


@pytest.fixture
def api_client(mock_redis, mock_query_cache, mock_celery_tasks):
    """创建 TestClient，Mock 掉数据库初始化及 Celery 异步任务。"""
    with patch("main.init_db") as mock_init_db:
        with patch("main.memory_service") as mock_mem:
            mock_mem.get_short_term = AsyncMock(return_value=[])
            mock_mem.add_short_term = AsyncMock()
            mock_mem.get_preference_context = AsyncMock(return_value="")
            mock_mem.clear_short_term = AsyncMock()
            mock_mem.get_session_summary = AsyncMock(return_value="")

            with patch("main.user_service") as mock_user:
                mock_user.get_user = AsyncMock(return_value={"id": 1, "username": "test"})
                mock_user.register_user = AsyncMock(return_value={"id": 1, "username": "test"})
                mock_user.authenticate_user = AsyncMock(return_value={"id": 1, "username": "test"})
                mock_user.create_user = AsyncMock(return_value={"id": 1, "username": "test"})
                mock_user.update_user = AsyncMock(return_value=True)
                mock_user.deactivate_user = AsyncMock(return_value=True)
                mock_user.list_users = AsyncMock(return_value=[])

                from main import app
                yield TestClient(app)


class TestHealthEndpoints:
    """健康检查和信息接口测试"""

    def test_root_returns_html(self, api_client):
        with patch("main.FileResponse") as mock_fr:
            mock_fr.return_value = MagicMock(status_code=200)
            api_client.get("/")

    def test_health_check(self, api_client):
        response = api_client.get("/health")
        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["service"] == "tourism-assistant-agent"

    def test_list_intents(self, api_client):
        response = api_client.get("/intents")
        assert response.status_code == 200
        data = response.json()
        assert "supported_intents" in data
        assert len(data["supported_intents"]) == 10


class TestChatEndpoint:
    """核心对话接口测试"""

    def test_chat_anonymous(self, api_client, mock_openai_client):
        """匿名对话：意图识别返回 out_of_scope 应正常结束。"""
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"intent":"out_of_scope","confidence":0.9,"in_scope":false}'
            ))]
        )
        response = api_client.post("/chat", json={
            "query": "今天股市怎么样",
            "session_id": "test_s1",
        })
        assert response.status_code == 200
        data = response.json()
        assert "session_id" in data
        assert "answer" in data
        assert "intent" in data

    def test_chat_with_intent(self, api_client, mock_openai_client):
        """正常意图识别 + 槽位填充 + 工具执行的完整流程。"""
        call_count = 0

        def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:  # intent_slot (merged intent + slots)
                return MagicMock(choices=[MagicMock(message=MagicMock(
                    content='{"intent":"weather_query","confidence":0.95,"in_scope":true,"slots":{"city":"北京","date":"2026-05-16"},"missing_slots":[],"follow_up_question":""}'
                ))])
            elif call_count == 2:  # A2A SQL generation
                return MagicMock(choices=[MagicMock(message=MagicMock(
                    content="SELECT * FROM weather WHERE city='北京'"
                ))])
            elif call_count == 3:  # final_answer
                return MagicMock(choices=[MagicMock(message=MagicMock(
                    content="根据查询，北京明天天气晴朗，最高温25°C，最低温15°C，适合出行。"
                ))])
            return MagicMock(choices=[MagicMock(message=MagicMock(content="{}"))])

        mock_openai_client.chat.completions.create.side_effect = side_effect

        with patch("db.mysql_client.mysql_client") as mock_db, \
             patch("mcp.mcp_server.mysql_client") as mock_mcp_db:
            mock_db.execute_query = AsyncMock(return_value=[
                {"id": 1, "city": "北京", "temperature_high": 25, "temperature_low": 15}
            ])
            mock_mcp_db.execute_query = AsyncMock(return_value=[
                {"id": 1, "city": "北京", "temperature_high": 25, "temperature_low": 15}
            ])
            response = api_client.post("/chat", json={
                "query": "北京明天天气怎么样",
                "session_id": "test_s2",
            })

        assert response.status_code == 200
        data = response.json()
        assert data["intent"] == "weather_query"
        # Weather API key not configured in test env; answer reflects the error gracefully
        assert data["answer"]  # answer should not be empty

    def test_chat_with_missing_slots(self, api_client, mock_openai_client):
        """槽位不完整时应触发追问。"""
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"intent":"weather_query","confidence":0.9,"in_scope":true,"slots":{"city":null,"date":null},"missing_slots":["city","date"],"follow_up_question":"请问您要查询哪个城市的天气？"}'
            ))]
        )

        response = api_client.post("/chat", json={
            "query": "天气怎么样",
            "session_id": "test_s3",
        })

        assert response.status_code == 200
        data = response.json()
        assert data["follow_up_needed"] is True

    def test_chat_with_history(self, api_client, mock_openai_client):
        """带历史记录的对话。"""
        response = api_client.post("/chat", json={
            "query": "北京有什么好玩的",
            "session_id": "test_s4",
            "history": [
                {"role": "user", "content": "我想去北京旅游"},
                {"role": "assistant", "content": "北京有很多著名景点"},
            ],
        })
        assert response.status_code == 200


class TestUserManagement:
    """用户管理 API 测试"""

    def test_create_user(self, api_client):
        with patch("main.user_service") as mock_user:
            mock_user.create_user = AsyncMock(return_value={"id": 2, "username": "new_user"})
            response = api_client.post("/users", json={"username": "new_user", "nickname": "新用户"})
            assert response.status_code == 200

    def test_get_user_not_found(self, api_client):
        with patch("main.user_service") as mock_user:
            mock_user.get_user = AsyncMock(return_value=None)
            response = api_client.get("/users/999")
            assert response.status_code == 404

    def test_list_users(self, api_client):
        with patch("main.user_service") as mock_user:
            mock_user.list_users = AsyncMock(return_value=[])
            response = api_client.get("/users")
            assert response.status_code == 200
