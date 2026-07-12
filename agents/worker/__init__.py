"""Worker Agent 模块 —— 领域子智能体注册与发现。"""
from typing import Dict, List

# 意图 → Worker 名称映射表
INTENT_TO_WORKER: Dict[str, str] = {
    "weather_query": "worker.weather",
    "train_ticket": "worker.train_ticket",
    "flight_ticket": "worker.flight",
    "ship_ticket": "worker.ticket",
    "concert_ticket": "worker.ticket",
    "attraction_recommend": "worker.attraction",
    "hotel_query": "worker.hotel",
    "car_rental_query": "worker.car_rental",
    "insurance_query": "worker.insurance",
    "tour_group_query": "worker.tour_group",
}


def get_worker_name(intent: str) -> str:
    """根据意图获取对应的 Worker Agent 名称。"""
    return INTENT_TO_WORKER.get(intent, f"worker.{intent}")


def list_all_worker_names() -> List[str]:
    """列出所有已定义的 Worker Agent 名称。"""
    return sorted(set(INTENT_TO_WORKER.values()))
