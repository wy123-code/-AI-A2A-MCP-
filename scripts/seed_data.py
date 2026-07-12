"""数据库初始化与示例数据填充 —— MySQL 业务表 + ORM 表 + Milvus 景点向量库。"""
import json
import random
import sys
from datetime import date, datetime, timedelta

from loguru import logger
import pymysql
from pymysql.cursors import DictCursor

from config import MYSQL_CONFIG, MILVUS_CONFIG, LLM_CONFIG
from models.orm_models import (
    init_db, get_session, User, UserPreference, ConversationHistory, QueryLog,
)


# ============================================================
# 1. MySQL 业务表建表 SQL
# ============================================================
BUSINESS_TABLES_SQL = [
    """CREATE TABLE IF NOT EXISTS `weather` (
        id INT AUTO_INCREMENT PRIMARY KEY,
        city VARCHAR(50) NOT NULL,
        date DATE NOT NULL,
        temperature_high INT,
        temperature_low INT,
        weather_desc VARCHAR(100),
        humidity INT,
        wind_speed VARCHAR(50),
        INDEX idx_city_date (city, date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """CREATE TABLE IF NOT EXISTS `tour_group` (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        destination VARCHAR(100) NOT NULL,
        departure_city VARCHAR(50),
        start_date DATE,
        end_date DATE,
        duration_days INT,
        price DECIMAL(10,2),
        max_participants INT,
        current_participants INT,
        description TEXT,
        INDEX idx_dest_date (destination, start_date)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """CREATE TABLE IF NOT EXISTS `hotel` (
        id INT AUTO_INCREMENT PRIMARY KEY,
        name VARCHAR(100) NOT NULL,
        city VARCHAR(50) NOT NULL,
        district VARCHAR(100),
        star_rating INT,
        price_per_night DECIMAL(10,2),
        available_rooms INT,
        amenities TEXT,
        address VARCHAR(200),
        INDEX idx_city (city)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """CREATE TABLE IF NOT EXISTS `car_rental` (
        id INT AUTO_INCREMENT PRIMARY KEY,
        city VARCHAR(50) NOT NULL,
        car_model VARCHAR(100),
        car_type VARCHAR(50),
        price_per_day DECIMAL(10,2),
        available_from DATE,
        available_to DATE,
        company VARCHAR(100),
        INDEX idx_city_date (city, available_from)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",

    """CREATE TABLE IF NOT EXISTS `insurance` (
        id INT AUTO_INCREMENT PRIMARY KEY,
        insurance_type VARCHAR(50) NOT NULL,
        name VARCHAR(100) NOT NULL,
        coverage TEXT,
        price DECIMAL(10,2),
        duration_days INT,
        provider VARCHAR(100),
        INDEX idx_type (insurance_type)
    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""",
]


def _get_raw_connection():
    return pymysql.connect(
        host=MYSQL_CONFIG["host"],
        port=MYSQL_CONFIG["port"],
        user=MYSQL_CONFIG["user"],
        password=MYSQL_CONFIG["password"],
        database=MYSQL_CONFIG["database"],
        charset=MYSQL_CONFIG["charset"],
        cursorclass=DictCursor,
    )


def create_business_tables():
    """创建 MySQL 业务表（weather / tour_group / hotel / car_rental / insurance）。"""
    conn = _get_raw_connection()
    try:
        with conn.cursor() as cur:
            for sql in BUSINESS_TABLES_SQL:
                cur.execute(sql)
        conn.commit()
        logger.info("Business tables created (5 tables)")
    finally:
        conn.close()


def truncate_business_tables():
    """清空业务表数据（幂等插数用）。"""
    tables = ["weather", "tour_group", "hotel", "car_rental", "insurance"]
    conn = _get_raw_connection()
    try:
        with conn.cursor() as cur:
            for t in tables:
                cur.execute(f"TRUNCATE TABLE `{t}`")
        conn.commit()
        logger.info("Business tables truncated")
    finally:
        conn.close()


# ============================================================
# 2. MySQL 业务数据
# ============================================================

CITIES_WEATHER = ["北京", "上海", "广州", "成都", "杭州", "西安", "三亚", "昆明", "哈尔滨", "拉萨"]
WEATHER_DESC_POOL = ["晴", "多云", "阴", "小雨", "中雨", "阵雨", "雷阵雨", "小雪", "雾", "晴间多云"]

def generate_weather_data():
    """生成 10 个城市未来 7 天天气数据。"""
    rows = []
    today = date.today()
    for city in CITIES_WEATHER:
        for offset in range(7):
            d = today + timedelta(days=offset)
            if city in ("三亚", "广州"):
                high = random.randint(28, 35)
                low = random.randint(20, 26)
                desc = random.choice(["晴", "多云", "阵雨", "雷阵雨"])
                humidity = random.randint(60, 90)
                wind = f"{random.choice(['东南', '南', '西南'])}风 {random.randint(2,5)}级"
            elif city in ("哈尔滨", "拉萨"):
                high = random.randint(10, 22)
                low = random.randint(-2, 10)
                desc = random.choice(["晴", "多云", "阴", "小雪"])
                humidity = random.randint(20, 50)
                wind = f"{random.choice(['西北', '北', '东北'])}风 {random.randint(2,5)}级"
            else:
                high = random.randint(18, 30)
                low = random.randint(8, 20)
                desc = random.choice(WEATHER_DESC_POOL)
                humidity = random.randint(30, 70)
                wind = f"{random.choice(['南', '北', '东', '西'])}风 {random.randint(1,4)}级"
            rows.append((city, d.strftime("%Y-%m-%d"), high, low, desc, humidity, wind))
    return rows


# 酒店和旅行团数据改为从 generate_city_data 动态生成（每个城市 ≥15 条）
def _get_hotels_and_tours():
    """延迟导入，避免 seed_data 初始化时的循环依赖。"""
    from generate_city_data import generate_hotels, generate_tour_groups
    import random
    random.seed(42)
    return generate_hotels(), generate_tour_groups()

CAR_RENTALS = [
    ("北京", "丰田卡罗拉", "经济型", 180.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("北京", "大众帕萨特", "舒适型", 320.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("北京", "别克GL8", "商务型", 550.00, "2026-05-20", "2026-12-31", "一嗨租车"),
    ("上海", "丰田卡罗拉", "经济型", 160.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("上海", "特斯拉Model 3", "新能源", 450.00, "2026-05-20", "2026-12-31", "一嗨租车"),
    ("上海", "奔驰C级", "豪华型", 680.00, "2026-05-20", "2026-12-31", "赫兹租车"),
    ("广州", "日产轩逸", "经济型", 150.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("成都", "哈弗H6", "SUV", 280.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("三亚", "宝马X3", "豪华SUV", 750.00, "2026-05-20", "2026-12-31", "赫兹租车"),
    ("三亚", "大众宝来", "经济型", 160.00, "2026-05-20", "2026-12-31", "一嗨租车"),
    ("杭州", "丰田凯美瑞", "舒适型", 300.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("西安", "比亚迪秦", "新能源", 220.00, "2026-05-20", "2026-12-31", "一嗨租车"),
    ("昆明", "哈弗大狗", "SUV", 260.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("哈尔滨", "大众途观", "SUV", 350.00, "2026-05-20", "2026-12-31", "神州租车"),
    ("拉萨", "丰田普拉多", "越野", 680.00, "2026-05-20", "2026-12-31", "神州租车"),
]

INSURANCES = [
    ("旅游意外险", "国内旅游意外险-基础版", "意外伤害20万,意外医疗2万,紧急救援", 30.00, 7, "平安保险"),
    ("旅游意外险", "国内旅游意外险-升级版", "意外伤害50万,意外医疗5万,紧急救援,航班延误", 80.00, 15, "平安保险"),
    ("旅游意外险", "国内旅游意外险-尊享版", "意外伤害100万,意外医疗10万,紧急救援,航班延误,行李丢失", 150.00, 30, "中国人寿"),
    ("旅游意外险", "境外旅游意外险-亚洲版", "意外伤害50万,意外医疗10万,紧急救援,航班延误,证件丢失", 120.00, 15, "太平洋保险"),
    ("旅游意外险", "境外旅游意外险-全球版", "意外伤害100万,意外医疗20万,紧急救援,航班延误,行李丢失,证件丢失", 280.00, 30, "太平洋保险"),
    ("航班延误险", "航班延误险-国内", "延误2小时赔付200元,延误4小时赔付500元", 20.00, 1, "众安保险"),
    ("航班延误险", "航班延误险-国际", "延误2小时赔付500元,延误4小时赔付1000元", 50.00, 1, "众安保险"),
    ("自驾游保险", "自驾游保险-基础版", "车辆故障救援,意外伤害20万,意外医疗2万", 60.00, 7, "平安保险"),
    ("自驾游保险", "自驾游保险-升级版", "车辆故障救援,意外伤害50万,意外医疗5万,三者险50万", 120.00, 15, "中国人寿"),
    ("高原旅游险", "高原旅游险", "高原反应医疗10万,意外伤害50万,紧急救援,直升机转运", 180.00, 15, "太平洋保险"),
]


def _insert_many(table: str, columns: str, rows: list[tuple]):
    conn = _get_raw_connection()
    try:
        placeholders = ", ".join(["%s"] * len(rows[0]))
        with conn.cursor() as cur:
            cur.executemany(f"INSERT INTO `{table}` ({columns}) VALUES ({placeholders})", rows)
        conn.commit()
        logger.info(f"  Inserted {len(rows)} rows into `{table}`")
    finally:
        conn.close()


def seed_business_tables():
    """填充业务表数据。"""
    truncate_business_tables()

    weather_rows = generate_weather_data()
    _insert_many("weather",
        "city, date, temperature_high, temperature_low, weather_desc, humidity, wind_speed",
        weather_rows)

    # 酒店和旅行团改为动态生成（每城市 ≥15 条，共 15 个城市）
    hotel_rows, tour_rows = _get_hotels_and_tours()
    _insert_many("tour_group",
        "name, destination, departure_city, start_date, end_date, duration_days, price, max_participants, current_participants, description",
        tour_rows)
    _insert_many("hotel",
        "name, city, district, star_rating, price_per_night, available_rooms, amenities, address",
        hotel_rows)

    _insert_many("car_rental",
        "city, car_model, car_type, price_per_day, available_from, available_to, company",
        CAR_RENTALS)

    _insert_many("insurance",
        "insurance_type, name, coverage, price, duration_days, provider",
        INSURANCES)

    logger.info("Business tables seeded successfully")


# ============================================================
# 3. ORM 表（用户 / 偏好 / 对话历史 / 查询日志）
# ============================================================

def seed_orm_tables():
    """通过 ORM 填充用户、偏好、对话和查询日志示例数据。"""
    # 先清空（按依赖顺序）
    with get_session() as s:
        s.query(QueryLog).delete()
        s.query(ConversationHistory).delete()
        s.query(UserPreference).delete()
        s.query(User).delete()
    logger.info("ORM tables truncated")

    # 创建用户
    users_data = [
        {"username": "zhangsan", "nickname": "张三", "email": "zhangsan@example.com", "phone": "13800001111"},
        {"username": "lisi", "nickname": "李四", "email": "lisi@example.com", "phone": "13800002222"},
        {"username": "wangwu", "nickname": "王五", "email": None, "phone": None},
        {"username": "zhaoliu", "nickname": "赵六", "email": "zhaoliu@test.com", "phone": "13900003333"},
        {"username": "sunqi", "nickname": "孙七", "email": None, "phone": "13900004444"},
    ]
    user_ids = {}
    with get_session() as s:
        for u in users_data:
            user = User(**u)
            s.add(user)
            s.flush()
            user_ids[u["username"]] = user.id
    logger.info(f"  Created {len(users_data)} users")

    # 创建偏好
    prefs = [
        (user_ids["zhangsan"], "destination", "preferred_city", "北京", 0.9, "explicit"),
        (user_ids["zhangsan"], "hotel", "preferred_star", "5", 0.7, "explicit"),
        (user_ids["zhangsan"], "transport", "preferred_departure_city", "上海", 0.6, "inferred"),
        (user_ids["lisi"], "destination", "interested_city", "三亚", 0.8, "explicit"),
        (user_ids["lisi"], "budget", "max_budget", "5000", 0.7, "explicit"),
        (user_ids["lisi"], "food", "preference", "海鲜", 0.9, "inferred"),
        (user_ids["wangwu"], "transport", "preferred_departure_city", "成都", 0.8, "inferred"),
        (user_ids["wangwu"], "destination", "interested_city", "拉萨", 0.85, "explicit"),
        (user_ids["wangwu"], "travel_style", "preference", "自然风光", 0.95, "explicit"),
        (user_ids["zhaoliu"], "destination", "preferred_city", "杭州", 0.7, "inferred"),
        (user_ids["zhaoliu"], "hotel", "preferred_price_range", "300-500", 0.6, "inferred"),
        (user_ids["sunqi"], "destination", "interested_destination", "新疆", 0.9, "explicit"),
        (user_ids["sunqi"], "travel_style", "preference", "探险", 0.85, "explicit"),
    ]
    with get_session() as s:
        for uid, cat, key, val, conf, src in prefs:
            s.add(UserPreference(user_id=uid, category=cat, key=key, value=val, confidence=conf, source=src))
    logger.info(f"  Created {len(prefs)} user preferences")

    # 创建对话历史和查询日志
    conversations = [
        (user_ids["zhangsan"], "sess_001", [
            ("user", "北京明天天气怎么样", "weather_query", {"city": "北京", "date": "2026-05-16"}),
            ("assistant", "北京明天晴转多云，最高温25°C，最低温15°C，适合出行。", "weather_query", None),
            ("user", "北京有什么好玩的景点推荐", "attraction_recommend", {"city": "北京", "preference": "历史文化"}),
            ("assistant", "推荐您游览故宫、长城、天坛、颐和园……", "attraction_recommend", None),
            ("user", "帮我查一下6月1号上海飞北京的机票", "flight_ticket", {"departure_city": "上海", "arrival_city": "北京", "departure_date": "2026-06-01"}),
            ("assistant", "为您查到3个航班：MU5101 08:00-10:30 ￥880……", "flight_ticket", None),
        ]),
        (user_ids["lisi"], "sess_002", [
            ("user", "三亚有哪些五星级酒店", "hotel_query", {"city": "三亚", "star_rating": "5"}),
            ("assistant", "三亚五星级酒店有：亚特兰蒂斯(￥2280/晚)、亚龙湾万豪(￥1580/晚)……", "hotel_query", None),
            ("user", "有没有去海南的旅行团", "tour_group_query", {"destination": "海南"}),
            ("assistant", "海南三亚阳光5日游，6月15日出发，￥4599……", "tour_group_query", None),
        ]),
        (user_ids["wangwu"], "sess_003", [
            ("user", "拉萨天气怎么样", "weather_query", {"city": "拉萨", "date": "2026-05-16"}),
            ("assistant", "拉萨今天晴，最高温18°C，最低温2°C，注意保暖。", "weather_query", None),
            ("user", "西藏有没有旅行团", "tour_group_query", {"destination": "西藏"}),
            ("assistant", "西藏拉萨朝圣7日游，7月1日出发，￥6999……", "tour_group_query", None),
            ("user", "我需要买旅游保险", "insurance_query", {"insurance_type": "高原旅游险"}),
            ("assistant", "推荐高原旅游险，￥180/15天，含高原反应医疗和直升机转运。", "insurance_query", None),
        ]),
    ]

    log_id = 0
    for uid, sid, msgs in conversations:
        for role, content, intent, slots in msgs:
            # 对话历史
            with get_session() as s:
                s.add(ConversationHistory(
                    user_id=uid, session_id=sid, role=role,
                    content=content, intent=intent, slots=slots,
                ))
            # 只为 user 消息建查询日志
            if role == "user":
                log_id += 1
                with get_session() as s:
                    s.add(QueryLog(
                        user_id=uid, session_id=sid, query=content,
                        intent=intent, slots=slots or {},
                        tool_name=intent, tool_result_summary="(示例结果摘要)",
                        final_answer="(示例回答)", duration_ms=random.randint(500, 3000),
                        success=True,
                    ))

    total_msgs = sum(len(msgs) for _, _, msgs in conversations)
    logger.info(f"  Created {total_msgs} conversation messages and {log_id} query logs")


# ============================================================
# 4. Milvus 景点向量数据（从 generate_attraction_data 动态加载）
# ============================================================

def _get_attractions():
    """延迟加载景点数据，优先使用扩展数据源。"""
    try:
        from generate_attraction_data import generate_attractions
        return generate_attractions()
    except ImportError:
        logger.warning("generate_attraction_data not available, using empty list")
        return []

# ============================================================
# 旧景点数据已迁移至 generate_attraction_data.py 统一管理
# ============================================================


def seed_milvus():
    """向 Milvus 写入景点向量数据（先生成 embedding，再插入）。"""
    from pymilvus import (
        connections, Collection, FieldSchema, CollectionSchema, DataType, utility,
    )
    from openai import OpenAI

    # 先调用一次 embedding 确定维度
    llm = OpenAI(api_key=LLM_CONFIG["api_key"], base_url=LLM_CONFIG["base_url"])
    try:
        resp = llm.embeddings.create(model="text-embedding-v3", input="test")
        dim = len(resp.data[0].embedding)
        logger.info(f"Detected embedding dimension: {dim}")
    except Exception as e:
        logger.error(f"Cannot determine embedding dimension: {e}")
        dim = 1024

    # 连接 Milvus
    connections.connect(
        alias="default",
        host=MILVUS_CONFIG["host"],
        port=MILVUS_CONFIG["port"],
        db_name=MILVUS_CONFIG["database_name"],
    )

    collection_name = "tourism_attractions"

    # 如果集合已存在则删除重建
    if utility.has_collection(collection_name):
        utility.drop_collection(collection_name)
        logger.info(f"Dropped existing collection: {collection_name}")

    fields = [
        FieldSchema(name="id", dtype=DataType.INT64, is_primary=True, auto_id=True),
        FieldSchema(name="embedding", dtype=DataType.FLOAT_VECTOR, dim=dim),
        FieldSchema(name="name", dtype=DataType.VARCHAR, max_length=200),
        FieldSchema(name="description", dtype=DataType.VARCHAR, max_length=1000),
        FieldSchema(name="tags", dtype=DataType.VARCHAR, max_length=200),
        FieldSchema(name="city", dtype=DataType.VARCHAR, max_length=50),
    ]
    schema = CollectionSchema(fields, description="Tourism attractions collection")
    collection = Collection(collection_name, schema)
    logger.info(f"Created Milvus collection `{collection_name}` with dim={dim}")

    # 生成所有 embedding
    attractions = _get_attractions()
    logger.info(f"Generating embeddings for {len(attractions)} attractions...")
    emb_list = []
    for i, (name, desc, tags, city) in enumerate(attractions):
        text = f"景点名称：{name}\n城市：{city}\n描述：{desc}\n标签：{tags}"
        try:
            resp = llm.embeddings.create(model="text-embedding-v3", input=text)
            emb = resp.data[0].embedding
            if len(emb) < dim:
                emb = emb + [0.0] * (dim - len(emb))
            elif len(emb) > dim:
                emb = emb[:dim]
            emb_list.append(emb)
            if (i + 1) % 10 == 0:
                logger.info(f"  Generated {i+1}/{len(attractions)} embeddings")
        except Exception as e:
            logger.error(f"Embedding failed for {name}: {e}")
            emb_list.append([0.0] * dim)

    # 插入数据（字段顺序: name, description, tags, city, embedding）
    names = [a[0] for a in attractions]
    descs = [a[1] for a in attractions]
    tags_list = [a[2] for a in attractions]
    cities = [a[3] for a in attractions]

    # 字段顺序: embedding, name, description, tags, city (id 是 auto_id)
    collection.insert([emb_list, names, descs, tags_list, cities])
    collection.flush()

    # 创建索引
    index_params = {
        "metric_type": "IP",
        "index_type": "IVF_FLAT",
        "params": {"nlist": 128},
    }
    collection.create_index("embedding", index_params)
    collection.load()
    logger.info(f"Milvus: inserted {len(attractions)} attractions into `{collection_name}` (dim={dim})")


# ============================================================
# 5. 主流程
# ============================================================

def ensure_database():
    """确保 MySQL 数据库存在，不存在则创建。"""
    try:
        conn = pymysql.connect(
            host=MYSQL_CONFIG["host"],
            port=MYSQL_CONFIG["port"],
            user=MYSQL_CONFIG["user"],
            password=MYSQL_CONFIG["password"],
            charset=MYSQL_CONFIG["charset"],
        )
        with conn.cursor() as cur:
            db_name = MYSQL_CONFIG["database"]
            cur.execute(
                f"CREATE DATABASE IF NOT EXISTS `{db_name}` "
                f"DEFAULT CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
            )
        conn.commit()
        conn.close()
        logger.info(f"Database `{db_name}` ensured")
    except Exception as e:
        logger.error(f"Failed to create database: {e}")
        raise


def main():
    logger.info("=" * 60)
    logger.info("开始初始化数据库并填充示例数据...")
    logger.info("=" * 60)

    # Step 0: 确保数据库存在
    logger.info("[Step 0/6] Ensuring database exists...")
    ensure_database()

    # Step 1: 创建 ORM 表（users, user_preferences, conversation_history, query_logs）
    logger.info("[Step 1/5] Creating ORM tables...")
    init_db()

    # Step 2: 创建业务表
    logger.info("[Step 2/5] Creating business tables...")
    create_business_tables()

    # Step 3: 填充业务数据
    logger.info("[Step 3/5] Seeding business tables...")
    seed_business_tables()

    # Step 4: 填充 ORM 数据
    logger.info("[Step 4/5] Seeding ORM tables...")
    seed_orm_tables()

    # Step 5: 填充 Milvus
    logger.info("[Step 5/5] Seeding Milvus...")
    try:
        seed_milvus()
    except Exception as e:
        logger.error(f"Milvus seeding failed (可能未运行): {e}")
        logger.warning("Milvus 数据填充失败，请确认 Milvus 服务已启动后重试")

    logger.info("=" * 60)
    logger.info("全部数据填充完成！")
    logger.info("  MySQL ORM 表: users, user_preferences, conversation_history, query_logs")
    logger.info("  MySQL 业务表: weather(70行), tour_group(450行/30城市), hotel(450行/30城市), car_rental(15行), insurance(10条)")
    logger.info("  Milvus: tourism_attractions (景点向量，数量由 generate_attraction_data 决定)")
    logger.info("=" * 60)


if __name__ == "__main__":
    main()
