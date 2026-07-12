"""按中国旅游城市生成酒店和旅行团数据 —— 每个城市 ≥15 条。

运行方式:
    python generate_city_data.py          # 直接写入 MySQL
    python generate_city_data.py --print  # 仅打印数据预览
    python generate_city_data.py --sql    # 输出 SQL 文件
"""
import argparse
import random
from datetime import date, timedelta

import pymysql
from pymysql.cursors import DictCursor
from loguru import logger

from config import MYSQL_CONFIG

# ============================================================
# 30 个主要旅游城市（含区划、五星/四星/三星/经济酒店命名素材）
# ============================================================
CITIES = [
    {
        "name": "北京", "districts": ["朝阳区", "东城区", "西城区", "海淀区", "丰台区", "通州区", "大兴区"],
        "landmarks": ["国贸", "王府井", "中关村", "三里屯", "西单", "望京", "五棵松", "前门", "鸟巢", "金融街"],
        "hotel_prefix": ["北京", "首都", "京城", "燕京", "京华", "皇冠"],
    },
    {
        "name": "上海", "districts": ["黄浦区", "浦东新区", "静安区", "徐汇区", "长宁区", "虹口区", "闵行区"],
        "landmarks": ["外滩", "陆家嘴", "南京路", "静安寺", "徐家汇", "虹桥", "淮海路", "人民广场", "五角场", "新天地"],
        "hotel_prefix": ["上海", "申城", "沪上", "浦东", "东方", "黄埔"],
    },
    {
        "name": "广州", "districts": ["天河区", "越秀区", "海珠区", "荔湾区", "番禺区", "白云区", "黄埔区"],
        "landmarks": ["珠江新城", "天河城", "北京路", "上下九", "琶洲", "长隆", "白云山", "广州塔", "沙面", "太古汇"],
        "hotel_prefix": ["广州", "羊城", "花城", "穗城", "岭南", "珠江"],
    },
    {
        "name": "深圳", "districts": ["南山区", "福田区", "罗湖区", "宝安区", "龙岗区", "龙华区", "盐田区"],
        "landmarks": ["华侨城", "深圳湾", "福田CBD", "东门", "蛇口", "前海", "大梅沙", "世界之窗", "科技园", "华强北"],
        "hotel_prefix": ["深圳", "鹏城", "特区", "南海", "滨海", "粤海"],
    },
    {
        "name": "成都", "districts": ["锦江区", "青羊区", "武侯区", "成华区", "金牛区", "高新区", "龙泉驿区"],
        "landmarks": ["春熙路", "宽窄巷子", "锦里", "太古里", "天府广场", "世纪城", "大熊猫基地", "九眼桥", "文殊院", "金融城"],
        "hotel_prefix": ["成都", "蓉城", "锦城", "蜀都", "天府", "巴蜀"],
    },
    {
        "name": "杭州", "districts": ["西湖区", "上城区", "拱墅区", "滨江区", "萧山区", "余杭区", "钱塘区"],
        "landmarks": ["西湖", "武林广场", "钱江新城", "西溪", "灵隐", "龙井", "运河", "湘湖", "未来科技城", "吴山"],
        "hotel_prefix": ["杭州", "杭城", "西湖", "钱塘", "临安", "江南"],
    },
    {
        "name": "西安", "districts": ["碑林区", "雁塔区", "未央区", "莲湖区", "新城区", "长安区", "临潼区"],
        "landmarks": ["钟楼", "大雁塔", "小寨", "曲江", "高新", "浐灞", "大明宫", "兵马俑", "永宁门", "大唐不夜城"],
        "hotel_prefix": ["西安", "长安", "古城", "唐华", "秦都", "丝路"],
    },
    {
        "name": "三亚", "districts": ["海棠区", "吉阳区", "天涯区", "崖州区"],
        "landmarks": ["亚龙湾", "海棠湾", "三亚湾", "大东海", "天涯海角", "蜈支洲岛", "后海", "清水湾", "太阳湾", "凤凰岛"],
        "hotel_prefix": ["三亚", "海岛", "南海", "椰林", "海韵", "阳光"],
    },
    {
        "name": "昆明", "districts": ["五华区", "盘龙区", "西山区", "官渡区", "呈贡区", "晋宁区"],
        "landmarks": ["翠湖", "滇池", "南屏街", "西山", "世博园", "斗南", "金马碧鸡坊", "大观楼", "民族村", "长水"],
        "hotel_prefix": ["昆明", "春城", "云滇", "彩云", "滇池", "花都"],
    },
    {
        "name": "哈尔滨", "districts": ["道里区", "南岗区", "道外区", "松北区", "香坊区", "平房区"],
        "landmarks": ["中央大街", "冰雪大世界", "太阳岛", "索菲亚", "哈西", "群力", "松北", "会展中心", "哈尔滨站", "龙塔"],
        "hotel_prefix": ["哈尔滨", "冰城", "松花江", "北国", "龙江", "远东"],
    },
    {
        "name": "拉萨", "districts": ["城关区", "堆龙德庆区", "达孜区"],
        "landmarks": ["布达拉宫", "大昭寺", "八廓街", "罗布林卡", "纳金路", "仙足岛", "太阳岛", "柳梧新区", "宇拓路", "林廓北路"],
        "hotel_prefix": ["拉萨", "圣地", "雪域", "高原", "日光城", "藏地"],
    },
    {
        "name": "重庆", "districts": ["渝中区", "江北区", "南岸区", "沙坪坝区", "九龙坡区", "渝北区", "巴南区"],
        "landmarks": ["解放碑", "观音桥", "南滨路", "洪崖洞", "磁器口", "江北嘴", "弹子石", "朝天门", "三峡广场", "照母山"],
        "hotel_prefix": ["重庆", "山城", "渝州", "巴渝", "雾都", "两江"],
    },
    {
        "name": "南京", "districts": ["玄武区", "秦淮区", "建邺区", "鼓楼区", "栖霞区", "雨花台区", "江宁区"],
        "landmarks": ["新街口", "夫子庙", "玄武湖", "中山陵", "河西", "百家湖", "紫金山", "总统府", "老门东", "颐和路"],
        "hotel_prefix": ["南京", "金陵", "钟山", "秦淮", "建康", "石城"],
    },
    {
        "name": "厦门", "districts": ["思明区", "湖里区", "集美区", "海沧区", "同安区", "翔安区"],
        "landmarks": ["鼓浪屿", "中山路", "曾厝垵", "环岛路", "厦门大学", "白鹭洲", "五缘湾", "SM广场", "集美学村", "沙坡尾"],
        "hotel_prefix": ["厦门", "鹭岛", "闽南", "海滨", "鹭江", "嘉庚"],
    },
    {
        "name": "长沙", "districts": ["岳麓区", "芙蓉区", "天心区", "开福区", "雨花区", "望城区"],
        "landmarks": ["五一广场", "橘子洲", "岳麓山", "梅溪湖", "高铁南站", "万家丽", "洋湖", "北辰三角洲", "芙蓉广场", "省府"],
        "hotel_prefix": ["长沙", "星城", "潇湘", "楚汉", "橘洲", "湖湘"],
    },
    {
        "name": "苏州", "districts": ["姑苏区", "虎丘区", "吴中区", "相城区", "吴江区", "工业园区"],
        "landmarks": ["观前街", "金鸡湖", "拙政园", "虎丘", "平江路", "山塘街", "狮子林", "苏州中心", "独墅湖", "石路"],
        "hotel_prefix": ["苏州", "姑苏", "吴中", "江南", "园林", "水乡"],
    },
    {
        "name": "桂林", "districts": ["秀峰区", "象山区", "七星区", "叠彩区", "雁山区", "临桂区"],
        "landmarks": ["漓江", "阳朔西街", "象鼻山", "两江四湖", "龙脊梯田", "银子岩", "世外桃源", "正阳步行街", "东西巷", "桂林站"],
        "hotel_prefix": ["桂林", "漓江", "山水", "阳朔", "桂湖", "碧莲"],
    },
    {
        "name": "丽江", "districts": ["古城区", "玉龙纳西族自治县"],
        "landmarks": ["大研古城", "束河古镇", "玉龙雪山", "黑龙潭", "白沙古镇", "拉市海", "泸沽湖", "四方街", "木府", "狮子山"],
        "hotel_prefix": ["丽江", "古城", "玉龙", "纳西", "雪山", "束河"],
    },
    {
        "name": "青岛", "districts": ["市南区", "市北区", "崂山区", "李沧区", "城阳区", "黄岛区"],
        "landmarks": ["栈桥", "五四广场", "崂山", "八大关", "奥帆中心", "金沙滩", "台东", "啤酒街", "大学路", "小麦岛"],
        "hotel_prefix": ["青岛", "岛城", "琴岛", "崂山", "黄海", "胶澳"],
    },
    {
        "name": "大连", "districts": ["中山区", "西岗区", "沙河口区", "甘井子区", "旅顺口区", "金州区"],
        "landmarks": ["星海广场", "老虎滩", "金石滩", "棒棰岛", "中山广场", "东港", "滨海路", "青泥洼桥", "西安路", "渔人码头"],
        "hotel_prefix": ["大连", "滨城", "旅大", "星海", "辽东", "浪漫"],
    },
    {
        "name": "张家界", "districts": ["永定区", "武陵源区"],
        "landmarks": ["武陵源", "天门山", "大峡谷", "黄龙洞", "宝峰湖", "袁家界", "杨家界", "溪布街", "土司城", "天子山"],
        "hotel_prefix": ["张家界", "武陵源", "天门", "湘西", "峰林", "奇峰"],
    },
    {
        "name": "贵阳", "districts": ["南明区", "云岩区", "花溪区", "乌当区", "白云区", "观山湖区"],
        "landmarks": ["甲秀楼", "黔灵山", "花溪公园", "青岩古镇", "观山湖", "天河潭", "小车河", "花果园", "贵阳北站", "文昌阁"],
        "hotel_prefix": ["贵阳", "筑城", "黔中", "林城", "花溪", "贵山"],
    },
    {
        "name": "武汉", "districts": ["武昌区", "江汉区", "江岸区", "洪山区", "汉阳区", "硚口区", "青山区"],
        "landmarks": ["黄鹤楼", "东湖", "户部巷", "楚河汉街", "光谷", "江汉路", "武汉天地", "归元寺", "汉口江滩", "昙华林"],
        "hotel_prefix": ["武汉", "江城", "汉口", "武昌", "荆楚", "白云"],
    },
    {
        "name": "天津", "districts": ["和平区", "河东区", "河西区", "南开区", "河北区", "红桥区", "滨海新区"],
        "landmarks": ["五大道", "意式风情区", "天津之眼", "古文化街", "瓷房子", "滨江道", "津湾广场", "海河", "盘山", "泰达"],
        "hotel_prefix": ["天津", "津门", "沽上", "海河", "津城", "天塔"],
    },
    {
        "name": "郑州", "districts": ["中原区", "二七区", "金水区", "管城区", "惠济区", "郑东新区"],
        "landmarks": ["二七广场", "大玉米楼", "少林寺", "嵩山", "黄河风景区", "河南博物院", "如意湖", "中原福塔", "龙子湖", "北龙湖"],
        "hotel_prefix": ["郑州", "商都", "中州", "绿城", "嵩山", "中原"],
    },
    {
        "name": "济南", "districts": ["历下区", "市中区", "槐荫区", "天桥区", "历城区", "长清区"],
        "landmarks": ["趵突泉", "大明湖", "千佛山", "泉城广场", "芙蓉街", "宽厚里", "洪家楼", "奥体中心", "华山湖", "五龙潭"],
        "hotel_prefix": ["济南", "泉城", "历下", "齐州", "明湖", "舜耕"],
    },
    {
        "name": "福州", "districts": ["鼓楼区", "台江区", "仓山区", "晋安区", "马尾区", "长乐区"],
        "landmarks": ["三坊七巷", "鼓山", "闽江", "西湖公园", "上下杭", "烟台山", "东街口", "宝龙", "闽江之心", "海峡奥体"],
        "hotel_prefix": ["福州", "榕城", "闽都", "三山", "闽江", "海峡"],
    },
    {
        "name": "南宁", "districts": ["青秀区", "兴宁区", "江南区", "西乡塘区", "良庆区", "邕宁区"],
        "landmarks": ["青秀山", "南湖", "中山路", "万象城", "五象新区", "三街两巷", "民歌湖", "广西大学", "朝阳广场", "方特东盟"],
        "hotel_prefix": ["南宁", "邕城", "绿都", "八桂", "南国", "朱槿"],
    },
    {
        "name": "沈阳", "districts": ["和平区", "沈河区", "皇姑区", "大东区", "铁西区", "浑南区"],
        "landmarks": ["沈阳故宫", "中街", "太原街", "北陵公园", "张氏帅府", "浑河", "铁西广场", "青年大街", "棋盘山", "西塔"],
        "hotel_prefix": ["沈阳", "盛京", "奉天", "辽沈", "浑河", "关东"],
    },
    {
        "name": "兰州", "districts": ["城关区", "七里河区", "西固区", "安宁区", "红古区"],
        "landmarks": ["黄河风情线", "中山桥", "白塔山", "五泉山", "正宁路", "张掖路", "省博物馆", "水车博览园", "兰州中心", "银滩"],
        "hotel_prefix": ["兰州", "金城", "黄河", "陇上", "丝路", "白塔"],
    },
]

