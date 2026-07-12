"""ORM 模型定义 —— 用户、偏好、对话历史、查询日志四张表（SQLAlchemy）。"""

from datetime import datetime
from typing import Optional
from contextlib import contextmanager

from sqlalchemy import (
    create_engine, Column, Integer, String, Text, Float,
    DateTime, Boolean, ForeignKey, JSON, Index
)
from sqlalchemy.orm import declarative_base, sessionmaker, Session, relationship
from loguru import logger

from config import MYSQL_URL

engine = create_engine(MYSQL_URL, pool_pre_ping=True, pool_recycle=3600)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


@contextmanager
def get_session() -> Session:
    """获取数据库会话上下文管理器"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def init_db():
    """初始化数据库表（包含缺失列的迁移）"""
    Base.metadata.create_all(bind=engine)
    # 兼容性迁移：为已有 users 表补充 password_hash 列
    with engine.connect() as conn:
        try:
            conn.exec_driver_sql(
                "ALTER TABLE users ADD COLUMN password_hash VARCHAR(255) NULL"
            )
            conn.commit()
        except Exception:
            pass  # 列已存在则忽略
    logger.info("Database tables initialized successfully")


# ==================== 用户表 ====================
class User(Base):
    """用户表 —— 存储注册用户的基本信息。"""
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, autoincrement=True)
    username = Column(String(50), unique=True, nullable=False, index=True)
    password_hash = Column(String(255))
    nickname = Column(String(100))
    avatar = Column(String(255))
    email = Column(String(100))
    phone = Column(String(20))
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)
    is_active = Column(Boolean, default=True)

    preferences = relationship("UserPreference", back_populates="user", cascade="all, delete-orphan")
    history = relationship("ConversationHistory", back_populates="user", cascade="all, delete-orphan")
    query_logs = relationship("QueryLog", back_populates="user", cascade="all, delete-orphan")


# ==================== 用户偏好表（长期记忆） ====================
class UserPreference(Base):
    """用户偏好表（长期记忆） —— 存储用户旅行偏好，如城市、交通、酒店等。"""
    __tablename__ = "user_preferences"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    category = Column(String(50), nullable=False, comment="偏好类别: travel_style/budget/transport/hotel/destination/food")
    key = Column(String(100), nullable=False)
    value = Column(Text, nullable=False)
    confidence = Column(Float, default=1.0, comment="置信度 0-1")
    source = Column(String(50), default="explicit", comment="来源: explicit用户明确设置 / inferred系统推断")
    created_at = Column(DateTime, default=datetime.now)
    updated_at = Column(DateTime, default=datetime.now, onupdate=datetime.now)

    user = relationship("User", back_populates="preferences")

    __table_args__ = (
        Index("idx_user_category", "user_id", "category"),
    )


# ==================== 对话历史表（短期记忆持久化） ====================
class ConversationHistory(Base):
    """对话历史表（短期记忆持久化） —— 记录用户每轮对话的问答内容。"""
    __tablename__ = "conversation_history"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String(50), nullable=False, index=True)
    role = Column(String(20), nullable=False, comment="user / assistant / system")
    content = Column(Text, nullable=False)
    intent = Column(String(50), comment="该轮对话的意图")
    slots = Column(JSON, comment="该轮提取的槽位")
    metadata_json = Column(JSON, comment="额外元数据")
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="history")

    __table_args__ = (
        Index("idx_user_session", "user_id", "session_id"),
        Index("idx_session_time", "session_id", "created_at"),
    )


# ==================== 查询日志表（长期记忆） ====================
class QueryLog(Base):
    """查询日志表（长期记忆） —— 记录用户每次查询的完整链路信息。"""
    __tablename__ = "query_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="CASCADE"), nullable=False, index=True)
    session_id = Column(String(50), nullable=False)
    query = Column(Text, nullable=False)
    intent = Column(String(50))
    slots = Column(JSON)
    tool_name = Column(String(50))
    tool_result_summary = Column(Text, comment="工具返回结果摘要")
    final_answer = Column(Text)
    duration_ms = Column(Integer, comment="处理耗时（毫秒）")
    success = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.now)

    user = relationship("User", back_populates="query_logs")

    __table_args__ = (
        Index("idx_user_time", "user_id", "created_at"),
    )
