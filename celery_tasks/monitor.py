"""Celery 任务监控 —— 失败任务记录、Agent 健康检查、指标汇总。

优化说明 (P4):
  - 订阅 Celery task_failed 信号，自动记录失败任务到 Redis
  - 定时清理过期 Agent（调用 AgentRegistry.cleanup_expired）
  - 定时汇总失败任务数并记录日志
"""
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import redis as redis_sync
from loguru import logger

from celery_app import app
from celery.signals import task_failure
from config import REDIS_CONFIG

FAILED_TASKS_KEY = "celery:failed_tasks"
FAILED_TASKS_MAX = 100  # 最多保留最近 100 条失败记录
FAILURE_ALERT_THRESHOLD = 10  # 连续失败超过此值触发 WARNING


# ==================== 信号订阅 ====================

@task_failure.connect
def on_task_failure(sender=None, task_id=None, exception=None, args=None,
                    kwargs=None, traceback=None, einfo=None, **other):
    """Celery 任务失败信号处理 —— 自动记录到 Redis List。"""
    try:
        r = _get_redis()
        record = json.dumps({
            "task_id": task_id,
            "task_name": sender.name if sender else "unknown",
            "exception": str(exception),
            "args": str(args)[:200] if args else "",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        }, ensure_ascii=False)
        r.lpush(FAILED_TASKS_KEY, record)
        r.ltrim(FAILED_TASKS_KEY, 0, FAILED_TASKS_MAX - 1)
        logger.error(f"CeleryMonitor: task failed '{sender.name}' (id={task_id}): {exception}")
    except Exception as e:
        logger.error(f"CeleryMonitor: failed to record task failure: {e}")


# ==================== 定时任务 ====================

@app.task(name="celery_tasks.monitor.check_agent_health")
def check_agent_health():
    """定时清理过期 Agent（由 Celery Beat 每 5 分钟触发）。

    注意: 此任务运行在 Celery worker 中，通过同步 Redis 客户端操作。
    """
    try:
        r = _get_redis()
        from config import AGENT_BUS_CONFIG
        from datetime import datetime, timezone

        now = datetime.now(timezone.utc)
        heartbeat_timeout = AGENT_BUS_CONFIG["agent_heartbeat_timeout"]
        cleaned = 0

        cursor = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match="agent_registry:*", count=100)
            for key in keys:
                key_str = key.decode() if isinstance(key, bytes) else key
                agent_name = key_str.replace("agent_registry:", "")
                hb_str = r.hget(key_str, "last_heartbeat")
                if hb_str:
                    hb_str = hb_str.decode() if isinstance(hb_str, bytes) else hb_str
                    try:
                        last_hb = datetime.fromisoformat(hb_str)
                        elapsed = (now - last_hb).total_seconds()
                        if elapsed >= heartbeat_timeout:
                            r.delete(key_str)
                            r.delete(f"agent:queue:{agent_name}")
                            cleaned += 1
                            logger.warning(
                                f"CeleryMonitor: cleaned expired agent '{agent_name}' "
                                f"(elapsed={elapsed:.0f}s > timeout={heartbeat_timeout}s)"
                            )
                    except (ValueError, TypeError):
                        pass
            if cursor == 0:
                break

        if cleaned:
            logger.info(f"CeleryMonitor: check_agent_health cleaned {cleaned} expired agents")
        return {"cleaned": cleaned}
    except Exception as e:
        logger.error(f"CeleryMonitor: check_agent_health failed: {e}")
        return {"error": str(e)}


@app.task(name="celery_tasks.monitor.report_failed_tasks")
def report_failed_tasks():
    """汇总最近失败任务数并记录日志（由 Celery Beat 每 15 分钟触发）。"""
    try:
        r = _get_redis()
        failed_count = r.llen(FAILED_TASKS_KEY)

        if failed_count >= FAILURE_ALERT_THRESHOLD:
            logger.warning(
                f"CeleryMonitor: {failed_count} failed tasks in queue "
                f"(threshold={FAILURE_ALERT_THRESHOLD})"
            )
        else:
            logger.info(f"CeleryMonitor: {failed_count} failed tasks in queue")

        return {"failed_task_count": failed_count}
    except Exception as e:
        logger.error(f"CeleryMonitor: report_failed_tasks failed: {e}")
        return {"error": str(e)}


@app.task(name="celery_tasks.monitor.retry_failed_task")
def retry_failed_task(task_index: int = 0):
    """手动重试失败任务（从 Redis List 中取出）。

    Args:
        task_index: 要重试的任务在列表中的索引（0 = 最新）
    """
    try:
        r = _get_redis()
        record = r.lindex(FAILED_TASKS_KEY, task_index)
        if not record:
            return {"error": "No failed task at index {task_index}"}

        record = record.decode() if isinstance(record, bytes) else record
        data = json.loads(record)
        logger.info(f"CeleryMonitor: retrying task '{data['task_name']}' (id={data['task_id']})")

        # 重新发送任务
        app.send_task(data["task_name"], args=(), kwargs={})
        return {"retried": data["task_name"]}
    except Exception as e:
        logger.error(f"CeleryMonitor: retry_failed_task failed: {e}")
        return {"error": str(e)}


# ==================== 工具函数 ====================

def _get_redis() -> redis_sync.Redis:
    """获取同步 Redis 连接（Celery worker 环境）。"""
    return redis_sync.Redis(
        host=REDIS_CONFIG["host"],
        port=REDIS_CONFIG["port"],
        db=REDIS_CONFIG["db"],
        password=REDIS_CONFIG["password"] or None,
        socket_connect_timeout=3,
        decode_responses=False,  # 保持 bytes 以兼容 hget/lrange
    )


def get_failed_tasks(limit: int = 50) -> List[Dict[str, Any]]:
    """获取最近失败任务列表（供 API 查询使用）。

    Args:
        limit: 返回数量上限

    Returns:
        失败任务记录列表
    """
    try:
        r = _get_redis()
        records = r.lrange(FAILED_TASKS_KEY, 0, limit - 1)
        return [
            json.loads(r.decode() if isinstance(r, bytes) else r)
            for r in records
        ]
    except Exception:
        return []