# 酒店品牌/类型素材
HOTEL_CHAINS_5STAR = ["洲际", "万豪", "希尔顿", "丽思卡尔顿", "凯宾斯基", "君悦", "威斯汀", "艾美", "悦榕庄", "瑞吉",
                        "四季", "文华东方", "柏悦", "康莱德", "索菲特", "香格里拉", "凯悦", "喜来登", "皇冠假日", "万达文华",
                        "万达嘉华", "绿地铂瑞", "保利皇冠", "中海凯骊", "君澜度假", "建国铂萃", "华侨城洲际", "恒大海上",
                        "鲁能希尔顿", "明宇豪雅", "首旅南苑", "金陵江滨", "锦江国际", "新华联丽景", "朗廷", "宝格丽",
                        "半岛", "华尔道夫", "丽晶", "安缦", "六善", "虹夕诺雅", "嘉佩乐", "卓美亚", "莱佛士"]
HOTEL_CHAINS_4STAR = ["万怡", "福朋喜来登", "希尔顿逸林", "诺富特", "华美达", "假日酒店", "豪生", "开元名都",
                        "锦江都城", "亚朵S", "建国饭店", "金陵饭店", "维景国际", "桔子水晶", "全季旗舰",
                        "君亭", "开元曼居", "丽呈", "美居", "戴斯", "郁锦香", "凯里亚德", "康铂",
                        "希尔顿欢朋", "万枫", "美爵", "诺翰", "丽笙", "丽怡", "澳斯特", "雅阁",
                        "格兰云天", "世纪金源", "岷山", "中航泊悦", "远洋", "绿地九龙"]
