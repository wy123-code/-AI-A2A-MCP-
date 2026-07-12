"""国内航班数据生成器 —— 生成最近一个月全国主要城市间航班数据，输出 Excel + MySQL。

运行方式:
    python generate_flight_data.py              # 生成并导出
    python generate_flight_data.py --excel-only  # 仅导出 Excel
    python generate_flight_data.py --db-only     # 仅写入 MySQL
"""

import random
import sys
from datetime import date, datetime, timedelta
from collections import defaultdict

random.seed(42)

# ============================================================
# 国内主要机场数据（城市 → 机场名、机场代码）
# ============================================================
AIRPORTS = {
    "北京": [("北京首都国际机场", "PEK"), ("北京大兴国际机场", "PKX")],
    "上海": [("上海浦东国际机场", "PVG"), ("上海虹桥国际机场", "SHA")],
    "广州": [("广州白云国际机场", "CAN")],
    "深圳": [("深圳宝安国际机场", "SZX")],
    "成都": [("成都天府国际机场", "TFU"), ("成都双流国际机场", "CTU")],
    "重庆": [("重庆江北国际机场", "CKG")],
    "杭州": [("杭州萧山国际机场", "HGH")],
    "西安": [("西安咸阳国际机场", "XIY")],
    "昆明": [("昆明长水国际机场", "KMG")],
    "武汉": [("武汉天河国际机场", "WUH")],
    "长沙": [("长沙黄花国际机场", "CSX")],
    "南京": [("南京禄口国际机场", "NKG")],
    "厦门": [("厦门高崎国际机场", "XMN")],
    "青岛": [("青岛胶东国际机场", "TAO")],
    "大连": [("大连周水子国际机场", "DLC")],
    "三亚": [("三亚凤凰国际机场", "SYX")],
    "海口": [("海口美兰国际机场", "HAK")],
    "哈尔滨": [("哈尔滨太平国际机场", "HRB")],
    "沈阳": [("沈阳桃仙国际机场", "SHE")],
    "长春": [("长春龙嘉国际机场", "CGQ")],
    "天津": [("天津滨海国际机场", "TSN")],
    "郑州": [("郑州新郑国际机场", "CGO")],
    "济南": [("济南遥墙国际机场", "TNA")],
    "福州": [("福州长乐国际机场", "FOC")],
    "合肥": [("合肥新桥国际机场", "HFE")],
    "南昌": [("南昌昌北国际机场", "KHN")],
    "贵阳": [("贵阳龙洞堡国际机场", "KWE")],
    "南宁": [("南宁吴圩国际机场", "NNG")],
    "兰州": [("兰州中川国际机场", "LHW")],
    "乌鲁木齐": [("乌鲁木齐地窝堡国际机场", "URC")],
    "呼和浩特": [("呼和浩特白塔国际机场", "HET")],
    "拉萨": [("拉萨贡嘎国际机场", "LXA")],
    "银川": [("银川河东国际机场", "INC")],
    "西宁": [("西宁曹家堡国际机场", "XNN")],
    "太原": [("太原武宿国际机场", "TYN")],
    "石家庄": [("石家庄正定国际机场", "SJW")],
    "宁波": [("宁波栎社国际机场", "NGB")],
    "温州": [("温州龙湾国际机场", "WNZ")],
    "桂林": [("桂林两江国际机场", "KWL")],
    "丽江": [("丽江三义国际机场", "LJG")],
    "西双版纳": [("西双版纳嘎洒国际机场", "JHG")],
    "珠海": [("珠海金湾机场", "ZUH")],
    "烟台": [("烟台蓬莱国际机场", "YNT")],
    "威海": [("威海大水泊国际机场", "WEH")],
    "张家界": [("张家界荷花国际机场", "DYG")],
    "徐州": [("徐州观音国际机场", "XUZ")],
    "常州": [("常州奔牛国际机场", "CZX")],
    "南通": [("南通兴东国际机场", "NTG")],
    "泉州": [("泉州晋江国际机场", "JJN")],
}

# ============================================================
# 航空公司
# ============================================================
AIRLINES = [
    ("CA", "中国国际航空"), ("MU", "中国东方航空"), ("CZ", "中国南方航空"),
    ("HU", "海南航空"), ("3U", "四川航空"), ("ZH", "深圳航空"),
    ("FM", "上海航空"), ("MF", "厦门航空"), ("SC", "山东航空"),
    ("GS", "天津航空"), ("JD", "首都航空"), ("8L", "祥鹏航空"),
    ("PN", "西部航空"), ("GJ", "长龙航空"), ("9C", "春秋航空"),
    ("KN", "中国联合航空"), ("QW", "青岛航空"), ("EU", "成都航空"),
    ("TV", "西藏航空"), ("A6", "湖南航空"),
]

