"""全局配置文件 —— 从 .env 加载环境变量，集中管理 LLM、数据库、记忆与意图体系配置。"""

import os
from pathlib import Path
from dotenv import load_dotenv

# 使用绝对路径确保无论在哪个目录启动都能加载 .env
load_dotenv(dotenv_path=Path(__file__).resolve().parent / ".env", override=True)

# ==================== LLM 配置 ====================
LLM_CONFIG = {
    "model": os.getenv("LLM_MODEL", "qwen-turbo"),
    "api_key": os.getenv("APP_API_KEY", ""),
    "base_url": os.getenv("BASE_URL", ""),
    "temperature": 0.1,
    "max_tokens": 4096,
    # 各场景独立温度配置
    "answer_temperature": 0.3,
    "recommend_temperature": 0.7,
    "summary_temperature": 0.1,
    # 嵌入模型
    "embedding_model": "text-embedding-v3",
}

# 意图识别使用更快的小模型，响应时间远低于 max 系列
INTENT_LLM_CONFIG = {
    "model": os.getenv("INTENT_MODEL", "qwen-turbo"),
    "api_key": os.getenv("APP_API_KEY", ""),
    "base_url": os.getenv("BASE_URL", ""),
    "temperature": 0.05,
    "max_tokens": 2048,
}

# ==================== MySQL 配置 ====================
MYSQL_CONFIG = {
    "host": os.getenv("MYSQL_HOST", "localhost"),
    "port": int(os.getenv("MYSQL_PORT", "3307")),
    "user": os.getenv("MYSQL_USER", "root"),
    "password": os.getenv("MYSQL_PASSWORD", "12345678"),
    "database": os.getenv("MYSQL_DATABASE", "Tourism Assistant"),
    "charset": "utf8mb4",
}

# SQLAlchemy 连接串
MYSQL_URL = (
    f"mysql+pymysql://{MYSQL_CONFIG['user']}:{MYSQL_CONFIG['password']}"
    f"@{MYSQL_CONFIG['host']}:{MYSQL_CONFIG['port']}"
    f"/{MYSQL_CONFIG['database']}?charset={MYSQL_CONFIG['charset']}"
)

# ==================== Milvus 配置 ====================
MILVUS_CONFIG = {
    "host": os.getenv("MILVUS_HOST", "localhost"),
    "port": int(os.getenv("MILVUS_PORT", "19530")),
    "database_name": os.getenv("MILVUS_DB", "itcast"),
    "collection_name": os.getenv("MILVUS_COLLECTION", "tourism_attractions"),
    "nprobe": 10,
    "top_k": 5,
    "metric_type": "IP",
}

# ==================== Redis 配置（短期记忆） ====================
REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", "6379")),
    "db": int(os.getenv("REDIS_DB", "0")),
    "password": os.getenv("REDIS_PASSWORD", ""),
    "session_ttl": 3600,          # 会话过期时间（秒）
    "max_history_turns": 20,      # 最大对话轮数
}

# ==================== 长期记忆配置 ====================
MEMORY_CONFIG = {
    "max_preferences": 10,         # 最多保存的用户偏好数量
    "max_history_queries": 50,     # 最多保存的历史查询数
    "relevance_threshold": 0.6,    # 记忆检索相关度阈值
    "auto_save_threshold": 3,      # 同类意图出现N次后自动保存为偏好
}

# ==================== 意图体系 ====================
SUPPORTED_INTENTS = [
    "flight_ticket",
    "train_ticket",
    "ship_ticket",
    "concert_ticket",
    "weather_query",
    "tour_group_query",
    "hotel_query",
    "car_rental_query",
    "insurance_query",
    "attraction_recommend",
]

INTENT_CN_MAP = {
    "flight_ticket": "飞机票查询",
    "train_ticket": "火车票查询",
    "ship_ticket": "船票查询",
    "concert_ticket": "演唱会票查询",
    "weather_query": "天气查询",
    "tour_group_query": "旅行团查询",
    "hotel_query": "酒店查询",
    "car_rental_query": "租车查询",
    "insurance_query": "保险查询",
    "attraction_recommend": "景点推荐",
}

