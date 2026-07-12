---
description: 自动审计项目代码安全风险，标注严重级别，严重/高危问题自动修复，生成安全审计报告。检查项：硬编码密钥、SQL注入、环境变量泄露、敏感信息日志、输入校验缺失、认证授权、LLM注入、异常信息泄露、依赖漏洞。当用户说"安全审计"、"安全检查"、"代码安全"、"安全扫描"、"security audit"时自动触发。也可手动输入 /security-audit 调用。
---

# 代码安全审计 —— 自动检查 + 自动修复

**一句话**：扫描项目 → 审计安全风险 → 严重/高危自动修复 → 出报告，全程自动。

---

## 审计规则

### 🔴 严重（自动修复）

| 审计项 | 检查方法 | 修复方式 |
|--------|---------|---------|
| 硬编码密钥 | 正则匹配 `password\s*=\s*["'][^"']+["']`、`api_key\s*=`、`token\s*=`、`secret\s*=` | 替换为 `os.getenv("KEY_NAME")`，同时在 `.env.example` 添加占位 |
| `.env` 未忽略 | 检查 `.gitignore` 是否包含 `.env` | 自动添加到 `.gitignore` |
| 数据库密码明文 | 匹配 `mysql://user:密码@`、`redis://:密码@` 等连接字符串 | 替换为环境变量引用 |

**修复示例**：
```python
# 修复前
API_KEY = "sk-abc123xyz"
# 修复后
API_KEY = os.getenv("API_KEY", "")
```

### 🟠 高危（自动修复）

| 审计项 | 检查方法 | 修复方式 |
|--------|---------|---------|
| SQL 注入风险 | 扫描 `f"SELECT ... WHERE ... = '{var}'"` 模式 | 替换为参数化查询占位符 `:var` |
| 日志泄露敏感信息 | 搜索 `logger.*password`、`logger.*token`、`logger.*phone`、`logger.*id_card` | 脱敏处理 `logger.info(f"xxx: {var[:2]}***")` |
| LLM Prompt 注入 | 检查 Prompt 模板中 `{user_input}` 无过滤直接拼接 | 添加输入长度限制和特殊字符过滤 |
| 异常信息泄露 | `except.*:.*print(traceback)` 或 `str(e)` 直接返回给用户 | 替换为通用错误消息 + 内部日志记录 |
| 接口无鉴权 | 检查 FastAPI 路由是否缺少 `Depends(get_current_user)` | 添加认证依赖 |

**修复示例**：
```python
# 修复前（SQL注入风险）
query = f"SELECT * FROM users WHERE name = '{user_name}'"

# 修复后（参数化查询）
query = "SELECT * FROM users WHERE name = :user_name"
```

### 🟡 中危（记录到报告，不自动修复）

| 审计项 | 检查方法 | 处理方式 |
|--------|---------|---------|
| 依赖漏洞 | 运行 `pip-audit` 或 `safety check` | 记录漏洞名称和修复版本 |
| 文件上传无校验 | 检查上传接口是否限制文件类型和大小 | 记录文件路径，给出修复建议 |
| CORS 配置过宽 | 检查 `allow_origins=["*"]` | 记录并建议限制域名 |
| 调试模式开启 | `debug=True`、`DEBUG=True` | 记录并建议生产环境关闭 |
| 密码哈希算法弱 | 检查是否使用 `md5`、`sha1` 做密码哈希 | 记录并建议使用 `bcrypt` |

### 🟢 低危（记录到报告）

| 审计项 | 检查方法 |
|--------|---------|
| HTTP 而非 HTTPS | 检查 `http://` 硬编码 URL |
| 缺少 CSP 头 | 检查是否设置 Content-Security-Policy |

---

## 执行流程

```
扫描 → 审计 → 修复 → 出报告
```

### 步骤 1：扫描项目