HOTEL_CHAINS_3STAR = ["亚朵", "全季", "丽枫", "如家精选", "和颐", "桔子", "汉庭优佳", "维也纳",
                        "星程", "白玉兰", "锦江之星品尚", "城市便捷", "尚客优品", "宜必思尚品", "莫林风尚",
                        "如家商旅", "格林东方", "格盟", "都市花园", "雅斯特", "柏曼", "优程",
                        "途客中国", "青季", "城家", "逸柏", "锐思特", "美豪", "艺龙壹堂",
                        "秋果", "喆啡", "非繁城品", "希岸", "潮漫", "丽呈睿轩"]
HOTEL_CHAINS_BUDGET = ["如家", "汉庭", "7天", "锦江之星", "格林豪泰", "尚客优", "城市便捷",
                         "海友", "布丁", "易佰", "逸米", "贝壳", "派酒店", "99优选", "速8",
                         "轻住", "OYO", "轻时光", "宜居", "尚美", "骏怡", "六盘水",
                         "悦季", "逸客栈", "青旅之家", "拾光里", "云朵", "微客", "一宿",
                         "途窝", "花住", "爱舍", "悠度", "住友", "驿居"]

AMENITIES_5STAR = "游泳池,健身房,SPA,商务中心,行政酒廊,免费WiFi,停车场,接机服务"
AMENITIES_4STAR = "健身房,餐厅,会议室,免费WiFi,停车场,商务中心"
AMENITIES_3STAR = "免费WiFi,餐厅,洗衣房,会议室,停车场"
AMENITIES_BUDGET = "免费WiFi,24小时热水,空调,电视"

