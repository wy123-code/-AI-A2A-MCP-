"""工具层测试 —— 票务查询、景点推荐、A2A 工具链路。"""
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestTicketQuery:
    """票务查询工具测试"""

    @pytest.mark.asyncio
    async def test_query_flight_ticket(self, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"ticket_type":"飞机票","results":[{"flight_no":"CA1234","price":1280}],"total_count":1}'
            ))]
        )
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("flight_ticket", {
            "departure_city": "北京", "arrival_city": "上海", "departure_date": "2026-06-01"
        })
        assert result["success"] is True
        assert len(result["data"]["results"]) == 1

    @pytest.mark.asyncio
    async def test_query_train_ticket(self, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"ticket_type":"火车票","results":[{"train_no":"G101","price":553}],"total_count":1}'
            ))]
        )
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("train_ticket", {
            "departure_city": "北京", "arrival_city": "南京", "departure_date": "2026-06-01"
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_ship_ticket_valid_route(self, mock_openai_client):
        """合法航线（深圳→珠海）正常调用 LLM 生成数据。"""
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"ticket_type":"船票","results":[{"ship_name":"蛇口号","price":130}],"total_count":1}'
            ))]
        )
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("ship_ticket", {
            "departure_port": "深圳", "arrival_port": "珠海", "departure_date": "2026-06-01"
        })
        assert result["success"] is True
        assert "results" in result["data"]

    @pytest.mark.asyncio
    async def test_ship_ticket_invalid_route_rejected(self, mock_openai_client):
        """不合法航线（深圳→北京）直接拦截，不调用 LLM。"""
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("ship_ticket", {
            "departure_port": "深圳", "arrival_port": "北京", "departure_date": "2026-06-01"
        })
        assert result["success"] is True
        assert "暂无客运航线" in result["data"]
        # 确保没有调用 LLM
        mock_openai_client.chat.completions.create.assert_not_called()

    @pytest.mark.asyncio
    async def test_ship_ticket_bidirectional_route(self, mock_openai_client):
        """反向航线（珠海→深圳）同样识别为合法。"""
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"ticket_type":"船票","results":[{"ship_name":"九洲号","price":130}],"total_count":1}'
            ))]
        )
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("ship_ticket", {
            "departure_port": "珠海", "arrival_port": "深圳", "departure_date": "2026-06-01"
        })
        assert result["success"] is True
        assert "results" in result["data"]

    @pytest.mark.asyncio
    async def test_concert_ticket_skips_ferry_check(self, mock_openai_client):
        """演唱会票不触发航线校验。"""
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"ticket_type":"演唱会票","results":[{"concert_name":"周杰伦演唱会","price":880}],"total_count":1}'
            ))]
        )
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("concert_ticket", {
            "city": "上海", "concert_name": "周杰伦"
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_query_concert_ticket(self, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(
                content='{"ticket_type":"演唱会票","results":[{"concert_name":"周杰伦演唱会","price":880}],"total_count":1}'
            ))]
        )
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("concert_ticket", {
            "city": "上海", "concert_name": "周杰伦"
        })
        assert result["success"] is True

    @pytest.mark.asyncio
    async def test_handles_llm_failure(self, mock_openai_client):
        mock_openai_client.chat.completions.create.side_effect = Exception("API error")
        from agents.worker.ticket.tool import query_ticket
        result = await query_ticket("flight_ticket", {"departure_city": "北京"})
        assert result["success"] is False


class TestAttractionRecommend:
    """景点推荐工具测试"""

    @pytest.mark.asyncio
    async def test_recommend_with_milvus(self, mock_openai_client):
        with patch("agents.worker.attraction.tool.milvus_client") as mock_milvus:
            mock_milvus.search = AsyncMock(return_value=[
                {"id": "1", "score": 0.95, "name": "故宫", "description": "明清皇宫", "city": "北京"}
            ])
            mock_openai_client.embeddings.create.return_value = MagicMock(
                data=[MagicMock(embedding=[0.1] * 256)]
            )
            mock_openai_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="推荐故宫、天安门..."))]
            )

            from agents.worker.attraction.tool import recommend_attractions
            result = await recommend_attractions("attraction_recommend", {
                "city": "北京", "days": 3
            })

            assert result["success"] is True
            assert "故宫" in result["data"]["recommendation"]

    @pytest.mark.asyncio
    async def test_recommend_without_milvus(self, mock_openai_client):
        """Milvus 不可用时降级为纯 LLM 推荐。"""
        with patch("agents.worker.attraction.tool.milvus_client") as mock_milvus:
            mock_milvus.search = AsyncMock(return_value=[])
            mock_openai_client.embeddings.create.return_value = MagicMock(
                data=[MagicMock(embedding=[0.1] * 256)]
            )
            mock_openai_client.chat.completions.create.return_value = MagicMock(
                choices=[MagicMock(message=MagicMock(content="抱歉，未找到相关景点。"))]
            )

            from agents.worker.attraction.tool import recommend_attractions
            result = await recommend_attractions("attraction_recommend", {
                "city": "不存在的城市", "days": 1
            })

            assert result["success"] is True


