"""维护类定时任务 —— 清理过期数据、压缩历史记录。"""

from celery_app import app
from loguru import logger
from config import REDIS_CONFIG
from models.orm_models import get_session, QueryLog
import redis

from datetime import datetime, timedelta


@app.task(name="celery_tasks.maintenance.cleanup_expired_sessions")
def cleanup_expired_sessions():
    """清理 Redis 中已过期的会话键（每小时执行）。"""
    try:
        r = redis.Redis(
            host=REDIS_CONFIG["host"],
            port=REDIS_CONFIG["port"],
            db=REDIS_CONFIG["db"],
            password=REDIS_CONFIG["password"] or None,
            socket_connect_timeout=3,
        )
        # 扫描 session 键，检查是否快过期或已过期
        cursor = 0
        cleaned = 0
        while True:
            cursor, keys = r.scan(cursor=cursor, match="session:*", count=100)
            for key in keys:
                ttl = r.ttl(key)
                # 清理 TTL <= 0 的键（已过期或未设 TTL）
                if ttl <= 0:
                    r.delete(key)
                    cleaned += 1
            if cursor == 0:
                break
        logger.info(f"CleanupExpiredSessions: cleaned {cleaned} expired session keys")
        return {"cleaned": cleaned}
    except Exception as e:
        logger.error(f"CleanupExpiredSessions failed: {e}")
        return {"error": str(e)}


@app.task(name="celery_tasks.maintenance.prune_old_query_logs")
def prune_old_query_logs(retention_days: int = 90):
    """删除超过保留期的查询日志（每天凌晨 3 点执行，默认保留 90 天）。"""
    try:
        cutoff = datetime.now() - timedelta(days=retention_days)
        with get_session() as session:
            deleted = session.query(QueryLog).filter(
                QueryLog.created_at < cutoff
            ).delete(synchronize_session="fetch")
            session.commit()
        logger.info(f"PruneOldQueryLogs: deleted {deleted} records older than {cutoff.date()}")
        return {"deleted": deleted, "cutoff": cutoff.isoformat()}
    except Exception as e:
        logger.error(f"PruneOldQueryLogs failed: {e}")
        return {"error": str(e)}
