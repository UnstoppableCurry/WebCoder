# -*- coding: utf-8 -*-
"""
认证 API
"""

from fastapi import APIRouter, HTTPException, status, Depends, Header
from pydantic import BaseModel, EmailStr
from typing import Optional
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth import hash_password, verify_password, create_access_token, create_refresh_token, verify_refresh_token
from auth.dependencies import get_current_user
from db import crud
from services.email_service import send_verification_code, generate_verification_code
from config import DEFAULT_AUTH_DAYS, PROJECTS_BASE_DIR, JWT_REFRESH_TOKEN_EXPIRE_DAYS

router = APIRouter()

# ============================================================
# 请求/响应模型
# ============================================================

class RegisterRequest(BaseModel):
    email: EmailStr

class VerifyEmailRequest(BaseModel):
    email: EmailStr
    code: str
    password: str
    display_name: Optional[str] = None

class LoginRequest(BaseModel):
    email: EmailStr
    password: str

class RefreshRequest(BaseModel):
    refresh_token: str

class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int  # 秒

class UserResponse(BaseModel):
    id: int
    email: str
    display_name: Optional[str]
    auth_expires_at: str
    is_verified: bool
    created_at: str

# ============================================================
# API 端点
# ============================================================

@router.post("/register", summary="注册 - 发送验证码")
async def register(req: RegisterRequest):
    """
    注册第一步：发送验证码到邮箱
    """
    # 检查邮箱是否已注册
    existing = await crud.get_user_by_email(req.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该邮箱已注册"
        )

    # 生成验证码
    code = generate_verification_code()

    # 先发送邮件，成功后再保存验证码（避免发送失败时数据不一致）
    success = await send_verification_code(req.email, code, "register")
    if not success:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="验证码发送失败，请检查邮箱地址或稍后重试"
        )

    # 邮件发送成功，保存验证码
    await crud.create_verification_code(req.email, code, "register")

    return {"message": "验证码已发送到您的邮箱", "email": req.email}

@router.post("/verify-email", summary="验证邮箱并完成注册")
async def verify_email(req: VerifyEmailRequest):
    """
    注册第二步：验证邮箱并设置密码
    """
    # 验证验证码
    valid = await crud.verify_code(req.email, req.code, "register")
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码无效或已过期"
        )

    # 再次检查邮箱
    existing = await crud.get_user_by_email(req.email)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="该邮箱已注册"
        )

    # 创建用户目录
    import uuid
    user_uuid = str(uuid.uuid4())[:8]
    user_dir = str(PROJECTS_BASE_DIR / user_uuid)

    # 创建用户
    password_hash = hash_password(req.password)
    user_id = await crud.create_user(
        email=req.email,
        password_hash=password_hash,
        user_dir=user_dir,
        auth_days=DEFAULT_AUTH_DAYS,
        display_name=req.display_name,
        is_verified=True
    )

    # 创建用户目录
    Path(user_dir).mkdir(parents=True, exist_ok=True)

    # 生成 Token
    access_token = create_access_token(user_id, "user")
    refresh_token, refresh_hash = create_refresh_token()
    await crud.create_refresh_token(user_id, refresh_hash, expire_days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=15 * 60
    )

@router.post("/login", response_model=TokenResponse, summary="用户登录")
async def login(req: LoginRequest):
    """
    用户登录，返回 JWT Token
    """
    # 获取用户
    user = await crud.get_user_by_email(req.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误"
        )

    # 验证密码
    if not verify_password(req.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="邮箱或密码错误"
        )

    # 检查账号状态
    if not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号已被禁用"
        )

    # 检查授权是否过期
    if datetime.fromisoformat(user["auth_expires_at"]) < datetime.now():
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="账号授权已过期，请联系管理员续期"
        )

    # 更新登录时间
    await crud.update_user_login(user["id"])

    # 生成 Token
    access_token = create_access_token(user["id"], "user")
    refresh_token, refresh_hash = create_refresh_token()
    await crud.create_refresh_token(user["id"], refresh_hash, expire_days=JWT_REFRESH_TOKEN_EXPIRE_DAYS)

    return TokenResponse(
        access_token=access_token,
        refresh_token=refresh_token,
        expires_in=15 * 60
    )

