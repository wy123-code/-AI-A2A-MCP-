"""限流中间件 —— 基于令牌桶的 IP 级别限流。"""
import time
from collections import defaultdict
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse


class RateLimitMiddleware(BaseHTTPMiddleware):
    """简单的内存令牌桶限流器。

    默认：每 IP 每分钟 60 次请求，突发容量 10。
    """

    def __init__(self, app, rate: int = 60, burst: int = 10):
        super().__init__(app)
        self.rate = rate
        self.burst = burst
        self._buckets: dict = defaultdict(lambda: {"tokens": burst, "last": time.time()})

    async def dispatch(self, request: Request, call_next):
        client_ip = request.client.host if request.client else "unknown"
        bucket = self._buckets[client_ip]

        now = time.time()
        elapsed = now - bucket["last"]
        bucket["last"] = now
        bucket["tokens"] = min(self.burst, bucket["tokens"] + elapsed * self.rate / 60.0)

        if bucket["tokens"] >= 1.0:
            bucket["tokens"] -= 1.0
            return await call_next(request)

        trace_id = getattr(request.state, "trace_id", "")
        return JSONResponse(
            status_code=429,
            content={
                "error": {
                    "code": "RATE_LIMITED",
                    "message": "Too many requests. Please slow down.",
                    "trace_id": trace_id,
                }
            },
        )
