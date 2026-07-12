"""记忆服务 - 短期记忆（会话上下文）+ 长期记忆（用户偏好/历史）"""
import asyncio
import json
import time
from collections import OrderedDict
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional
from loguru import logger
import redis.asyncio as aioredis

from config import REDIS_CONFIG, MEMORY_CONFIG
from models.orm_models import (
    get_session, UserPreference, QueryLog, ConversationHistory,
)


class MemoryService:
    """统一记忆管理：短期记忆 (Redis) + 长期记忆 (MySQL)"""

    _MAX_FALLBACK_SESSIONS = 1000

    def __init__(self):
        self.redis: Optional[aioredis.Redis] = None
        self.redis_available = False
        self._fallback_store: Dict[str, List[Dict]] = {}
        self._fallback_access: OrderedDict = OrderedDict()
        self._last_redis_attempt = 0.0
        self.max_turns = REDIS_CONFIG["max_history_turns"]
        self.session_ttl = REDIS_CONFIG["session_ttl"]

    async def _ensure_redis(self):
        now = time.time()
        if self.redis is not None and self.redis_available:
            return
        if self.redis is not None and not self.redis_available:
            if now - self._last_redis_attempt < 30:
                return
        self._last_redis_attempt = now
        try:
            self.redis = await aioredis.from_url(
                f"redis://{REDIS_CONFIG['host']}:{REDIS_CONFIG['port']}/{REDIS_CONFIG['db']}",
                password=REDIS_CONFIG["password"] or None,
                decode_responses=True,
                socket_connect_timeout=3,
            )
            await self.redis.ping()
            self.redis_available = True
            logger.info("Redis connected for short-term memory")
        except Exception as e:
            logger.warning(f"Redis unavailable, using in-memory fallback: {e}")
            self.redis_available = False

    # ============ 短期记忆（会话上下文） ============

    async def get_short_term(self, session_id: str) -> List[Dict[str, str]]:
        """获取会话短期记忆（最近N轮对话）"""
        await self._ensure_redis()
        if self.redis_available:
            try:
                key = f"session:{session_id}:messages"
                raw = await self.redis.lrange(key, 0, self.max_turns * 2 - 1)
                messages = [json.loads(m) for m in reversed(raw)]
                return messages
            except Exception as e:
                logger.error(f"Redis get short-term failed: {e}")
        return self._fallback_store.get(session_id, [])

    async def add_short_term(self, session_id: str, role: str, content: str,
                             intent: str = "", slots: Dict = None):
        """添加一条消息到短期记忆"""
        await self._ensure_redis()
        msg = {
            "role": role,
            "content": content,
            "intent": intent,
            "slots": slots or {},
            "timestamp": datetime.now().isoformat(),
        }

        redis_ok = False
        if self.redis_available:
            try:
                key = f"session:{session_id}:messages"
                await self.redis.lpush(key, json.dumps(msg, ensure_ascii=False))
                await self.redis.ltrim(key, 0, self.max_turns * 2 - 1)
                await self.redis.expire(key, self.session_ttl)
                redis_ok = True
            except Exception as e:
                logger.error(f"Redis add short-term failed: {e}")

        if not redis_ok:
            self._add_to_fallback(session_id, msg)

    def _add_to_fallback(self, session_id: str, msg: Dict):
        """写入内存回退存储，含 LRU 淘汰防止内存泄漏"""
        if session_id not in self._fallback_store:
            self._fallback_store[session_id] = []
            self._fallback_access[session_id] = time.time()
            while len(self._fallback_store) > self._MAX_FALLBACK_SESSIONS:
                oldest = min(self._fallback_access, key=self._fallback_access.get)
                del self._fallback_store[oldest]
                del self._fallback_access[oldest]
                logger.warning(f"Fallback store LRU evicted session: {oldest}")
        self._fallback_store[session_id].insert(0, msg)
        self._fallback_access[session_id] = time.time()
        if len(self._fallback_store[session_id]) > self.max_turns * 2:
            self._fallback_store[session_id] = self._fallback_store[session_id][:self.max_turns * 2]

    async def summarize_and_compress(self, session_id: str, llm_call_fn) -> str:
        """当对话过长时，对历史进行摘要压缩"""
        messages = await self.get_short_term(session_id)
        if len(messages) <= self.max_turns:
            return ""

        old_messages = messages[self.max_turns:]
        recent = messages[:self.max_turns]

        summary_prompt = f"""请将以下对话历史压缩为一段简短的摘要（不超过200字），保留关键信息：
用户查询意图、已提取的槽位、用户的偏好倾向。

对话历史：
{json.dumps(old_messages, ensure_ascii=False, indent=2)}

请直接输出摘要文本："""

        try:
            summary = await llm_call_fn(summary_prompt)
            await self._ensure_redis()
            if self.redis_available:
                key = f"session:{session_id}:summary"
                await self.redis.set(key, summary, ex=self.session_ttl)
            logger.info(f"Session {session_id}: compressed {len(old_messages)} messages into summary")
            return summary
        except Exception as e:
            logger.error(f"Summarization failed: {e}")
            return ""

    async def get_session_summary(self, session_id: str) -> str:
        """获取会话摘要"""
        await self._ensure_redis()
        if self.redis_available:
            try:
                key = f"session:{session_id}:summary"
                return await self.redis.get(key) or ""
            except Exception:
                pass
        return ""

    async def clear_short_term(self, session_id: str):
        """清除会话短期记忆"""
        await self._ensure_redis()
        if self.redis_available:
            try:
                keys = [f"session:{session_id}:messages", f"session:{session_id}:summary"]
                await self.redis.delete(*keys)
            except Exception:
                pass
        self._fallback_store.pop(session_id, None)
        self._fallback_access.pop(session_id, None)

    # ============ 长期记忆（用户偏好/历史） ============

    async def save_preference(self, user_id: int, category: str, key: str,
                            value: str, confidence: float = 1.0, source: str = "inferred"):
        """保存用户偏好（长期记忆）"""
        def _sync():
            with get_session() as session:
                existing = session.query(UserPreference).filter_by(
                    user_id=user_id, category=category, key=key
                ).first()
                if existing:
                    existing.value = value
                    existing.confidence = max(existing.confidence, confidence)
                    existing.updated_at = datetime.now()
                else:
                    pref = UserPreference(
                        user_id=user_id, category=category, key=key,
                        value=value, confidence=confidence, source=source,
                    )
                    session.add(pref)
        await asyncio.to_thread(_sync)
        logger.info(f"User {user_id}: saved preference [{category}]{key}={value}")

    async def get_preferences(self, user_id: int, category: str = None) -> List[Dict]:
        """获取用户偏好"""
        def _sync():
            with get_session() as session:
                q = session.query(UserPreference).filter_by(user_id=user_id)
                if category:
                    q = q.filter_by(category=category)
                prefs = q.order_by(UserPreference.confidence.desc()).all()
                return [
                    {"category": p.category, "key": p.key, "value": p.value,
                     "confidence": p.confidence, "source": p.source}
                    for p in prefs
                ]
        return await asyncio.to_thread(_sync)

    async def get_preference_context(self, user_id: int) -> str:
        """将用户偏好格式化为 Prompt 可用的上下文文本"""
        prefs = await self.get_preferences(user_id)
        if not prefs:
            return ""
        lines = ["\n[用户长期偏好]"]
        for p in prefs:
            lines.append(f"- [{p['category']}] {p['key']}: {p['value']} (置信度: {p['confidence']:.0%})")
        return "\n".join(lines)

    async def save_query_log(self, user_id: int, session_id: str, query: str,
                           intent: str, slots: Dict, tool_name: str,
                           result_summary: str, final_answer: str,
                           duration_ms: int, success: bool):
        """保存查询日志到长期记忆"""
        def _sync():
            with get_session() as s:
                log = QueryLog(
                    user_id=user_id,
                    session_id=session_id,
                    query=query,
                    intent=intent,
                    slots=slots,
                    tool_name=tool_name,
                    tool_result_summary=result_summary[:1000] if result_summary else "",
                    final_answer=final_answer[:2000] if final_answer else "",
                    duration_ms=duration_ms,
                    success=success,
                )
                s.add(log)
        await asyncio.to_thread(_sync)

    async def get_recent_queries(self, user_id: int, intent: str = None, limit: int = 10) -> List[Dict]:
        """获取用户最近查询记录"""
        def _sync():
            with get_session() as s:
                q = s.query(QueryLog).filter_by(user_id=user_id, success=True)
                if intent:
                    q = q.filter_by(intent=intent)
                logs = q.order_by(QueryLog.created_at.desc()).limit(limit).all()
                return [
                    {"query": l.query, "intent": l.intent, "slots": l.slots,
                     "tool_name": l.tool_name, "created_at": l.created_at.isoformat()}
                    for l in logs
                ]
        return await asyncio.to_thread(_sync)

    async def get_similar_queries(self, user_id: int, intent: str) -> List[Dict]:
        """获取用户同类意图的历史查询（用于个性化推荐）"""
        return await self.get_recent_queries(user_id, intent=intent, limit=5)

    async def extract_and_save_preferences(self, user_id: int, intent: str, slots: Dict):
        """从查询槽位中自动提取并保存用户偏好"""
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
        for category, key, slot_name in mappings:
            value = slots.get(slot_name)
            if value:
                existing = await self.get_preferences(user_id, category=category)
                same_key = [p for p in existing if p["key"] == key]
                if not same_key:
                    await self.save_preference(user_id, category, key, str(value),
                                               confidence=0.5, source="inferred")
                elif same_key[0]["value"] != str(value):
                    await self.save_preference(user_id, category, key, str(value),
                                               confidence=min(same_key[0]["confidence"] + 0.2, 1.0),
                                               source="inferred")

    async def save_conversation(self, user_id: int, session_id: str, role: str,
                              content: str, intent: str = "", slots: Dict = None):
        """持久化保存对话记录到 MySQL"""
        def _sync():
            with get_session() as s:
                msg = ConversationHistory(
                    user_id=user_id,
                    session_id=session_id,
                    role=role,
                    content=content[:2000],
                    intent=intent,
                    slots=slots,
                )
                s.add(msg)
        await asyncio.to_thread(_sync)

    async def get_conversation_history(self, user_id: int, session_id: str,
                                      limit: int = 50) -> List[Dict]:
        """从 MySQL 获取对话历史"""
        def _sync():
            with get_session() as s:
                msgs = s.query(ConversationHistory).filter_by(
                    user_id=user_id, session_id=session_id
                ).order_by(ConversationHistory.created_at.asc()).limit(limit).all()
                return [
                    {"role": m.role, "content": m.content,
                     "intent": m.intent, "created_at": m.created_at.isoformat()}
                    for m in msgs
                ]
        return await asyncio.to_thread(_sync)


# 全局单例
memory_service = MemoryService()
