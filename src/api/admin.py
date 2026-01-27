# -*- coding: utf-8 -*-
"""
管理员 API
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel, EmailStr
from typing import Optional, List
from datetime import datetime
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth import hash_password, verify_password, create_access_token
from auth.dependencies import get_current_admin
from db import crud
from config import PROJECTS_BASE_DIR, DEFAULT_AUTH_DAYS

router = APIRouter()

# ============================================================
# 请求/响应模型
# ============================================================

class AdminLoginRequest(BaseModel):
    username: str
    password: str

class AdminTokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int

class CreateUserRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: Optional[str] = None
    auth_days: int = DEFAULT_AUTH_DAYS

class UpdateUserRequest(BaseModel):
    display_name: Optional[str] = None
    is_active: Optional[bool] = None
    auth_days: Optional[int] = None

class UserListResponse(BaseModel):
    id: int
    email: str
    display_name: Optional[str]
    auth_expires_at: str
    is_active: bool
    is_verified: bool
    created_at: str
    last_login: Optional[str]

class ExtendAuthRequest(BaseModel):
    days: int

# ============================================================
# API 端点
# ============================================================

@router.post("/login", response_model=AdminTokenResponse, summary="管理员登录")
async def admin_login(req: AdminLoginRequest):
    """
    管理员登录
    """
    admin = await crud.get_admin_by_username(req.username)
    if not admin:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )

    if not verify_password(req.password, admin["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )

    if not admin["is_active"]:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="管理员账号已被禁用"
        )

    await crud.update_admin_login(admin["id"])

    access_token = create_access_token(admin["id"], "admin")

    return AdminTokenResponse(
        access_token=access_token,
        expires_in=15 * 60
    )

@router.get("/users", response_model=List[UserListResponse], summary="获取用户列表")
async def list_users(admin: dict = Depends(get_current_admin)):
    """
    获取所有用户列表
    """
    users = await crud.get_all_users()
    return [UserListResponse(**u) for u in users]

@router.post("/users", summary="创建用户")
async def create_user(
    req: CreateUserRequest,
    admin: dict = Depends(get_current_admin)
):
    """
    管理员创建用户（跳过邮箱验证）
    """
    # 检查邮箱是否已存在
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
        auth_days=req.auth_days,
        display_name=req.display_name,
        created_by_admin=admin["id"],
        is_verified=True
    )

    # 创建目录
    Path(user_dir).mkdir(parents=True, exist_ok=True)

    return {"message": "用户创建成功", "user_id": user_id}

@router.get("/users/{user_id}", summary="获取用户详情")
async def get_user(
    user_id: int,
    admin: dict = Depends(get_current_admin)
):
    """
    获取用户详情
    """
    user = await crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    # 获取用户项目
    projects = await crud.get_user_projects(user_id)

    return {
        "user": UserListResponse(
            id=user["id"],
            email=user["email"],
            display_name=user["display_name"],
            auth_expires_at=user["auth_expires_at"],
            is_active=user["is_active"],
            is_verified=user["is_verified"],
            created_at=user["created_at"],
            last_login=user["last_login"]
        ),
        "projects_count": len(projects),
        "running_projects": len([p for p in projects if p["is_running"]])
    }

@router.patch("/users/{user_id}", summary="更新用户信息")
async def update_user(
    user_id: int,
    req: UpdateUserRequest,
    admin: dict = Depends(get_current_admin)
):
    """
    更新用户信息
    """
    user = await crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    updates = {}
    if req.display_name is not None:
        updates["display_name"] = req.display_name
    if req.is_active is not None:
        updates["is_active"] = req.is_active
        # 如果禁用用户，停止所有进程
        if not req.is_active:
            await _stop_user_projects(user_id)

    if updates:
        await crud.update_user(user_id, **updates)

    # 如果指定了 auth_days，延长授权
    if req.auth_days is not None and req.auth_days > 0:
        await crud.extend_user_auth(user_id, req.auth_days)

    return {"message": "用户信息已更新"}

@router.post("/users/{user_id}/extend", summary="延长授权时间")
async def extend_auth(
    user_id: int,
    req: ExtendAuthRequest,
    admin: dict = Depends(get_current_admin)
):
    """
    延长用户授权时间
    """
    user = await crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    await crud.extend_user_auth(user_id, req.days)

    # 获取更新后的过期时间
    user = await crud.get_user_by_id(user_id)

    return {
        "message": f"已延长 {req.days} 天",
        "new_expires_at": user["auth_expires_at"]
    }

@router.post("/users/{user_id}/disable", summary="禁用用户")
async def disable_user(
    user_id: int,
    admin: dict = Depends(get_current_admin)
):
    """
    禁用用户账号并停止所有进程
    """
    user = await crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    # 停止用户所有项目
    await _stop_user_projects(user_id)

    # 禁用账号
    await crud.disable_user(user_id)

    # 撤销所有 token
    await crud.revoke_all_user_tokens(user_id)

    return {"message": "用户已禁用，所有项目已停止"}

@router.delete("/users/{user_id}", summary="删除用户")
async def delete_user(
    user_id: int,
    admin: dict = Depends(get_current_admin)
):
    """
    删除用户（谨慎操作）
    """
    user = await crud.get_user_by_id(user_id)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )

    # 停止所有项目
    await _stop_user_projects(user_id)

    # 删除用户（级联删除项目等）
    await crud.delete_user(user_id)

    # 可选：删除用户目录
    # import shutil
    # shutil.rmtree(user["user_dir"], ignore_errors=True)

    return {"message": "用户已删除"}

async def _stop_user_projects(user_id: int):
    """
    停止用户所有运行中的项目
    """
    import httpx
    from config import CORE_URL

    projects = await crud.get_running_projects(user_id)
    for project in projects:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{CORE_URL}/project/{project['name']}/stop")
            await crud.update_project_running_status(project["id"], False)
        except Exception as e:
            print(f"[Admin] 停止项目 {project['name']} 失败: {e}")

# ============================================================
# 初始化默认管理员
# ============================================================

async def ensure_default_admin():
    """
    确保存在默认管理员
    """
    admin = await crud.get_admin_by_username("admin")
    if not admin:
        password_hash = hash_password("admin123")
        await crud.create_admin("admin", password_hash)
        print("[Admin] 已创建默认管理员: admin / admin123")
