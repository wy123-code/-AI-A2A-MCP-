"""插件注册中心 —— 自动扫描 Worker 子包 PLUGIN_MANIFEST 并注册 Agent 类。

优化说明 (P10):
  - auto_discover() 扫描 agents/worker/*/plugin.py 自动发现 Agent 类
  - 支持意图→Agent 映射、优先级、负载权重
  - 零代码改动即可接入新业务 Worker Agent
"""
import importlib
from pathlib import Path
from typing import Dict, List, Optional, Type

from loguru import logger

from agents.worker.base import BaseWorkerAgent


class PluginManifest:
    """插件声明 —— 每个 Worker 子包的 plugin.py 中声明。"""

    __slots__ = (
        "agent_class", "agent_name", "intents", "priority",
        "load_balancer_weight", "tool_functions",
    )

    def __init__(
        self,
        agent_class: Type[BaseWorkerAgent],
        agent_name: str,
        intents: List[str],
        priority: int = 5,
        load_balancer_weight: float = 1.0,
        tool_functions: List[str] = None,
    ):
        self.agent_class = agent_class
        self.agent_name = agent_name
        self.intents = intents
        self.priority = priority
        self.load_balancer_weight = load_balancer_weight
        self.tool_functions = tool_functions or []


class PluginRegistry:
    """插件注册中心 —— 自动扫描并注册所有 Worker Agent。

    使用方法:
        registry = PluginRegistry()
        manifests = registry.auto_discover()
        workers = [m.agent_class(pubsub, agent_registry) for m in manifests]
    """

    _PLUGIN_DIR = Path(__file__).parent / "worker"

    @classmethod
    def auto_discover(cls) -> List[PluginManifest]:
        """自动扫描 agents/worker/*/plugin.py 并返回所有插件声明清单。"""
        manifests: List[PluginManifest] = []

        for item in cls._PLUGIN_DIR.iterdir():
            if not item.is_dir():
                continue
            if item.name.startswith("_") or item.name.startswith("."):
                continue

            plugin_file = item / "plugin.py"
            if not plugin_file.exists():
                logger.debug(f"PluginRegistry: no plugin.py in {item.name}, skipping")
                continue

            try:
                module_path = f"agents.worker.{item.name}.plugin"
                mod = importlib.import_module(module_path)
                manifest = getattr(mod, "PLUGIN_MANIFEST", None)
                if manifest and isinstance(manifest, PluginManifest):
                    manifests.append(manifest)
                    logger.info(f"PluginRegistry: registered {manifest.agent_name} "
                                f"→ intents={manifest.intents} priority={manifest.priority}")
            except Exception as e:
                logger.warning(f"PluginRegistry: failed to load plugin from {item.name}: {e}")

        logger.info(f"PluginRegistry: auto-discovered {len(manifests)} Worker plugins")
        return manifests

    @classmethod
    def find_by_intent(cls, intent: str, manifests: List[PluginManifest] = None
                       ) -> Optional[PluginManifest]:
        """根据意图查找匹配的插件声明。"""
        if manifests is None:
            manifests = cls.auto_discover()
        for m in manifests:
            if intent in m.intents:
                return m
        return None

    @classmethod
    def get_intent_priority(cls, intent: str) -> int:
        """获取指定意图的调度优先级。"""
        manifest = cls.find_by_intent(intent)
        return manifest.priority if manifest else 5
