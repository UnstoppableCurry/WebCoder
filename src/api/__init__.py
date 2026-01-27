# -*- coding: utf-8 -*-
"""
API 路由模块
"""

from fastapi import APIRouter

# 创建主路由
api_router = APIRouter()

# 导入子路由
from . import auth, admin, projects, notifications, files

# 注册子路由
api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(admin.router, prefix="/admin", tags=["管理员"])
api_router.include_router(projects.router, prefix="/projects", tags=["项目"])
api_router.include_router(notifications.router, prefix="/notifications", tags=["通知"])
api_router.include_router(files.router, prefix="/projects", tags=["文件和Git"])