PRICE_RANGE_5STAR = (680, 2880)
PRICE_RANGE_4STAR = (350, 880)
PRICE_RANGE_3STAR = (180, 420)
PRICE_RANGE_BUDGET = (99, 220)

ROOMS_5STAR = (10, 40)
ROOMS_4STAR = (20, 60)
ROOMS_3STAR = (30, 80)
ROOMS_BUDGET = (40, 120)

# 旅行团素材
TOUR_THEMES = [
    ("{dest}经典{day}日游", "经典线路，覆盖{dest}核心景点，含{night}晚酒店+全程导游+接送服务", (4, 7)),
    ("{dest}深度{day}日文化之旅", "深度探索{dest}历史文化，专家讲解，小众秘境，含{night}晚特色住宿+文化体验", (4, 8)),
    ("{dest}美食{day}日之旅", "以美食为主题的{dest}深度游，打卡地道美食，含{night}晚酒店+美食向导", (3, 5)),
    ("{dest}自然风光{day}日游", "聚焦{dest}及周边自然景观，轻徒步+摄影，含{night}晚生态酒店+户外领队", (3, 7)),
    ("{dest}亲子欢乐{day}日游", "专为家庭设计，含亲子互动项目，轻松不赶路，含{night}晚亲子酒店+儿童餐", (3, 6)),
    ("{dest}精品小团{day}日", "6-8人精品小团，灵活自由，深度打卡，含{night}晚精品酒店+商务车", (3, 6)),
    ("{dest}全景{day}日游", "{dest}及周边全景覆盖，不走回头路，含{night}晚酒店+全程交通+门票", (5, 8)),
    ("{dest}周末{day}日微旅行", "短途周末游，轻松休闲，含{night}晚酒店+特色体验+接送", (2, 3)),
    ("{dest}摄影{day}日主题团", "专业摄影向导带队，最佳机位+最佳光线，含{night}晚酒店+摄影教学", (3, 6)),
    ("{dest}温泉康养{day}日", "以温泉和养生为主题，放松身心，含{night}晚温泉酒店+养生餐+SPA", (3, 5)),
    ("{dest}探险户外{day}日", "户外探险主题，徒步/骑行/露营，含{night}晚住宿+专业装备+户外领队", (3, 7)),
    ("{dest}古城漫步{day}日", "漫步{dest}古城古镇，感受慢生活，含{night}晚民宿+手作体验", (3, 5)),
    ("{dest}蜜月浪漫{day}日", "专为情侣设计，浪漫行程，含{night}晚高端酒店+烛光晚餐+旅拍", (4, 6)),
    ("{dest}红色记忆{day}日", "红色旅游主题，重温革命历史，含{night}晚酒店+讲解+红色景点门票", (3, 5)),
    ("{dest}四季赏花{day}日", "根据季节赏花观景，含{night}晚酒店+花卉景区门票+摄影", (2, 4)),
    ("{dest}轻奢度假{day}日", "精选高端酒店，慢节奏度假，含{night}晚五星酒店+下午茶+管家服务", (3, 5)),
    ("{dest}自驾探秘{day}日", "自驾车队出行，探索{dest}周边秘境，含{night}晚特色住宿+领航车+对讲机", (3, 6)),
    ("{dest}研学之旅{day}日", "边走边学，{dest}历史地理深度研学，含{night}晚酒店+专家讲座+研学手册", (4, 7)),
    ("{dest}银发悠然{day}日", "专为中老年人设计，行程舒缓，含{night}晚舒适酒店+随队医护+无购物", (5, 8)),
    ("{dest}毕业旅行{day}日", "青春不散场，{dest}毕业季专属，含{night}晚青旅/民宿+篝火晚会+纪念视频", (3, 6)),
    ("{dest}高铁周末{day}日", "高铁往返，充分利用周末，含{night}晚酒店+接站+精华景点", (2, 3)),
    ("{dest}茶文化{day}日体验", "以茶为媒，茶园采茶制茶品茶，含{night}晚茶主题民宿+茶艺课程", (3, 5)),
    ("{dest}非遗传承{day}日", "探访{dest}非遗传承人，动手体验传统手工艺，含{night}晚酒店+非遗体验课", (3, 5)),
    ("{dest}生态观鸟{day}日", "候鸟季限定，湿地观鸟+自然笔记，含{night}晚生态民宿+专业望远镜+观鸟导师", (3, 5)),
    ("{dest}滑雪温泉{day}日", "白天滑雪晚上泡温泉，冬日限定体验，含{night}晚酒店+滑雪票+温泉票", (3, 5)),
    ("{dest}海岛度假{day}日", "海岛慢生活，沙滩发呆+水上项目，含{night}晚海景酒店+海鲜大餐", (4, 6)),
    ("{dest}骑行挑战{day}日", "骑行{dest}最美公路，含{night}晚酒店+保障车+专业骑行装备", (3, 6)),
    ("{dest}禅修养生{day}日", "寺院/道观禅修体验，含{night}晚禅房/养生酒店+素食+禅修课程", (3, 5)),
    ("{dest}民俗节庆{day}日", "体验{dest}传统民俗节庆活动，含{night}晚酒店+节庆门票+民俗体验", (3, 5)),
    ("{dest}房车露营{day}日", "房车自驾+营地露营，含{night}晚房车/帐篷+户外烧烤+星空观测", (3, 5)),
]

