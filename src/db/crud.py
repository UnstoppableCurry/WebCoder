# -*- coding: utf-8 -*-
"""
数据库 CRUD 操作
"""

from datetime import datetime, timedelta
from typing import Optional, List
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))
from config import VERIFICATION_CODE_EXPIRE_MINUTES
from .database import db

# ============================================================
# 管理员 CRUD
# ============================================================

async def create_admin(username: str, password_hash: str) -> int:
    """创建管理员"""
    return await db.execute(
        "INSERT INTO admins (username, password_hash) VALUES (?, ?)",
        (username, password_hash)
    )

async def get_admin_by_username(username: str) -> Optional[dict]:
    """通过用户名获取管理员"""
    return await db.fetch_one(
        "SELECT * FROM admins WHERE username = ? AND is_active = 1",
        (username,)
    )

async def get_admin_by_id(admin_id: int) -> Optional[dict]:
    """通过ID获取管理员"""
    return await db.fetch_one(
        "SELECT * FROM admins WHERE id = ?",
        (admin_id,)
    )

async def update_admin_login(admin_id: int):
    """更新管理员登录时间"""
    await db.execute(
        "UPDATE admins SET last_login = ? WHERE id = ?",
        (datetime.now().isoformat(), admin_id)
    )

# ============================================================
# 用户 CRUD
# ============================================================

