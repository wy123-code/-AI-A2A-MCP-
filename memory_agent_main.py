"""Memory Agent 独立部署入口 —— 将 Memory Agent 作为独立进程运行。

Memory Agent 通过 Redis PubSub（MCP 协议）与其他 Agent 通信，
因此可以独立部署、独立扩缩容，不依赖主应用进程。

启动方式:
    python memory_agent_main.py

环境变量（与主应用共享 .env）:
    REDIS_HOST / REDIS_PORT / REDIS_DB / REDIS_PASSWORD
    MYSQL_HOST / MYSQL_PORT / MYSQL_USER / MYSQL_PASSWORD / MYSQL_DATABASE

架构说明:
    主应用进程                      Memory Agent 进程
    ┌──────────────┐               ┌──────────────────┐
    │ Orchestrator │──Redis PubSub──→│  MemoryAgent     │
    │   Workers    │               │  ├─ short_term    │
    │   A2A/MCP    │               │  ├─ preferences   │
    └──────────────┘               │  └─ conversation  │
                                   └────────┬─────────┘
                                            │
                                      ┌─────┴─────┐
                                      │ Redis     │
                                      │ MySQL     │
                                      └───────────┘
"""

import asyncio
import signal
import sys
from pathlib import Path

# 确保项目根目录在 Python path 中
sys.path.insert(0, str(Path(__file__).resolve().parent))

from loguru import logger
from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)


async def main():
    """启动独立的 Memory Agent 进程。"""
    logger.info("=" * 50)
    logger.info("Memory Agent - Standalone Process")
    logger.info("=" * 50)

    # 初始化基础设施
    from agent_bus.pubsub import MCPPubSub
    from agent_bus.registry import AgentRegistry

    pubsub = MCPPubSub()
    registry = AgentRegistry()

    await pubsub.start()
    await registry.start()
    registry.set_event_pubsub(pubsub)
    logger.info("Memory Agent: Redis PubSub connected, Registry ready")

    # 启动 Memory Agent
    from agents.memory.agent import MemoryAgent

    memory_agent = MemoryAgent(pubsub, registry)
    await memory_agent.start()
    logger.info("Memory Agent: listening for MCP requests on Redis PubSub")

    # 优雅关闭
    stop_event = asyncio.Event()

    def _signal_handler(sig, frame):
        logger.info(f"Memory Agent: received signal {sig}, shutting down...")
        stop_event.set()

    signal.signal(signal.SIGINT, _signal_handler)
    signal.signal(signal.SIGTERM, _signal_handler)

    try:
        await stop_event.wait()
    finally:
        await memory_agent.stop()
        await registry.stop()
        await pubsub.stop()
        logger.info("Memory Agent: shutdown complete")


if __name__ == "__main__":
    asyncio.run(main())
