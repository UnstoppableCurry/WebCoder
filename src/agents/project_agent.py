# -*- coding: utf-8 -*-
"""
项目代理 - 每个项目一个实例

职责：
- 10秒心跳监控 Claude Code 输出
- 调用 DeepSeek 判断当前环节
- 自动控制或通知用户介入
- 任务完成时发送通知

注：Git 自动版本管理功能已移除（不稳定），用户可手动操作
"""

import asyncio
import httpx
from datetime import datetime
from typing import Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import crud
from services.deepseek_service import analyze_phase, compress_context
from config import CORE_URL, AGENT_HEARTBEAT_INTERVAL

class ProjectAgent:
    """
    项目代理 - 无状态设计

    所有状态从数据库读取，代理本身可随时重启

    职责：
    1. 监控 Claude Code 输出
    2. 判断当前环节
    3. 自动控制或通知用户
    4. 自动版本管理（基于任务状态）
    """

    PHASES = {
        "pending_dispatch": "待分发",
        "understanding": "理解规划中",
        "pending_confirm": "待确认方案",
        "implementing": "编码实现中",
        "pending_acceptance": "待验收",
        "pending_deploy": "待部署",
        "completed": "已完成",
        "error": "出错",
        "idle": "空闲"
    }

    def __init__(self, project_id: int, supervisor):
        self.project_id = project_id
        self.supervisor = supervisor
        self.heartbeat_task: Optional[asyncio.Task] = None
        self.running = False
        self._last_error = None
        self._last_phase = None

    async def start(self):
        """启动代理"""
        self.running = True

        # 确保代理状态存在
        await crud.ensure_agent_state(self.project_id)

        # 标记代理为活跃
        await crud.update_agent_state(self.project_id, agent_active=True)

        # 启动心跳循环
        self.heartbeat_task = asyncio.create_task(self._heartbeat_loop())

        print(f"[Agent] 项目代理 {self.project_id} 已启动")

    async def stop(self):
        """停止代理"""
        self.running = False

        # 标记代理为非活跃
        await crud.update_agent_state(self.project_id, agent_active=False)

        # 取消心跳任务
        if self.heartbeat_task:
            self.heartbeat_task.cancel()
            try:
                await self.heartbeat_task
            except asyncio.CancelledError:
                pass

        print(f"[Agent] 项目代理 {self.project_id} 已停止")

    async def _heartbeat_loop(self):
        """心跳循环 - 每 N 秒执行"""
        while self.running:
            try:
                await self._heartbeat()
            except Exception as e:
                self._last_error = str(e)
                print(f"[Agent] 项目 {self.project_id} 心跳错误: {e}")

            await asyncio.sleep(AGENT_HEARTBEAT_INTERVAL)

    async def _heartbeat(self):
        """单次心跳 - 核心逻辑"""
        # 1. 获取项目信息
        project = await crud.get_project_by_id(self.project_id)
        if not project:
            print(f"[Agent] 项目 {self.project_id} 不存在，停止代理")
            self.running = False
            return

        # 如果项目没有运行，不需要分析
        if not project["is_running"]:
            await crud.update_heartbeat(self.project_id)
            return

        # 2. 更新心跳时间
        await crud.update_heartbeat(self.project_id)

        # 3. 获取代理状态
        state = await crud.get_agent_state(self.project_id)
        if not state:
            return

        # 4. 获取最新输出（同时获取完整输出用于等待检测）
        new_output, core_running, full_output = await self._fetch_new_output_with_full(
            project["name"], state["last_output_position"]
        )

        # 如果 Core 中进程不存在，同步数据库状态
        if not core_running and project["is_running"]:
            print(f"[Agent] 项目 {self.project_id} Core 进程已停止，同步数据库状态")
            await crud.update_project(self.project_id, is_running=False)
            return

        # 即使没有新输出，也检查 Butler 是否需要推进（检测等待状态）
        if not new_output:
            await self._butler_check_waiting_state(project, full_output, state)
            return

        # 5. 保存完整输出
        await crud.save_output(self.project_id, new_output)

        # 6. 压缩上下文
        context_summary = await compress_context(new_output, state["context_summary"])

        # 7. 调用 DeepSeek 判断环节
        phase_result = await analyze_phase(context_summary, state["current_phase"])

        # 8. 检查环节是否变化
        phase_changed = phase_result["phase"] != state["current_phase"]

        # 9. 更新状态
        new_position = state["last_output_position"] + len(new_output)
        await crud.update_agent_state(
            self.project_id,
            current_phase=phase_result["phase"],
            needs_human=phase_result["needs_human"],
            human_action_type=phase_result.get("human_action_type"),
            human_action_message=phase_result.get("human_action_message"),
            context_summary=context_summary,
            last_output_position=new_position
        )

        # 10. 更新项目状态
        await crud.update_project(
            self.project_id,
            current_phase=phase_result["phase"],
            status=self.PHASES.get(phase_result["phase"], "未知")
        )

        # 11. 自动控制或通知
        if phase_result["needs_human"]:
            await self._notify_human_needed(project, phase_result)
        elif state["auto_mode"]:
            await self._auto_control(project, phase_result)

        # 11.5 仅在任务关键节点发送邮件通知（减少打扰）
        await self._check_and_notify_task_complete(
            project, phase_result["phase"], phase_changed, context_summary
        )

        # 11.6 Butler Agent 自动推进（解耦设计，不影响原有逻辑）
        await self._butler_auto_continue(
            project, new_output, phase_result["phase"], context_summary
        )

        # 12. 记录环节变化
        if phase_changed:
            await crud.save_interaction(
                self.project_id,
                source="agent",
                action_type="phase_change",
                action_data={
                    "from": state["current_phase"],
                    "to": phase_result["phase"],
                    "reasoning": phase_result.get("reasoning")
                }
            )

        # 13. Git 版本管理已移除（功能不稳定）
        # 用户可通过文件管理页面的 Git 标签手动操作

    async def _fetch_new_output(self, project_name: str, position: int) -> tuple:
        """
        获取 Claude Code 最新输出

        Returns:
            tuple: (output_text, is_running)
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{CORE_URL}/project/{project_name}/output",
                    params={"tail": 2000}
                )
                if resp.status_code != 200:
                    return "", False

                data = resp.json()
                full_output = data.get("output", "")
                is_running = data.get("running", False)

                # 返回新增部分
                if len(full_output) > position:
                    return full_output[position:], is_running
                return "", is_running
        except Exception as e:
            print(f"[Agent] 获取输出失败: {e}")
            return "", False

    async def _fetch_new_output_with_full(self, project_name: str, position: int) -> tuple:
        """
        获取 Claude Code 最新输出（同时返回完整输出用于等待检测）

        Returns:
            tuple: (new_output, is_running, full_output)
        """
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{CORE_URL}/project/{project_name}/output",
                    params={"tail": 3000}  # 获取更多用于等待检测
                )
                if resp.status_code != 200:
                    return "", False, ""

                data = resp.json()
                full_output = data.get("output", "")
                is_running = data.get("running", False)

                # 返回新增部分和完整输出
                if len(full_output) > position:
                    return full_output[position:], is_running, full_output
                return "", is_running, full_output
        except Exception as e:
            print(f"[Agent] 获取输出失败: {e}")
            return "", False, ""

    async def _butler_check_waiting_state(self, project: dict, full_output: str, state: dict):
        """
        检测是否处于等待输入状态，如果是则触发 Butler 推进

        这个方法解决了核心问题：当 Claude Code 执行完毕等待输入时，
        没有新输出产生，原来的逻辑不会触发 Butler。
        """
        try:
            from agents.butler_agent import ButlerAgentManager

            # 检查 Butler 是否启用
            agent = ButlerAgentManager.get_agent(self.project_id, project["user_id"])
            if not agent.enabled:
                await agent.load_state()
            if not agent.enabled:
                return

            # 检测等待输入的模式
            if not self._detect_waiting_for_input(full_output):
                return

            # 检查是否已经在短时间内触发过（避免重复触发）
            from datetime import timedelta
            if hasattr(self, '_last_butler_trigger') and self._last_butler_trigger:
                if datetime.now() - self._last_butler_trigger < timedelta(seconds=15):
                    return

            print(f"[Butler] 项目 {project['name']} 检测到等待输入状态，触发自动推进")

            # 使用最近的输出触发 Butler
            recent_output = full_output[-2000:] if len(full_output) > 2000 else full_output
            current_phase = state.get("current_phase", "idle")

            result = await ButlerAgentManager.process_project_output(
                self.project_id,
                project["user_id"],
                recent_output,
                current_phase
            )

            if result and result.get("action") == "auto_continue":
                prompt = result.get("prompt", "")
                if prompt:
                    await self.send_instruction(project["name"], prompt)
                    self._last_butler_trigger = datetime.now()

                    await crud.save_interaction(
                        self.project_id,
                        source="butler",
                        action_type="auto_continue_on_wait",
                        action_data={
                            "prompt": prompt,
                            "evaluation": result.get("evaluation"),
                            "trigger": "waiting_detected"
                        }
                    )
                    print(f"[Butler] 项目 {project['name']} 等待状态推进: {prompt[:50]}...")

        except ImportError:
            pass
        except Exception as e:
            print(f"[Butler] 检测等待状态错误: {e}")

    def _detect_waiting_for_input(self, output: str) -> bool:
        """
        检测终端是否在等待用户输入

        Claude Code 等待输入的常见模式：
        1. 显示 ">" 或 "$" 提示符
        2. 完成任务后的空闲状态
        3. 询问用户确认
        4. Claude Code 特有的完成标志
        """
        if not output:
            return False

        # 获取最后1000个字符来判断
        recent = output[-1000:] if len(output) > 1000 else output

        # 清理 ANSI 转义序列
        import re
        clean_output = re.sub(r'\x1b\[[0-9;]*[mGKHJ]', '', recent)
        clean_output = re.sub(r'\x1b\][^\x07]*\x07', '', clean_output)
        clean_output = re.sub(r'\x1b[^\[]*', '', clean_output)

        # 获取最后几行
        lines = clean_output.strip().split('\n')
        if not lines:
            return False

        last_lines = '\n'.join(lines[-10:]).lower()
        last_line = lines[-1].strip().lower() if lines else ""

        # 等待输入的模式列表（按优先级排序）
        waiting_patterns = [
            # Claude Code 特有的完成/等待模式
            r'what would you like to do',
            r'what.*next',
            r'anything else',
            r'is there anything',
            r'let me know',
            r'feel free to',
            r'waiting for',
            r'ready for',
            # 任务完成指示
            r'task.*completed?',
            r'implementation.*complete',
            r'changes.*made',
            r'updated.*successfully',
            r'created.*successfully',
            r'finished',
            r'all done',
            r'i\'ve completed',
            r'i have completed',
            r'i\'ve finished',
            r'i have finished',
            r'i\'ve made',
            r'i have made',
            # 命令行提示符（在末尾）
            r'[>$#%]\s*$',
            r'❯\s*$',
            r'➜\s*$',
            # 用户交互提示
            r'\?\s*$',
            r'yes/no',
            r'y/n',
            r'\[y/n\]',
            r'\(y/n\)',
            # 按键提示
            r'enter.*to continue',
            r'press.*to',
            r'type.*to',
            r'input.*to',
            # 中文模式
            r'请输入',
            r'等待输入',
            r'请确认',
            r'是否继续',
            r'完成了',
            r'已完成',
            r'还需要',
            r'接下来',
        ]

        for pattern in waiting_patterns:
            if re.search(pattern, last_lines, re.IGNORECASE):
                return True

        # 检测输出已经稳定（最后几行没有代码执行痕迹）
        code_running_patterns = [
            r'running',
            r'executing',
            r'compiling',
            r'building',
            r'testing',
            r'installing',
            r'\.\.\.',
            r'loading',
            r'processing',
        ]

        # 如果最后一行很短且不包含执行中的模式，可能是等待状态
        if len(last_line) < 50:
            is_running = False
            for pattern in code_running_patterns:
                if re.search(pattern, last_line, re.IGNORECASE):
                    is_running = True
                    break
            if not is_running:
                # 额外检查：如果输出以标点符号结尾，可能是等待状态
                if last_line and last_line[-1] in '.!?:。！？：':
                    return True

        return False

    async def _auto_control(self, project: dict, phase_result: dict):
        """自动控制 Claude Code"""
        suggested_action = phase_result.get("suggested_action")
        if not suggested_action:
            return

        phase = phase_result["phase"]

        # 根据环节执行不同操作
        if phase == "understanding":
            # 理解中，不需要操作
            pass

        elif phase == "implementing":
            # 实现中，不需要操作
            pass

        elif phase == "completed":
            # 完成，通知用户验收
            await self._notify_human_needed(project, {
                "phase": phase,
                "needs_human": True,
                "human_action_type": "accept_result",
                "human_action_message": "任务已完成，请验收结果"
            })

        # 如果有具体的自动操作建议
        if suggested_action and suggested_action not in ["wait", "none", "无"]:
            await self.send_instruction(project["name"], suggested_action)

    async def send_instruction(self, project_name: str, instruction: str):
        """发送指令给 Claude Code"""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                await client.post(
                    f"{CORE_URL}/project/{project_name}/input",
                    json={"input": instruction}
                )

            # 记录交互
            await crud.save_interaction(
                self.project_id,
                source="agent",
                action_type="send_command",
                action_data={"instruction": instruction}
            )

            print(f"[Agent] 发送指令到 {project_name}: {instruction[:50]}...")
        except Exception as e:
            print(f"[Agent] 发送指令失败: {e}")

    async def _notify_human_needed(self, project: dict, phase_result: dict):
        """通知用户需要介入"""
        # 检查是否已经通知过（避免重复通知）
        state = await crud.get_agent_state(self.project_id)
        if state and state["needs_human"]:
            # 已经在等待用户，不重复通知
            return

        user_id = project["user_id"]
        project_name = project["display_name"] or project["name"]

        # 创建通知
        await crud.create_notification(
            user_id=user_id,
            title=f"项目 [{project_name}] 需要您的操作",
            content=phase_result.get("human_action_message", "请查看项目状态"),
            notification_type="need_action",
            project_id=self.project_id
        )

        # 通过 supervisor 发送邮件
        if self.supervisor:
            await self.supervisor.send_email_notification(
                user_id,
                f"项目 [{project_name}] 需要您的操作",
                phase_result.get("human_action_message", "请查看项目状态")
            )

        print(f"[Agent] 已通知用户 {user_id}: 项目 {project_name} 需要操作")

    async def _check_and_notify_task_complete(self, project: dict, phase: str, phase_changed: bool, context_summary: str):
        """
        仅在任务真正完成时发送邮件通知

        只在以下情况发送邮件：
        1. 环节变为 "completed" (任务完成)
        2. 环节变为 "pending_acceptance" (等待验收)
        3. 环节变为 "pending_confirm" (等待确认方案)
        """
        # 只有环节变化才可能发送通知
        if not phase_changed:
            return

        # 只在关键节点发送邮件
        NOTIFY_PHASES = {"completed", "pending_acceptance", "pending_confirm"}
        if phase not in NOTIFY_PHASES:
            return

        user_id = project["user_id"]
        project_name = project["display_name"] or project["name"]

        # 根据环节生成通知内容
        if phase == "completed":
            title = f"项目 [{project_name}] 任务已完成"
            content = "Claude 已完成任务，请查看结果并验收。"
        elif phase == "pending_acceptance":
            title = f"项目 [{project_name}] 编码完成，待验收"
            content = "Claude 已完成编码，请查看并验收结果。"
        elif phase == "pending_confirm":
            title = f"项目 [{project_name}] 方案待确认"
            content = "Claude 已制定方案，请确认后继续。"
        else:
            return

        # 添加摘要
        if context_summary:
            summary = context_summary[:300]
            if len(context_summary) > 300:
                summary += "..."
            content += f"\n\n摘要:\n{summary}"

        # 创建站内通知（始终创建）
        await crud.create_notification(
            user_id=user_id,
            title=title,
            content=content,
            notification_type="task_complete",
            project_id=self.project_id
        )

        # 发送邮件（仅关键节点）
        if self.supervisor:
            await self.supervisor.send_email_notification(
                user_id,
                title,
                f"{content}\n\n请登录系统查看详情。"
            )

        print(f"[Agent] 已发送任务完成通知: 项目 {project_name}, 环节: {phase}")

    async def _butler_auto_continue(self, project: dict, output: str, current_phase: str, context_summary: str):
        """
        Butler Agent 自动推进功能

        解耦设计：
        - 独立于原有的 auto_mode 逻辑
        - 只在 Butler Agent 启用时生效
        - 通过 AI 评估进度并生成推进提示词
        """
        try:
            from agents.butler_agent import ButlerAgentManager

            # 调用 Butler Agent 处理输出
            result = await ButlerAgentManager.process_project_output(
                self.project_id,
                project["user_id"],
                output,
                current_phase
            )

            # Butler 未启用，直接返回
            if result is None:
                return

            action = result.get("action")

            if action == "auto_continue":
                # 自动推进：发送生成的提示词
                prompt = result.get("prompt", "")
                if prompt:
                    await self.send_instruction(project["name"], prompt)

                    # 记录 Butler 自动推进
                    await crud.save_interaction(
                        self.project_id,
                        source="butler",
                        action_type="auto_continue",
                        action_data={
                            "prompt": prompt,
                            "evaluation": result.get("evaluation"),
                            "reason": result.get("reason")
                        }
                    )

                    print(f"[Butler] 项目 {project['name']} 自动推进: {prompt[:50]}...")

            elif action == "human_required":
                # 需要人工介入：通知用户
                reason = result.get("reason", "Butler 建议人工检查")
                evaluation = result.get("evaluation", {})

                await crud.create_notification(
                    user_id=project["user_id"],
                    title=f"项目 [{project['display_name'] or project['name']}] Butler 建议人工检查",
                    content=f"{reason}\n\n完成度: {evaluation.get('completion_rate', '?')}%",
                    notification_type="butler_alert",
                    project_id=self.project_id
                )

                print(f"[Butler] 项目 {project['name']} 需要人工介入: {reason}")

        except ImportError:
            # Butler Agent 模块不存在，忽略
            pass
        except Exception as e:
            print(f"[Butler] 项目 {self.project_id} 处理错误: {e}")

