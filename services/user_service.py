"""用户管理服务"""
import asyncio
import hashlib
import os
from datetime import datetime
from typing import Dict, List, Optional
from loguru import logger
from models.orm_models import get_session, User


def _hash_password(password: str) -> str:
    """Hash password with PBKDF2-SHA256 and random salt."""
    salt = os.urandom(32)
    key = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, 100000)
    return f"pbkdf2:sha256:100000${salt.hex()}${key.hex()}"


def _verify_password(password: str, stored: str) -> bool:
    """Verify password against stored PBKDF2 hash.

    Stored format: pbkdf2:sha256:100000$salt_hex$key_hex
    """
    try:
        method, salt_hex, key_hex = stored.split("$")
        algo, hash_name, iterations_str = method.split(":")
        iterations = int(iterations_str)
        salt = bytes.fromhex(salt_hex)
        stored_key = bytes.fromhex(key_hex)
        new_key = hashlib.pbkdf2_hmac(hash_name, password.encode("utf-8"), salt, iterations)
        return new_key == stored_key
    except (ValueError, AttributeError):
        return False


class UserService:
    """用户管理 —— 所有 DB 操作通过 asyncio.to_thread 异步化"""

    async def register_user(self, username: str, password: str,
                            nickname: str = "", email: str = "", phone: str = "") -> Optional[Dict]:
        """注册新用户（带密码）。如果用户已存在但无密码（老数据），则为老用户设置密码。"""
        if len(password) < 6:
            logger.warning(f"Registration failed: password too short for {username}")
            return None

        def _sync():
            with get_session() as s:
                existing = s.query(User).filter_by(username=username).first()
                if existing:
                    if existing.password_hash:
                        logger.warning(f"Registration failed: username already taken: {username}")
                        return None
                    existing.password_hash = _hash_password(password)
                    if nickname:
                        existing.nickname = nickname
                    existing.updated_at = datetime.now()
                    s.flush()
                    logger.info(f"User migrated with password: id={existing.id}, username={username}")
                    return self._to_dict(existing)
                user = User(
                    username=username,
                    password_hash=_hash_password(password),
                    nickname=nickname or username,
                    email=email,
                    phone=phone,
                )
                s.add(user)
                s.flush()
                logger.info(f"User registered: id={user.id}, username={username}")
                return self._to_dict(user)

        return await asyncio.to_thread(_sync)

    async def authenticate_user(self, username: str, password: str) -> Optional[Dict]:
        """验证用户登录，成功返回用户信息，失败返回 None"""
        def _sync():
            with get_session() as s:
                user = s.query(User).filter_by(username=username, is_active=True).first()
                if not user:
                    return None
                if not user.password_hash:
                    return None
                if _verify_password(password, user.password_hash):
                    logger.info(f"User authenticated: {username}")
                    return self._to_dict(user)
                logger.warning(f"Authentication failed: wrong password for {username}")
                return None

        return await asyncio.to_thread(_sync)

    async def set_password(self, user_id: int, password: str) -> bool:
        """为用户设置/修改密码"""
        if len(password) < 6:
            return False

        def _sync():
            with get_session() as s:
                user = s.query(User).filter_by(id=user_id).first()
                if not user:
                    return False
                user.password_hash = _hash_password(password)
                user.updated_at = datetime.now()
                return True

        return await asyncio.to_thread(_sync)

    async def create_user(self, username: str, nickname: str = "",
                          email: str = "", phone: str = "") -> Optional[Dict]:
        """创建新用户（无密码，兼容旧逻辑）"""
        def _sync():
            with get_session() as s:
                existing = s.query(User).filter_by(username=username).first()
                if existing:
                    logger.warning(f"User already exists: {username}")
                    return None
                user = User(
                    username=username,
                    nickname=nickname or username,
                    email=email,
                    phone=phone,
                )
                s.add(user)
                s.flush()
                logger.info(f"User created: id={user.id}, username={username}")
                return self._to_dict(user)

        return await asyncio.to_thread(_sync)

    async def get_user(self, user_id: int) -> Optional[Dict]:
        """获取用户信息"""
        def _sync():
            with get_session() as s:
                user = s.query(User).filter_by(id=user_id, is_active=True).first()
                return self._to_dict(user) if user else None

        return await asyncio.to_thread(_sync)

    async def get_user_by_username(self, username: str) -> Optional[Dict]:
        """通过用户名查找"""
        def _sync():
            with get_session() as s:
                user = s.query(User).filter_by(username=username, is_active=True).first()
                return self._to_dict(user) if user else None

        return await asyncio.to_thread(_sync)

    async def get_or_create_user(self, username: str, **kwargs) -> Dict:
        """获取或创建用户"""
        user = await self.get_user_by_username(username)
        if user:
            return user
        result = await self.create_user(username, **kwargs)
        if result:
            return result
        return await self.get_user_by_username(username)

    async def update_user(self, user_id: int, **kwargs) -> bool:
        """更新用户信息"""
        def _sync():
            with get_session() as s:
                user = s.query(User).filter_by(id=user_id).first()
                if not user:
                    return False
                allowed = ["nickname", "avatar", "email", "phone"]
                for k, v in kwargs.items():
                    if k in allowed and v is not None:
                        setattr(user, k, v)
                user.updated_at = datetime.now()
                return True

        return await asyncio.to_thread(_sync)

    async def deactivate_user(self, user_id: int) -> bool:
        """停用用户"""
        def _sync():
            with get_session() as s:
                user = s.query(User).filter_by(id=user_id).first()
                if not user:
                    return False
                user.is_active = False
                user.updated_at = datetime.now()
                return True

        return await asyncio.to_thread(_sync)

    async def list_users(self, limit: int = 50) -> List[Dict]:
        """用户列表"""
        def _sync():
            with get_session() as s:
                users = s.query(User).filter_by(is_active=True).limit(limit).all()
                return [self._to_dict(u) for u in users]

        return await asyncio.to_thread(_sync)

    def _to_dict(self, user: User) -> Dict:
        return {
            "id": user.id,
            "username": user.username,
            "nickname": user.nickname,
            "avatar": user.avatar,
            "email": user.email,
            "phone": user.phone,
            "created_at": user.created_at.isoformat() if user.created_at else "",
        }


# 全局单例
user_service = UserService()
