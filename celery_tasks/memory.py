"""记忆相关异步任务 —— 会话压缩、偏好提取、DB 写入等耗时操作。"""

import json
from datetime import datetime
from typing import Any, Dict

from celery_app import app
from loguru import logger

from config import REDIS_CONFIG, LLM_CONFIG, MEMORY_CONFIG
from services.memory_service import memory_service
from models.orm_models import get_session, QueryLog, ConversationHistory, UserPreference
from llm.client_pool import llm_manager


@app.task(name="celery_tasks.memory.compress_old_conversations")
def compress_old_conversations():
    """扫描所有活跃会话，对超长对话进行摘要压缩（每 30 分钟执行）。"""
    try:
        import redis as redis_sync

        r = redis_sync.Redis(
            host=REDIS_CONFIG["host"],
            port=REDIS_CONFIG["port"],
            db=REDIS_CONFIG["db"],
            password=REDIS_CONFIG["password"] or None,
            socket_connect_timeout=3,
        )

        max_turns = REDIS_CONFIG.get("max_history_turns", 20)
        compressed_count = 0

        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match="session:*:messages", count=100)
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                session_id = key_str.split(":")[1]
                msg_count = r.llen(key_str)
                if msg_count > max_turns * 2:
                    _compress_session(session_id)
                    compressed_count += 1
            if cursor == 0:
                break

        logger.info(f"CompressConversations: checked sessions, compressed={compressed_count}")
        return {"compressed": compressed_count}
    except Exception as e:
        logger.error(f"CompressConversations failed: {e}")
        return {"error": str(e)}


@app.task(name="celery_tasks.memory.compress_single_session")
def compress_single_session(session_id: str):
    """压缩单个会话的对话历史（由请求路径触发）。"""
    try:
        _compress_session(session_id)
    except Exception as e:
        logger.error(f"CompressSingleSession({session_id}) failed: {e}")


def _compress_session(session_id: str):
    """对单个会话执行摘要压缩（同步版，在 Celery worker 中运行）。"""
    import json
    import redis as redis_sync
    from openai import OpenAI
    from config import LLM_CONFIG

    r = redis_sync.Redis(
        host=REDIS_CONFIG["host"],
        port=REDIS_CONFIG["port"],
        db=REDIS_CONFIG["db"],
        password=REDIS_CONFIG["password"] or None,
        socket_connect_timeout=3,
    )

    key = f"session:{session_id}:messages"
    max_turns = REDIS_CONFIG.get("max_history_turns", 20)
    raw = r.lrange(key, 0, -1)
    messages = [json.loads(m) for m in raw]

    if len(messages) <= max_turns * 2:
        return

    recent = messages[:max_turns * 2]
    old = messages[max_turns * 2:]

    prompt = f"""请将以下对话历史压缩为一段简短的摘要（不超过200字），保留关键信息：
用户查询意图、已提取的槽位、用户的偏好倾向。

对话历史：
{json.dumps(old, ensure_ascii=False, indent=2)}

请直接输出摘要文本："""

    try:
        client = llm_manager.get_client("default")
        response = client.chat.completions.create(
            model=LLM_CONFIG["model"],
            messages=[{"role": "user", "content": prompt}],
            temperature=LLM_CONFIG["summary_temperature"],
            max_tokens=300,
            timeout=30.0,
        )
        summary = response.choices[0].message.content

        # 保存摘要
        summary_key = f"session:{session_id}:summary"
        r.set(summary_key, summary, ex=REDIS_CONFIG["session_ttl"])

        # 截断消息列表
        r.delete(key)
        for msg in reversed(recent):
            r.lpush(key, json.dumps(msg, ensure_ascii=False))
        r.expire(key, REDIS_CONFIG["session_ttl"])

        logger.info(f"Session {session_id}: compressed {len(old)} messages via Celery")
    except Exception as e:
        logger.error(f"Session {session_id}: compression failed: {e}")


@app.task(name="celery_tasks.memory.save_long_term_memory")
def save_long_term_memory(user_id: int, session_id: str, query: str, intent: str,
                          slots: Dict[str, Any], tool_name: str, result_summary: str,
                          final_answer: str, duration_ms: int, success: bool):
    """异步持久化完整查询记录到 MySQL（fire-and-forget）。"""
    try:
        with get_session() as s:
            log = QueryLog(
                user_id=user_id,
                session_id=session_id,
                query=query,
                intent=intent,
                slots=slots,
                tool_name=tool_name,
                tool_result_summary=(result_summary or "")[:1000],
                final_answer=(final_answer or "")[:2000],
                duration_ms=duration_ms,
                success=success,
            )
            s.add(log)
        logger.info(f"SaveLongTermMemory: user={user_id}, intent={intent}")
    except Exception as e:
        logger.error(f"SaveLongTermMemory failed: {e}")


@app.task(name="celery_tasks.memory.save_conversation_history")
def save_conversation_history(user_id: int, session_id: str, role: str,
                              content: str, intent: str = "", slots: Dict[str, Any] = None):
    """异步持久化单条对话消息到 MySQL（fire-and-forget）。"""
    try:
        with get_session() as s:
            msg = ConversationHistory(
                user_id=user_id,
                session_id=session_id,
                role=role,
                content=content[:2000],
                intent=intent,
                slots=slots or {},
            )
            s.add(msg)
    except Exception as e:
        logger.error(f"SaveConversationHistory failed: {e}")


@app.task(name="celery_tasks.memory.extract_and_save_preferences")
def extract_and_save_preferences(user_id: int, intent: str, slots: Dict[str, Any]):
    """异步从查询槽位中提取并保存用户偏好到 MySQL。"""
    category_map = {
        "flight_ticket": [("transport", "preferred_departure_city", "departure_city"),
                          ("transport", "preferred_arrival_city", "arrival_city")],
        "train_ticket": [("transport", "preferred_departure_city", "departure_city"),
                         ("transport", "preferred_arrival_city", "arrival_city")],
        "hotel_query": [("hotel", "preferred_address", "address")],
        "attraction_recommend": [("destination", "interested_city", "city")],
        "tour_group_query": [("destination", "interested_city", "city")],
    }

    mappings = category_map.get(intent, [])
    try:
        with get_session() as s:
            for category, key, slot_name in mappings:
                value = slots.get(slot_name)
                if not value:
                    continue
                existing = s.query(UserPreference).filter_by(
                    user_id=user_id, category=category, key=key
                ).first()
                if not existing:
                    pref = UserPreference(
                        user_id=user_id, category=category, key=key,
                        value=str(value), confidence=0.5, source="inferred",
                    )
                    s.add(pref)
                elif existing.value != str(value):
                    existing.value = str(value)
                    existing.confidence = min(existing.confidence + 0.2, 1.0)
    except Exception as e:
        logger.error(f"ExtractAndSavePreferences failed: {e}")
