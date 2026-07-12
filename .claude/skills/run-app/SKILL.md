---
description: 启动旅游助手Agent服务。支持Docker生产模式和本地开发模式两种启动方式。当用户说"启动应用"、"运行服务"、"启动旅游助手"、"打开服务"、"run app" 时自动触发。也可手动输入 /run-app 调用。
---

# 启动旅游助手Agent

旅游助手是一个基于 FastAPI + Docker 的 Web 服务，有两种启动方式可选。

## 方式选择

| 场景 | 推荐方式 | 特点 |
|------|---------|------|
| 本地开发/调试 | 方式 A — uvicorn 开发模式 | 改代码自动重载，方便调试 |
| 生产环境/完整测试 | 方式 B — Docker Compose | 包含 MySQL/Milvus/Redis 全套服务 |

---

## 方式 A — uvicorn 开发模式（本地开发用）

### 适用场景

- 正在开发中，需要频繁修改代码
- 只需要 Web 服务，不需要完整的 Docker 环境
- 快速验证改动的效果

### 前提条件

- Python 3.12 已安装
- 依赖已安装（`uv sync` 或 `pip install -r requirements.txt`）
- MySQL/Milvus/Redis 可以不需要（会使用降级模式）

### 步骤

#### 1. 安装依赖（首次）

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && uv sync
```

> **uv** 是什么：一个比 pip 快 10-100 倍的 Python 包管理工具。`uv sync` 类似于 `pip install -r requirements.txt`，但更快。

#### 2. 配置环境变量

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && cp .env.example .env
```

然后编辑 `.env`，填入 API Key（至少填 `APP_API_KEY`）。

#### 3. 启动服务

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && python main.py
```

服务启动后：
- 访问地址：`http://localhost:8000`
- API 文档：`http://localhost:8000/docs`（Swagger UI，可以在这里直接测试所有接口）
- 健康检查：`http://localhost:8000/health`

#### 4. 检查结果

- 如果终端显示 `Uvicorn running on http://0.0.0.0:8000` → 服务启动成功 ✅
- 如果报错 "ModuleNotFoundError" → 运行 `uv sync` 安装缺失依赖
- 如果报错 "Address already in use" → 端口 8000 被占用，关闭其他程序或改端口

---

## 方式 B — Docker Compose（生产环境/完整体验）

### 适用场景

- 需要完整的数据库（MySQL + Milvus + Redis）
- 需要 Celery Worker 后台处理异步任务
- 部署到服务器前的本地验证

### 前提条件

- Docker Desktop 已安装并运行
- 至少 4GB 空闲内存（Milvus 需要较多内存）

### 步骤

#### 1. 首次构建镜像

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && docker compose build
```

#### 2. 配置 .env

确保 `.env` 中填入了 `APP_API_KEY`、`MYSQL_ROOT_PASSWORD` 等关键配置。

#### 3. 启动所有服务

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && docker compose up -d
```

#### 4. 等待健康检查

```bash
docker compose ps
```

看到所有服务状态正常即可。

#### 5. 验证

```bash
curl http://localhost:8000/health
```

---

## 服务架构一览

```
浏览器 / API 客户端
        │
        ▼
   FastAPI (端口 8000)
        │
   ┌────┼────┐
   │    │    │
   ▼    ▼    ▼
 MySQL Milvus Redis
(端口3307)(端口19530)(端口6379)
        │
        ▼
   Celery Worker (后台异步任务)
```

## 关闭服务

| 启动方式 | 关闭方式 |
|----------|---------|
| uvicorn 开发模式 | 终端按 `Ctrl+C` |
| Docker Compose | `docker compose down` |

## 查看日志

| 需求 | 命令 |
|------|------|
| 查看 Web 实时日志 | `docker compose logs -f web` |
| 查看 Worker 日志 | `docker compose logs -f worker` |
| 查看最近 100 行 | `docker compose logs --tail=100` |
| 查看所有服务日志 | `docker compose logs -f` |

## 注意事项

- 开发模式使用 `reload=True`，修改 Python 代码后自动重启
- Docker 模式首次启动冷启动需要 2-3 分钟（等 MySQL/Milvus 初始化完成）
- 如果不想用 Milvus（向量搜索），config.py 中可以关闭
- Memory Agent 不可用时，主应用会自动降级为直接调用 MemoryService
