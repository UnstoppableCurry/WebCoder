# -*- coding: utf-8 -*-
"""
项目 API
"""

from fastapi import APIRouter, HTTPException, status, Depends
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime
from pathlib import Path
import httpx
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth.dependencies import get_current_user
from db import crud
from config import CORE_URL

router = APIRouter()

# ============================================================
# 请求/响应模型
# ============================================================

class CreateProjectRequest(BaseModel):
    name: str
    display_name: Optional[str] = None
    note: Optional[str] = None
    mode: str = "new"  # 新项目默认 new 模式

class UpdateProjectRequest(BaseModel):
    display_name: Optional[str] = None
    note: Optional[str] = None

class ProjectResponse(BaseModel):
    id: int
    name: str
    display_name: Optional[str]
    note: Optional[str]
    status: str
    current_phase: str
    is_running: bool
    needs_human: bool = False
    mode: str
    created_at: str
    last_active: Optional[str]

class AgentStateResponse(BaseModel):
    project_id: int
    current_phase: str
    needs_human: bool
    human_action_type: Optional[str]
    human_action_message: Optional[str]
    auto_mode: bool
    last_heartbeat: Optional[str]

class AgentActionRequest(BaseModel):
    action_type: str  # 'confirm', 'reject', 'retry', 'skip', 'manual_input'
    data: Optional[dict] = None

# ============================================================
# API 端点
# ============================================================

@router.get("", response_model=List[ProjectResponse], summary="获取项目列表")
async def list_projects(
    sync_core: bool = False,
    user: dict = Depends(get_current_user)
):
    """
    获取当前用户的所有项目

    Args:
        sync_core: 是否同步 Core 实际状态（默认否，可提高响应速度）
    """
    projects = await crud.get_user_projects(user["id"])

    # 如果需要同步 Core 状态，批量查询
    core_status_map = {}
    if sync_core:
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{CORE_URL}/projects")
                if resp.status_code == 200:
                    core_projects = resp.json()
                    core_status_map = {p["name"]: p["running"] for p in core_projects}
        except Exception as e:
            print(f"[Projects] 同步 Core 状态失败: {e}")

    result = []
    for p in projects:
        # 获取代理状态
        agent_state = await crud.get_agent_state(p["id"])

        # 同步 Core 状态
        is_running = p["is_running"]
        if sync_core and p["name"] in core_status_map:
            core_running = core_status_map[p["name"]]
            if core_running != is_running:
                # 状态不一致，同步数据库
                await crud.update_project(
                    p["id"],
                    is_running=core_running,
                    status="执行中" if core_running else "已停止"
                )
                is_running = core_running

        result.append(ProjectResponse(
            id=p["id"],
            name=p["name"],
            display_name=p["display_name"],
            note=p["note"],
            status=p["status"],
            current_phase=agent_state["current_phase"] if agent_state else p["current_phase"],
            is_running=is_running,
            needs_human=agent_state["needs_human"] if agent_state else False,
            mode=p["mode"],
            created_at=p["created_at"],
            last_active=p["last_active"]
        ))
    return result

@router.post("", summary="创建项目")
async def create_project(
    req: CreateProjectRequest,
    user: dict = Depends(get_current_user)
):
    """
    创建新项目
    """
    # 检查项目名是否已存在
    existing = await crud.get_project_by_name(user["id"], req.name)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="项目名已存在"
        )

    # 创建项目目录
    project_dir = str(Path(user["user_dir"]) / req.name)

    # 创建项目记录
    project_id = await crud.create_project(
        user_id=user["id"],
        name=req.name,
        project_dir=project_dir,
        display_name=req.display_name,
        note=req.note
    )

    # 初始化代理状态
    await crud.ensure_agent_state(project_id)

    # 创建目录
    Path(project_dir).mkdir(parents=True, exist_ok=True)

    return {"message": "项目创建成功", "project_id": project_id}

@router.get("/{project_id}", response_model=ProjectResponse, summary="获取项目详情")
async def get_project(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """
    获取项目详情
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    agent_state = await crud.get_agent_state(project_id)

    return ProjectResponse(
        id=project["id"],
        name=project["name"],
        display_name=project["display_name"],
        note=project["note"],
        status=project["status"],
        current_phase=agent_state["current_phase"] if agent_state else project["current_phase"],
        is_running=project["is_running"],
        mode=project["mode"],
        created_at=project["created_at"],
        last_active=project["last_active"]
    )

@router.patch("/{project_id}", summary="更新项目")
async def update_project(
    project_id: int,
    req: UpdateProjectRequest,
    user: dict = Depends(get_current_user)
):
    """
    更新项目信息
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    updates = {}
    if req.display_name is not None:
        updates["display_name"] = req.display_name
    if req.note is not None:
        updates["note"] = req.note

    if updates:
        await crud.update_project(project_id, **updates)

    return {"message": "项目已更新"}

@router.delete("/{project_id}", summary="删除项目")
async def delete_project(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """
    删除项目
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 如果正在运行，先停止
    if project["is_running"]:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.post(f"{CORE_URL}/project/{project['name']}/stop")
        except:
            pass

    # 删除项目记录
    await crud.delete_project(project_id)

    # 可选：删除项目目录
    # import shutil
    # shutil.rmtree(project["project_dir"], ignore_errors=True)

    return {"message": "项目已删除"}

@router.post("/{project_id}/start", summary="启动项目")
async def start_project(
    project_id: int,
    mode: str = "new",  # 默认 new 模式
    user: dict = Depends(get_current_user)
):
    """
    启动项目
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 调用 Core 服务启动
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                f"{CORE_URL}/project/{project['name']}/start",
                json={"mode": mode, "user_dir": user["user_dir"]}
            )
            if resp.status_code != 200:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="启动项目失败"
                )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Core 服务不可用: {str(e)}"
        )

    # 更新状态
    await crud.update_project(project_id, is_running=True, mode=mode, status="执行中")

    return {"message": "项目已启动"}

