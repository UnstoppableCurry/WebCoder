# -*- coding: utf-8 -*-
"""
DeepSeek AI 服务
"""

import httpx
import json
from typing import Optional
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

async def call_deepseek(
    prompt: str,
    system_prompt: str = None,
    max_tokens: int = 1000,
    temperature: float = 0.3
) -> str:
    """
    调用 DeepSeek API

    Args:
        prompt: 用户提示
        system_prompt: 系统提示
        max_tokens: 最大生成 token 数
        temperature: 温度参数

    Returns:
        生成的文本
    """
    messages = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": prompt})

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": messages,
                "temperature": temperature,
                "max_tokens": max_tokens
            }
        )
        result = response.json()
        return result["choices"][0]["message"]["content"]

async def analyze_phase(context: str, last_phase: str = None) -> dict:
    """
    分析当前项目环节

    Args:
        context: 对话上下文摘要
        last_phase: 上次判断的环节

    Returns:
        {
            "phase": 当前环节,
            "needs_human": 是否需要人介入,
            "human_action_type": 人介入类型,
            "human_action_message": 提示消息,
            "suggested_action": 建议的自动操作,
            "reasoning": 判断理由
        }
    """
    prompt = f"""你是一个项目管理助手。根据以下 Claude Code 的对话内容，判断当前处于哪个环节。

对话摘要：
{context}

上次环节：{last_phase or "未知"}

可选环节：
1. pending_dispatch - 待分发：等待用户提出需求
2. understanding - 理解规划中：Claude 正在理解需求并制定计划
3. pending_confirm - 待确认方案：Claude 提出了方案，等待用户确认
4. implementing - 编码实现中：Claude 正在编写代码
5. pending_acceptance - 待验收：代码完成，等待用户验收
6. pending_deploy - 待部署：验收通过，等待部署
7. completed - 已完成：任务完成
8. error - 出错：遇到错误需要处理
9. idle - 空闲：没有活动任务

判断规则：
- 如果看到 Claude 询问"是否继续"、"确认方案"等，标记为 pending_confirm
- 如果看到正在编写代码、运行测试等，标记为 implementing
- 如果看到"已完成"、"任务结束"等，标记为 completed 或 pending_acceptance
- 如果看到报错信息、异常等，标记为 error
- 如果没有输出或等待输入状态，标记为 idle

请以 JSON 格式回复（不要包含 markdown 代码块标记）：
{{"phase": "当前环节", "needs_human": true/false, "human_action_type": "confirm_plan/resolve_error/accept_result/deploy/none", "human_action_message": "需要用户做什么", "suggested_action": "如果不需要人介入，建议的自动操作", "reasoning": "判断理由"}}"""

    try:
        response = await call_deepseek(prompt, max_tokens=500, temperature=0.1)
        # 尝试解析 JSON
        response = response.strip()
        if response.startswith("```"):
            response = response.split("```")[1]
            if response.startswith("json"):
                response = response[4:]
        return json.loads(response)
    except Exception as e:
        print(f"[DeepSeek] 环节分析失败: {e}")
        return {
            "phase": "error",
            "needs_human": True,
            "human_action_type": "resolve_error",
            "human_action_message": f"环节分析失败: {str(e)}",
            "suggested_action": None,
            "reasoning": "API 调用或解析失败"
        }

async def compress_context(new_output: str, old_summary: str = None) -> str:
    """
    压缩上下文

    Args:
        new_output: 新的输出内容
        old_summary: 旧的摘要

    Returns:
        压缩后的摘要
    """
    prompt = f"""请将以下对话内容压缩成简洁的摘要，保留关键信息：

之前的摘要：
{old_summary or "无"}

新增内容：
{new_output[-5000:]}

要求：
1. 保留关键的任务信息、决策点、错误信息
2. 删除冗余的输出、重复内容
3. 摘要不超过 1000 字
4. 使用简洁的中文描述

请直接输出摘要内容，不要包含任何前缀或格式标记："""

    try:
        return await call_deepseek(prompt, max_tokens=1000, temperature=0.2)
    except Exception as e:
        print(f"[DeepSeek] 上下文压缩失败: {e}")
        # 失败时返回截断的原始内容
        if old_summary:
            return f"{old_summary}\n\n[新增] {new_output[-1000:]}"
        return new_output[-2000:]

async def butler_chat(
    user_message: str,
    projects_status: list,
    recent_events: list,
    plans: list,
    chat_history: list
) -> str:
    """
    管家对话

    Args:
        user_message: 用户消息
        projects_status: 项目状态列表
        recent_events: 最近事件
        plans: 用户计划
        chat_history: 对话历史

    Returns:
        管家回复
    """
    from datetime import datetime

    system_prompt = f"""你是用户的私人项目管家，名叫"小深"。职责：
1. 管理 Claude Code 项目状态
2. 记住用户日程和计划
3. 汇报项目进展
4. 提醒待办事项

当前时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}

【项目状态】
{json.dumps(projects_status, ensure_ascii=False, indent=2)}

【最近事件】
{json.dumps(recent_events[-20:], ensure_ascii=False, indent=2)}

【用户计划】
{json.dumps(plans[-20:], ensure_ascii=False, indent=2)}

用友好简洁的中文回复。"""

    messages = chat_history[-20:] + [{"role": "user", "content": user_message}]

    async with httpx.AsyncClient(timeout=60) as client:
        response = await client.post(
            f"{DEEPSEEK_BASE_URL}/chat/completions",
            headers={"Authorization": f"Bearer {DEEPSEEK_API_KEY}"},
            json={
                "model": DEEPSEEK_MODEL,
                "messages": [{"role": "system", "content": system_prompt}] + messages,
                "temperature": 0.7,
                "max_tokens": 1000
            }
        )
        result = response.json()
        return result["choices"][0]["message"]["content"]
