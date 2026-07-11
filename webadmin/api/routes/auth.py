"""鉴权路由 /api/auth/*。"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, status

from ..auth import get_auth_manager, get_current_user, require_admin
from ..schemas import (
    ChangePasswordRequest,
    CreateUserRequest,
    LoginRequest,
    LoginResponse,
    MessageResponse,
    RefreshTokenResponse,
    UserListItem,
    UserInfoResponse,
)

router = APIRouter(prefix="/api/auth", tags=["auth"])


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """用户登录，校验密码并签发 JWT。"""
    manager = get_auth_manager()
    try:
        user = manager.authenticate(req.username, req.password)
    except ValueError as e:
        # 区分锁定与其他错误：锁定返回 423
        msg = str(e)
        if "锁定" in msg:
            raise HTTPException(status_code=status.HTTP_423_LOCKED, detail=msg)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=msg,
            headers={"WWW-Authenticate": "Bearer"},
        )

    token, expires_in = manager.create_access_token(
        user["username"], user.get("role", "viewer")
    )
    # 首次登录若 must_change_password=true 则在响应中携带该字段
    must_change = user.get("must_change_password", False)
    resp = LoginResponse(
        access_token=token,
        token_type="bearer",
        expires_in=expires_in,
    )
    if must_change:
        resp.must_change_password = True
    return resp


@router.post("/logout", response_model=MessageResponse)
async def logout():
    """登出（前端清除 token，后端无操作）。"""
    return MessageResponse(message="已登出")


@router.post("/refresh", response_model=RefreshTokenResponse)
async def refresh_token(user=Depends(get_current_user)):
    """刷新 token。"""
    manager = get_auth_manager()
    token, expires_in = manager.create_access_token(
        user["username"], user.get("role", "viewer")
    )
    return RefreshTokenResponse(access_token=token, expires_in=expires_in)


@router.get("/me", response_model=UserInfoResponse)
async def me(user=Depends(get_current_user)):
    """返回当前用户信息。"""
    return UserInfoResponse(
        username=user["username"],
        role=user.get("role", "viewer"),
        must_change_password=user.get("must_change_password", False),
    )


@router.post("/users", response_model=UserListItem, dependencies=[Depends(require_admin)])
async def create_user(req: CreateUserRequest):
    """创建用户（仅 admin）。"""
    manager = get_auth_manager()
    try:
        return manager.add_user(req.username, req.password, req.role)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/users", response_model=list[UserListItem], dependencies=[Depends(require_admin)])
async def list_users():
    """用户列表（仅 admin，不含密码哈希）。"""
    manager = get_auth_manager()
    return manager.list_users()


@router.delete("/users/{username}", response_model=MessageResponse, dependencies=[Depends(require_admin)])
async def delete_user(username: str, current_user=Depends(get_current_user)):
    """删除用户（仅 admin，不能删自己）。"""
    if username == current_user["username"]:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="不能删除当前登录用户",
        )
    manager = get_auth_manager()
    try:
        manager.delete_user(username)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    return MessageResponse(message=f"用户 {username} 已删除")


@router.put("/password", response_model=MessageResponse)
async def change_password(req: ChangePasswordRequest, user=Depends(get_current_user)):
    """修改密码，校验旧密码，清除 must_change_password。"""
    manager = get_auth_manager()
    try:
        manager.change_password(user["username"], req.old_password, req.new_password)
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))
    return MessageResponse(message="密码已修改")
