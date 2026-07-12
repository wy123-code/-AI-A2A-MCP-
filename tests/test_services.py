"""服务层测试 —— 用户管理、记忆管理（长期/短期）。"""
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


class TestUserService:
    """用户管理服务测试"""

    @pytest.mark.asyncio
    async def test_create_user_success(self):
        from services.user_service import UserService
        svc = UserService()
        with patch("services.user_service.get_session") as mock_sess:
            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.first.return_value = None
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.create_user("alice", nickname="Alice", email="a@b.com")
            assert result is not None
            assert result["username"] == "alice"

    @pytest.mark.asyncio
    async def test_create_user_duplicate(self):
        from services.user_service import UserService
        svc = UserService()
        with patch("services.user_service.get_session") as mock_sess:
            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.first.return_value = MagicMock()
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.create_user("alice")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_user_found(self):
        from services.user_service import UserService
        svc = UserService()
        with patch("services.user_service.get_session") as mock_sess:
            mock_user = MagicMock()
            mock_user.id = 1
            mock_user.username = "alice"
            mock_user.nickname = "Alice"
            mock_user.avatar = None
            mock_user.email = "a@b.com"
            mock_user.phone = "123"
            mock_user.created_at = MagicMock()
            mock_user.created_at.isoformat.return_value = "2026-05-01T00:00:00"

            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.first.return_value = mock_user
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.get_user(1)
            assert result is not None
            assert result["id"] == 1
            assert result["username"] == "alice"

    @pytest.mark.asyncio
    async def test_get_user_not_found(self):
        from services.user_service import UserService
        svc = UserService()
        with patch("services.user_service.get_session") as mock_sess:
            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.first.return_value = None
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.get_user(999)
            assert result is None

    @pytest.mark.asyncio
    async def test_deactivate_user(self):
        from services.user_service import UserService
        svc = UserService()
        with patch("services.user_service.get_session") as mock_sess:
            mock_user = MagicMock()
            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.first.return_value = mock_user
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.deactivate_user(1)
            assert result is True
            assert mock_user.is_active is False


class TestMemoryServiceShortTerm:
    """短期记忆（会话上下文）测试"""

    @pytest.mark.asyncio
    async def test_get_short_term_empty(self, mock_redis):
        from services.memory_service import MemoryService
        svc = MemoryService()
        svc.redis_available = True
        svc.redis = mock_redis

        result = await svc.get_short_term("session_1")
        assert isinstance(result, list)

    @pytest.mark.asyncio
    async def test_add_short_term(self, mock_redis):
        from services.memory_service import MemoryService
        svc = MemoryService()
        svc.redis_available = True
        svc.redis = mock_redis

        await svc.add_short_term("session_1", "user", "北京天气怎么样", intent="weather_query")
        mock_redis.lpush.assert_called()
        mock_redis.expire.assert_called()

    @pytest.mark.asyncio
    async def test_clear_short_term(self, mock_redis):
        from services.memory_service import MemoryService
        svc = MemoryService()
        svc.redis_available = True
        svc.redis = mock_redis

        await svc.clear_short_term("session_1")
        mock_redis.delete.assert_called()


class TestMemoryServiceLongTerm:
    """长期记忆（用户偏好/历史）测试"""

    @pytest.mark.asyncio
    async def test_save_preference_new(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch("services.memory_service.get_session") as mock_sess:
            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.first.return_value = None
            mock_sess.return_value.__enter__.return_value = mock_session

            await svc.save_preference(1, "transport", "preferred_city", "北京")
            mock_session.add.assert_called()

    @pytest.mark.asyncio
    async def test_save_preference_update_existing(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch("services.memory_service.get_session") as mock_sess:
            mock_pref = MagicMock()
            mock_pref.value = "北京"
            mock_pref.confidence = 0.5
            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.first.return_value = mock_pref
            mock_sess.return_value.__enter__.return_value = mock_session

            await svc.save_preference(1, "transport", "preferred_city", "上海")
            assert mock_pref.value == "上海"

    @pytest.mark.asyncio
    async def test_get_preferences(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch("services.memory_service.get_session") as mock_sess:
            mock_pref = MagicMock()
            mock_pref.category = "transport"
            mock_pref.key = "preferred_city"
            mock_pref.value = "北京"
            mock_pref.confidence = 0.8
            mock_pref.source = "explicit"

            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.order_by.return_value.all.return_value = [mock_pref]
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.get_preferences(1)
            assert len(result) == 1
            assert result[0]["value"] == "北京"

    @pytest.mark.asyncio
    async def test_get_preference_context_empty(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch.object(svc, "get_preferences", return_value=[]):
            result = await svc.get_preference_context(1)
            assert result == ""

    @pytest.mark.asyncio
    async def test_get_preference_context_with_prefs(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        prefs = [
            {"category": "transport", "key": "city", "value": "北京", "confidence": 0.8, "source": "explicit"},
            {"category": "hotel", "key": "star", "value": "5", "confidence": 0.6, "source": "inferred"},
        ]
        with patch.object(svc, "get_preferences", return_value=prefs):
            result = await svc.get_preference_context(1)
            assert "[用户长期偏好]" in result
            assert "北京" in result

    @pytest.mark.asyncio
    async def test_extract_and_save_preferences(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch.object(svc, "get_preferences", return_value=[]):
            with patch.object(svc, "save_preference") as mock_save:
                await svc.extract_and_save_preferences(
                    1, "flight_ticket",
                    {"departure_city": "北京", "arrival_city": "上海"}
                )
                assert mock_save.call_count == 2

    @pytest.mark.asyncio
    async def test_save_query_log(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch("services.memory_service.get_session") as mock_sess:
            mock_session = MagicMock()
            mock_sess.return_value.__enter__.return_value = mock_session

            await svc.save_query_log(
                1, "s1", "query", "intent", {}, "tool", "summary", "answer", 100, True
            )
            mock_session.add.assert_called()

    @pytest.mark.asyncio
    async def test_get_recent_queries(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch("services.memory_service.get_session") as mock_sess:
            mock_log = MagicMock()
            mock_log.query = "北京天气"
            mock_log.intent = "weather_query"
            mock_log.slots = {}
            mock_log.tool_name = "weather_query"
            mock_log.created_at = MagicMock()
            mock_log.created_at.isoformat.return_value = "2026-05-15T00:00:00"

            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_log]
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.get_recent_queries(1)
            assert len(result) == 1
            assert result[0]["query"] == "北京天气"

    @pytest.mark.asyncio
    async def test_save_conversation(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch("services.memory_service.get_session") as mock_sess:
            mock_session = MagicMock()
            mock_sess.return_value.__enter__.return_value = mock_session

            await svc.save_conversation(1, "s1", "user", "你好", intent="greeting")
            mock_session.add.assert_called()

    @pytest.mark.asyncio
    async def test_get_conversation_history(self):
        from services.memory_service import MemoryService
        svc = MemoryService()
        with patch("services.memory_service.get_session") as mock_sess:
            mock_msg = MagicMock()
            mock_msg.role = "user"
            mock_msg.content = "你好"
            mock_msg.intent = "greeting"
            mock_msg.created_at = MagicMock()
            mock_msg.created_at.isoformat.return_value = "2026-05-15T00:00:00"

            mock_session = MagicMock()
            mock_session.query.return_value.filter_by.return_value.order_by.return_value.limit.return_value.all.return_value = [mock_msg]
            mock_sess.return_value.__enter__.return_value = mock_session

            result = await svc.get_conversation_history(1, "s1")
            assert len(result) == 1
            assert result[0]["role"] == "user"