class TestA2ATools:
    """A2A 工具链路测试（天气、旅行团、酒店、租车、保险）"""

    @pytest.mark.asyncio
    async def test_weather_tool(self, mock_openai_client):
        from agents.worker.weather.tool import weather_tool
        with patch("agents.worker.weather.tool.WEATHER_API_CONFIG", {"key": "test-key", "base_url": "https://test.api", "now_path": "/now", "forecast_path": "/fc"}):
         with patch("agents.worker.weather.tool.aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={
                "code": "200",
                "updateTime": "2026-05-16T14:00+08:00",
                "now": {"temp": "25", "feelsLike": "23", "text": "晴"},
            })
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=None),
            ))
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("tools.city_codes.get_location_id", return_value="101010100"):
                result = await weather_tool("weather_query", {"city": "北京", "date": "2026-05-16"})
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_weather_tool_defaults_date(self, mock_openai_client):
        from agents.worker.weather.tool import weather_tool
        with patch("agents.worker.weather.tool.WEATHER_API_CONFIG", {"key": "test-key", "base_url": "https://test.api", "now_path": "/now", "forecast_path": "/fc"}):
         with patch("agents.worker.weather.tool.aiohttp.ClientSession") as mock_session_cls:
            mock_session = MagicMock()
            mock_resp = MagicMock()
            mock_resp.status = 200
            mock_resp.json = AsyncMock(return_value={
                "code": "200", "now": {"temp": "25", "feelsLike": "23", "text": "晴"},
            })
            mock_session.get = MagicMock(return_value=AsyncMock(
                __aenter__=AsyncMock(return_value=mock_resp),
                __aexit__=AsyncMock(return_value=None),
            ))
            mock_session_cls.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_cls.return_value.__aexit__ = AsyncMock(return_value=None)

            with patch("tools.city_codes.get_location_id", return_value="101010100"):
                result = await weather_tool("weather_query", {"city": "北京"})
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_tour_group_tool(self, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="SELECT * FROM tour_group WHERE destination='三亚'"))]
        )
        with patch("agents.worker.tour_group.tool.tour_group_a2a") as mock_a2a:
            mock_a2a.query = AsyncMock(return_value={"success": True, "data": []})
            from agents.worker.tour_group.tool import tour_group_tool
            result = await tour_group_tool("tour_group_query", {"city": "三亚"})
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_hotel_tool(self, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="SELECT * FROM hotel WHERE address LIKE '%成都%'"))]
        )
        with patch("agents.worker.hotel.tool.hotel_a2a") as mock_a2a:
            mock_a2a.query = AsyncMock(return_value={"success": True, "data": []})
            from agents.worker.hotel.tool import hotel_tool
            result = await hotel_tool("hotel_query", {"address": "成都", "time": "2026-06-01"})
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_car_rental_tool(self, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="SELECT * FROM car_rental WHERE city='杭州'"))]
        )
        with patch("agents.worker.car_rental.tool.car_rental_a2a") as mock_a2a:
            mock_a2a.query = AsyncMock(return_value={"success": True, "data": []})
            from agents.worker.car_rental.tool import car_rental_tool
            result = await car_rental_tool("car_rental_query", {"city": "杭州"})
            assert result["success"] is True

    @pytest.mark.asyncio
    async def test_insurance_tool(self, mock_openai_client):
        mock_openai_client.chat.completions.create.return_value = MagicMock(
            choices=[MagicMock(message=MagicMock(content="SELECT * FROM insurance WHERE insurance_type='旅行'"))]
        )
        with patch("agents.worker.insurance.tool.insurance_a2a") as mock_a2a:
            mock_a2a.query = AsyncMock(return_value={"success": True, "data": []})
            from agents.worker.insurance.tool import insurance_tool
            result = await insurance_tool("insurance_query", {"insurance_type": "旅行"})
            assert result["success"] is True


class TestToolRegistry:
    """工具注册中心完整性测试"""

    def test_all_9_tools_registered(self):
        from tools import TOOL_REGISTRY
        expected = {
            "ticket_query", "train_ticket_query", "weather_query", "tour_group_query",
            "hotel_query", "car_rental_query", "insurance_query",
            "attraction_recommend", "flight_query",
        }
        assert set(TOOL_REGISTRY.keys()) == expected

    def test_all_tools_are_callable(self):
        from tools import TOOL_REGISTRY
        for name, func in TOOL_REGISTRY.items():
            assert callable(func), f"{name} is not callable"
