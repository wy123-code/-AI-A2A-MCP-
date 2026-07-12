# 🏖️ 旅游助手 Agent

基于 **LLM + Multi-Agent** 架构的智能旅游助手，支持机票、火车票、酒店、景点、天气等 10 种旅游场景的智能查询与推荐。

[![Build & Release](https://github.com/wy123-code/-AI-A2A-MCP-/actions/workflows/build.yml/badge.svg)](https://github.com/wy123-code/-AI-A2A-MCP-/actions/workflows/build.yml)

---

## ✨ 功能

| 意图 | 说明 | 示例 |
|------|------|------|
| 🛫 飞机票查询 | 查询国内航班信息 | "北京到上海的机票" |
| 🚄 火车票查询 | 查询火车票信息 | "明天去广州的高铁" |
| 🚢 船票查询 | 查询轮船班次 | "大连到烟台的船票" |
| 🎫 演唱会票 | 查询演唱会信息 | "周杰伦北京演唱会" |
| 🌤️ 天气查询 | 查询城市天气 | "三亚这周天气怎么样" |
| 🏨 酒店查询 | 查询酒店信息 | "成都锦江区附近酒店" |
| 🚗 租车查询 | 查询租车服务 | "昆明机场租车" |
| 🛡️ 保险查询 | 查询旅行保险 | "去泰国的旅行保险" |
| 🏔️ 景点推荐 | 智能推荐旅游景点 | "推荐杭州的景点" |
| 🗺️ 旅行团查询 | 查询旅行团信息 | "云南跟团游" |

---

## 🏗️ 系统架构

```
用户 → FastAPI (Web 服务)
          │
          ├─ 意图识别 (Intent Router) ── LLM 判断用户意图
          ├─ 槽位提取 (Slot Extractor) ── 提取关键信息（城市/日期等）
          │
          ├─ Multi-Agent 调度 (Agent Bus + Redis PubSub)
          │   ├─ Weather Agent      ── 天气查询
          │   ├─ Flight Agent       ── 机票查询
          │   ├─ Train Agent        ── 火车票查询
          │   ├─ Ticket Agent       ── 船票/演唱会票
          │   ├─ Hotel Agent        ── 酒店查询
          │   ├─ Attraction Agent   ── 景点推荐
          │   ├─ Tour Group Agent   ── 旅行团查询
          │   ├─ Car Rental Agent   ── 租车查询
          │   ├─ Insurance Agent    ── 保险查询
          │   └─ Memory Agent       ── 长期/短期记忆管理
          │
          ├─ 数据存储
          │   ├─ MySQL   ── 用户、偏好、对话历史
          │   ├─ Milvus  ── 景点向量搜索（语义匹配）
          │   └─ Redis   ── 短期记忆缓存、Celery 消息队列
          │
          └─ Celery Worker ── 后台异步任务（缓存预热、记忆整理等）
```

---

## 🚀 快速开始

### 前提条件

- **Docker Desktop**（Win/Mac）或 Docker Engine（Linux）
- 至少 **4 GB** 空闲内存
- 阿里云 DashScope API Key（[免费申请](https://dashscope.console.aliyun.com)）

### 1. 克隆项目

```bash
git clone git@github.com:wy123-code/-AI-A2A-MCP-.git
cd -AI-A2A-MCP-
```

### 2. 配置环境变量

```bash
cp .env.example .env
```

编辑 `.env`，修改以下配置：

```ini
# 必填：阿里云 DashScope API Key
APP_API_KEY=sk-xxxxxxxxxxxxxxxx

# 修改为强密码
MYSQL_ROOT_PASSWORD=你的密码
```

### 3. 启动服务

```bash
docker compose up -d
```

首次启动需要拉取镜像和初始化，约 2-3 分钟。等待健康检查通过：

```bash
docker compose ps
# 所有服务 STATUS 显示 healthy → 启动完成
```

### 4. 访问

| 地址 | 说明 |
|------|------|
| `http://localhost:8000` | 前端界面（聊天窗口） |
| `http://localhost:8000/docs` | API 文档（Swagger UI） |
| `http://localhost:8000/health` | 健康检查 |

---

## 🛠️ 技术栈

| 类别 | 技术 | 用途 |
|------|------|------|
| Web 框架 | **FastAPI** | HTTP API 服务 |
| LLM | **通义千问 (Qwen)** | 意图识别、对话生成、结果聚合 |
| 关系数据库 | **MySQL 8.0** | 用户信息、偏好、对话历史 |
| 向量数据库 | **Milvus 2.4** | 景点语义搜索 |
| 缓存 | **Redis 7.2** | 短期记忆、Celery Broker |
| 异步任务 | **Celery** | 后台缓存预热、记忆维护 |
| 多智能体通信 | **MCP Protocol + Redis PubSub** | Agent 间消息传递 |
| 容器化 | **Docker + Docker Compose** | 一键部署 |
| 可观测性 | **Loguru + 全链路 TraceID** | 日志、监控、慢请求追踪 |
| 前端 | **原生 HTML/CSS/JS** | 轻量聊天界面 |

---

## 📁 项目结构

```
tourism_assistant/
├── main.py                  # FastAPI 应用入口
├── config.py                # 全局配置（LLM/DB/意图体系）
├── memory_agent_main.py     # Memory Agent 独立入口
├── celery_app.py            # Celery 应用配置
│
├── agents/                  # 多智能体系统
│   ├── worker/              # Worker Agent（机票/酒店/天气/景点等）
│   │   ├── base.py          # Agent 基类
│   │   ├── protocol.py      # 通信协议定义
│   │   ├── flight/          # 机票 Worker
│   │   ├── hotel/           # 酒店 Worker
│   │   ├── weather/         # 天气 Worker
│   │   ├── attraction/      # 景点 Worker
│   │   └── ...              # 其他 Worker
│   ├── orchestrator/        # 编排器（DAG 调度 + 结果聚合）
│   └── memory/              # 记忆 Agent
│
├── graph/                   # 状态图/流程编排
│   ├── builder.py           # 流程构建器
│   ├── state.py             # 状态定义
│   └── nodes/               # 各阶段节点
│
├── agent_bus/               # Agent 通信总线（Redis PubSub + MCP）
├── a2a/                     # Agent-to-Agent 通信
├── mcp/                     # MCP 协议客户端/服务端
├── llm/                     # LLM 客户端池
├── services/                # 业务服务层
├── middleware/               # FastAPI 中间件（限流/超时/错误处理/TraceID）
├── models/                  # ORM + Pydantic 数据模型
├── cache/                   # 分层缓存（static/short/realtime）
├── db/                      # 数据库客户端 + 种子数据
├── prompts/                 # LLM Prompt 模板
├── celery_tasks/            # Celery 后台任务
├── tools/                   # 工具函数（城市代码等）
├── common/monitor/          # 可观测性（指标收集）
├── frontend/                # 前端静态资源
├── tests/                   # 测试文件（190 个用例）
├── scripts/                 # 数据生成脚本
├── Dockerfile               # Web/Worker 镜像
├── Dockerfile.memory        # Memory Agent 镜像
├── docker-compose.yml       # 容器编排配置
├── pyproject.toml           # 项目依赖
└── DEPLOY.md                # 阿里云部署指南
```

---

## 🧪 测试

```bash
# 运行全部测试
python -m pytest tests/ -v

# 运行特定模块测试
python -m pytest tests/test_tools.py -v
python -m pytest tests/test_services.py -v
```

---

## 📊 API 概览

| 方法 | 路径 | 说明 |
|------|------|------|
| `GET` | `/` | 前端聊天界面 |
| `GET` | `/health` | 健康检查 |
| `GET` | `/intents` | 支持的意图列表 |
| `GET` | `/docs` | Swagger API 文档 |
| `POST` | `/chat` | 匿名对话 |
| `POST` | `/chat/stream` | 流式对话（SSE） |
| `POST` | `/chat/authenticated` | 认证用户对话 |
| `POST` | `/auth/register` | 用户注册 |
| `POST` | `/auth/login` | 用户登录 |
| `GET/PUT/DELETE` | `/users/*` | 用户管理 |
| `GET/POST` | `/memory/*` | 记忆管理（偏好/历史/会话） |

---

## 🚢 部署

详细部署指南见 [DEPLOY.md](DEPLOY.md)。

### 生产环境（阿里云 ECS）

```bash
# 1. 上传代码
rsync -avz --exclude '.git' --exclude '.venv' ./ root@<ECS_IP>:/opt/tourism-assistant/

# 2. 配置并启动
cd /opt/tourism-assistant
cp .env.production .env   # 修改 API Key
docker compose up -d

# 3. 验证
curl http://localhost:8000/health
```

---

## 🤖 Claude Code 集成

本项目配置了 Claude Code 的 Skills 和 Agents 来提升开发效率：

| 名称 | 类型 | 用途 |
|------|------|------|
| `unit-test` | Skill | 自动生成单元测试 + 测试报告 |
| `comment-check` | Skill | 代码注释质量检查 + 自动修复 |
| `security-audit` | Skill | 安全审计（密钥泄露/SQL注入/LLM注入等） |
| `rebuild-app` | Skill | Docker 镜像构建 + 部署 |
| `run-app` | Skill | 启动服务（开发模式/Docker 模式） |
| `quality-engineer` | Agent | 5 维度代码质量综合检查 |
| `tester` | Agent | 测试专用 Agent（执行 + 修复 + 报告） |

---

## 📄 许可证

MIT License
