# -*- coding: utf-8 -*-
"""
FastAPI 认证依赖
"""

from fastapi import Depends, HTTPException, status, Header, Query
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from .jwt_handler import decode_token, TokenError
from db import crud

# HTTP Bearer 认证方案
security = HTTPBearer(auto_error=False)

async def get_current_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    获取当前登录的用户

    依赖项：从 Authorization header 获取 JWT 并验证

    Returns:
        用户信息字典

    Raises:
        HTTPException: 未认证或 Token 无效
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        payload = decode_token(credentials.credentials)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"}
        )

    # 验证 token 类型
    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 类型错误"
        )

    # 验证用户类型
    if payload.get("type") != "user":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="需要用户身份"
        )

    # 获取用户信息
    user_id = int(payload["sub"])
    user = await crud.get_user_by_id(user_id)

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在"
        )

    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用"
        )

    # 检查授权是否过期
    from datetime import datetime
    if datetime.fromisoformat(user["auth_expires_at"]) < datetime.now():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号授权已过期，请联系管理员续期"
        )

    return user

async def get_current_admin(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> dict:
    """
    获取当前登录的管理员

    Returns:
        管理员信息字典

    Raises:
        HTTPException: 未认证或非管理员
    """
    if not credentials:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="未提供认证信息",
            headers={"WWW-Authenticate": "Bearer"}
        )

    try:
        payload = decode_token(credentials.credentials)
    except TokenError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=str(e),
            headers={"WWW-Authenticate": "Bearer"}
        )

    # 验证 token 类型
    if payload.get("token_type") != "access":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token 类型错误"
        )

    # 验证用户类型
    if payload.get("type") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="需要管理员权限"
        )

    # 获取管理员信息
    admin_id = int(payload["sub"])
    admin = await crud.get_admin_by_id(admin_id)

    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="管理员不存在"
        )

    if not admin["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员账号已被禁用"
        )

    return admin

async def get_optional_user(
    credentials: HTTPAuthorizationCredentials = Depends(security)
) -> Optional[dict]:
    """
    可选的用户认证 - 不强制要求登录

    Returns:
        用户信息或 None
    """
    if not credentials:
        return None

    try:
        return await get_current_user(credentials)
    except HTTPException:
        return None

def require_auth(required: bool = True):
    """
    认证装饰器工厂

    Args:
        required: 是否必须认证
    """
    if required:
        return Depends(get_current_user)
    return Depends(get_optional_user)

async def get_token_from_query(token: str = Query(None, description="JWT Token")) -> Optional[dict]:
    """
    从查询参数获取用户 (用于 WebSocket)

    Args:
        token: URL 查询参数中的 token

    Returns:
        用户信息或 None
    """
    if not token:
        return None

    try:
        payload = decode_token(token)
        if payload.get("token_type") != "access" or payload.get("type") != "user":
            return None

        user_id = int(payload["sub"])
        user = await crud.get_user_by_id(user_id)

        if not user or not user["is_active"]:
            return None

        from datetime import datetime
        if datetime.fromisoformat(user["auth_expires_at"]) < datetime.now():
            return None

        return user
    except:
        return None
