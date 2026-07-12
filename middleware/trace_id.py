"""TraceID 中间件 —— 为每个请求注入全局追踪 ID，响应头返回，日志自动关联。"""
import uuid
from contextvars import ContextVar
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

trace_id_var: ContextVar[str] = ContextVar("trace_id", default="")


def get_trace_id() -> str:
    """获取当前请求的 TraceID。"""
    return trace_id_var.get() or ""


class TraceIDMiddleware(BaseHTTPMiddleware):
    """链路追踪中间件 —— 为每个 HTTP 请求注入 trace_id，实现全链路追踪。"""
    async def dispatch(self, request: Request, call_next) -> Response:
        trace_id = request.headers.get("X-Trace-ID", "") or str(uuid.uuid4())[:12]
        request.state.trace_id = trace_id
        token = trace_id_var.set(trace_id)
        try:
            response = await call_next(request)
        finally:
            trace_id_var.reset(token)
        response.headers["X-Trace-ID"] = trace_id
        return response