```bash
cd tourism_assistant && find . -path ./.venv -prune -o \( -name "*.py" -o -name "*.js" -o -name "*.html" -o -name ".env*" -o -name ".gitignore" -o -name "*.toml" -o -name "*.txt" -o -name "*.yml" -o -name "*.yaml" \) -type f -print | grep -v __pycache__ | sort
```

### 步骤 2：逐项审计

#### 2.1 硬编码密钥扫描

```bash
grep -rn "password\s*=\s*[\"']" --include="*.py" . --exclude-dir=.venv
grep -rn "api_key\s*=\s*[\"']" --include="*.py" . --exclude-dir=.venv
grep -rn "secret\s*=\s*[\"']" --include="*.py" . --exclude-dir=.venv
grep -rn "token\s*=\s*[\"'][a-zA-Z0-9_-]{10,}" --include="*.py" . --exclude-dir=.venv
```

#### 2.2 SQL 注入扫描

搜索 f-string 拼接的 SQL 语句：
```bash
grep -rn "f[\"'].*SELECT.*WHERE.*{" --include="*.py" . --exclude-dir=.venv
grep -rn "f[\"'].*INSERT.*{" --include="*.py" . --exclude-dir=.venv
```

#### 2.3 日志泄露扫描

```bash
grep -rn "logger.*password\|logger.*token\|logger.*secret\|logger.*phone" --include="*.py" . --exclude-dir=.venv
```

#### 2.4 `.env` 检查

```bash
# 检查 .env* 文件是否在 .gitignore 中
cat .gitignore 2>/dev/null | grep ".env"
```

#### 2.5 LLM Prompt 注入

检查 Prompt 模板中直接拼接用户输入：
```bash
grep -rn "f\".*{user_input\|{query\|{user_query\|{input" --include="*.py" . --exclude-dir=.venv
```

#### 2.6 依赖漏洞

```bash
pip-audit 2>/dev/null || safety check 2>/dev/null || echo "依赖审计工具未安装"
```

### 步骤 3：自动修复

按 🔴 → 🟠 顺序修复。每修复一个文件后，立即运行相关测试确保不破坏功能。

修复原则：
- 硬编码密钥 → `os.getenv()` + 在 `.env.example` 添加占位
- SQL f-string → 参数化查询
- 日志敏感信息 → 脱敏
- 异常信息 → 通用消息 + `logger.error()` 内部记录
- 缺失鉴权 → 添加 `Depends` 依赖

### 步骤 4：验证修复

```bash
cd tourism_assistant && python -m pytest tests/ -v --tb=line 2>&1 | tail -5
```

确保修复不破坏已有功能。

### 步骤 5：生成报告

```bash
rm -f tourism_assistant/tests/reports/安全审计报告.md
```

保存到 `tests/reports/安全审计报告.md`（固定文件名，每次覆盖）。

报告结构：
```markdown
# 🛡️ 代码安全审计报告
> 项目 / 时间 / 扫描文件数

## 📊 总览
| 级别 | 发现 | 已修复 | 待处理 |
| 🔴 严重 | N | N | 0 |
| 🟠 高危 | N | N | 0 |
| 🟡 中危 | N | 0 | N |
| 🟢 低危 | N | 0 | N |

## 🔴 严重（已自动修复）
| 文件:行号 | 问题 | 修复前 | 修复后 |

## 🟠 高危（已自动修复）
| 文件:行号 | 问题 | 修复前 | 修复后 |

## 🟡 中危（待确认）
| 文件:行号 | 问题 | 修复建议 |

## 🟢 低危（建议）
| 文件:行号 | 问题 | 建议 |
```

---

## 修复安全原则

1. **不破坏功能**：每次修复后运行 pytest 验证
2. **最小改动**：只改安全问题，不动业务逻辑
3. **可回退**：修复前记录原始代码到报告中
4. **测试优先**：如果测试因修复失败，回退修复并记录到报告

## 跳过规则

- `tests/` 目录（测试代码中的密钥/密码是 Mock 数据）
- `scripts/` 数据生成脚本
- `.venv/` 虚拟环境
- 注释中的代码