DEPARTURE_CITIES = ["北京", "上海", "广州", "深圳", "成都", "杭州", "西安", "重庆", "南京", "武汉",
                    "长沙", "郑州", "天津", "济南", "青岛", "沈阳", "大连", "厦门", "福州", "合肥"]

TOUR_PRICE_PER_DAY = {
    "经济": (300, 500),
    "舒适": (500, 800),
    "高端": (800, 1500),
    "豪华": (1500, 3000),
}

BASE_DATE = date(2026, 6, 1)


def generate_hotels():
    """为每个城市生成 100 条酒店数据（30城市 × 100 = 3000）。"""
    rows = []
    suffix_pool = ["", "旗舰", "中心", "商务", "精品", "行政", "景观", "度假", "都会"]

    for city in CITIES:
        city_name = city["name"]
        districts = city["districts"]
        landmarks = city["landmarks"]
        prefixes = city["hotel_prefix"]

        hotels_for_city = []
        seen_names = set()

        star_configs = [
            (5, 20, HOTEL_CHAINS_5STAR, PRICE_RANGE_5STAR, ROOMS_5STAR, AMENITIES_5STAR),
            (4, 25, HOTEL_CHAINS_4STAR, PRICE_RANGE_4STAR, ROOMS_4STAR, AMENITIES_4STAR),
            (3, 30, HOTEL_CHAINS_3STAR, PRICE_RANGE_3STAR, ROOMS_3STAR, AMENITIES_3STAR),
            (2, 25, HOTEL_CHAINS_BUDGET, PRICE_RANGE_BUDGET, ROOMS_BUDGET, AMENITIES_BUDGET),
        ]

        for star, target, chains, price_range, rooms_range, amenities in star_configs:
            count = 0
            attempts = 0
            max_attempts = target * 20

            while count < target and attempts < max_attempts:
                attempts += 1
                chain = random.choice(chains)
                prefix = random.choice(prefixes)
                landmark = random.choice(landmarks)
                district = random.choice(districts)
                suffix = random.choice(suffix_pool)

                # 多样化命名
                pattern = random.randint(1, 6)
                if pattern == 1:
                    name = f"{prefix}{landmark}{chain}酒店"
                elif pattern == 2:
                    name = f"{chain}({landmark}{suffix}店)" if suffix else f"{chain}({landmark}店)"
                elif pattern == 3:
                    name = f"{chain}({prefix}{landmark}店)"
                elif pattern == 4:
                    name = f"{prefix}{chain}({landmark}店)"
                elif pattern == 5:
                    name = f"{chain}({landmark}{random.randint(1, 5)}号店)"
                else:
                    name = f"{prefix}{landmark}{suffix}{chain}" if suffix else f"{prefix}{landmark}{chain}"

                if name in seen_names:
                    continue
                seen_names.add(name)

                price = random.randint(*price_range)
                rooms = random.randint(*rooms_range)
                street_no = random.randint(1, 500)
                address = f"{city_name}市{district}{landmark}路{street_no}号"

                hotels_for_city.append((name, city_name, district, star, price, rooms, amenities, address))
                count += 1

            if count < target:
                logger.warning(f"  {city_name} {star}星: only generated {count}/{target}")

        rows.extend(hotels_for_city)
        logger.info(f"  {city_name}: generated {len(hotels_for_city)} hotels")

    return rows