async def create_user(
    email: str,
    password_hash: str,
    user_dir: str,
    auth_days: int = 30,
    display_name: str = None,
    created_by_admin: int = None,
    is_verified: bool = False
) -> int:
    """创建用户"""
    auth_expires_at = (datetime.now() + timedelta(days=auth_days)).isoformat()
    return await db.execute(
        """INSERT INTO users
        (email, password_hash, user_dir, auth_expires_at, display_name, created_by_admin, is_verified)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (email, password_hash, user_dir, auth_expires_at, display_name, created_by_admin, is_verified)
    )

async def get_user_by_email(email: str) -> Optional[dict]:
    """通过邮箱获取用户"""
    return await db.fetch_one(
        "SELECT * FROM users WHERE email = ?",
        (email,)
    )

async def get_user_by_id(user_id: int) -> Optional[dict]:
    """通过ID获取用户"""
    return await db.fetch_one(
        "SELECT * FROM users WHERE id = ?",
        (user_id,)
    )

async def get_all_users() -> List[dict]:
    """获取所有用户"""
    return await db.fetch_all(
        "SELECT id, email, display_name, auth_expires_at, is_active, is_verified, created_at, last_login FROM users ORDER BY created_at DESC"
    )

async def update_user(user_id: int, **kwargs):
    """更新用户信息"""
    if not kwargs:
        return
    kwargs['updated_at'] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [user_id]
    await db.execute(f"UPDATE users SET {set_clause} WHERE id = ?", tuple(values))

async def update_user_login(user_id: int):
    """更新用户登录时间"""
    await db.execute(
        "UPDATE users SET last_login = ? WHERE id = ?",
        (datetime.now().isoformat(), user_id)
    )

async def extend_user_auth(user_id: int, days: int):
    """延长用户授权时间"""
    user = await get_user_by_id(user_id)
    if not user:
        return
    current_expires = datetime.fromisoformat(user['auth_expires_at'])
    if current_expires < datetime.now():
        current_expires = datetime.now()
    new_expires = current_expires + timedelta(days=days)
    await update_user(user_id, auth_expires_at=new_expires.isoformat())

async def disable_user(user_id: int):
    """禁用用户"""
    await update_user(user_id, is_active=False)

async def delete_user(user_id: int):
    """删除用户"""
    await db.execute("DELETE FROM users WHERE id = ?", (user_id,))

# ============================================================
# 验证码 CRUD
# ============================================================

async def create_verification_code(email: str, code: str, purpose: str, expire_minutes: int = None) -> int:
    """创建验证码"""
    if expire_minutes is None:
        expire_minutes = VERIFICATION_CODE_EXPIRE_MINUTES
    expires_at = (datetime.now() + timedelta(minutes=expire_minutes)).isoformat()
    return await db.execute(
        "INSERT INTO verification_codes (email, code, purpose, expires_at) VALUES (?, ?, ?, ?)",
        (email, code, purpose, expires_at)
    )

async def verify_code(email: str, code: str, purpose: str) -> bool:
    """验证验证码"""
    record = await db.fetch_one(
        """SELECT * FROM verification_codes
        WHERE email = ? AND code = ? AND purpose = ? AND used = 0 AND expires_at > ?
        ORDER BY created_at DESC LIMIT 1""",
        (email, code, purpose, datetime.now().isoformat())
    )
    if record:
        await db.execute(
            "UPDATE verification_codes SET used = 1 WHERE id = ?",
            (record['id'],)
        )
        return True
    return False

# ============================================================
# Refresh Token CRUD
# ============================================================

async def create_refresh_token(user_id: int, token_hash: str, device_info: str = None, expire_days: int = 7) -> int:
    """创建 Refresh Token"""
    expires_at = (datetime.now() + timedelta(days=expire_days)).isoformat()
    return await db.execute(
        "INSERT INTO refresh_tokens (user_id, token_hash, device_info, expires_at) VALUES (?, ?, ?, ?)",
        (user_id, token_hash, device_info, expires_at)
    )

async def get_refresh_token(token_hash: str) -> Optional[dict]:
    """获取 Refresh Token"""
    return await db.fetch_one(
        "SELECT * FROM refresh_tokens WHERE token_hash = ? AND is_revoked = 0 AND expires_at > ?",
        (token_hash, datetime.now().isoformat())
    )

async def update_refresh_token_used(token_id: int):
    """更新 Token 使用时间"""
    await db.execute(
        "UPDATE refresh_tokens SET last_used_at = ? WHERE id = ?",
        (datetime.now().isoformat(), token_id)
    )

async def revoke_refresh_token(token_hash: str):
    """撤销 Refresh Token"""
    await db.execute(
        "UPDATE refresh_tokens SET is_revoked = 1 WHERE token_hash = ?",
        (token_hash,)
    )

async def revoke_all_user_tokens(user_id: int):
    """撤销用户所有 Token"""
    await db.execute(
        "UPDATE refresh_tokens SET is_revoked = 1 WHERE user_id = ?",
        (user_id,)
    )

# ============================================================
# 项目 CRUD
# ============================================================

async def create_project(user_id: int, name: str, project_dir: str, display_name: str = None, note: str = None) -> int:
    """创建项目"""
    return await db.execute(
        "INSERT INTO projects (user_id, name, project_dir, display_name, note) VALUES (?, ?, ?, ?, ?)",
        (user_id, name, project_dir, display_name, note)
    )

async def get_project_by_id(project_id: int) -> Optional[dict]:
    """通过ID获取项目"""
    return await db.fetch_one(
        "SELECT * FROM projects WHERE id = ?",
        (project_id,)
    )

async def get_project_by_name(user_id: int, name: str) -> Optional[dict]:
    """通过名称获取项目"""
    return await db.fetch_one(
        "SELECT * FROM projects WHERE user_id = ? AND name = ?",
        (user_id, name)
    )

async def get_user_projects(user_id: int) -> List[dict]:
    """获取用户所有项目"""
    return await db.fetch_all(
        "SELECT * FROM projects WHERE user_id = ? ORDER BY last_active DESC NULLS LAST, created_at DESC",
        (user_id,)
    )

async def get_running_projects(user_id: int = None) -> List[dict]:
    """获取运行中的项目"""
    if user_id:
        return await db.fetch_all(
            "SELECT * FROM projects WHERE user_id = ? AND is_running = 1",
            (user_id,)
        )
    return await db.fetch_all("SELECT * FROM projects WHERE is_running = 1")

async def update_project(project_id: int, **kwargs):
    """更新项目"""
    if not kwargs:
        return
    kwargs['updated_at'] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [project_id]
    await db.execute(f"UPDATE projects SET {set_clause} WHERE id = ?", tuple(values))

async def update_project_running_status(project_id: int, is_running: bool, pid: int = None):
    """更新项目运行状态"""
    await update_project(
        project_id,
        is_running=is_running,
        pid=pid,
        last_active=datetime.now().isoformat()
    )

async def delete_project(project_id: int):
    """删除项目"""
    await db.execute("DELETE FROM projects WHERE id = ?", (project_id,))

# ============================================================
# 代理状态 CRUD
# ============================================================

async def ensure_agent_state(project_id: int) -> int:
    """确保代理状态存在"""
    existing = await db.fetch_one(
        "SELECT id FROM project_agent_states WHERE project_id = ?",
        (project_id,)
    )
    if existing:
        return existing['id']
    return await db.execute(
        "INSERT INTO project_agent_states (project_id) VALUES (?)",
        (project_id,)
    )

async def get_agent_state(project_id: int) -> Optional[dict]:
    """获取代理状态"""
    return await db.fetch_one(
        "SELECT * FROM project_agent_states WHERE project_id = ?",
        (project_id,)
    )

async def update_agent_state(project_id: int, **kwargs):
    """更新代理状态"""
    if not kwargs:
        return
    kwargs['updated_at'] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [project_id]
    await db.execute(f"UPDATE project_agent_states SET {set_clause} WHERE project_id = ?", tuple(values))

async def update_heartbeat(project_id: int):
    """更新心跳时间"""
    await update_agent_state(project_id, last_heartbeat=datetime.now().isoformat())

# ============================================================
# 项目输出 CRUD
# ============================================================

async def save_output(project_id: int, content: str, content_type: str = 'stdout') -> int:
    """保存项目输出"""
    return await db.execute(
        "INSERT INTO project_outputs (project_id, content, content_type) VALUES (?, ?, ?)",
        (project_id, content, content_type)
    )

async def get_recent_outputs(project_id: int, limit: int = 100) -> List[dict]:
    """获取最近输出"""
    return await db.fetch_all(
        "SELECT * FROM project_outputs WHERE project_id = ? ORDER BY created_at DESC LIMIT ?",
        (project_id, limit)
    )

# ============================================================
# 交互历史 CRUD
# ============================================================

async def save_interaction(project_id: int, source: str, action_type: str, action_data: dict = None, result: str = None) -> int:
    """保存交互记录"""
    return await db.execute(
        "INSERT INTO project_interactions (project_id, source, action_type, action_data, result) VALUES (?, ?, ?, ?, ?)",
        (project_id, source, action_type, json.dumps(action_data) if action_data else None, result)
    )

# ============================================================
# 任务队列 CRUD
# ============================================================

async def create_task(user_id: int, task_type: str, task_data: dict, project_id: int = None, priority: int = 0) -> int:
    """创建任务"""
    return await db.execute(
        "INSERT INTO task_queue (user_id, project_id, task_type, task_data, priority) VALUES (?, ?, ?, ?, ?)",
        (user_id, project_id, task_type, json.dumps(task_data), priority)
    )

async def get_pending_tasks(limit: int = 10) -> List[dict]:
    """获取待处理任务"""
    return await db.fetch_all(
        "SELECT * FROM task_queue WHERE status = 'pending' ORDER BY priority DESC, created_at LIMIT ?",
        (limit,)
    )

async def update_task_status(task_id: int, status: str, result: str = None, error: str = None):
    """更新任务状态"""
    updates = {'status': status}
    if status == 'processing':
        updates['started_at'] = datetime.now().isoformat()
    elif status in ('completed', 'failed'):
        updates['completed_at'] = datetime.now().isoformat()
    if result:
        updates['result'] = result
    if error:
        updates['error'] = error

    set_clause = ", ".join(f"{k} = ?" for k in updates.keys())
    values = list(updates.values()) + [task_id]
    await db.execute(f"UPDATE task_queue SET {set_clause} WHERE id = ?", tuple(values))

# ============================================================
# 通知 CRUD
# ============================================================

async def create_notification(user_id: int, title: str, content: str, notification_type: str, project_id: int = None) -> int:
    """创建通知"""
    return await db.execute(
        "INSERT INTO notifications (user_id, title, content, notification_type, project_id) VALUES (?, ?, ?, ?, ?)",
        (user_id, title, content, notification_type, project_id)
    )

async def get_user_notifications(user_id: int, unread_only: bool = False, limit: int = 50) -> List[dict]:
    """获取用户通知"""
    if unread_only:
        return await db.fetch_all(
            "SELECT * FROM notifications WHERE user_id = ? AND is_read = 0 ORDER BY created_at DESC LIMIT ?",
            (user_id, limit)
        )
    return await db.fetch_all(
        "SELECT * FROM notifications WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    )

async def mark_notification_read(notification_id: int):
    """标记通知已读"""
    await db.execute(
        "UPDATE notifications SET is_read = 1, read_at = ? WHERE id = ?",
        (datetime.now().isoformat(), notification_id)
    )

async def mark_all_notifications_read(user_id: int):
    """标记所有通知已读"""
    await db.execute(
        "UPDATE notifications SET is_read = 1, read_at = ? WHERE user_id = ? AND is_read = 0",
        (datetime.now().isoformat(), user_id)
    )

async def delete_notification(notification_id: int, user_id: int):
    """删除单个通知"""
    await db.execute(
        "DELETE FROM notifications WHERE id = ? AND user_id = ?",
        (notification_id, user_id)
    )

async def delete_all_notifications(user_id: int):
    """删除所有通知"""
    await db.execute(
        "DELETE FROM notifications WHERE user_id = ?",
        (user_id,)
    )

# ============================================================
# 管家 CRUD
# ============================================================

async def add_butler_message(user_id: int, role: str, content: str) -> int:
    """添加管家消息"""
    return await db.execute(
        "INSERT INTO butler_messages (user_id, role, content) VALUES (?, ?, ?)",
        (user_id, role, content)
    )

async def get_butler_messages(user_id: int, limit: int = 20) -> List[dict]:
    """获取管家消息历史"""
    rows = await db.fetch_all(
        "SELECT role, content FROM butler_messages WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    )
    # 反转顺序，最早的在前
    return list(reversed(rows))

async def clear_butler_messages(user_id: int):
    """清空管家消息历史"""
    await db.execute(
        "DELETE FROM butler_messages WHERE user_id = ?",
        (user_id,)
    )

async def add_butler_event(user_id: int, content: str) -> int:
    """添加管家事件"""
    return await db.execute(
        "INSERT INTO butler_events (user_id, content) VALUES (?, ?)",
        (user_id, content)
    )

async def get_butler_events(user_id: int, limit: int = 20) -> List[dict]:
    """获取管家事件"""
    return await db.fetch_all(
        "SELECT content, created_at FROM butler_events WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    )

async def add_butler_plan(user_id: int, content: str) -> int:
    """添加用户计划"""
    return await db.execute(
        "INSERT INTO butler_plans (user_id, content) VALUES (?, ?)",
        (user_id, content)
    )

async def get_butler_plans(user_id: int, limit: int = 20) -> List[dict]:
    """获取用户计划"""
    return await db.fetch_all(
        "SELECT id, content, is_done, created_at FROM butler_plans WHERE user_id = ? ORDER BY created_at DESC LIMIT ?",
        (user_id, limit)
    )


async def update_butler_plan(plan_id: int, user_id: int, is_done: bool = None, content: str = None):
    """更新计划状态"""
    updates = []
    params = []
    if is_done is not None:
        updates.append("is_done = ?")
        params.append(is_done)
    if content is not None:
        updates.append("content = ?")
        params.append(content)

    if updates:
        params.extend([plan_id, user_id])
        await db.execute(
            f"UPDATE butler_plans SET {', '.join(updates)} WHERE id = ? AND user_id = ?",
            tuple(params)
        )


async def delete_butler_plan(plan_id: int, user_id: int):
    """删除计划"""
    await db.execute(
        "DELETE FROM butler_plans WHERE id = ? AND user_id = ?",
        (plan_id, user_id)
    )


# ============================================================
# Git 配置 CRUD
# ============================================================

async def get_git_config(project_id: int) -> Optional[dict]:
    """获取项目 Git 配置"""
    return await db.fetch_one(
        "SELECT * FROM project_git_config WHERE project_id = ?",
        (project_id,)
    )

async def create_git_config(project_id: int, **kwargs) -> int:
    """创建 Git 配置"""
    kwargs['project_id'] = project_id
    columns = ", ".join(kwargs.keys())
    placeholders = ", ".join("?" * len(kwargs))
    return await db.execute(
        f"INSERT INTO project_git_config ({columns}) VALUES ({placeholders})",
        tuple(kwargs.values())
    )

async def update_git_config(project_id: int, **kwargs):
    """更新 Git 配置"""
    if not kwargs:
        return
    kwargs['updated_at'] = datetime.now().isoformat()
    set_clause = ", ".join(f"{k} = ?" for k in kwargs.keys())
    values = list(kwargs.values()) + [project_id]
    await db.execute(f"UPDATE project_git_config SET {set_clause} WHERE project_id = ?", tuple(values))

async def delete_git_config(project_id: int):
    """删除 Git 配置"""
    await db.execute("DELETE FROM project_git_config WHERE project_id = ?", (project_id,))

async def get_projects_with_auto_commit() -> List[dict]:
    """获取启用自动提交的项目"""
    return await db.fetch_all(
        """SELECT p.*, g.* FROM projects p
        JOIN project_git_config g ON p.id = g.project_id
        WHERE g.auto_commit = 1 AND p.is_running = 1"""
    )

async def update_last_commit(project_id: int):
    """更新最后提交时间"""
    await update_git_config(project_id, last_commit_at=datetime.now().isoformat())

async def update_last_push(project_id: int):
    """更新最后推送时间"""
    await update_git_config(project_id, last_push_at=datetime.now().isoformat())