# 机型
AIRCRAFT_TYPES = [
    "波音737-800", "波音737-700", "波音737 MAX 8", "波音787-9",
    "空客A320neo", "空客A321neo", "空客A319", "空客A330-300",
    "空客A350-900", "国产C919", "ARJ21-700", "波音777-300ER",
]


def _build_routes():
    """根据城市重要性构建航线网络。"""
    tier1 = ["北京", "上海", "广州", "深圳", "成都"]
    tier2 = ["重庆", "杭州", "西安", "昆明", "武汉", "长沙", "南京", "厦门",
             "青岛", "大连", "三亚", "海口", "哈尔滨", "沈阳", "天津", "郑州",
             "济南", "福州", "乌鲁木齐"]
    tier3 = list(set(AIRPORTS.keys()) - set(tier1) - set(tier2))

    routes = set()
    # 一线 ↔ 一线：全部互联
    for i, a in enumerate(tier1):
        for b in tier1[i+1:]:
            routes.add((a, b))
            routes.add((b, a))

    # 一线 ↔ 二线：全部连通
    for a in tier1:
        for b in tier2:
            routes.add((a, b))
            routes.add((b, a))

    # 一线 ↔ 三线：连通
    for a in tier1:
        for b in tier3:
            routes.add((a, b))
            routes.add((b, a))

    # 二线 ↔ 二线：部分连通（大区内部）
    hubs = {"北京": ["天津", "石家庄", "太原", "济南", "青岛", "大连", "沈阳", "哈尔滨", "长春", "郑州"],
            "上海": ["杭州", "南京", "合肥", "宁波", "温州", "福州", "厦门"],
            "广州": ["深圳", "三亚", "海口", "南宁", "珠海", "长沙", "武汉"],
            "成都": ["重庆", "昆明", "贵阳", "西安", "兰州", "拉萨", "乌鲁木齐"],
            "西安": ["兰州", "银川", "西宁", "乌鲁木齐", "太原", "郑州", "武汉"]}
    for hub, spokes in hubs.items():
        for spoke in spokes:
            if spoke in AIRPORTS and spoke != hub:
                routes.add((hub, spoke))
                routes.add((spoke, hub))

    # 二线间补充：每个区域内部互联
    north = ["哈尔滨", "长春", "沈阳", "大连", "天津", "石家庄", "太原", "呼和浩特"]
    east = ["南京", "杭州", "合肥", "宁波", "温州", "济南", "青岛", "烟台"]
    south = ["海口", "三亚", "南宁", "桂林", "珠海", "福州", "厦门", "泉州"]
    west = ["昆明", "丽江", "西双版纳", "贵阳", "拉萨", "兰州", "西宁", "银川", "乌鲁木齐"]
    central = ["武汉", "长沙", "郑州", "南昌", "张家界", "徐州", "常州", "南通"]

    for region in [north, east, south, west, central]:
        for i, a in enumerate(region):
            for b in region[i+1:]:
                routes.add((a, b))
                routes.add((b, a))

    return list(routes)