def generate_tour_groups():
    """为每个城市生成 100 条旅行团数据（30城市 × 100 = 3000）。"""
    rows = []
    seen_names = set()  # 全局去重

    for city in CITIES:
        city_name = city["name"]
        province_map = {
            "北京": "北京", "上海": "上海", "广州": "广东", "深圳": "广东",
            "成都": "四川", "杭州": "浙江", "西安": "陕西", "三亚": "海南",
            "昆明": "云南", "哈尔滨": "黑龙江", "拉萨": "西藏", "重庆": "重庆",
            "南京": "江苏", "厦门": "福建", "长沙": "湖南",
            "苏州": "江苏", "桂林": "广西", "丽江": "云南", "青岛": "山东",
            "大连": "辽宁", "张家界": "湖南", "贵阳": "贵州", "武汉": "湖北",
            "天津": "天津", "郑州": "河南", "济南": "山东", "福州": "福建",
            "南宁": "广西", "沈阳": "辽宁", "兰州": "甘肃",
        }

        tours_for_city = []

        # 每个主题使用 3-4 次（30主题 × 3.33 ≈ 100）
        theme_use_counts = [4] * 10 + [3] * 20  # 10 themes × 4 + 20 themes × 3 = 100
        random.shuffle(theme_use_counts)

        for theme_idx, use_count in enumerate(theme_use_counts):
            theme = TOUR_THEMES[theme_idx % len(TOUR_THEMES)]
            day_range = theme[2]

            for _ in range(use_count):
                attempts = 0
                while attempts < 30:
                    attempts += 1
                    days = random.randint(*day_range)
                    nights = days - 1

                    base_name = theme[0].format(day=days, dest=city_name)
                    theme_name = base_name

                    # 如果重名，系统尝试 A-Z 变体
                    if theme_name in seen_names:
                        for v in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
                            if f"{base_name}{v}" not in seen_names:
                                theme_name = f"{base_name}{v}"
                                break
                        else:
                            continue  # 所有变体都被占用，重新生成

                    if theme_name in seen_names:
                        continue
                    seen_names.add(theme_name)
                    break

                desc = theme[1].format(day=days, dest=city_name, night=nights)

                # 出发城市（排除自身）
                dep_candidates = [c for c in DEPARTURE_CITIES if c != city_name]
                dep_city = random.choice(dep_candidates)

                # 出发日期更广泛分布
                if days <= 3:
                    start_offset = random.randint(3, 365)
                elif days <= 5:
                    start_offset = random.randint(7, 300)
                else:
                    start_offset = random.randint(14, 270)
                start_date = BASE_DATE + timedelta(days=start_offset)
                end_date = start_date + timedelta(days=days)

                # 价格分层
                tier_roll = random.random()
                if days <= 2:
                    tier = "经济"
                elif days <= 3:
                    tier = "经济" if tier_roll < 0.4 else "舒适"
                elif days <= 5:
                    tier = random.choice(["经济", "舒适", "舒适", "高端"])
                elif days <= 7:
                    tier = random.choice(["舒适", "高端", "高端", "豪华"])
                else:
                    tier = random.choice(["高端", "豪华", "豪华"])
                price_per_day = random.randint(*TOUR_PRICE_PER_DAY[tier])
                price = price_per_day * days

                max_p = random.randint(10, 50) if days <= 3 else random.randint(15, 50)
                cur_p = random.randint(1, max(2, max_p - 3))

                tours_for_city.append((
                    theme_name,
                    f"{province_map.get(city_name, city_name)}·{city_name}",
                    dep_city,
                    start_date.strftime("%Y-%m-%d"),
                    end_date.strftime("%Y-%m-%d"),
                    days,
                    round(price, 2),
                    max_p,
                    cur_p,
                    desc,
                ))

        rows.extend(tours_for_city)
        logger.info(f"  {city_name}: generated {len(tours_for_city)} tour groups")

    return rows


