---
description: 扫描项目代码，检查注释规范性并自动修复。检查项：缺少docstring、公开接口无注释、注释与代码不一致、复杂逻辑无注释、TODO/FIXME残留、被注释掉的旧代码。当用户说"注释检查"、"代码注释"、"检查注释"、"comment check"时自动触发。也可手动输入 /comment-check 调用。
---

# 代码注释检查 —— 自动检查 + 自动修复

**一句话**：扫描项目 → 检查注释 → 自动修复 → 出报告，全程自动。

---

## 检查规则

### 🔴 严重（自动修复）

| 检查项 | 判定标准 | 修复方式 |
|--------|---------|---------|
| 函数缺少 docstring | `def xxx(...):` 下一行不是 `"""..."""` | 自动生成 docstring，包含函数名、参数、返回值 |
| 类缺少 docstring | `class Xxx:` 下一行不是 `"""..."""` | 自动生成 docstring，描述类职责 |
| 公开接口无注释 | `__init__.py` 或模块顶层函数无 docstring | 自动补全 docstring |
| `__init__.py` 为空 | 文件无任何注释说明包用途 | 自动添加包级别 docstring |

**docstring 生成模板**：
```python
def function_name(param1, param2):
    """[根据函数名和参数推断的一句话描述]。
    
    Args:
        param1: [参数说明]
        param2: [参数说明]
    
    Returns:
        [返回值说明]
    """
```

### 🟡 警告（自动修复）

| 检查项 | 判定标准 | 修复方式 |
|--------|---------|---------|
| 复杂逻辑无注释 | 函数体 > 15 行且内部无 `#` 注释 | 在关键逻辑块前添加分段注释 |
| 注释与函数名重复 | `def get_user` 的 docstring 只写了 "get user" | 扩展为更有意义的描述 |

### 🟢 建议（记录到报告，不自动修复）

| 检查项 | 判定标准 | 处理方式 |
|--------|---------|---------|
| TODO/FIXME 残留 | 代码含 `# TODO` 或 `# FIXME` | 列出清单，不修改 |
| 被注释掉的代码 | 连续 3 行以上 `#` 开头的旧代码 | 列出清单，询问是否删除 |
| 中英混杂注释 | 同一文件中文+英文注释混用 | 记录下来 |

---

## 执行流程

```
扫描 → 检查 → 修复 → 出报告
```

### 步骤 1：扫描

```bash
cd tourism_assistant && find . -path ./.venv -prune -o -name "*.py" -type f -print | grep -v __pycache__ | grep -v scripts/ | sort
```

按优先级扫描：
- 先 `agents/`、`graph/`、`agent_bus/`（核心业务，注释最重要）
- 再 `mcp/`、`a2a/`、`services/`、`tools/`
- 最后 `middleware/`、`cache/`、`models/`、`db/`、`llm/`、`common/`

### 步骤 2：逐文件检查

对每个 `.py` 文件：
1. 解析 AST（抽象语法树），找出所有 `FunctionDef` 和 `ClassDef`
2. 检查每个是否紧跟 docstring
3. 统计函数体行数，检查内部注释密度
4. 扫描 `# TODO`、`# FIXME`、连续 `#` 注释行
5. 检查注释语言一致性

### 步骤 3：自动修复

按 🔴 → 🟡 顺序修复：

```
修复前：
def query_weather(city, date):          ← 缺少 docstring
    result = api.get(city, date)
    if result["code"] != 200:
        return None
    data = result["data"]
    temp = data["temp"]                  ← 15行无分段注释
    humidity = data["humidity"]
    wind = data["wind"]
    ...
    return {"temp": temp, "humidity": humidity}

修复后：
def query_weather(city, date):
    """查询指定城市的天气信息。
    
    Args:
        city: 城市名称
        date: 查询日期
    
    Returns:
        包含温度、湿度、风力信息的字典，查询失败返回 None
    """
    # 调用天气 API
    result = api.get(city, date)
    if result["code"] != 200:
        return None
    
    # 解析响应数据
    data = result["data"]
    temp = data["temp"]
    humidity = data["humidity"]
    wind = data["wind"]
    ...
    return {"temp": temp, "humidity": humidity}
```

### 步骤 4：生成报告

生成前先删除旧报告：
```bash
rm -f tourism_assistant/tests/reports/注释检查报告.md
```

保存到 `tests/reports/注释检查报告.md`（固定文件名，每次覆盖）。

```markdown
# 🔍 代码注释检查报告
> 项目 / 时间 / 扫描文件数

## 📊 总览
| 指标 | 数值 |
| 扫描文件 | N |
| 扫描函数 | N |
| 🔴 严重 | N（已修复 N） |
| 🟡 警告 | N（已修复 N） |
| 🟢 建议 | N（待确认） |
| 注释覆盖率 | X% |

## 🔴 严重问题（已自动修复）
| 文件 | 行号 | 函数/类 | 问题 | 修复内容 |

## 🟡 警告（已自动修复）
| 文件 | 行号 | 函数名 | 行数 | 添加的分段注释 |

## 🟢 建议（未修复，需确认）
| 文件 | 行号 | 类型 | 内容 |
| ... | ... | TODO残留 | `# TODO: 添加缓存` |
| ... | ... | 注释代码 | 12行被注释掉的旧逻辑 |

## 📈 注释覆盖率
| 目录 | 函数总数 | 有注释 | 覆盖率 |
```

---

## 修复策略

| 场景 | 怎么做 |
|------|--------|
| 函数名清晰（如 `get_user_by_id`） | 根据函数名推断描述 |
| 函数名模糊（如 `process`、`handle`） | 阅读函数体推断意图 |
| 参数有类型注解 | 结合类型注解生成参数说明 |
| 有 return 语句 | 分析返回类型生成 Returns 说明 |
| `__init__.py` 为空 | 根据包名和目录下模块生成简要说明 |

---

## 跳过规则

以下情况不做修复：
- `tests/` 目录下的测试文件
- `scripts/` 数据生成脚本
- 单行 lambda 函数
- 魔术方法（`__str__`、`__repr__`、`__eq__` 等）
- 已有的 docstring（即使不完美也不改，避免误伤）
