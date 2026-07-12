---
description: 重新构建旅游助手Agent的Docker镜像并部署。当用户说"重新构建"、"构建镜像"、"打包部署"、"rebuild"、"docker build"、"重新部署" 时自动触发。也可手动输入 /rebuild-app 调用。
---

# 重新构建旅游助手Agent

使用 Docker Compose 构建镜像并重新部署旅游助手 Web 服务。

## 构建原理

- **Docker**：将应用 + 所有依赖打包成容器镜像（类似把一个应用"装进箱子"，箱子里有运行所需的一切）
- **Docker Compose**：用一个配置文件同时管理多个容器（MySQL、Milvus、Redis、Web、Worker 等），一键启动/停止
- 项目已有 `Dockerfile` 和 `docker-compose.yml`，直接用就行

## 步骤

### 1. 检查 .env 配置

构建前确保 `.env` 文件中的关键配置已填写：

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && cat .env | grep -E "APP_API_KEY|MYSQL_ROOT_PASSWORD|WEATHER_API_KEY"
```

必填项：
| 配置项 | 说明 |
|--------|------|
| `APP_API_KEY` | 阿里云 DashScope API Key（LLM 调用需要） |
| `MYSQL_ROOT_PASSWORD` | MySQL root 密码 |
| `WEATHER_API_KEY` | 和风天气 API Key |

> 如果 `.env` 不存在，复制 `.env.production` 为 `.env` 并修改。

### 2. 停止旧服务

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && docker compose down
```

### 3. 重新构建镜像

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && docker compose build --no-cache
```

- `--no-cache`：不使用缓存，从零开始构建（确保用的是最新代码和依赖）

> ⚠️ 首次构建可能需要 5-10 分钟（需要下载基础镜像 + 安装 Python 依赖），后续构建会快很多

### 4. 启动所有服务

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && docker compose up -d
```

- `-d`：后台运行（detached mode），不占用终端

### 5. 等待健康检查通过

服务启动顺序是自动的：MySQL → Milvus → Redis → Web + Worker + Beat

```bash
# 查看各服务状态
docker compose ps
```

所有服务的 STATUS 列显示 `healthy` 或 `Up` 即表示成功。

### 6. 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 测试对话
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"推荐北京的景点"}'
```

## 常见问题

| 问题 | 原因 | 解决 |
|------|------|------|
| "port already in use" | 端口被占用（3307/6379/8000） | 修改 `docker-compose.yml` 中的端口映射 |
| "Milvus 启动失败" | 内存不足（Milvus 需要至少 2GB） | 确保 Docker 分配了足够内存 |
| "build 失败" | 依赖下载超时 | 检查网络，已配置阿里云镜像加速 |
| "MySQL 连接被拒绝" | MySQL 还没完成初始化 | 等待 30 秒后重试 |

## 仅构建不启动（CI/CD 场景）

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && docker compose build
```

## 仅重新构建 Web 服务（代码改动后快速更新）

```bash
cd "b:/010旅游助手Agent项目/tourism_assistant" && docker compose build web && docker compose up -d web
```

## 产物说明

| 产物 | 说明 |
|------|------|
| Docker 镜像 (`tourism_assistant-web`) | Web 服务镜像，包含 FastAPI + 所有依赖 |
| Docker 镜像 (`tourism_assistant-worker`) | Celery Worker 镜像，处理后台异步任务 |
| Docker 镜像 (`tourism_assistant-beat`) | Celery Beat 镜像，定时任务调度 |
| Docker 镜像 (`tourism_assistant-memory-agent`) | Memory Agent 镜像，记忆管理独立服务 |
| 运行中的容器 | 6 个容器（MySQL + Milvus + Redis + Web + Worker + Beat + Memory Agent） |