def insert_to_mysql(hotels, tours):
    """将数据写入 MySQL。"""
    conn = pymysql.connect(
        host=MYSQL_CONFIG["host"],
        port=MYSQL_CONFIG["port"],
        user=MYSQL_CONFIG["user"],
        password=MYSQL_CONFIG["password"],
        database=MYSQL_CONFIG["database"],
        charset=MYSQL_CONFIG["charset"],
        cursorclass=DictCursor,
    )
    try:
        with conn.cursor() as cur:
            # 清空现有数据
            cur.execute("TRUNCATE TABLE hotel")
            cur.execute("TRUNCATE TABLE tour_group")
            logger.info("Cleared existing hotel and tour_group data")

            # 插入酒店
            hotel_cols = "name, city, district, star_rating, price_per_night, available_rooms, amenities, address"
            placeholders = ", ".join(["%s"] * 8)
            cur.executemany(
                f"INSERT INTO hotel ({hotel_cols}) VALUES ({placeholders})",
                hotels,
            )
            logger.info(f"Inserted {len(hotels)} hotels")

            # 插入旅行团
            tour_cols = ("name, destination, departure_city, start_date, end_date, "
                         "duration_days, price, max_participants, current_participants, description")
            placeholders = ", ".join(["%s"] * 10)
            cur.executemany(
                f"INSERT INTO tour_group ({tour_cols}) VALUES ({placeholders})",
                tours,
            )
            logger.info(f"Inserted {len(tours)} tour groups")

        conn.commit()
        logger.info("Data committed successfully")
    finally:
        conn.close()


