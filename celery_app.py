"""Celery 应用配置 —— 基于 Redis 作为消息代理和结果后端。

优化说明 (P4):
  - 使用 CELERY_REDIS_CONFIG (DB=1)，与业务 Redis (DB=0) 隔离
  - 新增任务失败监控 (celery_tasks.monitor)
  - 新增定时任务: Agent 健康检查、失败任务汇总
"""

from celery import Celery
from celery.schedules import crontab
from config import CELERY_REDIS_CONFIG

broker_url = (
    f"redis://{CELERY_REDIS_CONFIG['host']}:{CELERY_REDIS_CONFIG['port']}"
    f"/{CELERY_REDIS_CONFIG['db']}"
)
result_backend = broker_url

app = Celery(
    "tourism_assistant",
    broker=broker_url,
    backend=result_backend,
    include=["celery_tasks.maintenance", "celery_tasks.memory", "celery_tasks.monitor", "celery_tasks.cache"],
)

app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    task_track_started=True,
    task_time_limit=600,
    task_soft_time_limit=300,
    worker_max_tasks_per_child=200,
    worker_prefetch_multiplier=1,
)

# 定时任务调度
app.conf.beat_schedule = {
    "cleanup-expired-sessions-every-hour": {
        "task": "celery_tasks.maintenance.cleanup_expired_sessions",
        "schedule": crontab(minute=0),
    },
    "prune-old-query-logs-daily": {
        "task": "celery_tasks.maintenance.prune_old_query_logs",
        "schedule": crontab(hour=3, minute=0),
    },
    "compress-conversations-every-30min": {
        "task": "celery_tasks.memory.compress_old_conversations",
        "schedule": crontab(minute="*/30"),
    },
    # P4 新增: Agent 注册中心健康检查（每 5 分钟）
    "check-agent-health-every-5min": {
        "task": "celery_tasks.monitor.check_agent_health",
        "schedule": crontab(minute="*/5"),
    },
    # P4 新增: 失败任务汇总（每 15 分钟）
    "report-failed-tasks-every-15min": {
        "task": "celery_tasks.monitor.report_failed_tasks",
        "schedule": crontab(minute="*/15"),
    },
    # P4 新增: 热点数据缓存预热（每 5 分钟）
    "preload-hot-cache-every-5min": {
        "task": "celery_tasks.cache.preload_hot_data",
        "schedule": crontab(minute="*/5"),
    },
    # P4 新增: 过期缓存统计（每天凌晨 4 点）
    "invalidate-stale-cache-daily": {
        "task": "celery_tasks.cache.invalidate_stale_cache",
        "schedule": crontab(hour=4, minute=0),
    },
}
