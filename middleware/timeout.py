"""超时中间件 —— 按端点类型设置不同超时 + 指标采集。

优化说明 (P5): 超时时自动记录到 MetricsCollector。
"""
import asyncio
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from loguru import logger


class TimeoutMiddleware(BaseHTTPMiddleware):
    """为不同端点设置超时保护。

    聊天端点: 60s (含 LLM + 工具调用)
    其他端点: 30s
    """

    CHAT_PATHS = {"/chat", "/chat/stream", "/chat/authenticated"}
    CHAT_TIMEOUT = 120.0
    DEFAULT_TIMEOUT = 30.0

    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        timeout = self.CHAT_TIMEOUT if path in self.CHAT_PATHS else self.DEFAULT_TIMEOUT
        start = time.time()
        try:
            response = await asyncio.wait_for(call_next(request), timeout=timeout)
            return response
        except asyncio.TimeoutError:
            duration_ms = int((time.time() - start) * 1000)
            trace_id = getattr(request.state, "trace_id", "")
            logger.warning(f"TimeoutMiddleware: {path} timed out after {duration_ms}ms")
            # 记录超时指标
            try:
                from common.monitor.metrics import metrics_collector
                await metrics_collector.start()
                await metrics_collector.record_request(
                    intent=path.strip("/"), duration_ms=duration_ms, success=False
                )
            except Exception:
                pass
            return JSONResponse(
                status_code=504,
                content={
                    "error": {
                        "code": "TIMEOUT",
                        "message": f"Request timeout after {timeout}s",
                        "trace_id": trace_id,
                    }
                },
            )
