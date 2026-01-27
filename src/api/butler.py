# -*- coding: utf-8 -*-
"""
Butler Agent API - 自动化管家代理接口

解耦设计：
- 独立的 API 路由
- 不影响原有的项目管理功能
- 可独立开启/关闭
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from typing import Optional, List
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth.dependencies import get_current_user
from db import crud
from agents.butler_agent import ButlerAgentManager

router = APIRouter()


# ============================================================
# 请求/响应模型
# ============================================================

class ButlerInitRequest(BaseModel):
    """初始化管家代理请求"""
    goal: str  # 最终目标
    sub_goals: Optional[List[str]] = None  # 子目标列表


class ButlerProcessRequest(BaseModel):
    """处理输出请求"""
    output: str  # Claude Code 输出
    current_phase: str  # 当前阶段


class ButlerConfigRequest(BaseModel):
    """配置管家代理请求"""
    max_auto_continue: Optional[int] = None  # 最大自动推进次数


# ============================================================
# API 端点
# ============================================================

@router.post("/{project_id}/butler/init", summary="初始化管家代理")
async def init_butler(
    project_id: int,
    req: ButlerInitRequest,
    user: dict = Depends(get_current_user)
):
    """
    初始化项目的管家代理

    - goal: 项目的最终目标描述
    - sub_goals: 可选的子目标列表
    """
    # 验证项目权限
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 获取或创建管家代理
    agent = ButlerAgentManager.get_agent(project_id, user["id"])

    # 初始化
    result = await agent.initialize(req.goal, req.sub_goals)

    return result


@router.get("/{project_id}/butler/status", summary="获取管家代理状态")
async def get_butler_status(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """获取管家代理当前状态"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    agent = ButlerAgentManager.get_agent(project_id, user["id"])
    await agent.load_state()

    return agent.get_status()


@router.post("/{project_id}/butler/process", summary="处理输出并决策")
async def process_output(
    project_id: int,
    req: ButlerProcessRequest,
    user: dict = Depends(get_current_user)
):
    """
    处理 Claude Code 的输出，返回决策结果

    返回值：
    - action: "auto_continue" | "human_required" | "wait"
    - prompt: 推进提示词（如果 action 是 auto_continue）
    - reason: 决策原因
    - evaluation: 进度评估结果
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    result = await ButlerAgentManager.process_project_output(
        project_id,
        user["id"],
        req.output,
        req.current_phase
    )

    if result is None:
        return {
            "action": "wait",
            "reason": "Butler Agent 未启用"
        }

    return result


@router.post("/{project_id}/butler/disable", summary="禁用管家代理")
async def disable_butler(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """禁用项目的管家代理"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    agent = ButlerAgentManager.get_agent(project_id, user["id"])
    agent.disable()

    # 清除数据库状态
    await crud.update_project(project_id, butler_state=None)

    return {"success": True, "message": "Butler Agent 已禁用"}


@router.post("/{project_id}/butler/reset", summary="重置自动推进计数")
async def reset_butler_count(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """重置自动推进次数计数"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    agent = ButlerAgentManager.get_agent(project_id, user["id"])
    agent.reset_auto_count()

    return {"success": True, "message": "自动推进计数已重置"}


@router.patch("/{project_id}/butler/config", summary="配置管家代理")
async def config_butler(
    project_id: int,
    req: ButlerConfigRequest,
    user: dict = Depends(get_current_user)
):
    """配置管家代理参数"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    agent = ButlerAgentManager.get_agent(project_id, user["id"])

    if req.max_auto_continue is not None:
        agent.max_auto_continue = req.max_auto_continue

    return {
        "success": True,
        "config": {
            "max_auto_continue": agent.max_auto_continue
        }
    }


@router.get("/{project_id}/butler/history", summary="获取进度历史")
async def get_butler_history(
    project_id: int,
    limit: int = 20,
    user: dict = Depends(get_current_user)
):
    """获取管家代理的进度历史"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    agent = ButlerAgentManager.get_agent(project_id, user["id"])
    await agent.load_state()

    # 返回最近的历史记录
    history = agent.progress_history[-limit:] if agent.progress_history else []

    return {
        "total": len(agent.progress_history),
        "history": history
    }