INTENT_SLOTS = {
    "flight_ticket": ["departure_city", "arrival_city", "departure_date"],
    "train_ticket": ["departure_city", "arrival_city", "departure_date"],
    "ship_ticket": ["departure_port", "arrival_port", "departure_date"],
    "concert_ticket": ["city", "concert_name", "date_range"],
    "weather_query": ["city", "date"],
    "tour_group_query": ["city"],
    "hotel_query": ["address", "time"],
    "car_rental_query": ["city", "pickup_date", "return_date", "car_type"],
    "insurance_query": ["insurance_type", "travel_date", "destination"],
    "attraction_recommend": ["city", "days"],
}

# 可选的附加槽位 —— 不阻塞追问，有则提取
INTENT_OPTIONAL_SLOTS = {
    "flight_ticket": ["return_date"],
}

# ==================== MCP 服务配置 ====================
MCP_SERVERS = {
    "12306-mcp": {
        "type": "streamable_http",
        "url": "https://mcp.api-inference.modelscope.net/7235d1c9c2cd4a/mcp",
    },
}

# ==================== 天气 API 配置（和风天气） ====================
WEATHER_API_CONFIG = {
    "key": os.getenv("WEATHER_API_KEY", ""),
    "base_url": os.getenv("WEATHER_API_BASE_URL", "https://n63qqt6pg5.re.qweatherapi.com"),
    "now_path": "/v7/weather/now",
    "forecast_path": "/v7/weather/30d",
}

# ==================== 工具路由 ====================
TOOL_ROUTING = {
    "flight_ticket": "flight_query",
    "train_ticket": "train_ticket_query",
    "ship_ticket": "ticket_query",
    "concert_ticket": "ticket_query",
    "weather_query": "weather_query",
    "tour_group_query": "tour_group_query",
    "hotel_query": "hotel_query",
    "car_rental_query": "car_rental_query",
    "insurance_query": "insurance_query",
    "attraction_recommend": "attraction_recommend",
}

# ==================== Celery Redis 配置（独立 DB，避免与业务 Redis 资源竞争） ====================
CELERY_REDIS_CONFIG = {
    "host": os.getenv("REDIS_HOST", "localhost"),
    "port": int(os.getenv("REDIS_PORT", "6379")),
    "db": int(os.getenv("CELERY_REDIS_DB", "1")),  # 默认 DB=1，与业务 DB=0 隔离
    "password": os.getenv("REDIS_PASSWORD", ""),
}

# ==================== Agent Bus（多 Agent 通信配置） ====================
AGENT_BUS_CONFIG = {
    "redis_channel_prefix": "agent",
    "agent_response_ttl": 120,
    "agent_heartbeat_interval": 10,
    "agent_heartbeat_timeout": 30,
    "default_task_timeout": 120.0,
    "max_retries": 2,
}

# ==================== 意图优先级（DAG 调度排序用） ====================
# 数值越小优先级越高，同层内按优先级排序
INTENT_PRIORITY = {
    "flight_ticket": 1,
    "train_ticket": 2,
    "ship_ticket": 3,
    "weather_query": 4,
    "hotel_query": 5,
    "tour_group_query": 6,
    "attraction_recommend": 7,
    "concert_ticket": 8,
    "car_rental_query": 9,
    "insurance_query": 10,
}

# ==================== Token 预算配置 ====================
MAX_CONTEXT_TOKENS = 4000
TOKEN_WARNING_THRESHOLD = 3000

# ==================== 分层缓存 TTL 配置 ====================
# static: 静态数据（景点/酒店）— 30 分钟
# short: 短期数据（天气）— 5 分钟
# realtime: 实时数据（机票/火车票）— 不缓存
CACHE_TIERS = {
    "flight_ticket": "realtime",
    "train_ticket": "realtime",
    "ship_ticket": "realtime",
    "concert_ticket": "realtime",
    "weather_query": "short",
    "tour_group_query": "static",
    "hotel_query": "static",
    "attraction_recommend": "static",
    "car_rental_query": "realtime",
    "insurance_query": "realtime",
}

CACHE_TIER_TTL = {
    "static": 1800,   # 30 分钟
    "short": 300,     # 5 分钟
    "realtime": 0,    # 不缓存
}
