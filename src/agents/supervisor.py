# -*- coding: utf-8 -*-
"""
总代理 (Supervisor) - 管理所有项目代理

职责：
- 管理项目代理的生命周期
- 汇总状态，统一对外接口
- 任务分发
- 协调用户通知
"""

import asyncio
from datetime import datetime
from typing import Dict, Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import crud
from services.email_service import send_notification_email
from .project_agent import ProjectAgent

class Supervisor:
    """
    总代理 - 单例模式

    管理所有项目代理，提供统一接口
    """

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._initialized = False
        return cls._instance

    def __init__(self):
        if self._initialized:
            return

        self.project_agents: Dict[int, ProjectAgent] = {}
        self.task_processor: Optional[asyncio.Task] = None
        self.health_checker: Optional[asyncio.Task] = None
        self.running = False
        self._initialized = True

    async def start(self):
        """启动总代理"""
        if self.running:
            return

        self.running = True
        print("[Supervisor] 总代理启动中...")

        # 1. 恢复所有运行中的项目代理
        running_projects = await crud.get_running_projects()
        for project in running_projects:
            await self.spawn_agent(project["id"])

        # 2. 启动任务处理循环
        self.task_processor = asyncio.create_task(self._task_loop())

        # 3. 启动健康检查循环
        self.health_checker = asyncio.create_task(self._health_check_loop())

        print(f"[Supervisor] 总代理已启动，恢复了 {len(running_projects)} 个项目代理")

    async def stop(self):
        """停止总代理"""
        self.running = False

        # 停止所有项目代理
        for project_id in list(self.project_agents.keys()):
            await self.stop_agent(project_id)

        # 取消后台任务
        if self.task_processor:
            self.task_processor.cancel()
        if self.health_checker:
            self.health_checker.cancel()

        print("[Supervisor] 总代理已停止")

    async def spawn_agent(self, project_id: int):
        """创建并启动项目代理"""
        if project_id in self.project_agents:
            return self.project_agents[project_id]

        agent = ProjectAgent(project_id, self)
        self.project_agents[project_id] = agent
        await agent.start()

        return agent

    async def stop_agent(self, project_id: int):
        """停止项目代理"""
        if project_id not in self.project_agents:
            return

        agent = self.project_agents[project_id]
        await agent.stop()
        del self.project_agents[project_id]

    async def get_agent(self, project_id: int) -> Optional[ProjectAgent]:
        """获取项目代理"""
        return self.project_agents.get(project_id)

    async def submit_task(self, user_id: int, task_type: str, task_data: dict, project_id: int = None) -> int:
        """
        提交任务到队列

        task_type:
        - new_project: 创建新项目并启动
        - start_project: 启动项目
        - stop_project: 停止项目
        - send_instruction: 发送指令
        - confirm: 确认操作
        """
        task_id = await crud.create_task(
            user_id=user_id,
            task_type=task_type,
            task_data=task_data,
            project_id=project_id
        )
        return task_id

    async def get_user_status(self, user_id: int) -> dict:
        """获取用户的所有项目状态汇总"""
        projects = await crud.get_user_projects(user_id)

        result = []
        for p in projects:
            agent_state = await crud.get_agent_state(p["id"])
            agent_active = p["id"] in self.project_agents

            result.append({
                "project_id": p["id"],
                "name": p["name"],
                "display_name": p["display_name"],
                "status": p["status"],
                "phase": agent_state["current_phase"] if agent_state else "idle",
                "needs_human": agent_state["needs_human"] if agent_state else False,
                "is_running": p["is_running"],
                "agent_active": agent_active
            })

        # 统计
        running_count = len([p for p in result if p["is_running"]])
        needs_action_count = len([p for p in result if p["needs_human"]])

        return {
            "projects": result,
            "summary": {
                "total": len(result),
                "running": running_count,
                "needs_action": needs_action_count
            }
        }

    async def send_email_notification(self, user_id: int, title: str, content: str):
        """发送邮件通知"""
        user = await crud.get_user_by_id(user_id)
        if not user:
            return

        try:
            await send_notification_email(user["email"], title, content)
        except Exception as e:
            print(f"[Supervisor] 发送邮件失败: {e}")

    async def _task_loop(self):
        """任务处理循环"""
        while self.running:
            try:
                # 获取待处理任务
                tasks = await crud.get_pending_tasks(limit=10)

                for task in tasks:
                    await self._process_task(task)

            except Exception as e:
                print(f"[Supervisor] 任务处理错误: {e}")

            await asyncio.sleep(1)

    async def _process_task(self, task: dict):
        """处理单个任务"""
        task_id = task["id"]
        task_type = task["task_type"]
        task_data = task["task_data"]

        if isinstance(task_data, str):
            import json
            task_data = json.loads(task_data)

        await crud.update_task_status(task_id, "processing")

        try:
            if task_type == "start_project":
                project_id = task_data.get("project_id")
                if project_id:
                    await self.spawn_agent(project_id)

            elif task_type == "stop_project":
                project_id = task_data.get("project_id")
                if project_id:
                    await self.stop_agent(project_id)

            elif task_type == "send_instruction":
                project_id = task_data.get("project_id")
                instruction = task_data.get("instruction")
                if project_id and instruction:
                    agent = await self.get_agent(project_id)
                    if agent:
                        project = await crud.get_project_by_id(project_id)
                        if project:
                            await agent.send_instruction(project["name"], instruction)

            await crud.update_task_status(task_id, "completed")

        except Exception as e:
            await crud.update_task_status(task_id, "failed", error=str(e))
            print(f"[Supervisor] 任务 {task_id} 处理失败: {e}")

    async def _health_check_loop(self):
        """健康检查循环"""
        while self.running:
            await asyncio.sleep(30)

            try:
                # 检查代理健康状态
                for project_id in list(self.project_agents.keys()):
                    state = await crud.get_agent_state(project_id)
                    if not state:
                        continue

                    # 检查心跳超时
                    if state["last_heartbeat"]:
                        last_heartbeat = datetime.fromisoformat(state["last_heartbeat"])
                        if (datetime.now() - last_heartbeat).seconds > 60:
                            print(f"[Supervisor] 代理 {project_id} 心跳超时，重启中...")
                            await self.stop_agent(project_id)
                            await self.spawn_agent(project_id)

                # 检查应该有代理但没有的项目
                running_projects = await crud.get_running_projects()
                for project in running_projects:
                    if project["id"] not in self.project_agents:
                        print(f"[Supervisor] 发现遗漏的运行项目 {project['id']}，启动代理...")
                        await self.spawn_agent(project["id"])

            except Exception as e:
                print(f"[Supervisor] 健康检查错误: {e}")

    def get_stats(self) -> dict:
        """获取统计信息"""
        return {
            "running": self.running,
            "active_agents": len(self.project_agents),
            "agent_ids": list(self.project_agents.keys())
        }

# 全局单例
supervisor = Supervisor()
