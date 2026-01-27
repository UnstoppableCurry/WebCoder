# -*- coding: utf-8 -*-
"""
Butler Agent - 自动化管家代理

核心职责：
1. 监控 Claude Code 的输出，识别当前阶段
2. 在非关键阶段自动评估交付结果与目标的一致性
3. 自动生成推进提示词，让 Claude Code 继续工作
4. 只在关键阶段才通知人工介入

设计原则：
- 解耦设计，不影响原有功能
- 可独立开启/关闭
- 支持自定义评估策略
"""

import asyncio
import json
import re
from datetime import datetime
from typing import Optional, Dict, List, Tuple
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL
from db import crud

import httpx


class ButlerAgent:
    """
    自动化管家代理

    工作流程：
    1. 接收 Claude Code 的输出
    2. 分析当前状态（阶段、进度、问题）
    3. 评估与目标的一致性
    4. 决策：自动推进 or 人工介入
    5. 生成推进提示词（如果自动推进）
    """

    # 需要人工介入的阶段
    HUMAN_REQUIRED_PHASES = {
        "pending_confirm",      # 等待确认方案
        "pending_acceptance",   # 等待验收
        "completed",            # 任务完成
        "error",                # 发生错误
        "stuck",                # 卡住了
    }

    # 可以自动推进的阶段
    AUTO_CONTINUE_PHASES = {
        "idle",                 # 空闲，等待输入
        "thinking",             # 思考中
        "coding",               # 编码中
        "testing",              # 测试中
        "debugging",            # 调试中
        "refactoring",          # 重构中
        "documenting",          # 文档中
    }

    def __init__(self, project_id: int, user_id: int):
        self.project_id = project_id
        self.user_id = user_id
        self.enabled = False
        self.goal = ""  # 最终目标
        self.sub_goals: List[str] = []  # 子目标
        self.progress_history: List[Dict] = []  # 进度历史
        self.last_evaluation: Optional[Dict] = None  # 上次评估结果
        self.auto_continue_count = 0  # 自动推进次数
        self.max_auto_continue = 10  # 最大自动推进次数（防止死循环）

    async def initialize(self, goal: str, sub_goals: List[str] = None):
        """
        初始化管家代理

        Args:
            goal: 最终目标描述
            sub_goals: 子目标列表（可选）
        """
        self.goal = goal
        self.sub_goals = sub_goals or []
        self.enabled = True
        self.auto_continue_count = 0
        self.progress_history = []

        # 保存到数据库
        await self._save_state()

        return {
            "success": True,
            "message": "Butler Agent 已启动",
            "goal": self.goal,
            "sub_goals": self.sub_goals
        }

    async def process_output(self, output: str, current_phase: str) -> Dict:
        """
        处理 Claude Code 的输出

        Args:
            output: Claude Code 的最新输出
            current_phase: 当前阶段

        Returns:
            {
                "action": "auto_continue" | "human_required" | "wait",
                "prompt": 推进提示词（如果 auto_continue）,
                "reason": 决策原因,
                "evaluation": 评估结果
            }
        """
        if not self.enabled:
            return {"action": "wait", "reason": "Butler Agent 未启用"}

        # 1. 判断是否需要人工介入
        if current_phase in self.HUMAN_REQUIRED_PHASES:
            return {
                "action": "human_required",
                "reason": f"当前阶段 [{current_phase}] 需要人工介入",
                "phase": current_phase
            }

        # 2. 检查自动推进次数限制
        if self.auto_continue_count >= self.max_auto_continue:
            return {
                "action": "human_required",
                "reason": f"已自动推进 {self.auto_continue_count} 次，建议人工检查",
                "evaluation": self.last_evaluation
            }

        # 3. 评估当前进度
        evaluation = await self._evaluate_progress(output, current_phase)
        self.last_evaluation = evaluation

        # 4. 记录进度历史
        self.progress_history.append({
            "timestamp": datetime.now().isoformat(),
            "phase": current_phase,
            "evaluation": evaluation,
            "output_preview": output[:500] if output else ""
        })

        # 5. 根据评估结果决策
        if evaluation.get("completion_rate", 0) >= 90:
            # 接近完成，让人工确认
            return {
                "action": "human_required",
                "reason": "任务接近完成，建议人工确认结果",
                "evaluation": evaluation
            }

        if evaluation.get("has_blocking_issue"):
            # 有阻塞问题
            return {
                "action": "human_required",
                "reason": evaluation.get("blocking_reason", "遇到阻塞问题"),
                "evaluation": evaluation
            }

        # 6. 生成推进提示词
        prompt = await self._generate_continue_prompt(output, current_phase, evaluation)
        self.auto_continue_count += 1

        # 保存状态
        await self._save_state()

        return {
            "action": "auto_continue",
            "prompt": prompt,
            "reason": f"自动推进（第 {self.auto_continue_count} 次）",
            "evaluation": evaluation
        }

    async def _evaluate_progress(self, output: str, current_phase: str) -> Dict:
        """
        使用 AI 评估当前进度
        """
        prompt = f"""你是一个项目进度评估助手。请分析以下信息，评估当前任务的完成情况。

## 最终目标
{self.goal}

## 子目标
{json.dumps(self.sub_goals, ensure_ascii=False) if self.sub_goals else "无"}

## 当前阶段
{current_phase}

## Claude Code 最新输出
```
{output[-2000:] if output else "无输出"}
```

## 历史进度摘要
{self._summarize_history()}

请用 JSON 格式返回评估结果：
{{
    "completion_rate": 0-100 的完成度百分比,
    "completed_items": ["已完成的子任务列表"],
    "pending_items": ["待完成的子任务列表"],
    "current_work": "当前正在进行的工作",
    "has_blocking_issue": true/false 是否有阻塞问题,
    "blocking_reason": "阻塞原因（如果有）",
    "next_step_suggestion": "建议的下一步",
    "confidence": 0-100 评估置信度
}}

只返回 JSON，不要其他内容。"""

        try:
            result = await self._call_ai(prompt)
            # 解析 JSON
            json_match = re.search(r'\{[\s\S]*\}', result)
            if json_match:
                return json.loads(json_match.group())
            return {"completion_rate": 50, "error": "无法解析评估结果"}
        except Exception as e:
            return {"completion_rate": 50, "error": str(e)}

    async def _generate_continue_prompt(self, output: str, current_phase: str, evaluation: Dict) -> str:
        """
        生成推进提示词
        """
        prompt = f"""你是一个项目管理助手。请根据以下信息，生成一个简洁有效的推进提示词，让 Claude Code 继续完成任务。

## 最终目标
{self.goal}

## 当前进度评估
- 完成度：{evaluation.get('completion_rate', 0)}%
- 当前工作：{evaluation.get('current_work', '未知')}
- 待完成：{json.dumps(evaluation.get('pending_items', []), ensure_ascii=False)}
- 建议下一步：{evaluation.get('next_step_suggestion', '继续')}

## 当前阶段
{current_phase}

## 最近输出
```
{output[-1000:] if output else "无"}
```

请生成一个简洁的推进提示词（1-3句话），引导 Claude Code 继续完成任务。
提示词应该：
1. 明确指出下一步要做什么
2. 与最终目标保持一致
3. 简洁有力，不啰嗦

只返回提示词本身，不要其他内容。"""

        try:
            result = await self._call_ai(prompt)
            return result.strip()
        except Exception as e:
            # 降级：返回通用提示词
            return f"请继续完成任务。当前进度约 {evaluation.get('completion_rate', 0)}%，{evaluation.get('next_step_suggestion', '继续执行下一步')}。"

    def _summarize_history(self) -> str:
        """生成历史进度摘要"""
        if not self.progress_history:
            return "无历史记录"

        recent = self.progress_history[-5:]  # 最近5条
        summary = []
        for h in recent:
            rate = h.get('evaluation', {}).get('completion_rate', '?')
            phase = h.get('phase', '?')
            summary.append(f"- [{phase}] 完成度: {rate}%")

        return "\n".join(summary)

    async def _call_ai(self, prompt: str) -> str:
        """调用 DeepSeek AI"""
        async with httpx.AsyncClient(timeout=30) as client:
            response = await client.post(
                f"{DEEPSEEK_BASE_URL}/chat/completions",
                headers={
                    "Authorization": f"Bearer {DEEPSEEK_API_KEY}",
                    "Content-Type": "application/json"
                },
                json={
                    "model": DEEPSEEK_MODEL,
                    "messages": [{"role": "user", "content": prompt}],
                    "temperature": 0.3,
                    "max_tokens": 1000
                }
            )
            response.raise_for_status()
            data = response.json()
            return data["choices"][0]["message"]["content"]

    async def _save_state(self):
        """保存状态到数据库"""
        state = {
            "enabled": self.enabled,
            "goal": self.goal,
            "sub_goals": self.sub_goals,
            "auto_continue_count": self.auto_continue_count,
            "last_evaluation": self.last_evaluation,
            "progress_history_count": len(self.progress_history)
        }

        # 使用项目的扩展字段存储
        await crud.update_project(
            self.project_id,
            butler_state=json.dumps(state, ensure_ascii=False)
        )

    async def load_state(self) -> bool:
        """从数据库加载状态"""
        project = await crud.get_project_by_id(self.project_id)
        if not project:
            return False

        state_json = project.get("butler_state")
        if not state_json:
            return False

        try:
            state = json.loads(state_json)
            self.enabled = state.get("enabled", False)
            self.goal = state.get("goal", "")
            self.sub_goals = state.get("sub_goals", [])
            self.auto_continue_count = state.get("auto_continue_count", 0)
            self.last_evaluation = state.get("last_evaluation")
            return True
        except:
            return False

    def disable(self):
        """禁用管家代理"""
        self.enabled = False

    def reset_auto_count(self):
        """重置自动推进计数"""
        self.auto_continue_count = 0

    def get_status(self) -> Dict:
        """获取当前状态"""
        return {
            "enabled": self.enabled,
            "goal": self.goal,
            "sub_goals": self.sub_goals,
            "auto_continue_count": self.auto_continue_count,
            "max_auto_continue": self.max_auto_continue,
            "last_evaluation": self.last_evaluation,
            "progress_history_count": len(self.progress_history)
        }


# ============================================================
# 管家代理管理器（单例模式）
# ============================================================

class ButlerAgentManager:
    """管家代理管理器"""

    _instances: Dict[int, ButlerAgent] = {}

    @classmethod
    def get_agent(cls, project_id: int, user_id: int) -> ButlerAgent:
        """获取或创建项目的管家代理"""
        if project_id not in cls._instances:
            cls._instances[project_id] = ButlerAgent(project_id, user_id)
        return cls._instances[project_id]

    @classmethod
    def remove_agent(cls, project_id: int):
        """移除项目的管家代理"""
        if project_id in cls._instances:
            del cls._instances[project_id]

    @classmethod
    async def process_project_output(cls, project_id: int, user_id: int,
                                     output: str, current_phase: str) -> Optional[Dict]:
        """
        处理项目输出（供外部调用）

        Returns:
            None 如果 Butler 未启用
            Dict 包含决策结果
        """
        agent = cls.get_agent(project_id, user_id)

        # 尝试加载保存的状态
        if not agent.enabled:
            await agent.load_state()

        if not agent.enabled:
            return None

        return await agent.process_output(output, current_phase)
