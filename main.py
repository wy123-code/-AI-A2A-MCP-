"""旅游助手 Agent - FastAPI 主入口（含用户管理 + 记忆管理 API + 流式输出）"""
import asyncio
import uuid
from contextlib import asynccontextmanager
from typing import Dict, List, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse, StreamingResponse
from loguru import logger
from pydantic import BaseModel, Field

from config import SUPPORTED_INTENTS, INTENT_SLOTS, INTENT_CN_MAP, MCP_SERVERS
from models.schemas import ChatRequest, ChatResponse
from graph.builder import run_agent, run_agent_stream
from mcp.mcp_client import prewarm_mcp_clients
from services.memory_service import memory_service
from services.user_service import user_service
from models.orm_models import init_db
from celery_app import app as celery_app
from middleware import TraceIDMiddleware, TimeoutMiddleware, ErrorHandlerMiddleware, RateLimitMiddleware


async def _prewarm_mcp_async(servers: dict) -> None:
    """后台预热 MCP 客户端，失败不影响服务运行。"""
    try:
        from mcp.mcp_client import prewarm_mcp_clients
        await prewarm_mcp_clients(servers)
    except Exception:
        pass


# ==================== 应用生命周期管理 ====================
@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    应用生命周期管理器 - 处理应用启动和关闭时的初始化/清理工作

    Args:
        app: FastAPI 应用实例

    Yields:
        None: 在应用运行期间暂停，关闭时继续执行清理逻辑
    """
    logger.info("Tourism Assistant Agent starting up...")
    # 初始化数据库表结构
    try:
        init_db()
        logger.info("Database tables initialized")
    except Exception as e:
        logger.warning(f"DB init skipped (may already exist): {e}")

    # 初始化 MCP Protocol 通信层 + 多智能体系统
    try:
        from agent_bus.pubsub import MCPPubSub
        from agent_bus.registry import AgentRegistry
        from agents import initialize_agents, shutdown_agents

        _pubsub = MCPPubSub()
        _registry = AgentRegistry()
        await _pubsub.start()
        await _registry.start()
        # 注入 PubSub 到 Registry，以便发布 Agent 上线/下线系统事件
        _registry.set_event_pubsub(_pubsub)
        await initialize_agents(_pubsub, _registry)
        logger.info("Multi-Agent system initialized (A2A + MCP)")
    except Exception as e:
        logger.error(f"Multi-Agent init failed (will use fallback): {e}")

    # 预热 MCP 客户端（后台执行，不阻塞服务启动）
    asyncio.create_task(_prewarm_mcp_async(MCP_SERVERS))

    # 启动全链路可观测性
    try:
        from common.monitor import metrics_collector
        await metrics_collector.start()
        logger.info("MetricsCollector started")
    except Exception as e:
        logger.warning(f"MetricsCollector start failed: {e}")

    logger.info(f"Supported intents: {SUPPORTED_INTENTS}")
    logger.info("Celery worker is configured — start with: celery -A celery_app worker -l info -P gevent")
    yield
    logger.info("Tourism Assistant Agent shutting down...")
    # 优雅关闭多智能体系统 + 可观测性
    try:
        from agents import shutdown_agents
        await shutdown_agents()
    except Exception:
        pass
    try:
        from common.monitor import metrics_collector
        await metrics_collector.stop()
    except Exception:
        pass


# 创建 FastAPI 应用实例，配置基本信息和生命周期管理
app = FastAPI(
    title="旅游助手 Agent API",
    description="基于 LLM + Multi-Agent 架构的智能旅游助手，支持长期/短期记忆",
    version="2.0.0",
    lifespan=lifespan,
)

# 中间件注册顺序：TraceID(最外层) → Timeout → ErrorHandler → RateLimit → CORS
app.add_middleware(TraceIDMiddleware)
app.add_middleware(TimeoutMiddleware)
app.add_middleware(ErrorHandlerMiddleware)
app.add_middleware(RateLimitMiddleware, rate=300, burst=30)

# 配置跨域资源共享中间件，允许所有来源访问
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务（前端）
app.mount("/static", StaticFiles(directory="frontend"), name="static")


# ==================== 数据模型定义 ====================
class UserCreateRequest(BaseModel):
    """用户创建请求模型"""
    username: str
    nickname: str = ""
    email: str = ""
    phone: str = ""


class UserUpdateRequest(BaseModel):
    """用户信息更新请求模型"""
    nickname: str = None
    avatar: str = None
    email: str = None
    phone: str = None


class AuthRegisterRequest(BaseModel):
    """用户注册请求模型"""
    username: str
    password: str
    nickname: str = ""
    email: str = ""
    phone: str = ""


class AuthLoginRequest(BaseModel):
    """用户登录请求模型"""
    username: str
    password: str


class PreferenceSetRequest(BaseModel):
    """用户偏好设置请求模型"""
    user_id: int
    category: str
    key: str
    value: str
    confidence: float = 1.0


class MemoryQueryRequest(BaseModel):
    """记忆查询请求模型"""
    user_id: int
    intent: str = None
    limit: int = 10


# ==================== 基础 API 端点 ====================
@app.get("/")
async def root():
    """
    根路径 - 返回前端首页
    
    Returns:
        FileResponse: 前端 index.html 文件
    """
    return FileResponse("frontend/index.html")


@app.get("/health")
async def health_check():
    """
    健康检查接口 - 用于服务监控和可用性检测
    
    Returns:
        dict: 包含服务状态、名称和版本信息
    """
    return {
        "status": "ok",
        "service": "tourism-assistant-agent",
        "version": "2.0.0",
    }


@app.get("/intents")
async def list_intents():
    """
    获取支持的意图列表 - 返回系统能够识别的所有用户意图类型
    
    Returns:
        dict: 包含支持的意图列表，每个意图包含 ID、中文名称和槽位信息
    """
    return {
        "supported_intents": [
            {"id": k, "name": INTENT_CN_MAP.get(k, k), "slots": INTENT_SLOTS.get(k, [])}
            for k in SUPPORTED_INTENTS
        ],
    }


# ==================== 核心对话 API ====================
@app.post("/chat", response_model=ChatResponse)
async def chat(request: ChatRequest):
    """
    主对话接口 - 处理匿名用户的用户查询，集成长期记忆和短期记忆
    
    Args:
        request: 聊天请求对象，包含查询内容、会话ID和历史记录
    
    Returns:
        ChatResponse: 包含回答、意图识别结果、跟进问题和响应时间
    """
    session_id = request.session_id or str(uuid.uuid4())[:8]
    logger.info(f"[/chat] session={session_id}, query='{request.query[:80]}'")

    result = await run_agent(
        query=request.query,
        session_id=session_id,
        user_id=None,
        history=request.history if request.history else None,
    )

    return ChatResponse(
        session_id=session_id,
        answer=result["answer"],
        intent=result["intent"],
        follow_up_needed=result["follow_up_needed"],
        follow_up_question=result["follow_up_question"],
        duration_ms=result.get("duration_ms", 0),
    )


@app.post("/chat/stream")
async def chat_stream(request: ChatRequest):
    """
    流式对话接口 - 使用 Server-Sent Events (SSE) 实时输出回答内容，提升用户体验
    
    Args:
        request: 聊天请求对象，包含查询内容、会话ID和历史记录
    
    Returns:
        StreamingResponse: SSE 格式的流式响应，包含禁用缓存的头部配置
    """
    session_id = request.session_id or str(uuid.uuid4())[:8]
    logger.info(f"[/chat/stream] session={session_id}, query='{request.query[:80]}'")

    return StreamingResponse(
        run_agent_stream(
            query=request.query,
            session_id=session_id,
            user_id=None,
            history=request.history if request.history else None,
        ),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.post("/chat/authenticated", response_model=ChatResponse)
async def chat_authenticated(request: ChatRequest, user_id: int = Query(..., description="用户ID")):
    """
    认证用户对话接口 - 为已登录用户提供个性化对话服务，集成用户长期记忆和会话短期记忆
    
    Args:
        request: 聊天请求对象，包含查询内容、会话ID和历史记录
        user_id: 用户ID，从查询参数中获取
    
    Returns:
        ChatResponse: 包含回答、意图识别结果、跟进问题和响应时间
    
    Raises:
        HTTPException: 当用户不存在时抛出 404 错误
    """
    session_id = request.session_id or str(uuid.uuid4())[:8]
    logger.info(f"[/chat/auth] user={user_id}, session={session_id}, query='{request.query[:80]}'")

    user = await user_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")

    result = await run_agent(
        query=request.query,
        session_id=session_id,
        user_id=user_id,
        history=request.history if request.history else None,
    )

    return ChatResponse(
        session_id=session_id,
        answer=result["answer"],
        intent=result["intent"],
        follow_up_needed=result["follow_up_needed"],
        follow_up_question=result["follow_up_question"],
        duration_ms=result.get("duration_ms", 0),
    )


# ==================== 用户认证 API ====================
@app.post("/auth/register")
async def register(req: AuthRegisterRequest):
    """
    用户注册接口 - 创建新用户账户并进行基本验证
    
    Args:
        req: 注册请求对象，包含用户名、密码、昵称、邮箱和电话
    
    Returns:
        dict: 包含成功标志和用户信息
    
    Raises:
        HTTPException: 用户名长度不足2字符时抛出 400；密码长度不足6字符时抛出 400；
                      用户名已存在时抛出 409
    """
    if len(req.username) < 2:
        raise HTTPException(status_code=400, detail="Username must be at least 2 characters")
    if len(req.password) < 6:
        raise HTTPException(status_code=400, detail="Password must be at least 6 characters")
    user = await user_service.register_user(
        username=req.username,
        password=req.password,
        nickname=req.nickname,
        email=req.email,
        phone=req.phone,
    )
    if not user:
        raise HTTPException(status_code=409, detail="Username already exists")
    return {"success": True, "user": user}


@app.post("/auth/login")
async def login(req: AuthLoginRequest):
    """
    用户登录接口 - 验证用户凭据并返回用户信息
    
    Args:
        req: 登录请求对象，包含用户名和密码
    
    Returns:
        dict: 包含成功标志和用户信息
    
    Raises:
        HTTPException: 用户名或密码无效时抛出 401 错误
    """
    user = await user_service.authenticate_user(req.username, req.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    return {"success": True, "user": user}


# ==================== 用户管理 API ====================
@app.post("/users")
async def create_user(req: UserCreateRequest):
    """
    创建用户接口 - 管理员或系统创建新用户（无需密码）
    
    Args:
        req: 用户创建请求对象，包含用户名、昵称、邮箱和电话
    
    Returns:
        dict: 包含成功标志和用户信息
    
    Raises:
        HTTPException: 用户名已存在时抛出 409 错误
    """
    user = await user_service.create_user(
        username=req.username,
        nickname=req.nickname,
        email=req.email,
        phone=req.phone,
    )
    if not user:
        raise HTTPException(status_code=409, detail="Username already exists")
    return {"success": True, "user": user}


@app.get("/users/{user_id}")
async def get_user(user_id: int):
    """
    获取用户信息接口 - 根据用户ID查询用户详细信息
    
    Args:
        user_id: 用户ID
    
    Returns:
        dict: 包含成功标志和用户信息
    
    Raises:
        HTTPException: 用户不存在时抛出 404 错误
    """
    user = await user_service.get_user(user_id)
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True, "user": user}


@app.put("/users/{user_id}")
async def update_user(user_id: int, req: UserUpdateRequest):
    """
    更新用户信息接口 - 修改用户的昵称、头像、邮箱或电话
    
    Args:
        user_id: 用户ID
        req: 用户更新请求对象，包含要更新的字段（nickname、avatar、email、phone）
    
    Returns:
        dict: 包含成功标志
    
    Raises:
        HTTPException: 用户不存在时抛出 404 错误
    """
    ok = await user_service.update_user(
        user_id,
        nickname=req.nickname,
        avatar=req.avatar,
        email=req.email,
        phone=req.phone,
    )
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}


@app.delete("/users/{user_id}")
async def deactivate_user(user_id: int):
    """
    停用用户接口 - 软删除用户账户（非物理删除）
    
    Args:
        user_id: 用户ID
    
    Returns:
        dict: 包含成功标志
    
    Raises:
        HTTPException: 用户不存在时抛出 404 错误
    """
    ok = await user_service.deactivate_user(user_id)
    if not ok:
        raise HTTPException(status_code=404, detail="User not found")
    return {"success": True}


@app.get("/users")
async def list_users(limit: int = 50):
    """
    用户列表接口 - 获取用户列表，支持分页限制
    
    Args:
        limit: 返回用户数量上限，默认 50
    
    Returns:
        dict: 包含成功标志、用户列表和总数
    """
    users = await user_service.list_users(limit=limit)
    return {"success": True, "users": users, "total": len(users)}


# ==================== 长期记忆管理 API ====================
@app.post("/memory/preferences")
async def set_preference(req: PreferenceSetRequest):
    """
    设置用户偏好接口 - 保存用户的长期偏好记忆（如旅行偏好、饮食喜好等）
    
    Args:
        req: 偏好设置请求对象，包含 user_id、category、key、value、confidence
    
    Returns:
        dict: 包含成功标志
    """
    await memory_service.save_preference(
        user_id=req.user_id,
        category=req.category,
        key=req.key,
        value=req.value,
        confidence=req.confidence,
        source="explicit",
    )
    return {"success": True}


@app.get("/memory/preferences/{user_id}")
async def get_preferences(user_id: int, category: str = None):
    """
    获取用户偏好接口 - 查询用户的长期偏好记忆，可按类别筛选
    
    Args:
        user_id: 用户ID
        category: 偏好类别（可选），如不指定则返回所有类别
    
    Returns:
        dict: 包含成功标志、偏好列表和总数
    """
    prefs = await memory_service.get_preferences(user_id, category=category)
    return {"success": True, "preferences": prefs, "total": len(prefs)}


@app.post("/memory/query-history")
async def get_query_history(req: MemoryQueryRequest):
    """
    获取用户查询历史接口 - 查询用户的历史搜索记录，可按意图类型筛选
    
    Args:
        req: 记忆查询请求对象，包含 user_id、intent（可选）、limit
    
    Returns:
        dict: 包含成功标志、查询历史列表和总数
    """
    queries = await memory_service.get_recent_queries(
        user_id=req.user_id,
        intent=req.intent,
        limit=req.limit,
    )
    return {"success": True, "queries": queries, "total": len(queries)}


@app.get("/memory/conversation/{user_id}/{session_id}")
async def get_conversation(user_id: int, session_id: str, limit: int = 50):
    """
    获取对话历史接口 - 查询特定会话的对话记录
    
    Args:
        user_id: 用户ID
        session_id: 会话ID
        limit: 返回消息数量上限，默认 50
    
    Returns:
        dict: 包含成功标志、消息列表和总数
    """
    msgs = await memory_service.get_conversation_history(user_id, session_id, limit=limit)
    return {"success": True, "messages": msgs, "total": len(msgs)}


# ==================== 短期记忆管理 API ====================
@app.get("/memory/session/{session_id}")
async def get_session_memory(session_id: str):
    """
    获取会话短期记忆接口 - 查询特定会话的短期记忆（包括消息列表和会话摘要）
    
    Args:
        session_id: 会话ID
    
    Returns:
        dict: 包含成功标志、会话ID、消息列表、会话摘要和消息总数
    """
    messages = await memory_service.get_short_term(session_id)
    summary = await memory_service.get_session_summary(session_id)
    return {
        "success": True,
        "session_id": session_id,
        "messages": messages,
        "summary": summary,
        "total": len(messages),
    }


@app.delete("/memory/session/{session_id}")
async def clear_session_memory(session_id: str):
    """
    清除会话短期记忆接口 - 删除特定会话的短期记忆数据

    Args:
        session_id: 会话ID

    Returns:
        dict: 包含成功标志
    """
    await memory_service.clear_short_term(session_id)
    return {"success": True}


# ==================== 可观测性 API ====================

@app.get("/metrics")
async def get_metrics(window_minutes: int = 60):
    """全链路性能指标接口 —— 返回 p50/p95/p99 耗时、成功率等统计数据。

    Args:
        window_minutes: 统计时间窗口（分钟），默认 60
    """
    try:
        from common.monitor import metrics_collector
        stats = await metrics_collector.get_stats(window_seconds=window_minutes * 60)
        return {"success": True, "metrics": stats, "window_minutes": window_minutes}
    except Exception as e:
        return {"success": False, "error": str(e)}


@app.get("/metrics/slow")
async def get_slow_requests(limit: int = 20):
    """慢请求列表接口 —— 返回最近耗时超过 5 秒的请求记录。

    Args:
        limit: 返回条数上限，默认 20
    """
    try:
        from common.monitor import metrics_collector
        slow = await metrics_collector.get_slow_requests(limit=limit)
        return {"success": True, "slow_requests": slow, "total": len(slow)}
    except Exception as e:
        return {"success": False, "error": str(e)}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