@router.post("/refresh", response_model=TokenResponse, summary="刷新 Token")
async def refresh_token(req: RefreshRequest):
    """
    使用 Refresh Token 获取新的 Access Token
    """
    # 验证 refresh token
    token_hash = verify_refresh_token(req.refresh_token)
    token_record = await crud.get_refresh_token(token_hash)

    if not token_record:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh Token 无效或已过期"
        )

    user_id = token_record["user_id"]

    # 检查用户状态
    user = await crud.get_user_by_id(user_id)
    if not user or not user["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户不存在或已被禁用"
        )

    # 更新 token 使用时间
    await crud.update_refresh_token_used(token_record["id"])

    # 生成新的 access token
    access_token = create_access_token(user_id, "user")

    # 可选：轮换 refresh token (更安全)
    # await crud.revoke_refresh_token(token_hash)
    # new_refresh, new_hash = create_refresh_token()
    # await crud.create_refresh_token(user_id, new_hash)

    return TokenResponse(
        access_token=access_token,
        refresh_token=req.refresh_token,  # 保持原来的 refresh token
        expires_in=15 * 60
    )

@router.post("/logout", summary="登出")
async def logout(
    req: RefreshRequest,
    user: dict = Depends(get_current_user)
):
    """
    登出当前设备
    """
    token_hash = verify_refresh_token(req.refresh_token)
    await crud.revoke_refresh_token(token_hash)
    return {"message": "已登出"}

@router.post("/logout-all", summary="登出所有设备")
async def logout_all(user: dict = Depends(get_current_user)):
    """
    登出所有设备
    """
    await crud.revoke_all_user_tokens(user["id"])
    return {"message": "已登出所有设备"}

@router.get("/me", response_model=UserResponse, summary="获取当前用户信息")
async def get_me(user: dict = Depends(get_current_user)):
    """
    获取当前登录用户信息
    """
    return UserResponse(
        id=user["id"],
        email=user["email"],
        display_name=user["display_name"],
        auth_expires_at=user["auth_expires_at"],
        is_verified=user["is_verified"],
        created_at=user["created_at"]
    )

@router.post("/forgot-password", summary="忘记密码 - 发送验证码")
async def forgot_password(req: RegisterRequest):
    """
    忘记密码：发送验证码
    """
    user = await crud.get_user_by_email(req.email)
    if not user:
        # 为了安全，不提示邮箱不存在
        return {"message": "如果该邮箱已注册，验证码已发送"}

    code = generate_verification_code()
    await crud.create_verification_code(req.email, code, "reset_password")
    await send_verification_code(req.email, code, "reset_password")

    return {"message": "验证码已发送到您的邮箱"}

class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str

@router.post("/reset-password", summary="重置密码")
async def reset_password(req: ResetPasswordRequest):
    """
    验证码重置密码
    """
    valid = await crud.verify_code(req.email, req.code, "reset_password")
    if not valid:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="验证码无效或已过期"
        )

    user = await crud.get_user_by_email(req.email)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    password_hash = hash_password(req.new_password)
    await crud.update_user(user["id"], password_hash=password_hash)

    # 撤销所有 token，强制重新登录
    await crud.revoke_all_user_tokens(user["id"])

    return {"message": "密码重置成功，请重新登录"}


class ChangePasswordRequest(BaseModel):
    old_password: str
    new_password: str


@router.post("/change-password", summary="修改密码")
async def change_password(
    req: ChangePasswordRequest,
    user: dict = Depends(get_current_user)
):
    """
    登录状态下修改密码
    """
    # 验证旧密码
    if not verify_password(req.old_password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="原密码错误"
        )

    if len(req.new_password) < 6:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="新密码至少6位"
        )

    # 更新密码
    password_hash = hash_password(req.new_password)
    await crud.update_user(user["id"], password_hash=password_hash)

    return {"message": "密码修改成功"}


class UpdateProfileRequest(BaseModel):
    display_name: Optional[str] = None


@router.patch("/me", summary="更新个人信息")
async def update_profile(
    req: UpdateProfileRequest,
    user: dict = Depends(get_current_user)
):
    """
    更新个人信息
    """
    updates = {}
    if req.display_name is not None:
        updates["display_name"] = req.display_name

    if updates:
        await crud.update_user(user["id"], **updates)

    return {"message": "更新成功"}
