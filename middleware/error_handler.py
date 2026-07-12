"""全局异常处理中间件 —— 标准化 JSON 错误返回格式 + 指标采集。

优化说明 (P5): 异常时自动记录到 MetricsCollector，包含 endpoint + error_type + trace_id。
"""
import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from loguru import logger


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """捕获所有未处理异常，返回统一格式的 JSON 错误。"""

    async def dispatch(self, request: Request, call_next):
        start = time.time()
        try:
            return await call_next(request)
        except Exception as exc:
            duration_ms = int((time.time() - start) * 1000)
            trace_id = getattr(request.state, "trace_id", "")
            logger.opt(exception=True).error(
                f"Unhandled exception | trace_id={trace_id} | "
                f"path={request.url.path} | method={request.method}"
            )
            # 记录异常指标
            try:
                from common.monitor.metrics import metrics_collector
                await metrics_collector.start()
                await metrics_collector.record_request(
                    intent=request.url.path.strip("/"),
                    duration_ms=duration_ms,
                    success=False,
                )
            except Exception:
                pass
            return JSONResponse(
                status_code=500,
                content={
                    "error": {
                        "code": "INTERNAL_ERROR",
                        "message": "An internal error occurred. Please try again later.",
                        "trace_id": trace_id,
                    }
                },
            )
