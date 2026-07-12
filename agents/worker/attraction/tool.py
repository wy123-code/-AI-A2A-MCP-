"""景点推荐工具 —— 通过 Milvus 向量检索 + LLM 生成个性化景点推荐文案。"""

import asyncio
import json
from typing import Any, Dict, List
from loguru import logger
from config import LLM_CONFIG, MILVUS_CONFIG
from llm.client_pool import llm_manager
from db.milvus_client import milvus_client
from prompts import RECOMMENDATION_PROMPT


async def _get_embedding(text: str) -> List[float]:
    """Generate embedding vector for query text using LLM."""
    client = llm_manager.get_client("default")
    try:
        response = await asyncio.to_thread(
            lambda: client.embeddings.create(
                model=LLM_CONFIG["embedding_model"],
                input=text,
                timeout=30.0,
            )
        )
        return response.data[0].embedding
    except Exception as e:
        logger.warning(f"Embedding generation failed: {e}, using fallback")
        return []


async def recommend_attractions(intent: str, slots: Dict[str, Any]) -> Dict[str, Any]:
    city = slots.get("city", "")
    days = int(slots.get("days", 1))
    user_query = slots.get("_query", "")  # 用户原始输入

    search_text = f"{city} 旅游景点 {user_query}".strip()
    query_vector = await _get_embedding(search_text)

    retrieved = []
    if query_vector:
        filter_expr = f'city == "{city}"' if city else None
        retrieved = await milvus_client.search(
            query_vector=query_vector,
            top_k=MILVUS_CONFIG["top_k"],
            filter_expr=filter_expr,
        )

    retrieved_str = json.dumps(retrieved, ensure_ascii=False, indent=2) if retrieved else "未检索到相关景点信息（Milvus不可用或结果为空）"

    prompt = RECOMMENDATION_PROMPT.format(
        query=user_query or f"推荐{city}的景点",
        city=city,
        days=days,
        retrieved_attractions=retrieved_str,
    )

    logger.info(f"AttractionRecommend: querying for city={city}, days={days}")
    client = llm_manager.get_client("default")

    try:
        response = await asyncio.to_thread(
            lambda: client.chat.completions.create(
                model=LLM_CONFIG["model"],
                messages=[{"role": "user", "content": prompt}],
                temperature=LLM_CONFIG["recommend_temperature"],
                max_tokens=LLM_CONFIG["max_tokens"],
                timeout=60.0,
            )
        )
        content = response.choices[0].message.content
        logger.info(f"AttractionRecommend: got recommendation ({len(content)} chars)")
        return {"success": True, "error": None, "data": {"recommendation": content}}
    except Exception as e:
        logger.error(f"AttractionRecommend failed: {e}")
        return {"success": False, "error": str(e), "data": {}}
