# -*- coding: utf-8 -*-
"""
JWT Token 处理
"""

import jwt
import hashlib
import secrets
from datetime import datetime, timedelta
from typing import Optional, Tuple
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import (
    JWT_SECRET_KEY,
    JWT_ALGORITHM,
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES,
    JWT_REFRESH_TOKEN_EXPIRE_DAYS
)

class TokenError(Exception):
    """Token 相关错误"""
    pass

def create_access_token(
    user_id: int,
    user_type: str = "user",  # "user" or "admin"
    extra_data: dict = None
) -> str:
    """
    创建 Access Token

    Args:
        user_id: 用户ID
        user_type: 用户类型 (user/admin)
        extra_data: 额外数据

    Returns:
        JWT Token 字符串
    """
    expire = datetime.utcnow() + timedelta(minutes=JWT_ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "sub": str(user_id),
        "type": user_type,
        "exp": expire,
        "iat": datetime.utcnow(),
        "token_type": "access"
    }
    if extra_data:
        payload.update(extra_data)

    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)

def create_refresh_token() -> Tuple[str, str]:
    """
    创建 Refresh Token

    Returns:
        (token, token_hash) - 原始 token 返回给客户端, hash 存数据库
    """
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode()).hexdigest()
    return token, token_hash

def decode_token(token: str) -> dict:
    """
    解码并验证 Token

    Args:
        token: JWT Token 字符串

    Returns:
        解码后的 payload

    Raises:
        TokenError: Token 无效或过期
    """
    try:
        payload = jwt.decode(
            token,
            JWT_SECRET_KEY,
            algorithms=[JWT_ALGORITHM]
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise TokenError("Token 已过期")
    except jwt.InvalidTokenError as e:
        raise TokenError(f"Token 无效: {str(e)}")

def verify_refresh_token(token: str) -> str:
    """
    验证 Refresh Token 并返回 hash

    Args:
        token: 原始 refresh token

    Returns:
        token 的 hash 值 (用于数据库查询)
    """
    return hashlib.sha256(token.encode()).hexdigest()

def get_token_from_header(authorization: str) -> str:
    """
    从 Authorization header 提取 token

    Args:
        authorization: "Bearer <token>" 格式的字符串

    Returns:
        token 字符串

    Raises:
        TokenError: 格式错误
    """
    if not authorization:
        raise TokenError("缺少认证信息")

    parts = authorization.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise TokenError("认证格式错误，应为 'Bearer <token>'")

    return parts[1]
