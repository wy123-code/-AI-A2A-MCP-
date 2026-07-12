"""MCP Streamable HTTP 客户端 —— 支持连接远程 MCP 服务并调用工具。"""

import json
from typing import Any, Dict, List, Optional

import aiohttp
from loguru import logger


class MCPClient:
    """MCP Streamable HTTP 客户端。

    实现 MCP 协议中的 initialize → tools/list → tools/call 流程，
    通过 streamable_http 传输方式与远程 MCP 服务通信。
    """

    def __init__(self, name: str, url: str):
        self.name = name
        self.url = url.rstrip("/")
        self._session_id: Optional[str] = None
        self._tools: Optional[List[Dict[str, Any]]] = None
        self._initialized = False
        self._http: Optional[aiohttp.ClientSession] = None

    async def initialize(self) -> bool:
        """发送 initialize 请求，获取服务端能力与工具列表。"""
        try:
            timeout = aiohttp.ClientTimeout(total=60)
            self._http = aiohttp.ClientSession(timeout=timeout)

            # Step 1: initialize
            resp = await self._http.post(
                self.url,
                json={
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": "2024-11-05",
                        "capabilities": {},
                        "clientInfo": {
                            "name": "tourism-assistant",
                            "version": "1.0.0",
                        },
                    },
                },
                headers=self._build_headers(),
            )
            resp.raise_for_status()
            self._session_id = resp.headers.get("Mcp-Session-Id")
            result = await resp.json()
            logger.info(
                f"MCP [{self.name}]: initialized, "
                f"server={result.get('result', {}).get('serverInfo', {}).get('name', 'unknown')}"
            )

            # Step 2: send initialized notification
            await self._http.post(
                self.url,
                json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                headers=self._build_headers(),
            )

            # Step 3: tools/list
            tools = await self._list_tools()
            if tools is not None:
                self._tools = tools
                self._initialized = True
                return True

            return False

        except Exception as e:
            logger.error(f"MCP [{self.name}]: initialize failed: {e}")
            return False

    async def call_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        """调用远程 MCP 工具。返回 {success, error, data}。

        会话过期时自动重连并重试一次。
        """
        if not self._initialized:
            ok = await self.initialize()
            if not ok:
                return {"success": False, "error": "MCP server not initialized", "data": []}

        last_error = None
        for attempt in range(2):
            try:
                resp = await self._http.post(
                    self.url,
                    json={
                        "jsonrpc": "2.0",
                        "id": 2,
                        "method": "tools/call",
                        "params": {"name": tool_name, "arguments": arguments},
                    },
                    headers=self._build_headers(),
                )
                resp.raise_for_status()
                result = await resp.json()

                if "error" in result:
                    err = result["error"]
                    err_code = err.get("code") if isinstance(err, dict) else None
                    err_msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
                    # 401/403 → 会话过期，重连重试
                    if err_code in (401, 403) and attempt == 0:
                        logger.warning(f"MCP [{self.name}]: session expired (code={err_code}), re-initializing...")
                        await self._reinitialize()
                        continue
                    logger.warning(f"MCP [{self.name}]: tool '{tool_name}' JSON-RPC error: {err}")
                    return {"success": False, "error": err_msg, "data": []}

                rpc_result = result.get("result", {})
                if rpc_result.get("isError"):
                    err_text = (rpc_result.get("content", [{}])[0].get("text", "unknown error"))
                    logger.warning(f"MCP [{self.name}]: tool '{tool_name}' returned error: {err_text[:200]}")
                    return {"success": False, "error": err_text, "data": []}

                content = (rpc_result.get("content", [{}])[0].get("text", ""))
                try:
                    parsed = json.loads(content) if isinstance(content, str) else content
                except (json.JSONDecodeError, TypeError):
                    parsed = content
                logger.info(f"MCP [{self.name}]: tool '{tool_name}' success")
                return {"success": True, "error": None, "data": parsed}

            except aiohttp.ClientResponseError as e:
                last_error = str(e)
                # HTTP 401/403 → 会话过期，重连重试
                if e.status in (401, 403) and attempt == 0:
                    logger.warning(f"MCP [{self.name}]: HTTP {e.status}, re-initializing session...")
                    await self._reinitialize()
                    continue
                logger.error(f"MCP [{self.name}]: call_tool '{tool_name}' HTTP error: {e}")
                if attempt == 0:
                    break

            except Exception as e:
                last_error = str(e)
                logger.error(f"MCP [{self.name}]: call_tool '{tool_name}' failed: {e}")
                if attempt == 0:
                    break

        return {"success": False, "error": last_error or "unknown error", "data": []}


    async def _reinitialize(self) -> None:
        """强制重新初始化 MCP 会话（用于会话过期恢复）。"""
        if self._http:
            try:
                await self._http.close()
            except Exception:
                pass
        self._http = None
        self._session_id = None
        self._initialized = False
        await self.initialize()

    async def list_tools(self) -> List[Dict[str, Any]]:
        """获取可用工具列表。"""
        if not self._initialized:
            await self.initialize()
        return self._tools or []

    async def _list_tools(self) -> Optional[List[Dict[str, Any]]]:
        try:
            resp = await self._http.post(
                self.url,
                json={"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}},
                headers=self._build_headers(),
            )
            resp.raise_for_status()
            result = await resp.json()
            tools = result.get("result", {}).get("tools", [])
            names = [t.get("name", "?") for t in tools]
            logger.info(f"MCP [{self.name}]: available tools: {names}")
            return tools
        except Exception as e:
            logger.error(f"MCP [{self.name}]: tools/list failed: {e}")
            return None

    async def close(self):
        if self._http:
            await self._http.close()
            self._http = None

    def _build_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers


# ==================== 全局 MCP 客户端实例 ====================
_mcp_clients: Dict[str, MCPClient] = {}


async def get_mcp_client(name: str, url: str) -> MCPClient:
    """获取或创建 MCP 客户端（单例模式）。"""
    if name not in _mcp_clients:
        client = MCPClient(name, url)
        await client.initialize()
        _mcp_clients[name] = client
    return _mcp_clients[name]


async def prewarm_mcp_clients(servers: dict) -> None:
    """应用启动时预热所有 MCP 客户端，避免首次查询的冷启动延迟。"""
    for name, config in servers.items():
        try:
            client = await get_mcp_client(name, config["url"])
            tools = await client.list_tools()
            logger.info(f"MCP prewarm [{name}]: {len(tools)} tools available")
        except Exception as e:
            logger.warning(f"MCP prewarm [{name}] failed (will retry on first use): {e}")
