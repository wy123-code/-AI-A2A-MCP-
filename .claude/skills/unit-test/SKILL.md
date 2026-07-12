---
description: 自动为项目生成单元测试并输出MD测试报告。扫描源码→识别未覆盖模块→编写测试→运行验证→修复失败→生成报告，全自动完成。当用户说"单元测试"、"单元审查"、"生成测试"、"补充测试"、"写测试用例"、"unit test" 时自动触发。也可手动输入 /unit-test 调用。
---

# 旅游助手Agent —— 全自动单元测试

**一句话**：扫描项目 → 找到没测的模块 → 写测试 → 跑测试 → 出报告，全程自动。

---

## 前置：调研

### 1. 扫描已有测试
```bash
cd tourism_assistant && find tests/ -name "*.py" -not -name "__init__.py" -not -path "*/reports/*" -type f | sort
```
逐个读取，统计每个文件的测试类、用例数、覆盖模块。

### 2. 扫描源码
```bash
cd tourism_assistant && find . -path ./.venv -prune -o -name "*.py" -type f -print | grep -v __pycache__ | grep -v scripts/ | grep -v tests/ | sort
```

### 3. 读 conftest
读取 `tests/conftest.py`，确认可复用的 Mock 夹具：
- `mock_openai_client` — Mock LLM
- `mock_query_cache` — Mock Redis 缓存
- `mock_celery_tasks` — Mock Celery
- `mock_mysql` — Mock MySQL
- `mock_milvus` — Mock Milvus
- `mock_redis` — Mock Redis
- `sample_state` — 标准状态模板

---

## 执行流程（全自动，无需确认）

```
扫描 → 方案 → 写代码 → 运行 → 修复 → 报告
```

### 步骤 1：生成方案

比对新旧模块，列出：
- 已有覆盖（✅）、缺失模块（❌）、部分覆盖（⚠️）
- 按 P0/P1/P2 排优先级
- 选择 5~8 个最关键的未覆盖模块

> **注意**：跳过以下模块：
> - `scripts/` 数据生成脚本
> - `celery_tasks/` Celery 任务（需真实 broker）
> - `common/monitor/` 监控指标（运行时采集）
> - `agents/worker/*/agent.py` Worker 子类（纯配置继承）
> - `agents/worker/*/plugin.py` 插件声明（纯数据）

### 步骤 2：写测试代码

文件命名：`tests/test_unit_补充.py`（如已存在则追加序号 `_补充2.py`）

规范：
```python
"""测试文件描述 —— 覆盖哪些模块。"""
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch


class TestModuleName:
    """被测模块及测试范围。"""

    @pytest.mark.asyncio
    async def test_case_name(self, fixture1, fixture2):
        """测试用例描述 —— 输入/预期行为/验证点。"""
        from target.module import function_under_test
        # Given: 准备输入
        # When: 执行
        result = await function_under_test(input)
        # Then: 验证
        assert result["key"] == expected_value
```

质量要求：
- 每个类至少 1 个异常/失败用例
- Given-When-Then 注释
- 优先复用 conftest 夹具
- 每个测试独立运行

### 步骤 3：运行验证
```bash
cd tourism_assistant && python -m pytest tests/<新文件>.py -v --tb=short 2>&1
```
- 编译/导入错误 → 自动修复 → 重跑
- 测试失败 → 分析原因 → 自动修复 → 重跑
- 全部通过 → 继续

### 步骤 4：全量回归
```bash
cd tourism_assistant && python -m pytest tests/ -v --tb=line 2>&1
```
确保新测试不影响已有测试。

### 步骤 5：生成 MD 报告

保存到 `tests/reports/测试报告.md`（**固定文件名，每次覆盖旧报告**）。

生成前先删除已有报告：
```bash
rm -f tourism_assistant/tests/reports/测试报告.md
```

报告结构：
```markdown
# 🧪 单元测试报告
> 项目 / 时间 / 分支

## 📊 测试总览
| 指标 | 数值 |
| 测试文件数 / 用例总数 / 通过 / 失败 / 跳过 / 耗时 / 通过率 |

## 📁 按文件统计
| 文件 | 用例数 | 通过 | 失败 | 跳过 | 状态 |

## ❌ 失败详情
每个失败：错误类型 + 错误信息 + 原因分析 + 修复状态
（如果全部通过则写"✅ 全部通过，无失败用例"）

## 🆕 新增覆盖
| 优先级 | 模块 | 文件 | 测试类 | 用例数 |

## 📈 覆盖统计
| 优先级 | 模块数 | 已覆盖 | 覆盖率 |

## 🔧 环境修复
（如有修复则记录）
```

### 步骤 6：写入门禁标记
```bash
mkdir -p tourism_assistant/.claude/passed
echo "passed-$(date +%s)" > tourism_assistant/.claude/passed/unit-test
```

---

## 自动修复策略

运行测试后如有失败，按以下优先级处理：

| 问题类型 | 修复方式 |
|----------|---------|
| `ImportError`（pymilvus/protobuf） | conftest.py 注入 sys.modules fake 模块 |
| `ModuleNotFoundError`（路径错误） | 检查 import 路径，修正为正确路径 |
| `AssertionError`（断言不匹配） | 对比实际返回值，修正断言 |
| `TypeError: MagicMock can't be used in 'await'` | 显式设置 `mock_redis.xxx = AsyncMock()` |
| `AttributeError`（Mock 缺失属性） | 补充 Mock 属性定义 |

---

## 项目的特殊注意事项

1. **LLM 客户端**：patch `llm.client_pool.llm_manager.get_client`
2. **Celery**：使用 `mock_celery_tasks` fixture，通过 `sys.modules` 注入
3. **pymilvus 兼容**：conftest.py 中已有 fake module 预置
4. **MySQL/Milvus**：使用 `mock_mysql` / `mock_milvus` fixture
5. **意图/槽位**：参考 `sample_state` fixture 构建 state 字典
6. **链式导入**：`tools/__init__.py` 会触发 milvus 导入链，必要时在测试文件顶部预置 fake module