def print_preview(hotels, tours):
    """打印数据预览。"""
    print(f"\n{'='*80}")
    print(f"酒店数据预览 (共 {len(hotels)} 条)")
    print(f"{'='*80}")
    current_city = None
    for h in hotels:
        if h[1] != current_city:
            current_city = h[1]
            print(f"\n--- {current_city} ---")
        print(f"  [{h[3]}星] {h[0]:30s} | {h[2]:8s} | ¥{h[4]:6.0f}/晚 | {h[5]}间")

    print(f"\n{'='*80}")
    print(f"旅行团数据预览 (共 {len(tours)} 条)")
    print(f"{'='*80}")
    current_dest = None
    for t in tours:
        if t[1] != current_dest:
            current_dest = t[1]
            print(f"\n--- {current_dest} ---")
        print(f"  {t[0]:35s} | {t[2]}出发 | {t[6]}天 | ¥{t[7]:8.0f} | {t[8]}/{t[9]}人")


def generate_sql(hotels, tours):
    """生成 SQL 文件。"""
    lines = ["-- Auto-generated hotel and tour group data",
             f"-- Generated: {date.today()}", ""]

    lines.append("-- Clear existing data")
    lines.append("TRUNCATE TABLE hotel;")
    lines.append("TRUNCATE TABLE tour_group;")
    lines.append("")

    # Hotels
    lines.append(f"-- Hotels ({len(hotels)} rows)")
    for h in hotels:
        name = h[0].replace("'", "\\'")
        city = h[1]
        district = h[2]
        star = h[3]
        price = h[4]
        rooms = h[5]
        amenities = h[6]
        address = h[7].replace("'", "\\'")
        lines.append(
            f"INSERT INTO hotel (name, city, district, star_rating, price_per_night, "
            f"available_rooms, amenities, address) VALUES "
            f"('{name}', '{city}', '{district}', {star}, {price}, {rooms}, '{amenities}', '{address}');"
        )

    lines.append("")
    lines.append(f"-- Tour Groups ({len(tours)} rows)")
    for t in tours:
        name = t[0].replace("'", "\\'")
        dest = t[1]
        dep = t[2]
        sd, ed = t[3], t[4]
        days = t[5]
        price = t[6]
        mx, cur = t[7], t[8]
        desc = t[9].replace("'", "\\'")
        lines.append(
            f"INSERT INTO tour_group (name, destination, departure_city, start_date, end_date, "
            f"duration_days, price, max_participants, current_participants, description) VALUES "
            f"('{name}', '{dest}', '{dep}', '{sd}', '{ed}', {days}, {price}, {mx}, {cur}, '{desc}');"
        )

    lines.append("")
    path = "generated_city_data.sql"
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    logger.info(f"SQL file written to {path}")


def main():
    parser = argparse.ArgumentParser(description="Generate hotel and tour group data for Chinese tourist cities")
    parser.add_argument("--print", action="store_true", help="Print preview only, do not write to DB")
    parser.add_argument("--sql", action="store_true", help="Output as SQL file")
    parser.add_argument("--seed", type=int, default=42, help="Random seed for reproducibility")
    args = parser.parse_args()

    random.seed(args.seed)
    logger.info(f"Generating data for {len(CITIES)} cities (seed={args.seed})")

    logger.info("Generating hotels...")
    hotels = generate_hotels()

    logger.info("Generating tour groups...")
    tours = generate_tour_groups()

    logger.info(f"Total: {len(hotels)} hotels, {len(tours)} tour groups")

    if args.print:
        print_preview(hotels, tours)
    elif args.sql:
        generate_sql(hotels, tours)
    else:
        logger.info("Writing to MySQL...")
        insert_to_mysql(hotels, tours)
        logger.info("Done! Run with --print to preview, or --sql to export SQL.")


if __name__ == "__main__":
    main()