def generate_flights(start_date="2026-05-20", end_date="2026-06-20"):
    """生成航班数据。

    返回: list[dict]
    """
    routes = _build_routes()
    print(f"航线数量: {len(routes)}")

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days = (end - start).days + 1

    flights = []
    flight_counter = defaultdict(int)  # 全局航班计数器 (按航空公司)

    # 每条航线每天 1~4 个航班（按距离/重要性）
    for dep_city, arr_city in routes:
        # 一线城市航线更多
        tier1 = {"北京", "上海", "广州", "深圳", "成都"}
        if dep_city in tier1 and arr_city in tier1:
            flights_per_day = random.choices([3, 4, 5], weights=[3, 5, 2])[0]
        elif dep_city in tier1 or arr_city in tier1:
            flights_per_day = random.choices([1, 2, 3], weights=[3, 5, 2])[0]
        else:
            flights_per_day = random.choices([1, 2], weights=[6, 4])[0]

        # 分配航空公司（同航线可能多家航司运营）
        route_airlines = random.sample(AIRLINES, min(flights_per_day + 1, len(AIRLINES)))

        dep_airport_info = random.choice(AIRPORTS[dep_city])
        arr_airport_info = random.choice(AIRPORTS[arr_city])

        for day_offset in range(days):
            current_date = start + timedelta(days=day_offset)

            for f_idx in range(flights_per_day):
                airline_code, airline_name = route_airlines[f_idx % len(route_airlines)]
                flight_counter[airline_code] += 1
                fn = flight_counter[airline_code]
                flight_no = f"{airline_code}{fn:04d}"

                # 出发时间分布在 6:00 ~ 22:00
                dep_hour = random.randint(6, 21)
                dep_minute = random.choice([0, 5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55])
                dep_time = f"{dep_hour:02d}:{dep_minute:02d}"

                # 飞行时间 1h~5h（根据距离模拟）
                # 近似：同区域 1-2h，跨区域 3-5h
                all_north = {"北京", "天津", "石家庄", "太原", "呼和浩特", "哈尔滨", "长春", "沈阳", "大连", "济南", "青岛", "烟台", "威海"}
                all_east = {"上海", "南京", "杭州", "合肥", "宁波", "温州", "徐州", "常州", "南通"}
                all_south = {"广州", "深圳", "三亚", "海口", "南宁", "桂林", "珠海", "福州", "厦门", "泉州"}
                all_west = {"成都", "重庆", "昆明", "贵阳", "丽江", "西双版纳", "拉萨", "乌鲁木齐", "兰州", "西宁", "银川", "张家界"}
                all_central = {"武汉", "长沙", "郑州", "南昌", "西安", "张家界", "徐州"}

                def _region(city):
                    if city in all_north: return "north"
                    if city in all_east: return "east"
                    if city in all_south: return "south"
                    if city in all_west: return "west"
                    if city in all_central: return "central"
                    return "unknown"

                r_dep = _region(dep_city)
                r_arr = _region(arr_city)
                if r_dep == r_arr:
                    duration = random.randint(55, 150)
                elif (r_dep in ("north", "east") and r_arr in ("east", "north")) or \
                     (r_dep in ("central", "south") and r_arr in ("south", "central")):
                    duration = random.randint(90, 180)
                else:
                    duration = random.randint(150, 330)

                # 到达时间
                dep_total = dep_hour * 60 + dep_minute
                arr_total = dep_total + duration
                arr_hour = (arr_total // 60) % 24
                arr_minute = arr_total % 60
                arr_time = f"{arr_hour:02d}:{arr_minute:02d}"

                # 票价（经济舱）：基准价 + 距离因子
                base_price = 300 + duration * 2.5
                # 一线城市加价
                if dep_city in tier1 and arr_city in tier1:
                    base_price *= 1.5
                elif dep_city in tier1 or arr_city in tier1:
                    base_price *= 1.2
                # 旅游城市加价
                tourist = {"三亚", "丽江", "西双版纳", "拉萨", "乌鲁木齐", "张家界", "桂林", "厦门", "海口", "大连"}
                if dep_city in tourist or arr_city in tourist:
                    base_price *= 1.15
                price = round(base_price, -1)  # 取整到10元

                # 准点率
                on_time = random.choices([1, 0], weights=[75, 25])[0]

                flights.append({
                    "flight_no": flight_no,
                    "airline": airline_name,
                    "departure_city": dep_city,
                    "departure_airport": dep_airport_info[0],
                    "arrival_city": arr_city,
                    "arrival_airport": arr_airport_info[0],
                    "departure_time": dep_time,
                    "arrival_time": arr_time,
                    "duration": duration,
                    "price": price,
                    "aircraft_type": random.choice(AIRCRAFT_TYPES),
                    "flight_date": current_date.strftime("%Y-%m-%d"),
                    "on_time": on_time,
                })

    # 去重 (flight_no + flight_date)
    seen = set()
    unique = []
    for f in flights:
        key = (f["flight_no"], f["flight_date"])
        if key not in seen:
            seen.add(key)
            unique.append(f)

    return unique


def export_csv(flights, output_path="db/flights.csv"):
    """导出航班数据到 CSV。"""
    import csv
    import os

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)

    with open(output_path, "w", encoding="utf-8-sig", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["航班号", "航空公司", "出发城市", "出发机场", "到达城市", "到达机场",
                         "出发时间", "到达时间", "飞行时长(分钟)", "经济舱票价(元)", "机型",
                         "航班日期", "准点"])
        for f_data in flights:
            writer.writerow([
                f_data["flight_no"], f_data["airline"], f_data["departure_city"],
                f_data["departure_airport"], f_data["arrival_city"], f_data["arrival_airport"],
                f_data["departure_time"], f_data["arrival_time"], f_data["duration"],
                f_data["price"], f_data["aircraft_type"], f_data["flight_date"],
                "是" if f_data["on_time"] else "否",
            ])

    print(f"CSV 已导出: {output_path} ({len(flights)} 条航班记录)")


