"""合并所有景点补充数据到主文件。
运行方式: python merge_attractions.py
"""

import ast
import sys
import os


def safe_load_module(path):
    """安全加载 Python 模块，提取字典变量。"""
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read())
    # 找到第一个赋值语句中的字典
    for node in ast.iter_child_nodes(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    # 直接执行该模块
                    pass
    # 简单的 import 方式
    import importlib.util
    spec = importlib.util.spec_from_file_location("supplement", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def merge_all():
    base_dir = os.path.dirname(os.path.abspath(__file__))

    # 加载主数据
    from generate_attraction_data import ATTRACTIONS_BY_PROVINCE, generate_attractions

    original_count = sum(len(v) for v in ATTRACTIONS_BY_PROVINCE.values())
    print(f"原始数据: {original_count} 条景点")

    # 收集所有已有景点名称用于去重
    existing = set()
    for province, attractions in ATTRACTIONS_BY_PROVINCE.items():
        for name, city, tags in attractions:
            existing.add((name, city))

    # 加载所有补充文件
    supplement_files = [
        "attractions_supplement.py",
        "attractions_supplement_east.py",
        "attractions_supplement_north.py",
        "attractions_supplement_central.py",
        "attractions_supplement_west.py",
    ]

    total_added = 0
    for sf in supplement_files:
        path = os.path.join(base_dir, sf)
        if not os.path.exists(path):
            print(f"  跳过 (不存在): {sf}")
            continue

        try:
            mod = safe_load_module(path)
            # 尝试不同的字典名
            for attr_name in dir(mod):
                if attr_name.startswith("SUPPLEMENT"):
                    supplement = getattr(mod, attr_name)
                    if isinstance(supplement, dict):
                        added = 0
                        for province, attractions in supplement.items():
                            if province not in ATTRACTIONS_BY_PROVINCE:
                                ATTRACTIONS_BY_PROVINCE[province] = []
                            for name, city, tags in attractions:
                                if (name, city) not in existing:
                                    ATTRACTIONS_BY_PROVINCE[province].append((name, city, tags))
                                    existing.add((name, city))
                                    added += 1
                        print(f"  {sf}: +{added} 条 (去重后)")
                        total_added += added
        except Exception as e:
            print(f"  加载失败 {sf}: {e}")

    new_count = sum(len(v) for v in ATTRACTIONS_BY_PROVINCE.values())
    print(f"\n合并后: {new_count} 条景点 (新增 {total_added} 条)")

    # 打印各省统计
    for province, attractions in sorted(ATTRACTIONS_BY_PROVINCE.items()):
        cities = len(set(a[1] for a in attractions))
        print(f"  {province}: {len(attractions)} 个景点, {cities} 个城市")

    return new_count


if __name__ == "__main__":
    merge_all()
