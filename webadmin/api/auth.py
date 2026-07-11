"""JWT 鉴权、bcrypt 密码哈希、登录失败锁定、FastAPI 依赖项。

模块通过 AuthManager 单例管理鉴权状态，由 app.py 在启动时调用 init_auth 初始化。
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

import jwt
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from passlib.context import CryptContext

# OAuth2 token 入口指向登录路由
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/api/auth/login")

# bcrypt 密码上下文
_pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")

# 登录失败锁定策略
_MAX_FAIL_COUNT = 5
_LOCK_SECONDS = 5 * 60
# 密码最小长度
MIN_PASSWORD_LENGTH = 8


class AuthManager:
    """鉴权状态管理器（单例，由 app.py 初始化）。"""

    def __init__(self) -> None:
        self.secret_key: str = ""
        self.token_expire_minutes: int = 720
        self.algorithm: str = "HS256"
        # 用户列表，每项: {username, password_hash, role, must_change_password}
        self.users: List[Dict[str, Any]] = []
        # 持久化回调，修改用户后写回 webadmin.json
        self._persist_callback = None
        # 登录失败记录: username -> {"count": int, "lock_until": float}
        self._fail_records: Dict[str, Dict[str, Any]] = {}

    def configure(
        self,
        secret_key: str,
        token_expire_minutes: int,
        users: List[Dict[str, Any]],
        persist_callback=None,
    ) -> None:
        """初始化鉴权配置。"""
        self.secret_key = secret_key
        self.token_expire_minutes = token_expire_minutes
        self.users = users
        self._persist_callback = persist_callback

    # ---------- 密码工具 ----------

    def hash_password(self, password: str) -> str:
        """对明文密码进行 bcrypt 哈希。"""
        return _pwd_context.hash(password)

    def verify_password(self, plain: str, hashed: str) -> bool:
        """校验明文密码与哈希是否匹配。"""
        try:
            return _pwd_context.verify(plain, hashed)
        except Exception:
            return False

    # ---------- JWT 工具 ----------

    def create_access_token(
        self, username: str, role: str, expires_minutes: Optional[int] = None
    ) -> tuple:
        """签发 JWT，返回 (token, expires_in_seconds)。"""
        minutes = expires_minutes if expires_minutes is not None else self.token_expire_minutes
        expire = datetime.now(timezone.utc) + timedelta(minutes=minutes)
        payload = {
            "sub": username,
            "role": role,
            "exp": expire,
        }
        token = jwt.encode(payload, self.secret_key, algorithm=self.algorithm)
        return token, minutes * 60

    def decode_token(self, token: str) -> Dict[str, Any]:
        """解码并校验 JWT，返回 payload。"""
        return jwt.decode(token, self.secret_key, algorithms=[self.algorithm])

    # ---------- 用户查询 ----------

    def find_user(self, username: str) -> Optional[Dict[str, Any]]:
        """按用户名查找用户。"""
        for u in self.users:
            if u.get("username") == username:
                return u
        return None

    def list_users(self) -> List[Dict[str, Any]]:
        """返回用户列表（不含密码哈希）。"""
        return [
            {
                "username": u.get("username"),
                "role": u.get("role", "viewer"),
                "must_change_password": u.get("must_change_password", False),
            }
            for u in self.users
        ]

    def add_user(self, username: str, password: str, role: str = "viewer") -> Dict[str, Any]:
        """新增用户。"""
        if self.find_user(username):
            raise ValueError(f"用户 {username} 已存在")
        if len(password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"密码长度至少 {MIN_PASSWORD_LENGTH} 位")
        user = {
            "username": username,
            "password_hash": self.hash_password(password),
            "role": role,
            "must_change_password": False,
        }
        self.users.append(user)
        self._persist()
        return {
            "username": username,
            "role": role,
            "must_change_password": False,
        }

    def delete_user(self, username: str) -> None:
        """删除用户。"""
        user = self.find_user(username)
        if not user:
            raise ValueError(f"用户 {username} 不存在")
        self.users.remove(user)
        self._persist()

    def change_password(
        self, username: str, old_password: str, new_password: str
    ) -> None:
        """修改密码，校验旧密码，清除 must_change_password。"""
        user = self.find_user(username)
        if not user:
            raise ValueError("用户不存在")
        if not self.verify_password(old_password, user.get("password_hash", "")):
            raise ValueError("旧密码不正确")
        if len(new_password) < MIN_PASSWORD_LENGTH:
            raise ValueError(f"新密码长度至少 {MIN_PASSWORD_LENGTH} 位")
        user["password_hash"] = self.hash_password(new_password)
        user["must_change_password"] = False
        self._persist()

    # ---------- 登录失败锁定 ----------

    def _check_locked(self, username: str) -> Optional[int]:
        """检查用户是否被锁定，返回剩余锁定秒数；未锁定返回 None。"""
        record = self._fail_records.get(username)
        if not record:
            return None
        lock_until = record.get("lock_until", 0)
        now = time.time()
        if lock_until > now:
            return int(lock_until - now)
        # 锁定已过期，清除记录
        if record.get("count", 0) >= _MAX_FAIL_COUNT:
            self._fail_records.pop(username, None)
        return None

    def _record_fail(self, username: str) -> None:
        """记录一次登录失败。"""
        record = self._fail_records.setdefault(
            username, {"count": 0, "lock_until": 0}
        )
        record["count"] = record.get("count", 0) + 1
        if record["count"] >= _MAX_FAIL_COUNT:
            record["lock_until"] = time.time() + _LOCK_SECONDS

    def _clear_fail(self, username: str) -> None:
        """登录成功后清除失败记录。"""
        self._fail_records.pop(username, None)

    def authenticate(self, username: str, password: str) -> Dict[str, Any]:
        """校验用户名密码，返回用户字典；失败抛出 ValueError。

        若账户被锁定，ValueError 的消息中包含剩余秒数。
        """
        remaining = self._check_locked(username)
        if remaining is not None:
            raise ValueError(f"账户已锁定，请 {remaining} 秒后重试")
        user = self.find_user(username)
        if not user:
            self._record_fail(username)
            raise ValueError("用户名或密码错误")
        if not self.verify_password(password, user.get("password_hash", "")):
            self._record_fail(username)
            raise ValueError("用户名或密码错误")
        self._clear_fail(username)
        return user

    # ---------- 持久化 ----------

    def _persist(self) -> None:
        """触发配置写回 webadmin.json。"""
        if self._persist_callback:
            try:
                self._persist_callback()
            except Exception:
                pass


# 模块级单例
_auth_manager: Optional[AuthManager] = None


def init_auth(
    secret_key: str,
    token_expire_minutes: int,
    users: List[Dict[str, Any]],
    persist_callback=None,
) -> AuthManager:
    """初始化全局 AuthManager 单例。"""
    global _auth_manager
    if _auth_manager is None:
        _auth_manager = AuthManager()
    _auth_manager.configure(secret_key, token_expire_minutes, users, persist_callback)
    return _auth_manager


def get_auth_manager() -> AuthManager:
    """获取全局 AuthManager。"""
    if _auth_manager is None:
        # 未初始化时返回空配置实例，避免启动期报错
        return init_auth("default-secret", 720, [])
    return _auth_manager


# ---------- 快捷函数 ----------


def create_access_token(username: str, role: str, expires_minutes: Optional[int] = None) -> tuple:
    """签发 JWT 的快捷函数。"""
    return get_auth_manager().create_access_token(username, role, expires_minutes)


def hash_password(password: str) -> str:
    """密码哈希快捷函数。"""
    return get_auth_manager().hash_password(password)


def verify_password(plain: str, hashed: str) -> bool:
    """密码校验快捷函数。"""
    return get_auth_manager().verify_password(plain, hashed)


# ---------- FastAPI 依赖项 ----------


async def get_current_user(token: str = Depends(oauth2_scheme)) -> Dict[str, Any]:
    """从 JWT 解析当前用户。"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    manager = get_auth_manager()
    try:
        payload = manager.decode_token(token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="token 已过期",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except jwt.PyJWTError:
        raise credentials_exception

    username = payload.get("sub")
    if not username:
        raise credentials_exception

    user = manager.find_user(username)
    if not user:
        raise credentials_exception
    return user


async def require_admin(user: Dict[str, Any] = Depends(get_current_user)) -> Dict[str, Any]:
    """要求当前用户为 admin 角色。"""
    if user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限",
        )
    return user


async def get_user_from_query_token(token: Optional[str]) -> Dict[str, Any]:
    """从 query 参数解析 token（用于 WebSocket 鉴权）。

    返回用户字典；无效则抛出 HTTPException(401)。
    """
    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="缺少 token 参数",
        )
    manager = get_auth_manager()
    try:
        payload = manager.decode_token(token)
    except jwt.PyJWTError:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 token",
        )
    username = payload.get("sub")
    if not username:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="无效的 token",
        )
    user = manager.find_user(username)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在",
        )
    return user