def create_flight_table():
    """创建航班表。"""
    import pymysql
    from config import MYSQL_CONFIG
    conn = pymysql.connect(
        host=MYSQL_CONFIG["host"], port=MYSQL_CONFIG["port"],
        user=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"],
        database=MYSQL_CONFIG["database"], charset=MYSQL_CONFIG["charset"],
    )
    try:
        with conn.cursor() as cur:
            cur.execute("""CREATE TABLE IF NOT EXISTS `flight` (
                id INT AUTO_INCREMENT PRIMARY KEY,
                flight_no VARCHAR(10) NOT NULL,
                airline VARCHAR(50) NOT NULL,
                departure_city VARCHAR(50) NOT NULL,
                departure_airport VARCHAR(100) NOT NULL,
                arrival_city VARCHAR(50) NOT NULL,
                arrival_airport VARCHAR(100) NOT NULL,
                departure_time VARCHAR(5) NOT NULL,
                arrival_time VARCHAR(5) NOT NULL,
                duration INT NOT NULL,
                price DECIMAL(10,0) NOT NULL,
                aircraft_type VARCHAR(50) NOT NULL,
                flight_date DATE NOT NULL,
                on_time TINYINT DEFAULT 1,
                INDEX idx_flight_date (flight_date),
                INDEX idx_dep_city (departure_city),
                INDEX idx_arr_city (arrival_city),
                INDEX idx_flight_no (flight_no),
                INDEX idx_dep_arr (departure_city, arrival_city)
            ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;""")
        conn.commit()
        print("航班表 `flight` 已创建")
    finally:
        conn.close()


def insert_to_mysql(flights, batch_size=500):
    """将航班数据批量写入 MySQL。"""
    import pymysql
    from config import MYSQL_CONFIG
    conn = pymysql.connect(
        host=MYSQL_CONFIG["host"], port=MYSQL_CONFIG["port"],
        user=MYSQL_CONFIG["user"], password=MYSQL_CONFIG["password"],
        database=MYSQL_CONFIG["database"], charset=MYSQL_CONFIG["charset"],
    )

    columns = ("flight_no, airline, departure_city, departure_airport, "
               "arrival_city, arrival_airport, departure_time, arrival_time, "
               "duration, price, aircraft_type, flight_date, on_time")
    placeholders = ", ".join(["%s"] * 13)

    try:
        with conn.cursor() as cur:
            # 先清空
            cur.execute("TRUNCATE TABLE `flight`")
            conn.commit()

            for i in range(0, len(flights), batch_size):
                batch = flights[i:i+batch_size]
                rows = [(f["flight_no"], f["airline"], f["departure_city"],
                         f["departure_airport"], f["arrival_city"], f["arrival_airport"],
                         f["departure_time"], f["arrival_time"], f["duration"],
                         f["price"], f["aircraft_type"], f["flight_date"], f["on_time"])
                        for f in batch]
                cur.executemany(
                    f"INSERT INTO `flight` ({columns}) VALUES ({placeholders})", rows)
                conn.commit()
                print(f"  已插入 {min(i+batch_size, len(flights))}/{len(flights)} 条")

        print(f"MySQL: 共插入 {len(flights)} 条航班数据")
    finally:
        conn.close()


def main():
    import os

    csv_only = "--csv-only" in sys.argv
    db_only = "--db-only" in sys.argv

    print("生成航班数据...")
    start_date = "2026-05-20"
    end_date = "2026-06-20"
    print(f"时间范围: {start_date} ~ {end_date}")

    flights = generate_flights(start_date, end_date)
    print(f"共生成 {len(flights)} 条航班记录")

    # 统计
    cities_dep = set(f["departure_city"] for f in flights)
    cities_arr = set(f["arrival_city"] for f in flights)
    airlines_set = set(f["airline"] for f in flights)
    print(f"覆盖城市: {len(cities_dep | cities_arr)} 个")
    print(f"覆盖航空公司: {len(airlines_set)} 家")

    if not db_only:
        os.makedirs("db", exist_ok=True)
        export_csv(flights)

    if not csv_only:
        create_flight_table()
        insert_to_mysql(flights)


if __name__ == "__main__":
    main()
