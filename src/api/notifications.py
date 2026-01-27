# -*- coding: utf-8 -*-
"""
通知 API
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from typing import List, Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth.dependencies import get_current_user
from db import crud

router = APIRouter()

# ============================================================
# 响应模型
# ============================================================

class NotificationResponse(BaseModel):
    id: int
    title: str
    content: str
    notification_type: str
    project_id: Optional[int]
    is_read: bool
    created_at: str
    read_at: Optional[str]

# ============================================================
# API 端点
# ============================================================

@router.get("", response_model=List[NotificationResponse], summary="获取通知列表")
async def list_notifications(
    unread_only: bool = False,
    limit: int = 50,
    user: dict = Depends(get_current_user)
):
    """
    获取用户通知列表
    """
    notifications = await crud.get_user_notifications(user["id"], unread_only, limit)
    return [NotificationResponse(**n) for n in notifications]

@router.get("/unread-count", summary="获取未读通知数")
async def get_unread_count(user: dict = Depends(get_current_user)):
    """
    获取未读通知数量
    """
    notifications = await crud.get_user_notifications(user["id"], unread_only=True)
    return {"count": len(notifications)}

@router.patch("/{notification_id}/read", summary="标记已读")
async def mark_read(
    notification_id: int,
    user: dict = Depends(get_current_user)
):
    """
    标记单个通知为已读
    """
    # 这里简化处理，实际应该验证通知属于当前用户
    await crud.mark_notification_read(notification_id)
    return {"message": "已标记为已读"}

@router.post("/read-all", summary="全部标记已读")
async def mark_all_read(user: dict = Depends(get_current_user)):
    """
    标记所有通知为已读
    """
    await crud.mark_all_notifications_read(user["id"])
    return {"message": "已全部标记为已读"}

@router.delete("/{notification_id}", summary="删除通知")
async def delete_notification(
    notification_id: int,
    user: dict = Depends(get_current_user)
):
    """
    删除单个通知
    """
    await crud.delete_notification(notification_id, user["id"])
    return {"message": "已删除"}

@router.delete("", summary="删除所有通知")
async def delete_all_notifications(user: dict = Depends(get_current_user)):
    """
    删除所有通知
    """
    await crud.delete_all_notifications(user["id"])
    return {"message": "已删除所有通知"}
