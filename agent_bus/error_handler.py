"""Agent 通信异常处理 —— 超时重试、故障降级、日志埋点。"""
import asyncio
from typing import Any, Awaitable, Callable, Dict

from loguru import logger


class ErrorHandler:
    """多 Agent 通信容错工具集。"""

    @staticmethod
    async def with_timeout(
        coro: Awaitable,
        timeout: float,
        fallback_result: Any = None,
    ) -> Any:
        """带超时的协程执行，超时返回降级结果。

        Args:
            coro: 要执行的协程
            timeout: 超时时间（秒）
            fallback_result: 超时时返回的降级值
        """
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"ErrorHandler: timeout after {timeout}s")
            return fallback_result

    @staticmethod
    async def with_retry(
        coro_factory: Callable[[], Awaitable],
        max_retries: int = 2,
        backoff: float = 1.0,
    ) -> Any:
        """带指数退避的重试执行。

        Args:
            coro_factory: 每次重试时创建新协程的工厂函数
            max_retries: 最大重试次数
            backoff: 基础退避时间（秒），每次重试翻倍
        """
        last_error = None
        for attempt in range(max_retries + 1):
            try:
                return await coro_factory()
            except Exception as e:
                last_error = e
                if attempt < max_retries:
                    wait = backoff * (2 ** attempt)
                    logger.warning(
                        f"ErrorHandler: attempt {attempt + 1} failed: {e}, retrying in {wait}s"
                    )
                    await asyncio.sleep(wait)
        raise last_error

    @staticmethod
    def degrade_factory(intent: str) -> Dict[str, Any]:
        """当 Worker Agent 不可用时，生成优雅降级响应。

        Args:
            intent: 失败的意图标识
        """
        return {
            "success": False,
            "error": f"服务「{intent}」暂时不可用，请稍后重试",
            "data": [],
            "degraded": True,
        }