@router.post("/{project_id}/stop", summary="停止项目")
async def stop_project(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """
    停止项目
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 调用 Core 服务停止
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{CORE_URL}/project/{project['name']}/stop")
    except:
        pass

    # 更新状态
    await crud.update_project(project_id, is_running=False, status="已停止")

    return {"message": "项目已停止"}

@router.post("/{project_id}/restart", summary="重启项目")
async def restart_project(
    project_id: int,
    mode: str = None,
    user: dict = Depends(get_current_user)
):
    """
    重启项目
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 停止
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{CORE_URL}/project/{project['name']}/stop")
    except:
        pass

    # 等待
    import asyncio
    await asyncio.sleep(0.5)

    # 启动
    use_mode = mode or project["mode"]
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            await client.post(
                f"{CORE_URL}/project/{project['name']}/start",
                json={"mode": use_mode, "user_dir": user["user_dir"]}
            )
    except httpx.RequestError as e:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Core 服务不可用: {str(e)}"
        )

    await crud.update_project(project_id, is_running=True, mode=use_mode, status="执行中")

    return {"message": "项目已重启"}

# ============================================================
# 代理状态 API
# ============================================================

@router.get("/{project_id}/agent", response_model=AgentStateResponse, summary="获取代理状态")
async def get_agent_state(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """
    获取项目代理状态
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    state = await crud.get_agent_state(project_id)
    if not state:
        # 初始化状态
        await crud.ensure_agent_state(project_id)
        state = await crud.get_agent_state(project_id)

    return AgentStateResponse(
        project_id=project_id,
        current_phase=state["current_phase"],
        needs_human=state["needs_human"],
        human_action_type=state["human_action_type"],
        human_action_message=state["human_action_message"],
        auto_mode=state["auto_mode"],
        last_heartbeat=state["last_heartbeat"]
    )

@router.post("/{project_id}/agent/action", summary="人工介入操作")
async def agent_action(
    project_id: int,
    req: AgentActionRequest,
    user: dict = Depends(get_current_user)
):
    """
    人工介入操作

    action_type:
    - confirm: 确认方案/结果
    - reject: 拒绝，要求重做
    - retry: 重试当前步骤
    - skip: 跳过当前步骤
    - manual_input: 手动输入指令
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    # 记录交互
    await crud.save_interaction(
        project_id,
        source="user",
        action_type=req.action_type,
        action_data=req.data
    )

    # 根据操作类型处理
    if req.action_type == "confirm":
        # 发送确认指令给 Claude
        await _send_to_claude(project["name"], "是，继续")
        await crud.update_agent_state(project_id, needs_human=False)

    elif req.action_type == "reject":
        reason = req.data.get("reason", "") if req.data else ""
        await _send_to_claude(project["name"], f"不，请重新考虑。{reason}")
        await crud.update_agent_state(project_id, needs_human=False)

    elif req.action_type == "manual_input":
        if req.data and "input" in req.data:
            await _send_to_claude(project["name"], req.data["input"])
        await crud.update_agent_state(project_id, needs_human=False)

    elif req.action_type == "skip":
        await _send_to_claude(project["name"], "跳过这个步骤，继续下一步")
        await crud.update_agent_state(project_id, needs_human=False)

    return {"message": "操作已执行"}

async def _send_to_claude(project_name: str, input_text: str):
    """发送输入给 Claude"""
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(
                f"{CORE_URL}/project/{project_name}/input",
                json={"input": input_text}
            )
    except Exception as e:
        print(f"[Projects] 发送到 Claude 失败: {e}")

# ============================================================
# Core 状态同步 API
# ============================================================

@router.get("/{project_id}/core-status", summary="获取 Core 实际状态")
async def get_core_status(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """
    查询 Core 服务中项目的实际运行状态
    用于前端判断是否需要启动或重连
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    try:
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{CORE_URL}/project/{project['name']}/status")
            if resp.status_code == 200:
                core_status = resp.json()
                core_running = core_status.get("running", False)

                # 同步数据库状态
                if core_running != project["is_running"]:
                    await crud.update_project(
                        project_id,
                        is_running=core_running,
                        status="执行中" if core_running else "已停止"
                    )

                return {
                    "core_running": core_running,
                    "db_running": project["is_running"],
                    "synced": core_running == project["is_running"],
                    "connections": core_status.get("connections", 0),
                    "buffer_size": core_status.get("buffer_size", 0)
                }
    except Exception as e:
        print(f"[Projects] 查询 Core 状态失败: {e}")

    # Core 不可用时返回数据库状态
    return {
        "core_running": False,
        "db_running": project["is_running"],
        "synced": not project["is_running"],
        "connections": 0,
        "buffer_size": 0
    }

# ============================================================
# 输出历史 API
# ============================================================

@router.get("/{project_id}/outputs", summary="获取输出历史")
async def get_outputs(
    project_id: int,
    limit: int = 100,
    user: dict = Depends(get_current_user)
):
    """
    获取项目输出历史
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="项目不存在"
        )

    outputs = await crud.get_recent_outputs(project_id, limit)
    return {"outputs": outputs}
