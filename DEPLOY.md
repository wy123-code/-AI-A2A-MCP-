# 旅游助手 Agent — 阿里云部署指南

## 前置条件

### 阿里云 ECS 配置建议

| 配置项 | 最低要求 | 推荐配置 |
|--------|----------|----------|
| CPU | 2 核 | 4 核 |
| 内存 | 4 GB | 8 GB |
| 系统盘 | 40 GB | 80 GB |
| 操作系统 | Ubuntu 22.04 / CentOS 7.9 | Ubuntu 22.04 |
| 带宽 | 1 Mbps | 3 Mbps+ |

> 注意：Milvus 向量数据库至少需要 2GB 空闲内存，建议 4GB+ 以保证稳定运行。

### 安全组规则

在阿里云 ECS 控制台 → 安全组 → 入方向，添加以下规则：

| 端口 | 协议 | 来源 | 说明 |
|------|------|------|------|
| 22 | TCP | 你的 IP | SSH 远程连接 |
| 8000 | TCP | 0.0.0.0/0 | Web 服务（或改为你的 IP） |
| 80/443 | TCP | 0.0.0.0/0 | 如使用 Nginx 反代 |

---

## 快速部署（6 步）

### 1. 连接服务器并安装 Docker

```bash
ssh root@<你的ECS公网IP>

# Ubuntu
apt update && apt install -y docker.io docker-compose-v2
systemctl enable docker && systemctl start docker

# CentOS
yum install -y docker docker-compose-plugin
systemctl enable docker && systemctl start docker
```

### 2. 上传项目代码

```bash
# 在本地机器上执行（先 cd 到项目目录）
rsync -avz --exclude '.git' --exclude '__pycache__' --exclude '*.log' \
  ./ root@<ECS_IP>:/opt/tourism-assistant/
```

### 3. 配置环境变量

```bash
# 在服务器上
cd /opt/tourism-assistant
cp .env.production .env

# 编辑 .env，填入真实的 API Key
vim .env
```

**必须修改的配置项：**
- `APP_API_KEY`: 阿里云 DashScope API Key（在[ dashscope.console.aliyun.com ](https://dashscope.console.aliyun.com)获取）
- `MYSQL_ROOT_PASSWORD`: 设置一个强密码

### 4. 构建镜像

```bash
docker compose build
```

### 5. 启动所有服务

```bash
docker compose up -d
```

启动顺序是自动的：MySQL → Milvus → Redis → Web + Worker + Beat，健康检查通过后才会启动下游服务。整个冷启动约需 2-3 分钟。

### 6. 验证部署

```bash
# 健康检查
curl http://localhost:8000/health

# 测试对话接口
curl -X POST http://localhost:8000/chat \
  -H "Content-Type: application/json" \
  -d '{"query":"推荐北京的景点"}'

# 查看服务状态
docker compose ps
```

---

## 数据初始化

### 导入种子数据（可选）

如果你的 MySQL 和 Milvus 中没有种子数据，可以手动导入：

```bash
# 进入 web 容器执行数据导入脚本
docker compose exec web python scripts/seed_data.py
```

> 种子数据文件位于 `db/` 目录下，包含景点、航班、酒店等旅游数据。

---

## 常用运维命令

```bash
# ===== 查看状态 =====
docker compose ps                    # 所有服务状态
docker compose logs -f web           # Web 服务实时日志
docker compose logs -f worker        # Celery Worker 日志
docker compose logs --tail=100 web   # 最近 100 行日志

# ===== 重启服务 =====
docker compose restart web           # 重启 Web
docker compose restart worker        # 重启 Worker
docker compose restart beat          # 重启 Beat
docker compose down && docker compose up -d  # 全部重建重启

# ===== 数据备份 =====
# MySQL
docker compose exec mysql mysqldump -u root -p Tourism > backup_$(date +%Y%m%d).sql

# Redis
docker compose cp redis:/data/appendonly.aof redis_backup_$(date +%Y%m%d).aof

# ===== 进入容器调试 =====
docker compose exec web bash         # 进入 Web 容器
docker compose exec mysql mysql -u root -p  # 进入 MySQL
docker compose exec redis redis-cli  # 进入 Redis

# ===== 更新部署 =====
git pull                             # 拉取最新代码
docker compose build                 # 重新构建
docker compose up -d                 # 滚动更新
```

---

## 配置 Nginx 反向代理（推荐）

生产环境建议通过 Nginx 反代，提供 HTTPS 支持和静态资源加速：

```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        # SSE 流式输出支持（/chat/stream）
        proxy_buffering off;
        proxy_cache off;
        proxy_read_timeout 300s;
    }
}
```

使用 Let's Encrypt 免费 SSL 证书：

```bash
apt install -y certbot python3-certbot-nginx
certbot --nginx -d your-domain.com
```

---

## 安全建议

1. **修改默认密码**: `.env` 中的 `MYSQL_ROOT_PASSWORD` 务必修改为强密码
2. **限制端口暴露**: 生产环境建议不对外暴露 MySQL(3307)、Redis(6379)、Milvus(19530)，仅保留 Web(8000)
3. **防火墙**: 使用 `ufw` 或安全组规则限制访问来源 IP
4. **定期备份**: 设置 cron 定时备份 MySQL 数据到 OSS 或其他存储
5. **日志轮转**: 配置日志轮转避免磁盘写满

---

## 常见问题

**Q: Milvus 启动失败？**
A: Milvus standalone 需要至少 2GB 空闲内存，检查 `docker compose logs milvus` 确认错误信息。

**Q: MySQL 连接被拒绝？**
A: 等待健康检查通过（约 30 秒），或检查 `.env` 中的 `MYSQL_ROOT_PASSWORD` 是否与 docker-compose 中一致。

**Q: LLM 调用失败？**
A: 确认 `APP_API_KEY` 有效，且 DashScope 账户余额充足。
