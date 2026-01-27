# -*- coding: utf-8 -*-
"""
Claude Mobile Gateway - 业务层 (v10)
处理 UI、邮件、管家、业务逻辑

v10 变更：
- 集成用户认证系统 (JWT)
- 集成代理系统 (Supervisor)
- 多用户支持，目录隔离
- 新增管理员功能

职责：
- Web UI（移动端友好）
- 邮件通知
- DeepSeek 管家
- 状态管理与业务逻辑
- WebSocket 代理到 Core（带认证）
- 接收 Core 的 Webhook 事件
- 代理系统管理

Port: 3000 (对外服务)
依赖: Core Service (3001)
"""

import os
import json
import asyncio
import websockets
from datetime import datetime
from typing import Optional
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, Request, HTTPException, Depends
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn

from config import (
    CORE_URL,
    CORE_WS_URL,
    GATEWAY_PORT,
    DATA_DIR
)
from db.database import db as database, init_db
from db import crud
from auth.dependencies import get_token_from_query, get_current_user
from auth.jwt_handler import decode_token
from agents import supervisor
from services.deepseek_service import butler_chat as deepseek_butler_chat

# API 路由
from api import auth as auth_router
from api import admin as admin_router
from api import projects as projects_router
from api import notifications as notifications_router
from api import files as files_router
from api import butler as butler_router  # Butler Agent API
# Git 版本管理已集成到 ProjectAgent 心跳中，不再需要独立的 git_worker

# 确保目录存在
os.makedirs(str(DATA_DIR), exist_ok=True)

# ============== 数据模型 ==============
class ChatRequest(BaseModel):
    message: str

class WebhookEvent(BaseModel):
    event: str
    data: dict
    timestamp: str

# ============== FastAPI ==============
@asynccontextmanager
async def lifespan(app: FastAPI):
    print("[Gateway v10] 服务启动中...")

    # 1. 初始化数据库
    init_db()
    print("[Gateway v10] 数据库已初始化")

    # 2. 确保默认管理员存在
    from api.admin import ensure_default_admin
    await ensure_default_admin()

    # 3. 启动代理系统（Git 版本管理已集成到代理心跳中）
    await supervisor.start()
    print("[Gateway v10] 代理系统已启动")

    print(f"[Gateway v10] 服务已启动，端口: {GATEWAY_PORT}")
    print(f"[Gateway v10] Core 服务: {CORE_URL}")

    yield

    # 停止服务
    await supervisor.stop()
    print("[Gateway v10] 服务已关闭")

app = FastAPI(title="Claude Mobile Gateway v10", lifespan=lifespan)

# CORS 配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 静态文件服务
STATIC_DIR = os.path.join(os.path.dirname(__file__), "static")
if os.path.exists(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

# ============== 注册 API 路由 ==============
app.include_router(auth_router.router, prefix="/api/auth", tags=["认证"])
app.include_router(admin_router.router, prefix="/api/admin", tags=["管理员"])
app.include_router(projects_router.router, prefix="/api/projects", tags=["项目"])
app.include_router(notifications_router.router, prefix="/api/notifications", tags=["通知"])
app.include_router(files_router.router, prefix="/api/projects", tags=["文件和Git"])
app.include_router(butler_router.router, prefix="/api/projects", tags=["Butler Agent"])

# ============== Webhook 端点 ==============
@app.post("/webhook")
async def receive_webhook(event: WebhookEvent):
    """接收 Core 的事件回调"""
    print(f"[Webhook] 收到事件: {event.event}")

    project_name = event.data.get("project", "未知项目")

    # 尝试获取项目信息
    # 注意：v10 中项目名可能需要通过数据库查找
    # 这里简化处理，只记录事件

    if event.event == "process_started":
        print(f"[Webhook] 项目 [{project_name}] 已启动")

    elif event.event == "process_stopped":
        print(f"[Webhook] 项目 [{project_name}] 已停止")

    elif event.event == "process_finished":
        print(f"[Webhook] 项目 [{project_name}] 执行完毕")

    elif event.event == "process_error":
        print(f"[Webhook] 项目 [{project_name}] 出错: {event.data.get('error')}")

    return {"status": "ok"}

# ============== 代理系统 API ==============
@app.get("/api/supervisor/status")
async def get_supervisor_status():
    """获取代理系统状态"""
    return supervisor.get_stats()

# ============== 管家 API ==============
@app.post("/api/butler/chat")
async def butler_chat_api(req: ChatRequest, user: dict = Depends(get_current_user)):
    """与管家对话"""
    user_id = user["id"]

    # 获取项目状态
    projects = await crud.get_user_projects(user_id)
    projects_status = []
    for p in projects:
        agent_state = await crud.get_agent_state(p["id"])
        projects_status.append({
            "name": p["display_name"] or p["name"],
            "status": p["status"],
            "is_running": p["is_running"],
            "phase": agent_state["current_phase"] if agent_state else "idle"
        })

    # 获取事件和计划
    events = await crud.get_butler_events(user_id, 20)
    plans = await crud.get_butler_plans(user_id, 20)
    chat_history = await crud.get_butler_messages(user_id, 20)

    # 保存用户消息
    await crud.add_butler_message(user_id, "user", req.message)

    # 检测计划关键词，自动记录计划
    if any(kw in req.message for kw in ["计划", "打算", "准备", "安排", "提醒", "记住"]):
        await crud.add_butler_plan(user_id, req.message)

    try:
        # 调用 DeepSeek
        response = await deepseek_butler_chat(
            user_message=req.message,
            projects_status=projects_status,
            recent_events=[{"content": e["content"], "time": e["created_at"]} for e in events],
            plans=[{"content": p["content"], "done": p["is_done"]} for p in plans],
            chat_history=chat_history
        )

        # 保存助手回复
        await crud.add_butler_message(user_id, "assistant", response)

        return {"response": response}
    except Exception as e:
        print(f"[Butler] 错误: {e}")
        return {"response": f"抱歉，出错了: {str(e)}"}


class PlanRequest(BaseModel):
    content: str


class PlanUpdateRequest(BaseModel):
    is_done: Optional[bool] = None
    content: Optional[str] = None


@app.get("/api/butler/plans")
async def get_plans(user: dict = Depends(get_current_user)):
    """获取用户的计划列表"""
    plans = await crud.get_butler_plans(user["id"], 100)
    return {"plans": plans}


@app.post("/api/butler/plans")
async def create_plan(req: PlanRequest, user: dict = Depends(get_current_user)):
    """创建新计划"""
    plan_id = await crud.add_butler_plan(user["id"], req.content)
    return {"id": plan_id, "message": "计划已添加"}


@app.patch("/api/butler/plans/{plan_id}")
async def update_plan(plan_id: int, req: PlanUpdateRequest, user: dict = Depends(get_current_user)):
    """更新计划状态"""
    await crud.update_butler_plan(plan_id, user["id"], is_done=req.is_done, content=req.content)
    return {"message": "更新成功"}


@app.delete("/api/butler/plans/{plan_id}")
async def delete_plan(plan_id: int, user: dict = Depends(get_current_user)):
    """删除计划"""
    await crud.delete_butler_plan(plan_id, user["id"])
    return {"message": "删除成功"}


@app.get("/api/butler/history")
async def get_butler_history(user: dict = Depends(get_current_user)):
    """获取管家聊天历史"""
    messages = await crud.get_butler_messages(user["id"], 50)
    return {"messages": [{"role": m["role"], "content": m["content"]} for m in messages]}


@app.delete("/api/butler/history")
async def clear_butler_history(user: dict = Depends(get_current_user)):
    """清空管家聊天历史"""
    await crud.clear_butler_messages(user["id"])
    return {"message": "已清空"}


# ============== WebSocket 代理 ==============
@app.websocket("/ws/{project_name}")
async def websocket_proxy(
    websocket: WebSocket,
    project_name: str,
    mode: str = "new",
    token: str = None
):
    """
    代理 WebSocket 到 Core

    v10: 增加认证，通过 token 参数验证用户
    """
    # 验证 token
    if token:
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
            user = await crud.get_user_by_id(int(user_id))
            if not user:
                await websocket.close(code=4001, reason="用户不存在")
                return
        except Exception as e:
            await websocket.close(code=4001, reason="Token 无效")
            return
    else:
        # v10: 暂时允许无 token 连接，后续可改为强制认证
        user = None

    await websocket.accept()

    # 获取用户目录
    user_dir = user["user_dir"] if user else None

    # 构建 Core WebSocket URL
    core_ws_url = f"{CORE_WS_URL}/ws/{project_name}?mode={mode}"
    if user_dir:
        core_ws_url += f"&user_dir={user_dir}"

    try:
        async with websockets.connect(core_ws_url) as core_ws:
            async def client_to_core():
                try:
                    while True:
                        data = await websocket.receive()
                        if data["type"] == "websocket.disconnect":
                            break
                        if "bytes" in data:
                            await core_ws.send(data["bytes"])
                        elif "text" in data:
                            await core_ws.send(data["text"])
                except:
                    pass

            async def core_to_client():
                try:
                    async for msg in core_ws:
                        if isinstance(msg, bytes):
                            await websocket.send_bytes(msg)
                        else:
                            await websocket.send_text(msg)
                except:
                    pass

            await asyncio.gather(client_to_core(), core_to_client())
    except Exception as e:
        print(f"[Gateway] WebSocket 代理错误: {e}")
    finally:
        try:
            await websocket.close()
        except:
            pass

# ============== Web UI ==============
@app.get("/", response_class=HTMLResponse)
async def index():
    """v10 登录页面"""
    return get_login_page()

@app.get("/app", response_class=HTMLResponse)
async def app_page():
    """v10 主应用页面"""
    return get_app_page()

@app.get("/admin", response_class=HTMLResponse)
async def admin_page():
    """v10 管理员页面"""
    return get_admin_page()

def get_login_page():
    """登录页面 HTML"""
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <title>Claude Mobile - 登录</title>
    <style>
        :root {
            --bg: #0d0d0d; --card: #1a1a1a; --border: #2a2a2a;
            --text: #fff; --text2: #888; --accent: #0066ff;
            --danger: #ef4444; --success: #22c55e;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg); color: var(--text);
            min-height: 100vh; display: flex; flex-direction: column;
            justify-content: center; align-items: center; padding: 20px;
        }
        .logo { font-size: 48px; margin-bottom: 8px; }
        h1 { font-size: 24px; margin-bottom: 8px; }
        .subtitle { color: var(--text2); margin-bottom: 32px; font-size: 14px; }
        .login-box {
            background: var(--card); padding: 24px; border-radius: 16px;
            width: 100%; max-width: 360px;
        }
        .tabs { display: flex; margin-bottom: 20px; }
        .tab {
            flex: 1; padding: 10px; text-align: center; cursor: pointer;
            border-bottom: 2px solid transparent; color: var(--text2);
        }
        .tab.active { color: var(--accent); border-color: var(--accent); }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-size: 12px; color: var(--text2); margin-bottom: 6px; }
        .form-group input {
            width: 100%; padding: 12px 14px; background: var(--bg);
            border: 1px solid var(--border); border-radius: 10px;
            color: var(--text); font-size: 15px;
        }
        .form-group input:focus { outline: none; border-color: var(--accent); }
        .btn {
            width: 100%; padding: 14px; border: none; border-radius: 10px;
            font-size: 16px; font-weight: 500; cursor: pointer;
            background: var(--accent); color: #fff;
        }
        .btn:disabled { opacity: 0.5; cursor: not-allowed; }
        .error { color: var(--danger); font-size: 13px; margin-bottom: 12px; display: none; }
        .success { color: var(--success); font-size: 13px; margin-bottom: 12px; display: none; }
        .admin-link {
            margin-top: 24px; text-align: center;
            font-size: 12px; color: var(--text2);
        }
        .admin-link a { color: var(--accent); text-decoration: none; }
        .hidden { display: none; }
        .verify-row { display: flex; gap: 8px; }
        .verify-row input { flex: 1; }
        .verify-btn {
            padding: 12px 16px; background: var(--border); border: none;
            border-radius: 10px; color: var(--text); font-size: 13px; cursor: pointer;
            white-space: nowrap;
        }
        .verify-btn:disabled { opacity: 0.5; }
    </style>
</head>
<body>
    <div class="logo">🚀</div>
    <h1>Claude Mobile</h1>
    <p class="subtitle">v10 - 多用户 · 代理自动化</p>

    <div class="login-box">
        <div class="tabs">
            <div class="tab active" onclick="switchTab('login')">登录</div>
            <div class="tab" onclick="switchTab('register')">注册</div>
        </div>

        <div id="loginForm">
            <div class="error" id="loginError"></div>
            <div class="form-group">
                <label>邮箱</label>
                <input type="email" id="loginEmail" placeholder="your@email.com">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" id="loginPassword" placeholder="输入密码">
            </div>
            <button class="btn" onclick="login()">登录</button>
        </div>

        <div id="registerForm" class="hidden">
            <div class="error" id="registerError"></div>
            <div class="success" id="registerSuccess"></div>
            <div class="form-group">
                <label>邮箱</label>
                <input type="email" id="regEmail" placeholder="your@email.com">
            </div>
            <div class="form-group">
                <label>验证码</label>
                <div class="verify-row">
                    <input type="text" id="regCode" placeholder="6位验证码">
                    <button class="verify-btn" id="sendCodeBtn" onclick="sendCode()">发送验证码</button>
                </div>
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" id="regPassword" placeholder="至少6位密码">
            </div>
            <button class="btn" onclick="register()">注册</button>
        </div>
    </div>

    <div class="admin-link">
        <a href="/admin">管理员登录</a>
    </div>

    <script>
        function switchTab(tab) {
            document.querySelectorAll('.tab').forEach((el, i) => {
                el.classList.toggle('active', (i === 0 && tab === 'login') || (i === 1 && tab === 'register'));
            });
            document.getElementById('loginForm').classList.toggle('hidden', tab !== 'login');
            document.getElementById('registerForm').classList.toggle('hidden', tab !== 'register');
        }

        async function login() {
            const email = document.getElementById('loginEmail').value.trim();
            const password = document.getElementById('loginPassword').value;
            const errorEl = document.getElementById('loginError');

            if (!email || !password) {
                errorEl.textContent = '请填写邮箱和密码';
                errorEl.style.display = 'block';
                return;
            }

            try {
                const res = await fetch('/api/auth/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, password })
                });
                const data = await res.json();

                if (!res.ok) {
                    errorEl.textContent = data.detail || '登录失败';
                    errorEl.style.display = 'block';
                    return;
                }

                localStorage.setItem('access_token', data.access_token);
                localStorage.setItem('refresh_token', data.refresh_token);
                window.location.href = '/app';
            } catch (e) {
                errorEl.textContent = '网络错误';
                errorEl.style.display = 'block';
            }
        }

        let codeCountdown = 0;
        async function sendCode() {
            const email = document.getElementById('regEmail').value.trim();
            const errorEl = document.getElementById('registerError');
            const successEl = document.getElementById('registerSuccess');
            const btn = document.getElementById('sendCodeBtn');

            if (!email) {
                errorEl.textContent = '请输入邮箱';
                errorEl.style.display = 'block';
                successEl.style.display = 'none';
                return;
            }

            btn.disabled = true;
            try {
                const res = await fetch('/api/auth/register', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email })
                });
                const data = await res.json();

                if (!res.ok) {
                    errorEl.textContent = data.detail || '发送失败';
                    errorEl.style.display = 'block';
                    successEl.style.display = 'none';
                    btn.disabled = false;
                    return;
                }

                successEl.textContent = '验证码已发送到邮箱';
                successEl.style.display = 'block';
                errorEl.style.display = 'none';

                codeCountdown = 60;
                const timer = setInterval(() => {
                    codeCountdown--;
                    btn.textContent = codeCountdown + 's';
                    if (codeCountdown <= 0) {
                        clearInterval(timer);
                        btn.textContent = '发送验证码';
                        btn.disabled = false;
                    }
                }, 1000);
            } catch (e) {
                errorEl.textContent = '网络错误';
                errorEl.style.display = 'block';
                btn.disabled = false;
            }
        }

        async function register() {
            const email = document.getElementById('regEmail').value.trim();
            const code = document.getElementById('regCode').value.trim();
            const password = document.getElementById('regPassword').value;
            const errorEl = document.getElementById('registerError');
            const successEl = document.getElementById('registerSuccess');

            if (!email || !code || !password) {
                errorEl.textContent = '请填写所有字段';
                errorEl.style.display = 'block';
                successEl.style.display = 'none';
                return;
            }

            if (password.length < 6) {
                errorEl.textContent = '密码至少6位';
                errorEl.style.display = 'block';
                successEl.style.display = 'none';
                return;
            }

            try {
                const res = await fetch('/api/auth/verify-email', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ email, code, password })
                });
                const data = await res.json();

                if (!res.ok) {
                    errorEl.textContent = data.detail || '注册失败';
                    errorEl.style.display = 'block';
                    successEl.style.display = 'none';
                    return;
                }

                successEl.textContent = '注册成功！正在跳转...';
                successEl.style.display = 'block';
                errorEl.style.display = 'none';

                localStorage.setItem('access_token', data.access_token);
                localStorage.setItem('refresh_token', data.refresh_token);
                setTimeout(() => { window.location.href = '/app'; }, 1000);
            } catch (e) {
                errorEl.textContent = '网络错误';
                errorEl.style.display = 'block';
            }
        }

        // 检查是否已登录
        const token = localStorage.getItem('access_token');
        if (token) {
            window.location.href = '/app';
        }
    </script>
</body>
</html>
"""

def get_app_page():
    """主应用页面 HTML"""
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no, viewport-fit=cover">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">
    <title>Claude Mobile v10</title>
    <link rel="stylesheet" href="/static/xterm.min.css">
    <style>
        :root {
            --bg: #0d0d0d; --card: #1a1a1a; --card2: #222; --border: #2a2a2a;
            --text: #fff; --text2: #888; --accent: #0066ff; --success: #22c55e;
            --danger: #ef4444; --warning: #f59e0b; --purple: #8b5cf6;
            --safe-bottom: env(safe-area-inset-bottom, 0px);
            --safe-top: env(safe-area-inset-top, 0px);
        }
        * { margin: 0; padding: 0; box-sizing: border-box; -webkit-tap-highlight-color: transparent; }
        html, body { height: 100%; overflow: hidden; touch-action: manipulation; }
        body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); }

        .tab-bar {
            position: fixed; bottom: 0; left: 0; right: 0;
            height: calc(50px + var(--safe-bottom)); padding-bottom: var(--safe-bottom);
            background: var(--card); border-top: 1px solid var(--border);
            display: flex; z-index: 100;
        }
        .tab-item {
            flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
            gap: 2px; color: var(--text2); font-size: 10px; cursor: pointer;
        }
        .tab-item.active { color: var(--accent); }
        .tab-item .icon { font-size: 20px; }
        .tab-item .badge {
            position: absolute; top: 4px; right: calc(50% - 20px);
            background: var(--danger); color: #fff; font-size: 10px;
            padding: 1px 5px; border-radius: 8px; min-width: 16px; text-align: center;
        }

        .page {
            position: fixed; top: 0; left: 0; right: 0;
            bottom: calc(50px + var(--safe-bottom));
            display: none; flex-direction: column; overflow: hidden;
        }
        .page.active { display: flex; }
        .page.fullscreen { bottom: 0; }

        .header {
            padding: 12px 16px; padding-top: calc(12px + var(--safe-top));
            background: var(--card); border-bottom: 1px solid var(--border);
            display: flex; justify-content: space-between; align-items: center;
        }
        .header h1 { font-size: 18px; }
        .header-sub { font-size: 11px; color: var(--text2); margin-top: 2px; }
        .header-right { display: flex; align-items: center; gap: 12px; }
        .logout-btn { font-size: 12px; color: var(--text2); cursor: pointer; }

        .project-list { flex: 1; overflow-y: auto; padding: 8px; -webkit-overflow-scrolling: touch; }
        .swipe-hint { font-size: 10px; color: var(--text2); text-align: center; padding: 6px; background: var(--card); }

        .project-wrapper { position: relative; margin-bottom: 8px; overflow: hidden; border-radius: 10px; }
        .project-actions {
            position: absolute; top: 0; bottom: 0; right: 0; display: flex; align-items: stretch;
        }
        .project-actions button {
            width: 60px; border: none; color: #fff; font-size: 10px; font-weight: 500;
            display: flex; flex-direction: column; align-items: center; justify-content: center; gap: 2px;
        }
        .project-actions button .icon { font-size: 16px; }
        .btn-restart { background: var(--purple); }
        .btn-stop { background: var(--warning); }
        .btn-delete { background: var(--danger); }

        .project-card {
            position: relative; background: var(--card); padding: 12px;
            display: flex; align-items: center; gap: 10px;
            transition: transform 0.2s ease-out; z-index: 1; cursor: pointer;
        }
        .project-card.running { border-left: 3px solid var(--success); }
        .project-card.needs-action { border-left: 3px solid var(--warning); }
        .project-icon {
            width: 40px; height: 40px; background: var(--border); border-radius: 8px;
            display: flex; align-items: center; justify-content: center; font-size: 18px; flex-shrink: 0;
        }
        .project-info { flex: 1; min-width: 0; }
        .project-name { font-size: 14px; font-weight: 500; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .project-meta { font-size: 11px; color: var(--text2); margin-top: 2px; display: flex; gap: 6px; align-items: center; }
        .project-status { padding: 1px 6px; border-radius: 8px; font-size: 9px; font-weight: 500; }
        .status-running { background: var(--success); color: #fff; }
        .status-needs-action { background: var(--warning); color: #000; }
        .status-idle { background: var(--border); color: var(--text2); }
        .project-phase { font-size: 10px; color: var(--purple); }
        .project-arrow { color: var(--text2); font-size: 16px; }

        .add-btn {
            position: fixed; right: 16px; bottom: calc(66px + var(--safe-bottom));
            width: 50px; height: 50px; background: var(--accent); border-radius: 50%;
            display: flex; align-items: center; justify-content: center;
            font-size: 24px; color: #fff; box-shadow: 0 4px 12px rgba(0,102,255,0.4);
            cursor: pointer; z-index: 50;
        }

        .terminal-header {
            padding: 8px 12px; padding-top: calc(8px + var(--safe-top));
            background: var(--card); display: flex; align-items: center; gap: 8px;
            border-bottom: 1px solid var(--border); flex-shrink: 0;
        }
        .back-btn {
            width: 32px; height: 32px; display: flex; align-items: center; justify-content: center;
            background: var(--border); border-radius: 6px; font-size: 16px; cursor: pointer;
        }
        .terminal-title { font-size: 14px; font-weight: 500; flex: 1; }
        .terminal-status { font-size: 10px; color: var(--text2); display: flex; align-items: center; gap: 4px; }
        .terminal-status.connected { color: var(--success); }
        .terminal-status::before { content: ''; width: 5px; height: 5px; border-radius: 50%; background: currentColor; }

        .terminal-toolbar {
            display: flex; gap: 6px; padding: 8px 12px; background: var(--card);
            border-bottom: 1px solid var(--border); overflow-x: auto; flex-shrink: 0;
        }
        .toolbar-btn {
            padding: 8px 12px; background: var(--border); border: none; border-radius: 6px;
            color: var(--text); font-size: 12px; white-space: nowrap; cursor: pointer;
            display: flex; align-items: center; gap: 4px; flex-shrink: 0;
        }
        .toolbar-btn:active { opacity: 0.7; }
        .toolbar-btn.danger { background: var(--danger); }
        .toolbar-btn.warning { background: var(--warning); color: #000; }
        .toolbar-btn.success { background: var(--success); }
        .toolbar-btn.primary { background: var(--accent); }
        .toolbar-btn .icon { font-size: 14px; }

        #terminal { flex: 1; padding: 4px; }

        /* 移动端输入栏 */
        .mobile-input-bar {
            display: none; flex-direction: column; gap: 6px; padding: 8px 12px;
            background: var(--card); border-top: 1px solid var(--border);
        }
        .mobile-input-row {
            display: flex; gap: 6px; align-items: center;
        }
        .mobile-input-bar input {
            flex: 1; padding: 10px 12px; background: var(--bg); border: 1px solid var(--border);
            border-radius: 8px; color: var(--text); font-size: 14px;
        }
        .mobile-input-bar input:focus { outline: none; border-color: var(--accent); }
        .mobile-send-btn {
            padding: 10px 14px; background: var(--accent); border: none; border-radius: 8px;
            color: #fff; font-size: 13px; font-weight: 500; cursor: pointer; white-space: nowrap;
        }
        .mobile-send-btn:active { opacity: 0.8; }
        /* 快捷键栏 */
        .mobile-keys-bar {
            display: flex; gap: 4px; flex-wrap: wrap; padding-top: 4px;
        }
        .mobile-key-btn {
            padding: 8px 10px; background: var(--bg); border: 1px solid var(--border);
            border-radius: 6px; color: var(--text); font-size: 12px; cursor: pointer;
            min-width: 36px; text-align: center;
        }
        .mobile-key-btn:active { background: var(--accent); color: #fff; }
        .mobile-key-btn.mod { background: var(--border); font-weight: 500; }
        .mobile-key-btn.mod.active { background: var(--accent); color: #fff; }
        @media (max-width: 768px) {
            .mobile-input-bar { display: flex; }
        }

        /* 终端设置样式 */
        .theme-btn, .cursor-btn {
            padding: 8px; border: 1px solid var(--border); border-radius: 6px;
            background: var(--bg); color: var(--text); cursor: pointer; font-size: 12px;
        }
        .theme-btn span, .cursor-btn { display: block; padding: 4px 8px; border-radius: 4px; }
        .theme-btn.active, .cursor-btn.active { border-color: var(--accent); background: var(--accent); color: #fff; }
        .theme-btn:hover, .cursor-btn:hover { border-color: var(--accent); }
        input[type="range"] {
            -webkit-appearance: none; height: 6px; background: var(--border); border-radius: 3px;
        }
        input[type="range"]::-webkit-slider-thumb {
            -webkit-appearance: none; width: 18px; height: 18px; background: var(--accent);
            border-radius: 50%; cursor: pointer;
        }

        /* 文件管理器样式 */
        .file-manager { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .file-toolbar {
            display: flex; gap: 6px; padding: 8px 12px; background: var(--card);
            border-bottom: 1px solid var(--border); flex-wrap: wrap;
        }
        .file-path {
            flex: 1; min-width: 200px; padding: 8px 12px; background: var(--bg);
            border: 1px solid var(--border); border-radius: 6px; color: var(--text2);
            font-size: 12px; font-family: monospace; overflow: hidden; text-overflow: ellipsis;
        }
        .file-list { flex: 1; overflow-y: auto; padding: 8px; }
        .file-item {
            display: flex; align-items: center; gap: 10px; padding: 10px 12px;
            background: var(--card); border-radius: 8px; margin-bottom: 6px; cursor: pointer;
        }
        .file-item:active { opacity: 0.8; }
        .file-item.selected { border: 1px solid var(--accent); }
        .file-icon { font-size: 20px; width: 28px; text-align: center; }
        .file-info { flex: 1; min-width: 0; }
        .file-name { font-size: 14px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
        .file-meta { font-size: 11px; color: var(--text2); margin-top: 2px; }
        .file-actions { display: flex; gap: 8px; }
        .file-action-btn {
            width: 32px; height: 32px; border: none; border-radius: 6px;
            background: var(--border); color: var(--text); font-size: 14px; cursor: pointer;
        }
        .upload-area {
            border: 2px dashed var(--border); border-radius: 10px; padding: 20px;
            text-align: center; margin: 8px; color: var(--text2);
        }
        .upload-area.dragover { border-color: var(--accent); background: rgba(0,102,255,0.1); }
        .view-tabs { display: flex; border-bottom: 1px solid var(--border); }
        .view-tab {
            flex: 1; padding: 10px; text-align: center; cursor: pointer;
            color: var(--text2); font-size: 13px; border-bottom: 2px solid transparent;
        }
        .view-tab.active { color: var(--accent); border-color: var(--accent); }
        .view-content { display: none; flex: 1; flex-direction: column; overflow: hidden; }
        .view-content.active { display: flex; }

        /* Git 管理样式 */
        .git-panel { flex: 1; overflow-y: auto; padding: 12px; }
        .git-section { background: var(--card); border-radius: 10px; padding: 12px; margin-bottom: 12px; }
        .git-section-title { font-size: 14px; font-weight: 500; margin-bottom: 10px; display: flex; align-items: center; gap: 6px; }
        .git-status-item { font-size: 12px; padding: 6px 0; border-bottom: 1px solid var(--border); display: flex; gap: 8px; }
        .git-status-item:last-child { border-bottom: none; }
        .git-badge { padding: 2px 8px; border-radius: 4px; font-size: 10px; }
        .git-badge.modified { background: var(--warning); color: #000; }
        .git-badge.new { background: var(--success); color: #fff; }
        .git-input { width: 100%; padding: 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 13px; margin-bottom: 8px; }
        .git-commits { max-height: 200px; overflow-y: auto; }
        .git-commit-item { padding: 8px; border-bottom: 1px solid var(--border); }
        .git-commit-msg { font-size: 13px; margin-bottom: 4px; }
        .git-commit-meta { font-size: 11px; color: var(--text2); }

        /* 设置页面样式 */
        .settings-section { background: var(--card); border-radius: 10px; padding: 16px; margin-bottom: 12px; }
        .settings-title { font-size: 14px; font-weight: 500; margin-bottom: 12px; color: var(--text2); }
        .settings-item { display: flex; justify-content: space-between; align-items: center; padding: 10px 0; border-bottom: 1px solid var(--border); }
        .settings-item:last-child { border-bottom: none; }
        .settings-label { font-size: 14px; }
        .settings-value { font-size: 13px; color: var(--text2); text-align: right; max-width: 60%; word-break: break-all; }
        .settings-input { flex: 1; max-width: 200px; padding: 8px 12px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text); font-size: 14px; text-align: right; }
        .settings-btn { padding: 6px 16px; background: var(--border); border: none; border-radius: 6px; color: var(--text); font-size: 13px; cursor: pointer; }
        .settings-btn.primary { background: var(--accent); }
        .settings-btn.danger { background: var(--danger); }

        /* 文件编辑器样式 */
        .editor-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .editor-header { display: flex; align-items: center; gap: 8px; padding: 8px 12px; background: var(--card); border-bottom: 1px solid var(--border); }
        .editor-filename { flex: 1; font-size: 13px; font-family: monospace; color: var(--text2); }
        .editor-textarea { flex: 1; width: 100%; padding: 12px; background: var(--bg); border: none; color: var(--text); font-family: 'Monaco', 'Menlo', monospace; font-size: 13px; line-height: 1.5; resize: none; outline: none; }
        .editor-status { padding: 6px 12px; background: var(--card); font-size: 11px; color: var(--text2); display: flex; justify-content: space-between; }

        /* 代理状态详情样式 */
        .agent-detail { padding: 12px; }
        .agent-timeline { position: relative; padding-left: 20px; }
        .agent-timeline::before { content: ''; position: absolute; left: 6px; top: 0; bottom: 0; width: 2px; background: var(--border); }
        .timeline-item { position: relative; padding: 10px 0; padding-left: 16px; }
        .timeline-item::before { content: ''; position: absolute; left: -14px; top: 14px; width: 10px; height: 10px; border-radius: 50%; background: var(--accent); }
        .timeline-item.completed::before { background: var(--success); }
        .timeline-item.error::before { background: var(--danger); }
        .timeline-title { font-size: 13px; font-weight: 500; }
        .timeline-time { font-size: 11px; color: var(--text2); }
        .timeline-desc { font-size: 12px; color: var(--text2); margin-top: 4px; }

        /* 计划列表样式 */
        .plan-item { background: var(--card); padding: 12px; border-radius: 8px; margin-bottom: 8px; }
        .plan-item.done { opacity: 0.6; }
        .plan-content { font-size: 14px; display: flex; align-items: center; gap: 8px; }
        .plan-checkbox { width: 18px; height: 18px; cursor: pointer; }
        .plan-time { font-size: 11px; color: var(--text2); margin-top: 4px; }
        .plan-actions { display: flex; gap: 8px; margin-top: 8px; }

        /* 搜索样式 */
        .search-bar { display: flex; gap: 8px; padding: 8px 12px; background: var(--card); border-bottom: 1px solid var(--border); }
        .search-input { flex: 1; padding: 10px 14px; background: var(--bg); border: 1px solid var(--border); border-radius: 20px; color: var(--text); font-size: 14px; }
        .search-results { flex: 1; overflow-y: auto; padding: 8px; }
        .search-result-item { background: var(--card); padding: 12px; border-radius: 8px; margin-bottom: 8px; cursor: pointer; }
        .search-result-title { font-size: 14px; font-weight: 500; }
        .search-result-path { font-size: 11px; color: var(--text2); margin-top: 2px; font-family: monospace; }
        .search-result-preview { font-size: 12px; color: var(--text2); margin-top: 6px; padding: 6px; background: var(--bg); border-radius: 4px; }

        .chat-container { flex: 1; display: flex; flex-direction: column; overflow: hidden; }
        .chat-messages { flex: 1; overflow-y: auto; padding: 12px; -webkit-overflow-scrolling: touch; }
        .message {
            max-width: 85%; padding: 10px 14px; border-radius: 16px; margin-bottom: 8px;
            line-height: 1.4; word-wrap: break-word; white-space: pre-wrap; font-size: 14px;
        }
        .message.user { background: var(--accent); margin-left: auto; border-bottom-right-radius: 4px; }
        .message.assistant { background: var(--card); border-bottom-left-radius: 4px; }
        .chat-input-area {
            padding: 10px 12px; background: var(--card); border-top: 1px solid var(--border);
            display: flex; gap: 8px; align-items: flex-end;
        }
        .chat-input {
            flex: 1; background: var(--bg); border: 1px solid var(--border); border-radius: 18px;
            padding: 8px 14px; color: var(--text); font-size: 15px; resize: none; max-height: 80px; outline: none;
        }
        .chat-send {
            width: 40px; height: 40px; background: var(--accent); border: none; border-radius: 50%;
            color: #fff; font-size: 18px; cursor: pointer;
        }
        .chat-send:disabled { background: var(--border); }
        .message.typing { color: var(--text2); }
        .typing-indicator {
            display: inline-block;
            animation: blink 1s infinite;
        }
        @keyframes blink {
            0%, 50% { opacity: 1; }
            51%, 100% { opacity: 0.3; }
        }

        .notification-list { flex: 1; overflow-y: auto; padding: 8px; }
        .notification-item {
            background: var(--card); padding: 12px; border-radius: 10px; margin-bottom: 8px;
            cursor: pointer;
        }
        .notification-item.unread { border-left: 3px solid var(--accent); }
        .notification-header { display: flex; justify-content: space-between; align-items: flex-start; }
        .notification-title { font-size: 14px; font-weight: 500; margin-bottom: 4px; flex: 1; }
        .notification-delete {
            background: none; border: none; color: var(--text2); font-size: 18px;
            padding: 0 4px; cursor: pointer; line-height: 1;
        }
        .notification-delete:hover { color: var(--danger); }
        .notification-content { font-size: 12px; color: var(--text2); white-space: pre-wrap; }
        .notification-time { font-size: 10px; color: var(--text2); margin-top: 4px; }

        .modal {
            display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
            justify-content: center; align-items: flex-end; z-index: 200;
            padding-bottom: calc(50px + var(--safe-bottom)); /* 为底部导航栏留出空间 */
        }
        .modal.show { display: flex; }
        .modal-box {
            background: var(--card); width: 100%; padding: 20px;
            padding-bottom: calc(20px + var(--safe-bottom)); border-radius: 16px 16px 0 0;
            max-height: 80vh; /* 限制最大高度，避免超出屏幕 */
            overflow-y: auto; /* 支持滚动 */
        }
        .modal-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; }
        .modal input, .modal textarea {
            width: 100%; padding: 12px 14px; background: var(--bg); border: 1px solid var(--border);
            border-radius: 10px; color: var(--text); font-size: 15px; margin-bottom: 10px;
        }
        .modal-btns { display: flex; gap: 10px; }
        .modal-btns button {
            flex: 1; padding: 12px; border: none; border-radius: 10px;
            font-size: 15px; font-weight: 500; cursor: pointer;
        }
        .btn-primary { background: var(--accent); color: #fff; }
        .btn-secondary { background: var(--border); color: var(--text); }

        .mode-select { display: flex; gap: 8px; margin-bottom: 12px; }
        .mode-btn {
            flex: 1; padding: 12px 8px; border: 2px solid var(--border); border-radius: 10px;
            background: transparent; color: var(--text); font-size: 13px; cursor: pointer;
            display: flex; flex-direction: column; align-items: center; gap: 4px;
        }
        .mode-btn.active { border-color: var(--accent); background: rgba(0,102,255,0.1); }
        .mode-btn .icon { font-size: 20px; }

        /* Butler Agent 样式 */
        .butler-section { margin-bottom: 12px; }
        .butler-toggle { display: flex; align-items: center; gap: 10px; flex-wrap: wrap; }
        .butler-label { font-size: 14px; font-weight: 500; }
        .butler-hint { font-size: 11px; color: var(--text2); width: 100%; margin-left: 50px; margin-top: 2px; }
        .butler-goal-section { margin-top: 10px; }
        .butler-goal-section textarea { height: 60px; font-size: 13px; }
        /* 开关样式 */
        .switch { position: relative; width: 40px; height: 22px; }
        .switch input { opacity: 0; width: 0; height: 0; }
        .slider {
            position: absolute; cursor: pointer; top: 0; left: 0; right: 0; bottom: 0;
            background-color: var(--border); border-radius: 22px; transition: .3s;
        }
        .slider:before {
            position: absolute; content: ""; height: 16px; width: 16px; left: 3px; bottom: 3px;
            background-color: white; border-radius: 50%; transition: .3s;
        }
        input:checked + .slider { background-color: var(--accent); }
        input:checked + .slider:before { transform: translateX(18px); }
        /* Butler 状态指示器 */
        .butler-status {
            display: flex; align-items: center; gap: 6px; padding: 6px 10px;
            background: var(--card); border-radius: 6px; font-size: 11px; margin-left: 8px;
        }
        .butler-status.active { background: rgba(0,102,255,0.2); color: var(--accent); }
        .butler-status .dot { width: 6px; height: 6px; border-radius: 50%; background: var(--text2); }
        .butler-status.active .dot { background: var(--accent); animation: pulse 1.5s infinite; }
        @keyframes pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }

        .empty-state {
            flex: 1; display: flex; flex-direction: column; align-items: center; justify-content: center;
            color: var(--text2); padding: 32px;
        }
        .empty-state .icon { font-size: 40px; margin-bottom: 12px; opacity: 0.5; }
        .empty-state h2 { font-size: 16px; color: var(--text); margin-bottom: 6px; }

        .agent-action-bar {
            padding: 10px 12px; background: var(--warning); color: #000;
            display: flex; align-items: center; gap: 8px;
        }
        .agent-action-bar .message { flex: 1; font-size: 13px; }
        .agent-action-bar button {
            padding: 8px 16px; border: none; border-radius: 6px;
            font-size: 12px; cursor: pointer;
        }
        .agent-action-bar .btn-confirm { background: var(--success); color: #fff; }
        .agent-action-bar .btn-reject { background: var(--danger); color: #fff; }
    </style>
</head>
<body>
    <div class="page active" id="projectsPage">
        <div class="header">
            <div>
                <h1>项目管理</h1>
                <div class="header-sub" id="projectsStatus">加载中...</div>
            </div>
            <div class="header-right">
                <span class="logout-btn" onclick="logout()">退出</span>
            </div>
        </div>
        <div class="swipe-hint">← 左滑管理 | 点击进入终端 →</div>
        <div class="project-list" id="projectList"></div>
        <div class="add-btn" onclick="showNewModal()">+</div>
    </div>

    <div class="page fullscreen" id="terminalPage">
        <div class="terminal-header">
            <div class="back-btn" onclick="suspendAndBack()">←</div>
            <span class="terminal-title" id="terminalTitle">项目</span>
            <div class="butler-status" id="butlerStatusBar" style="display:none;">
                <span class="dot"></span>
                <span id="butlerStatusText">管家</span>
            </div>
            <span class="terminal-status" id="terminalStatus">未连接</span>
        </div>
        <div id="agentActionBar" class="agent-action-bar" style="display:none;">
            <span class="message" id="agentActionMessage">需要您的确认</span>
            <button class="btn-confirm" onclick="agentAction('confirm')">确认</button>
            <button class="btn-reject" onclick="agentAction('reject')">拒绝</button>
        </div>

        <!-- 视图切换标签 -->
        <div class="view-tabs">
            <div class="view-tab active" onclick="switchView('terminal')">💻 终端</div>
            <div class="view-tab" onclick="switchView('files')">📁 文件</div>
            <div class="view-tab" onclick="switchView('git')">🔀 Git</div>
        </div>

        <!-- 终端视图 -->
        <div class="view-content active" id="terminalView">
            <div class="terminal-toolbar">
                <button class="toolbar-btn primary" onclick="terminalAction('continue')"><span class="icon">↻</span>继续(-c)</button>
                <button class="toolbar-btn success" onclick="terminalAction('new')"><span class="icon">+</span>新对话</button>
                <button class="toolbar-btn" onclick="terminalAction('restart')"><span class="icon">⟳</span>重启</button>
                <button class="toolbar-btn warning" onclick="terminalAction('stop')"><span class="icon">■</span>停止</button>
                <button class="toolbar-btn" onclick="showButlerMenu()"><span class="icon">🤖</span>管家</button>
                <button class="toolbar-btn" onclick="showTermSettings()"><span class="icon">⚙</span>设置</button>
            </div>
            <div id="terminal"></div>
            <!-- 移动端输入栏 - 解决第三方输入法兼容问题 -->
            <div class="mobile-input-bar">
                <div class="mobile-input-row">
                    <input type="text" id="mobileInput" placeholder="在此输入..." autocomplete="off" autocorrect="off" autocapitalize="off" spellcheck="false">
                    <button class="mobile-send-btn" onclick="sendMobileInput()">发送</button>
                    <button class="mobile-send-btn" onclick="sendMobileEnter()">↵</button>
                </div>
                <div class="mobile-keys-bar">
                    <!-- 修饰键 -->
                    <button class="mobile-key-btn mod" id="modCtrl" onclick="toggleMod('ctrl')">Ctrl</button>
                    <button class="mobile-key-btn mod" id="modShift" onclick="toggleMod('shift')">Shift</button>
                    <button class="mobile-key-btn mod" id="modAlt" onclick="toggleMod('alt')">Alt</button>
                    <!-- 方向键 -->
                    <button class="mobile-key-btn" onclick="sendKey('up')">↑</button>
                    <button class="mobile-key-btn" onclick="sendKey('down')">↓</button>
                    <button class="mobile-key-btn" onclick="sendKey('left')">←</button>
                    <button class="mobile-key-btn" onclick="sendKey('right')">→</button>
                    <!-- 常用键 -->
                    <button class="mobile-key-btn" onclick="sendKey('tab')">Tab</button>
                    <button class="mobile-key-btn" onclick="sendKey('esc')">Esc</button>
                    <button class="mobile-key-btn" onclick="sendKey('backspace')">⌫</button>
                    <!-- 常用组合 -->
                    <button class="mobile-key-btn" onclick="sendCombo('c')">^C</button>
                    <button class="mobile-key-btn" onclick="sendCombo('d')">^D</button>
                    <button class="mobile-key-btn" onclick="sendCombo('z')">^Z</button>
                    <button class="mobile-key-btn" onclick="sendCombo('l')">^L</button>
                    <!-- 粘贴 -->
                    <button class="mobile-key-btn" onclick="mobilePasteText()">📋</button>
                    <button class="mobile-key-btn" onclick="mobilePasteImage()">🖼️</button>
                </div>
            </div>
        </div>

        <!-- Butler 菜单弹窗 -->
        <div class="modal" id="butlerMenuModal">
            <div class="modal-box" style="padding: 16px;">
                <div class="modal-title" style="margin-bottom: 12px;">🤖 智能管家</div>
                <div id="butlerMenuContent">
                    <div class="butler-info" id="butlerInfo"></div>
                    <div class="butler-menu-btns">
                        <button class="btn-primary" id="butlerEnableBtn" onclick="enableButler()" style="width:100%;margin-bottom:8px;">启用管家</button>
                        <button class="btn-secondary" id="butlerDisableBtn" onclick="disableButler()" style="width:100%;margin-bottom:8px;display:none;">禁用管家</button>
                        <button class="btn-secondary" id="butlerResetBtn" onclick="resetButlerCount()" style="width:100%;margin-bottom:8px;display:none;">重置推进次数</button>
                    </div>
                </div>
                <button class="btn-secondary" onclick="closeButlerMenu()" style="width:100%;margin-top:8px;">关闭</button>
            </div>
        </div>

        <!-- 终端设置弹窗 -->
        <div class="modal" id="termSettingsModal">
            <div class="modal-box" style="padding: 16px; max-width: 360px;">
                <div class="modal-title" style="margin-bottom: 16px;">⚙️ 终端设置</div>
                <div class="term-setting-group">
                    <label style="font-size:13px;color:var(--text2);margin-bottom:6px;display:block;">字体大小</label>
                    <div style="display:flex;align-items:center;gap:10px;">
                        <input type="range" id="termFontSize" min="10" max="24" value="13" style="flex:1;">
                        <span id="termFontSizeVal" style="min-width:32px;text-align:center;">13</span>
                    </div>
                </div>
                <div class="term-setting-group" style="margin-top:12px;">
                    <label style="font-size:13px;color:var(--text2);margin-bottom:6px;display:block;">主题配色</label>
                    <div style="display:grid;grid-template-columns:repeat(3,1fr);gap:8px;">
                        <button class="theme-btn active" data-theme="dark" onclick="setTermTheme('dark')">
                            <span style="background:#0d0d0d;color:#fff;">深色</span>
                        </button>
                        <button class="theme-btn" data-theme="light" onclick="setTermTheme('light')">
                            <span style="background:#fff;color:#000;">浅色</span>
                        </button>
                        <button class="theme-btn" data-theme="monokai" onclick="setTermTheme('monokai')">
                            <span style="background:#272822;color:#f8f8f2;">Monokai</span>
                        </button>
                        <button class="theme-btn" data-theme="dracula" onclick="setTermTheme('dracula')">
                            <span style="background:#282a36;color:#f8f8f2;">Dracula</span>
                        </button>
                        <button class="theme-btn" data-theme="solarized" onclick="setTermTheme('solarized')">
                            <span style="background:#002b36;color:#839496;">Solarized</span>
                        </button>
                        <button class="theme-btn" data-theme="nord" onclick="setTermTheme('nord')">
                            <span style="background:#2e3440;color:#d8dee9;">Nord</span>
                        </button>
                    </div>
                </div>
                <div class="term-setting-group" style="margin-top:12px;">
                    <label style="font-size:13px;color:var(--text2);margin-bottom:6px;display:block;">光标样式</label>
                    <div style="display:flex;gap:8px;">
                        <button class="cursor-btn active" data-cursor="block" onclick="setTermCursor('block')">▮ 块状</button>
                        <button class="cursor-btn" data-cursor="underline" onclick="setTermCursor('underline')">▁ 下划线</button>
                        <button class="cursor-btn" data-cursor="bar" onclick="setTermCursor('bar')">▏ 竖线</button>
                    </div>
                </div>
                <div style="display:flex;gap:8px;margin-top:16px;padding-bottom:8px;">
                    <button class="btn-primary" onclick="saveTermSettings()" style="flex:1;">保存</button>
                    <button class="btn-secondary" onclick="closeTermSettings()" style="flex:1;">关闭</button>
                </div>
            </div>
        </div>

        <!-- 文件管理视图 -->
        <div class="view-content" id="filesView">
            <div class="file-toolbar">
                <div class="file-path" id="currentPath">/</div>
                <button class="toolbar-btn" onclick="fileAction('refresh')"><span class="icon">🔄</span></button>
                <button class="toolbar-btn" onclick="fileAction('upload')"><span class="icon">⬆️</span>上传</button>
                <button class="toolbar-btn" onclick="fileAction('newFolder')"><span class="icon">📁</span>新建</button>
                <button class="toolbar-btn" onclick="fileAction('download')"><span class="icon">⬇️</span>下载</button>
            </div>
            <div class="upload-area" id="uploadArea" ondrop="handleDrop(event)" ondragover="handleDragOver(event)" ondragleave="handleDragLeave(event)">
                📤 拖拽文件到此处上传，或点击上传按钮
                <input type="file" id="fileInput" multiple style="display:none" onchange="handleFileSelect(event)">
            </div>
            <div class="file-list" id="fileList"></div>
        </div>

        <!-- Git 管理视图 -->
        <div class="view-content" id="gitView">
            <div class="git-panel" id="gitPanel">
                <div class="git-section">
                    <div class="git-section-title">📊 仓库状态</div>
                    <div id="gitStatusInfo">加载中...</div>
                </div>
                <div class="git-section">
                    <div class="git-section-title">📝 变更文件</div>
                    <div id="gitChanges">无变更</div>
                </div>
                <div class="git-section">
                    <div class="git-section-title">💾 提交</div>
                    <input type="text" class="git-input" id="commitMessage" placeholder="提交信息（可选，自动生成）">
                    <div style="display:flex;gap:8px;">
                        <button class="toolbar-btn primary" style="flex:1" onclick="gitAction('commit')">提交</button>
                        <button class="toolbar-btn success" style="flex:1" onclick="gitAction('push')">推送</button>
                        <button class="toolbar-btn" style="flex:1" onclick="gitAction('pull')">拉取</button>
                    </div>
                </div>
                <div class="git-section">
                    <div class="git-section-title">⚙️ 远程配置</div>
                    <input type="text" class="git-input" id="remoteUrl" placeholder="远程仓库地址 (https://github.com/...)">
                    <div style="display:flex;gap:8px;margin-bottom:8px;">
                        <label style="display:flex;align-items:center;gap:4px;font-size:12px;">
                            <input type="checkbox" id="autoCommit" checked> 自动提交
                        </label>
                        <label style="display:flex;align-items:center;gap:4px;font-size:12px;">
                            <input type="checkbox" id="autoPush"> 自动推送
                        </label>
                    </div>
                    <button class="toolbar-btn" onclick="gitAction('saveConfig')">保存配置</button>
                </div>
                <div class="git-section">
                    <div class="git-section-title">📜 提交历史</div>
                    <div class="git-commits" id="gitHistory">加载中...</div>
                </div>
            </div>
        </div>
    </div>

    <div class="page" id="butlerPage">
        <div class="header">
            <div>
                <h1>小深管家</h1>
                <div class="header-sub">项目助手 · 日程提醒</div>
            </div>
            <div class="header-right">
                <span class="logout-btn" onclick="clearChatHistory()">清空记录</span>
            </div>
        </div>
        <div class="chat-container">
            <div class="chat-messages" id="chatMessages">
                <div class="message assistant">你好！我是小深，你的项目管家。可以帮你查看项目状态、记录计划、提醒待办。</div>
            </div>
            <div class="chat-input-area">
                <textarea class="chat-input" id="chatInput" placeholder="输入消息..." rows="1"></textarea>
                <button class="chat-send" id="chatSendBtn" onclick="sendChat()">↑</button>
            </div>
        </div>
    </div>

    <div class="page" id="notificationsPage">
        <div class="header">
            <div>
                <h1>通知</h1>
                <div class="header-sub" id="notificationsStatus">加载中...</div>
            </div>
            <div class="header-right">
                <span class="logout-btn" onclick="markAllRead()">全部已读</span>
                <span class="logout-btn" style="color: var(--danger);" onclick="deleteAllNotifications()">清空</span>
            </div>
        </div>
        <div class="notification-list" id="notificationList"></div>
    </div>

    <div class="tab-bar">
        <div class="tab-item active" onclick="switchTab('projects')"><span class="icon">📁</span><span>项目</span></div>
        <div class="tab-item" onclick="switchTab('butler')"><span class="icon">🤖</span><span>管家</span></div>
        <div class="tab-item" style="position:relative" onclick="switchTab('notifications')">
            <span class="icon">🔔</span><span>通知</span>
            <span class="badge" id="unreadBadge" style="display:none">0</span>
        </div>
        <div class="tab-item" onclick="switchTab('settings')"><span class="icon">⚙️</span><span>设置</span></div>
    </div>

    <!-- 设置页面 -->
    <div class="page" id="settingsPage">
        <div class="header">
            <div>
                <h1>设置</h1>
                <div class="header-sub">账号与偏好</div>
            </div>
        </div>
        <div style="padding: 12px; overflow-y: auto; flex: 1;">
            <div class="settings-section">
                <div class="settings-title">👤 账号信息</div>
                <div class="settings-item">
                    <span class="settings-label">邮箱</span>
                    <span class="settings-value" id="settingsEmail">-</span>
                </div>
                <div class="settings-item">
                    <span class="settings-label">昵称</span>
                    <input type="text" class="settings-input" id="settingsDisplayName" placeholder="未设置">
                </div>
                <div class="settings-item">
                    <span class="settings-label">授权到期</span>
                    <span class="settings-value" id="settingsExpires">-</span>
                </div>
                <div class="settings-item">
                    <span class="settings-label">注册时间</span>
                    <span class="settings-value" id="settingsCreated">-</span>
                </div>
                <div style="margin-top: 12px;">
                    <button class="settings-btn primary" onclick="saveProfile()">保存昵称</button>
                </div>
            </div>

            <div class="settings-section">
                <div class="settings-title">🔐 修改密码</div>
                <div class="settings-item" style="flex-direction: column; align-items: stretch; gap: 8px;">
                    <input type="password" class="settings-input" id="oldPassword" placeholder="原密码" style="max-width: 100%; text-align: left;">
                    <input type="password" class="settings-input" id="newPassword" placeholder="新密码（至少6位）" style="max-width: 100%; text-align: left;">
                    <input type="password" class="settings-input" id="confirmPassword" placeholder="确认新密码" style="max-width: 100%; text-align: left;">
                </div>
                <div style="margin-top: 12px;">
                    <button class="settings-btn primary" onclick="changePassword()">修改密码</button>
                </div>
            </div>

            <div class="settings-section">
                <div class="settings-title">🚪 退出登录</div>
                <div style="display: flex; gap: 8px;">
                    <button class="settings-btn" onclick="logout()">退出当前设备</button>
                    <button class="settings-btn danger" onclick="logoutAll()">退出所有设备</button>
                </div>
            </div>
        </div>
    </div>

    <div class="modal" id="newModal">
        <div class="modal-box">
            <div class="modal-title">新建项目</div>
            <input type="text" id="newProjectName" placeholder="项目名称" autocomplete="off">
            <textarea id="newProjectNote" placeholder="项目备注（可选）"></textarea>
            <div class="mode-select">
                <div class="mode-btn" onclick="selectMode(this, 'continue')">
                    <span class="icon">↻</span><span>继续对话</span>
                </div>
                <div class="mode-btn active" onclick="selectMode(this, 'new')">
                    <span class="icon">+</span><span>新对话</span>
                </div>
            </div>
            <!-- Butler Agent 设置 -->
            <div class="butler-section">
                <div class="butler-toggle">
                    <label class="switch">
                        <input type="checkbox" id="butlerEnabled">
                        <span class="slider"></span>
                    </label>
                    <span class="butler-label">🤖 启用智能管家</span>
                    <span class="butler-hint">自动推进任务，关键节点通知你</span>
                </div>
                <div class="butler-goal-section" id="butlerGoalSection" style="display: none;">
                    <textarea id="butlerGoal" placeholder="输入最终目标，例如：实现用户登录注册功能，包含邮箱验证"></textarea>
                </div>
            </div>
            <div class="modal-btns">
                <button class="btn-secondary" onclick="closeNewModal()">取消</button>
                <button class="btn-primary" onclick="createProject()">创建并启动</button>
            </div>
        </div>
    </div>

    <!-- 项目设置弹窗 -->
    <div class="modal" id="projectSettingsModal">
        <div class="modal-box">
            <div class="modal-title">项目设置</div>
            <input type="text" id="editProjectName" placeholder="项目显示名称" autocomplete="off">
            <textarea id="editProjectNote" placeholder="项目备注"></textarea>
            <div class="modal-btns">
                <button class="btn-secondary" onclick="closeProjectSettings()">取消</button>
                <button class="btn-primary" onclick="saveProjectSettings()">保存</button>
            </div>
            <div style="margin-top: 16px; padding-top: 16px; border-top: 1px solid var(--border);">
                <button class="settings-btn danger" style="width: 100%;" onclick="confirmDeleteProject()">删除此项目</button>
            </div>
        </div>
    </div>

    <!-- 文件编辑器弹窗 -->
    <div class="modal" id="editorModal" style="padding: 0;">
        <div class="modal-box" style="width: 100%; height: 100%; max-width: none; border-radius: 0; display: flex; flex-direction: column;">
            <div class="editor-header">
                <div class="back-btn" onclick="closeEditor()">←</div>
                <span class="editor-filename" id="editorFilename">file.txt</span>
                <button class="toolbar-btn primary" onclick="saveFile()">💾 保存</button>
            </div>
            <textarea class="editor-textarea" id="editorContent"></textarea>
            <div class="editor-status">
                <span id="editorLineInfo">行: 1, 列: 1</span>
                <span id="editorFileSize">0 字节</span>
            </div>
        </div>
    </div>

    <!-- 代理状态详情弹窗 -->
    <div class="modal" id="agentDetailModal">
        <div class="modal-box" style="max-height: 80vh; overflow-y: auto;">
            <div class="modal-title">📊 代理状态详情</div>
            <div id="agentDetailContent">
                <div class="settings-section">
                    <div class="settings-title">当前状态</div>
                    <div id="agentCurrentStatus">加载中...</div>
                </div>
                <div class="settings-section">
                    <div class="settings-title">操作历史</div>
                    <div class="agent-timeline" id="agentTimeline">加载中...</div>
                </div>
            </div>
            <div class="modal-btns">
                <button class="btn-secondary" onclick="closeAgentDetail()">关闭</button>
            </div>
        </div>
    </div>

    <!-- 搜索弹窗 -->
    <div class="modal" id="searchModal" style="padding: 0;">
        <div class="modal-box" style="width: 100%; height: 100%; max-width: none; border-radius: 0; display: flex; flex-direction: column;">
            <div class="search-bar">
                <div class="back-btn" onclick="closeSearch()">←</div>
                <input type="text" class="search-input" id="searchInput" placeholder="搜索项目或文件..." oninput="debounceSearch()">
            </div>
            <div class="search-results" id="searchResults">
                <div class="empty-state"><span class="icon">🔍</span><p>输入关键词搜索</p></div>
            </div>
        </div>
    </div>

    <!-- 计划列表弹窗 -->
    <div class="modal" id="plansModal">
        <div class="modal-box" style="max-height: 80vh; overflow-y: auto;">
            <div class="modal-title">📋 我的计划</div>
            <div style="margin-bottom: 12px; display: flex; gap: 8px;">
                <input type="text" id="newPlanInput" placeholder="添加新计划..." style="flex: 1; padding: 10px; background: var(--bg); border: 1px solid var(--border); border-radius: 6px; color: var(--text);">
                <button class="toolbar-btn primary" onclick="addPlan()">添加</button>
            </div>
            <div id="plansList"></div>
            <div class="modal-btns">
                <button class="btn-secondary" onclick="closePlans()">关闭</button>
            </div>
        </div>
    </div>

    <script src="/static/xterm.min.js"></script>
    <script src="/static/xterm-addon-fit.min.js"></script>
    <script>
        let term = null, fitAddon = null, ws = null, currentProject = null, currentProjectId = null, currentMode = 'new';
        let selectedMode = 'new';  // 新项目默认不带 -c
        let touchStartX = 0, touchCurrentX = 0, currentSwipeCard = null;
        let swipeDistance = 0, touchHandled = false;
        let termDataHandler = null;

        function getToken() {
            return localStorage.getItem('access_token');
        }

        async function apiRequest(method, path, body = null) {
            const token = getToken();
            const headers = { 'Content-Type': 'application/json' };
            if (token) headers['Authorization'] = 'Bearer ' + token;

            const options = { method, headers };
            if (body) options.body = JSON.stringify(body);

            const res = await fetch(path, options);

            if (res.status === 401) {
                // Token 过期，尝试刷新
                const refreshed = await refreshToken();
                if (refreshed) {
                    headers['Authorization'] = 'Bearer ' + getToken();
                    return fetch(path, { ...options, headers });
                } else {
                    logout();
                    throw new Error('认证失败');
                }
            }
            return res;
        }

        async function refreshToken() {
            const refreshToken = localStorage.getItem('refresh_token');
            if (!refreshToken) return false;

            try {
                const res = await fetch('/api/auth/refresh', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ refresh_token: refreshToken })
                });

                if (res.ok) {
                    const data = await res.json();
                    localStorage.setItem('access_token', data.access_token);
                    return true;
                }
            } catch (e) {}
            return false;
        }

        function logout() {
            localStorage.removeItem('access_token');
            localStorage.removeItem('refresh_token');
            window.location.href = '/';
        }

        function switchTab(tab) {
            document.querySelectorAll('.tab-item').forEach((el, i) => {
                el.classList.toggle('active',
                    (i === 0 && tab === 'projects') ||
                    (i === 1 && tab === 'butler') ||
                    (i === 2 && tab === 'notifications') ||
                    (i === 3 && tab === 'settings')
                );
            });
            document.getElementById('projectsPage').classList.toggle('active', tab === 'projects');
            document.getElementById('butlerPage').classList.toggle('active', tab === 'butler');
            document.getElementById('notificationsPage').classList.toggle('active', tab === 'notifications');
            document.getElementById('settingsPage').classList.toggle('active', tab === 'settings');
            document.getElementById('terminalPage').classList.remove('active');
            if (tab === 'projects') loadProjects();
            if (tab === 'butler') loadChatHistory();
            if (tab === 'notifications') loadNotifications();
            if (tab === 'settings') loadSettings();
        }

        async function loadProjects() {
            try {
                // 同步 Core 实际状态，确保显示准确
                const res = await apiRequest('GET', '/api/projects?sync_core=true');
                const projects = await res.json();
                const list = document.getElementById('projectList');
                const status = document.getElementById('projectsStatus');

                const running = projects.filter(p => p.is_running).length;
                const needsAction = projects.filter(p => p.needs_human).length;
                status.textContent = projects.length + ' 个项目，' + running + ' 个运行中' + (needsAction > 0 ? '，' + needsAction + ' 个待处理' : '');

                if (projects.length === 0) {
                    list.innerHTML = '<div class="empty-state"><span class="icon">📁</span><h2>暂无项目</h2><p>点击右下角 + 创建</p></div>';
                    return;
                }

                list.innerHTML = projects.map(function(p) {
                    const cardClass = p.is_running ? (p.needs_human ? 'needs-action' : 'running') : '';
                    const statusClass = p.is_running ? (p.needs_human ? 'needs-action' : 'running') : 'idle';
                    const statusText = p.is_running ? (p.needs_human ? '待处理' : '运行中') : '空闲';
                    const icon = p.is_running ? (p.needs_human ? '⚠️' : '⚡') : '📁';

                    return '<div class="project-wrapper" data-id="' + p.id + '">' +
                        '<div class="project-actions">' +
                            '<button class="btn-restart" onclick="event.stopPropagation();restartProject(' + p.id + ')"><span class="icon">⟳</span>重启</button>' +
                            '<button class="btn-stop" onclick="event.stopPropagation();stopProject(' + p.id + ')"><span class="icon">■</span>停止</button>' +
                            '<button class="btn-delete" onclick="event.stopPropagation();deleteProject(' + p.id + ')"><span class="icon">✕</span>删除</button>' +
                        '</div>' +
                        '<div class="project-card ' + cardClass + '"' +
                             ' ontouchstart="onTouchStart(event)"' +
                             ' ontouchmove="onTouchMove(event)"' +
                             ' ontouchend="onTouchEnd(event, ' + p.id + ')"' +
                             ' onclick="onCardClick(' + p.id + ')">' +
                            '<div class="project-icon">' + icon + '</div>' +
                            '<div class="project-info">' +
                                '<div class="project-name">' + (p.display_name || p.name) + '</div>' +
                                '<div class="project-meta">' +
                                    '<span class="project-status status-' + statusClass + '">' + statusText + '</span>' +
                                    '<span class="project-phase">' + p.current_phase + '</span>' +
                                '</div>' +
                            '</div>' +
                            '<span class="project-arrow">›</span>' +
                        '</div>' +
                    '</div>';
                }).join('');
            } catch (e) { console.error(e); }
        }

        async function loadNotifications() {
            try {
                const res = await apiRequest('GET', '/api/notifications');
                const notifications = await res.json();
                const list = document.getElementById('notificationList');
                const status = document.getElementById('notificationsStatus');

                const unread = notifications.filter(n => !n.is_read).length;
                status.textContent = notifications.length + ' 条通知，' + unread + ' 条未读';

                updateUnreadBadge(unread);

                if (notifications.length === 0) {
                    list.innerHTML = '<div class="empty-state"><span class="icon">🔔</span><h2>暂无通知</h2></div>';
                    return;
                }

                list.innerHTML = notifications.map(n => {
                    const time = new Date(n.created_at).toLocaleString('zh-CN');
                    return '<div class="notification-item ' + (n.is_read ? '' : 'unread') + '" onclick="markRead(' + n.id + ')">' +
                        '<div class="notification-header">' +
                            '<div class="notification-title">' + n.title + '</div>' +
                            '<button class="notification-delete" onclick="deleteNotification(' + n.id + ', event)">×</button>' +
                        '</div>' +
                        '<div class="notification-content">' + n.content + '</div>' +
                        '<div class="notification-time">' + time + '</div>' +
                    '</div>';
                }).join('');
            } catch (e) { console.error(e); }
        }

        function updateUnreadBadge(count) {
            const badge = document.getElementById('unreadBadge');
            if (count > 0) {
                badge.textContent = count > 99 ? '99+' : count;
                badge.style.display = 'block';
            } else {
                badge.style.display = 'none';
            }
        }

        async function markRead(id) {
            await apiRequest('PATCH', '/api/notifications/' + id + '/read');
            loadNotifications();
        }

        async function markAllRead() {
            await apiRequest('POST', '/api/notifications/read-all');
            loadNotifications();
        }

        async function deleteNotification(id, e) {
            e.stopPropagation();
            await apiRequest('DELETE', '/api/notifications/' + id);
            loadNotifications();
        }

        async function deleteAllNotifications() {
            if (!confirm('确定要清空所有通知吗？')) return;
            await apiRequest('DELETE', '/api/notifications');
            loadNotifications();
        }

        async function loadUnreadCount() {
            try {
                const res = await apiRequest('GET', '/api/notifications/unread-count');
                const data = await res.json();
                updateUnreadBadge(data.count);
            } catch (e) {}
        }

        function onTouchStart(e) {
            touchStartX = e.touches[0].clientX;
            touchCurrentX = touchStartX;
            swipeDistance = 0;
            touchHandled = false;
            currentSwipeCard = e.currentTarget;
            currentSwipeCard.style.transition = 'none';
        }

        function onTouchMove(e) {
            if (!currentSwipeCard) return;
            touchCurrentX = e.touches[0].clientX;
            swipeDistance = touchCurrentX - touchStartX;
            let transform = Math.max(-180, Math.min(0, swipeDistance));
            currentSwipeCard.style.transform = 'translateX(' + transform + 'px)';
        }

        async function onTouchEnd(e, projectId) {
            if (!currentSwipeCard) return;
            touchHandled = true;
            currentSwipeCard.style.transition = 'transform 0.2s ease-out';

            if (Math.abs(swipeDistance) < 15) {
                currentSwipeCard.style.transform = 'translateX(0)';
                currentSwipeCard = null;
                await openProject(projectId);
            } else if (swipeDistance < -60) {
                currentSwipeCard.style.transform = 'translateX(-180px)';
                currentSwipeCard = null;
            } else {
                currentSwipeCard.style.transform = 'translateX(0)';
                currentSwipeCard = null;
            }
            swipeDistance = 0;
        }

        async function onCardClick(projectId) {
            if (touchHandled) { touchHandled = false; return; }
            await openProject(projectId);
        }

        async function restartProject(id) {
            try {
                await apiRequest('POST', '/api/projects/' + id + '/restart');
                loadProjects();
            } catch (error) {
                console.error('重启项目失败:', error);
                alert('重启失败: ' + (error.message || '未知错误'));
            }
        }

        async function stopProject(id) {
            await apiRequest('POST', '/api/projects/' + id + '/stop');
            loadProjects();
        }

        async function deleteProject(id) {
            if (!confirm('确定删除此项目？')) return;
            await apiRequest('DELETE', '/api/projects/' + id);
            loadProjects();
        }

        async function openProject(projectId, overrideMode) {
            currentProjectId = projectId;

            // 获取项目详情
            const res = await apiRequest('GET', '/api/projects/' + projectId);
            const project = await res.json();
            currentProject = project.name;
            // 优先使用传入的 mode（新建项目时），否则用数据库值，最后默认 new
            currentMode = overrideMode || project.mode || 'new';

            document.getElementById('projectsPage').classList.remove('active');
            document.getElementById('terminalPage').classList.add('active');
            document.getElementById('terminalTitle').textContent = project.display_name || project.name;

            const statusEl = document.getElementById('terminalStatus');
            statusEl.textContent = '连接中...';
            statusEl.className = 'terminal-status';

            // 检查是否需要人工介入
            await checkAgentAction(projectId);

            // 加载 Butler 状态
            await loadButlerStatus(projectId);

            // 重新初始化终端 - 修复返回后再进入无法显示的问题
            const termContainer = document.getElementById('terminal');
            if (term) {
                term.dispose();
                term = null;
                fitAddon = null;
            }
            termContainer.innerHTML = '';

            term = new Terminal({
                cursorBlink: true, fontSize: 13, fontFamily: 'Menlo, Monaco, monospace',
                theme: { background: '#0d0d0d', foreground: '#fff', cursor: '#fff' },
                scrollback: 5000,
                allowProposedApi: true
            });
            fitAddon = new FitAddon.FitAddon();
            term.loadAddon(fitAddon);
            term.open(termContainer);

            // 检测是否移动端
            var isMobile = /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent);

            // 自定义粘贴处理函数 - 文本
            function handlePasteText(text) {
                if (text && ws && ws.readyState === WebSocket.OPEN) {
                    // 分块发送长文本，避免 WebSocket 缓冲区溢出
                    var chunkSize = 4096;
                    for (var i = 0; i < text.length; i += chunkSize) {
                        var chunk = text.slice(i, i + chunkSize);
                        ws.send(chunk);
                    }
                    return true;
                }
                return false;
            }

            // 自定义粘贴处理函数 - 图片
            function handlePasteImage(blob) {
                if (!blob || !ws || ws.readyState !== WebSocket.OPEN) return false;
                var reader = new FileReader();
                reader.onload = function(e) {
                    var base64 = e.target.result;
                    // 发送 OSC 52 格式（终端图片粘贴标准）
                    var data = base64.split(',')[1];
                    ws.send('\x1b]52;c;' + data + '\x07');
                };
                reader.readAsDataURL(blob);
                return true;
            }

            // PC 端粘贴处理 - 同时支持文本和图片
            if (!isMobile) {
                // 使用 xterm 的 customKeyEventHandler 拦截 Ctrl+V
                term.attachCustomKeyEventHandler(function(e) {
                    // 拦截 Ctrl+V / Cmd+V 粘贴
                    if ((e.ctrlKey || e.metaKey) && e.key === 'v' && e.type === 'keydown') {
                        // 尝试读取剪贴板（优先图片，其次文本）
                        navigator.clipboard.read().then(function(items) {
                            var hasImage = false;
                            for (var item of items) {
                                for (var type of item.types) {
                                    if (type.startsWith('image/')) {
                                        hasImage = true;
                                        item.getType(type).then(function(blob) {
                                            handlePasteImage(blob);
                                        });
                                        break;
                                    }
                                }
                                if (hasImage) break;
                            }
                            // 没有图片则读取文本
                            if (!hasImage) {
                                navigator.clipboard.readText().then(function(text) {
                                    handlePasteText(text);
                                });
                            }
                        }).catch(function(err) {
                            // 降级：只读取文本
                            navigator.clipboard.readText().then(function(text) {
                                handlePasteText(text);
                            }).catch(function(e) {
                                console.log('Clipboard read failed:', e);
                            });
                        });
                        return false;  // 阻止 xterm 默认处理
                    }
                    return true;
                });

                // 同时在容器上处理右键粘贴事件
                termContainer.addEventListener('paste', function(e) {
                    e.preventDefault();
                    e.stopPropagation();
                    var clipboardData = e.clipboardData || window.clipboardData;
                    if (!clipboardData) return;

                    // 检查是否有图片
                    var items = clipboardData.items;
                    if (items) {
                        for (var i = 0; i < items.length; i++) {
                            if (items[i].type.indexOf('image') !== -1) {
                                var blob = items[i].getAsFile();
                                handlePasteImage(blob);
                                return;
                            }
                        }
                    }

                    // 没有图片则粘贴文本
                    var text = clipboardData.getData('text/plain') || clipboardData.getData('text');
                    handlePasteText(text);
                }, true);
            }

            setTimeout(function() {
                fitAddon.fit();
                // 确保终端获得焦点
                term.focus();
            }, 100);

            // 查询 Core 实际状态（同步数据库状态）
            let coreRunning = false;
            try {
                const coreRes = await apiRequest('GET', '/api/projects/' + projectId + '/core-status');
                const coreStatus = await coreRes.json();
                coreRunning = coreStatus.core_running;
                console.log('[Project] Core 状态:', coreStatus);
            } catch (e) {
                console.log('[Project] 查询 Core 状态失败，使用数据库状态');
                coreRunning = project.is_running;
            }

            // 启动项目（如果 Core 中没有运行）
            if (!coreRunning) {
                statusEl.textContent = '启动中...';
                await apiRequest('POST', '/api/projects/' + projectId + '/start?mode=' + currentMode);
                await new Promise(function(r) { setTimeout(r, 500); });
                connectWS(project.name, currentMode);
            } else {
                // Core 进程已存在，直接重连（不传 mode，避免重新创建进程）
                console.log('[Project] Core 进程已存在，重连中...');
                statusEl.textContent = '重连中...';
                connectWS(project.name, null);  // 不传 mode
            }

            // 应用终端设置
            applyTermSettings();

            // 加载文件列表（首次进入）
            currentFilePath = '';
            loadFiles('');
        }

        async function checkAgentAction(projectId) {
            try {
                const res = await apiRequest('GET', '/api/projects/' + projectId + '/agent');
                const agent = await res.json();

                const actionBar = document.getElementById('agentActionBar');
                if (agent.needs_human) {
                    document.getElementById('agentActionMessage').textContent = agent.human_action_message || '需要您的确认';
                    actionBar.style.display = 'flex';
                } else {
                    actionBar.style.display = 'none';
                }
            } catch (e) {
                document.getElementById('agentActionBar').style.display = 'none';
            }
        }

        async function agentAction(type) {
            if (!currentProjectId) return;
            await apiRequest('POST', '/api/projects/' + currentProjectId + '/agent/action', {
                action_type: type
            });
            document.getElementById('agentActionBar').style.display = 'none';
        }

        // ============== Butler Agent 功能 ==============
        let butlerState = { enabled: false, goal: '', completion: 0 };

        async function loadButlerStatus(projectId) {
            const statusBar = document.getElementById('butlerStatusBar');
            const statusText = document.getElementById('butlerStatusText');

            try {
                const res = await apiRequest('GET', '/api/projects/' + projectId + '/butler/status');
                const data = await res.json();
                butlerState = data;

                if (data.enabled) {
                    statusBar.style.display = 'flex';
                    statusBar.classList.add('active');
                    const rate = data.last_evaluation ? data.last_evaluation.completion_rate : 0;
                    statusText.textContent = '管家 ' + rate + '%';
                } else {
                    statusBar.style.display = 'none';
                    statusBar.classList.remove('active');
                }
            } catch (e) {
                statusBar.style.display = 'none';
            }
        }

        async function enableButler() {
            if (!currentProjectId) return;
            closeButlerMenu();
            const goal = prompt('请输入项目目标：');
            if (!goal) return;

            try {
                await apiRequest('POST', '/api/projects/' + currentProjectId + '/butler/init', {
                    goal: goal,
                    sub_goals: []
                });
                alert('智能管家已启用');
                await loadButlerStatus(currentProjectId);
            } catch (e) {
                alert('启用失败');
            }
        }

        async function disableButler() {
            if (!currentProjectId) return;
            if (!confirm('确定禁用智能管家？')) return;

            try {
                await apiRequest('POST', '/api/projects/' + currentProjectId + '/butler/disable');
                alert('智能管家已禁用');
                await loadButlerStatus(currentProjectId);
                closeButlerMenu();
            } catch (e) {
                alert('禁用失败');
            }
        }

        async function resetButlerCount() {
            if (!currentProjectId) return;
            try {
                await apiRequest('POST', '/api/projects/' + currentProjectId + '/butler/reset');
                alert('已重置自动推进次数');
                closeButlerMenu();
            } catch (e) {
                alert('重置失败');
            }
        }

        function showButlerMenu() {
            // 更新菜单内容
            const info = document.getElementById('butlerInfo');
            const enableBtn = document.getElementById('butlerEnableBtn');
            const disableBtn = document.getElementById('butlerDisableBtn');
            const resetBtn = document.getElementById('butlerResetBtn');

            if (butlerState.enabled) {
                enableBtn.style.display = 'none';
                disableBtn.style.display = 'block';
                resetBtn.style.display = 'block';

                const rate = butlerState.last_evaluation ? butlerState.last_evaluation.completion_rate : 0;
                info.innerHTML = '<div style="background:var(--card2);padding:12px;border-radius:8px;margin-bottom:12px;">' +
                    '<div style="font-size:12px;color:var(--text2);margin-bottom:4px;">目标</div>' +
                    '<div style="font-size:14px;margin-bottom:8px;">' + escapeHtml(butlerState.goal || '未设置') + '</div>' +
                    '<div style="font-size:12px;color:var(--text2);margin-bottom:4px;">进度</div>' +
                    '<div style="display:flex;align-items:center;gap:8px;">' +
                        '<div style="flex:1;height:6px;background:var(--border);border-radius:3px;overflow:hidden;">' +
                            '<div style="width:' + rate + '%;height:100%;background:var(--accent);"></div>' +
                        '</div>' +
                        '<span style="font-size:12px;">' + rate + '%</span>' +
                    '</div>' +
                    '<div style="font-size:11px;color:var(--text2);margin-top:8px;">自动推进: ' + butlerState.auto_continue_count + '/' + butlerState.max_auto_continue + ' 次</div>' +
                '</div>';
            } else {
                enableBtn.style.display = 'block';
                disableBtn.style.display = 'none';
                resetBtn.style.display = 'none';
                info.innerHTML = '<div style="text-align:center;padding:20px;color:var(--text2);">' +
                    '<div style="font-size:32px;margin-bottom:8px;">🤖</div>' +
                    '<div>智能管家未启用</div>' +
                    '<div style="font-size:12px;margin-top:4px;">启用后自动推进任务，关键节点通知您</div>' +
                '</div>';
            }

            document.getElementById('butlerMenuModal').classList.add('show');
        }

        function closeButlerMenu() {
            document.getElementById('butlerMenuModal').classList.remove('show');
        }

        // ============== 终端设置 ==============
        var termThemes = {
            dark: { background: '#0d0d0d', foreground: '#ffffff', cursor: '#ffffff', cursorAccent: '#000000' },
            light: { background: '#ffffff', foreground: '#000000', cursor: '#000000', cursorAccent: '#ffffff' },
            monokai: { background: '#272822', foreground: '#f8f8f2', cursor: '#f8f8f2', cursorAccent: '#272822',
                       green: '#a6e22e', red: '#f92672', blue: '#66d9ef', yellow: '#e6db74', magenta: '#ae81ff', cyan: '#a1efe4' },
            dracula: { background: '#282a36', foreground: '#f8f8f2', cursor: '#f8f8f2', cursorAccent: '#282a36',
                       green: '#50fa7b', red: '#ff5555', blue: '#8be9fd', yellow: '#f1fa8c', magenta: '#ff79c6', cyan: '#8be9fd' },
            solarized: { background: '#002b36', foreground: '#839496', cursor: '#839496', cursorAccent: '#002b36',
                         green: '#859900', red: '#dc322f', blue: '#268bd2', yellow: '#b58900', magenta: '#d33682', cyan: '#2aa198' },
            nord: { background: '#2e3440', foreground: '#d8dee9', cursor: '#d8dee9', cursorAccent: '#2e3440',
                    green: '#a3be8c', red: '#bf616a', blue: '#81a1c1', yellow: '#ebcb8b', magenta: '#b48ead', cyan: '#88c0d0' }
        };

        var termSettings = {
            fontSize: 13,
            theme: 'dark',
            cursorStyle: 'block'
        };

        // 从 localStorage 加载设置
        function loadTermSettings() {
            try {
                var saved = localStorage.getItem('termSettings');
                if (saved) {
                    termSettings = JSON.parse(saved);
                }
            } catch(e) {}
        }

        function showTermSettings() {
            loadTermSettings();
            document.getElementById('termFontSize').value = termSettings.fontSize;
            document.getElementById('termFontSizeVal').textContent = termSettings.fontSize;

            // 更新主题按钮状态
            document.querySelectorAll('.theme-btn').forEach(function(btn) {
                btn.classList.toggle('active', btn.dataset.theme === termSettings.theme);
            });
            // 更新光标按钮状态
            document.querySelectorAll('.cursor-btn').forEach(function(btn) {
                btn.classList.toggle('active', btn.dataset.cursor === termSettings.cursorStyle);
            });

            document.getElementById('termSettingsModal').classList.add('show');
        }

        function closeTermSettings() {
            document.getElementById('termSettingsModal').classList.remove('show');
        }

        // 字体大小滑块实时预览
        document.getElementById('termFontSize').addEventListener('input', function(e) {
            var size = parseInt(e.target.value);
            document.getElementById('termFontSizeVal').textContent = size;
            if (term) {
                term.options.fontSize = size;
                fitAddon.fit();
            }
        });

        function setTermTheme(themeName) {
            termSettings.theme = themeName;
            document.querySelectorAll('.theme-btn').forEach(function(btn) {
                btn.classList.toggle('active', btn.dataset.theme === themeName);
            });
            if (term && termThemes[themeName]) {
                term.options.theme = termThemes[themeName];
            }
        }

        function setTermCursor(style) {
            termSettings.cursorStyle = style;
            document.querySelectorAll('.cursor-btn').forEach(function(btn) {
                btn.classList.toggle('active', btn.dataset.cursor === style);
            });
            if (term) {
                term.options.cursorStyle = style;
            }
        }

        function saveTermSettings() {
            const saveBtn = document.querySelector('#termSettingsModal .btn-primary');
            const originalText = saveBtn.textContent;
            
            try {
                // 显示保存状态
                saveBtn.textContent = '保存中...';
                saveBtn.disabled = true;
                
                // 获取当前设置值
                const fontSize = parseInt(document.getElementById('termFontSize').value);
                const theme = document.querySelector('.theme-btn.active')?.dataset.theme || 'dark';
                const cursorStyle = document.querySelector('.cursor-btn.active')?.dataset.cursor || 'block';
                
                // 验证设置值
                if (fontSize < 10 || fontSize > 24) {
                    alert('字体大小必须在10-24之间');
                    saveBtn.textContent = originalText;
                    saveBtn.disabled = false;
                    return;
                }
                
                // 保存设置
                termSettings.fontSize = fontSize;
                termSettings.theme = theme;
                termSettings.cursorStyle = cursorStyle;
                localStorage.setItem('termSettings', JSON.stringify(termSettings));
                
                // 立即应用设置
                applyTermSettings();
                
                // 恢复按钮状态，关闭弹窗并提示成功
                saveBtn.textContent = originalText;
                saveBtn.disabled = false;
                closeTermSettings();
                alert('设置已保存并应用');
            } catch (error) {
                console.error('保存设置失败:', error);
                saveBtn.textContent = originalText;
                saveBtn.disabled = false;
                alert('保存设置失败: ' + error.message);
            }
        }

        // 应用已保存的设置到终端
        function applyTermSettings() {
            loadTermSettings();
            if (term) {
                term.options.fontSize = termSettings.fontSize;
                if (termThemes[termSettings.theme]) {
                    term.options.theme = termThemes[termSettings.theme];
                }
                term.options.cursorStyle = termSettings.cursorStyle;
                setTimeout(function() { fitAddon.fit(); }, 50);
            }
        }

        async function terminalAction(action) {
            if (!currentProjectId) return;

            switch(action) {
                case 'continue':
                    if (!confirm('将停止当前进程并用 -c 继续上次对话，确定？')) return;
                    try {
                        term.clear();
                        await apiRequest('POST', '/api/projects/' + currentProjectId + '/stop');
                        await new Promise(function(r) { setTimeout(r, 300); });
                        await apiRequest('POST', '/api/projects/' + currentProjectId + '/start?mode=continue');
                        connectWS(currentProject, 'continue');
                    } catch (error) {
                        console.error('继续会话失败:', error);
                        alert('继续会话失败: ' + (error.message || '未知错误'));
                    }
                    break;
                case 'new':
                    if (!confirm('将停止当前进程并开始全新对话，确定？')) return;
                    term.clear();
                    await apiRequest('POST', '/api/projects/' + currentProjectId + '/stop');
                    await new Promise(function(r) { setTimeout(r, 300); });
                    await apiRequest('POST', '/api/projects/' + currentProjectId + '/start?mode=new');
                    connectWS(currentProject, 'new');
                    break;
                case 'restart':
                    if (!confirm('将重启当前进程，确定？')) return;
                    try {
                        term.clear();
                        await apiRequest('POST', '/api/projects/' + currentProjectId + '/restart');
                        await new Promise(function(r) { setTimeout(r, 500); });
                        connectWS(currentProject, null);
                    } catch (error) {
                        console.error('重启进程失败:', error);
                        alert('重启进程失败: ' + (error.message || '未知错误'));
                    }
                    break;
                case 'stop':
                    if (!confirm('将停止当前进程，确定？')) return;
                    await apiRequest('POST', '/api/projects/' + currentProjectId + '/stop');
                    document.getElementById('terminalStatus').textContent = '已停止';
                    document.getElementById('terminalStatus').className = 'terminal-status';
                    break;
            }
        }

        function suspendAndBack() {
            if (ws) { ws.close(); ws = null; }
            document.getElementById('terminalPage').classList.remove('active');
            document.getElementById('projectsPage').classList.add('active');
            loadProjects();
        }

        // ============== 视图切换 ==============
        let currentFilePath = '';

        function switchView(view) {
            document.querySelectorAll('.view-tab').forEach((el, i) => {
                el.classList.toggle('active',
                    (i === 0 && view === 'terminal') ||
                    (i === 1 && view === 'files') ||
                    (i === 2 && view === 'git')
                );
            });
            document.getElementById('terminalView').classList.toggle('active', view === 'terminal');
            document.getElementById('filesView').classList.toggle('active', view === 'files');
            document.getElementById('gitView').classList.toggle('active', view === 'git');

            if (view === 'files') {
                loadFiles('');
            } else if (view === 'git') {
                loadGitStatus();
            } else if (view === 'terminal' && fitAddon) {
                setTimeout(function() { fitAddon.fit(); }, 50);
            }
        }

        // ============== 文件管理 ==============
        async function loadFiles(path) {
            if (!currentProjectId) return;
            currentFilePath = path || '';
            document.getElementById('currentPath').textContent = '/' + currentFilePath;

            try {
                const res = await apiRequest('GET', '/api/projects/' + currentProjectId + '/files?path=' + encodeURIComponent(currentFilePath));
                const data = await res.json();
                renderFileList(data);
            } catch (e) {
                document.getElementById('fileList').innerHTML = '<div class="empty-state">加载失败</div>';
            }
        }

        function renderFileList(data) {
            const list = document.getElementById('fileList');
            let html = '';

            // 后端返回 items 数组，需要按 is_dir 分组
            const items = data.items || [];
            const directories = items.filter(function(item) { return item.is_dir; });
            const files = items.filter(function(item) { return !item.is_dir; });

            // 返回上级按钮
            if (currentFilePath) {
                html += '<div class="file-item" onclick="navigateUp()">' +
                    '<span class="file-icon">⬆️</span>' +
                    '<div class="file-info"><div class="file-name">..</div><div class="file-meta">返回上级</div></div>' +
                '</div>';
            }

            // 目录
            directories.forEach(function(dir) {
                // 生成相对路径
                var dirPath = currentFilePath ? (currentFilePath + '/' + dir.name) : dir.name;
                html += '<div class="file-item" onclick="loadFiles(\\'' + escapeQuote(dirPath) + '\\')">' +
                    '<span class="file-icon">📁</span>' +
                    '<div class="file-info"><div class="file-name">' + escapeHtml(dir.name) + '</div>' +
                    '<div class="file-meta">目录</div></div>' +
                    '<div class="file-actions">' +
                        '<button class="file-action-btn" onclick="event.stopPropagation();renameItem(\\'' + escapeQuote(dirPath) + '\\')">✏️</button>' +
                        '<button class="file-action-btn" onclick="event.stopPropagation();deleteItem(\\'' + escapeQuote(dirPath) + '\\')">🗑️</button>' +
                    '</div>' +
                '</div>';
            });

            // 文件
            files.forEach(function(file) {
                var filePath = currentFilePath ? (currentFilePath + '/' + file.name) : file.name;
                const icon = getFileIcon(file.name);
                const size = formatSize(file.size);
                html += '<div class="file-item" onclick="previewFile(\\'' + escapeQuote(filePath) + '\\')">' +
                    '<span class="file-icon">' + icon + '</span>' +
                    '<div class="file-info"><div class="file-name">' + escapeHtml(file.name) + '</div>' +
                    '<div class="file-meta">' + size + '</div></div>' +
                    '<div class="file-actions">' +
                        '<button class="file-action-btn" onclick="event.stopPropagation();downloadFile(\\'' + escapeQuote(filePath) + '\\')">⬇️</button>' +
                        '<button class="file-action-btn" onclick="event.stopPropagation();renameItem(\\'' + escapeQuote(filePath) + '\\')">✏️</button>' +
                        '<button class="file-action-btn" onclick="event.stopPropagation();deleteItem(\\'' + escapeQuote(filePath) + '\\')">🗑️</button>' +
                    '</div>' +
                '</div>';
            });

            if (!html) {
                html = '<div class="empty-state"><span class="icon">📂</span><p>空目录</p></div>';
            }
            list.innerHTML = html;
        }

        function navigateUp() {
            const parts = currentFilePath.split('/').filter(Boolean);
            parts.pop();
            loadFiles(parts.join('/'));
        }

        function getFileIcon(name) {
            const ext = name.split('.').pop().toLowerCase();
            const icons = {
                'js': '📜', 'ts': '📜', 'py': '🐍', 'java': '☕', 'go': '🔷',
                'html': '🌐', 'css': '🎨', 'json': '📋', 'xml': '📋', 'yaml': '📋', 'yml': '📋',
                'md': '📝', 'txt': '📄', 'log': '📄',
                'png': '🖼️', 'jpg': '🖼️', 'jpeg': '🖼️', 'gif': '🖼️', 'svg': '🖼️',
                'zip': '📦', 'tar': '📦', 'gz': '📦', 'rar': '📦',
                'pdf': '📕', 'doc': '📘', 'docx': '📘', 'xls': '📗', 'xlsx': '📗'
            };
            return icons[ext] || '📄';
        }

        function formatSize(bytes) {
            if (bytes < 1024) return bytes + ' B';
            if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB';
            return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
        }

        function escapeHtml(str) { return str.replace(/</g, '&lt;').replace(/>/g, '&gt;'); }
        function escapeQuote(str) {
            var r = '';
            for (var i = 0; i < str.length; i++) {
                var c = str.charAt(i);
                if (c === "'" || c === String.fromCharCode(92)) r += String.fromCharCode(92);
                r += c;
            }
            return r;
        }

        async function fileAction(action) {
            if (!currentProjectId) return;
            switch(action) {
                case 'refresh':
                    loadFiles(currentFilePath);
                    break;
                case 'upload':
                    document.getElementById('fileInput').click();
                    break;
                case 'newFolder':
                    const folderName = prompt('新建目录名称:');
                    if (folderName) {
                        const path = currentFilePath ? currentFilePath + '/' + folderName : folderName;
                        await apiRequest('POST', '/api/projects/' + currentProjectId + '/files/mkdir', { path });
                        loadFiles(currentFilePath);
                    }
                    break;
                case 'download':
                    window.open('/api/projects/' + currentProjectId + '/files/download-zip?path=' + encodeURIComponent(currentFilePath) + '&token=' + getToken());
                    break;
            }
        }

        async function handleFileSelect(event) {
            const files = event.target.files;
            if (files.length === 0) return;
            await uploadFiles(files);
            event.target.value = '';
        }

        function handleDragOver(event) {
            event.preventDefault();
            document.getElementById('uploadArea').classList.add('dragover');
        }

        function handleDragLeave(event) {
            document.getElementById('uploadArea').classList.remove('dragover');
        }

        async function handleDrop(event) {
            event.preventDefault();
            document.getElementById('uploadArea').classList.remove('dragover');
            const files = event.dataTransfer.files;
            if (files.length > 0) await uploadFiles(files);
        }

        async function uploadFiles(files) {
            const formData = new FormData();
            formData.append('path', currentFilePath);
            for (let i = 0; i < files.length; i++) {
                formData.append('files', files[i]);
            }

            try {
                const res = await fetch('/api/projects/' + currentProjectId + '/files/upload-multiple', {
                    method: 'POST',
                    headers: { 'Authorization': 'Bearer ' + getToken() },
                    body: formData
                });
                const data = await res.json();
                alert('上传完成: ' + data.success_count + '/' + data.total + ' 个文件');
                loadFiles(currentFilePath);
            } catch (e) {
                alert('上传失败');
            }
        }

        async function downloadFile(path) {
            window.open('/api/projects/' + currentProjectId + '/files/download?path=' + encodeURIComponent(path) + '&token=' + getToken());
        }

        async function renameItem(path) {
            const oldName = path.split('/').pop();
            const newName = prompt('新名称:', oldName);
            if (newName && newName !== oldName) {
                await apiRequest('POST', '/api/projects/' + currentProjectId + '/files/rename', {
                    old_path: path, new_name: newName
                });
                loadFiles(currentFilePath);
            }
        }

        async function deleteItem(path) {
            if (!confirm('确定删除 ' + path.split('/').pop() + '？')) return;
            await apiRequest('DELETE', '/api/projects/' + currentProjectId + '/files?path=' + encodeURIComponent(path));
            loadFiles(currentFilePath);
        }

        // ============== Git 管理 ==============
        async function loadGitStatus() {
            if (!currentProjectId) return;
            try {
                const res = await apiRequest('GET', '/api/projects/' + currentProjectId + '/git/status');
                const data = await res.json();
                renderGitStatus(data);
                loadGitHistory();
            } catch (e) {
                document.getElementById('gitStatusInfo').innerHTML = '加载失败';
            }
        }

        function renderGitStatus(data) {
            const status = data.status || {};
            const config = data.config || {};

            // 状态信息
            let statusHtml = '';
            if (!status.initialized) {
                statusHtml = '<p>仓库未初始化</p><button class="toolbar-btn primary" onclick="gitAction(\\'init\\')">初始化 Git</button>';
            } else {
                statusHtml = '<div class="git-status-item"><span>分支:</span><strong>' + (status.branch || 'main') + '</strong></div>';
                if (status.remote) {
                    statusHtml += '<div class="git-status-item"><span>远程:</span><span style="font-size:11px;word-break:break-all;">' + status.remote + '</span></div>';
                }
                statusHtml += '<div class="git-status-item"><span>变更:</span><strong>' + (status.has_changes ? '有未提交变更' : '无变更') + '</strong></div>';
            }
            document.getElementById('gitStatusInfo').innerHTML = statusHtml;

            // 变更文件
            let changesHtml = '';
            (status.modified_files || []).forEach(function(f) {
                changesHtml += '<div class="git-status-item"><span class="git-badge modified">M</span>' + escapeHtml(f) + '</div>';
            });
            (status.untracked_files || []).forEach(function(f) {
                changesHtml += '<div class="git-status-item"><span class="git-badge new">+</span>' + escapeHtml(f) + '</div>';
            });
            document.getElementById('gitChanges').innerHTML = changesHtml || '无变更';

            // 配置
            if (config) {
                document.getElementById('remoteUrl').value = config.remote_url || '';
                document.getElementById('autoCommit').checked = config.auto_commit !== false;
                document.getElementById('autoPush').checked = config.auto_push === true;
            }
        }

        async function loadGitHistory() {
            try {
                const res = await apiRequest('GET', '/api/projects/' + currentProjectId + '/git/history?limit=10');
                const data = await res.json();
                let html = '';
                (data.commits || []).forEach(function(c) {
                    const time = new Date(c.timestamp * 1000).toLocaleString('zh-CN');
                    html += '<div class="git-commit-item">' +
                        '<div class="git-commit-msg">' + escapeHtml(c.message) + '</div>' +
                        '<div class="git-commit-meta">' + c.hash.substring(0,7) + ' · ' + c.author + ' · ' + time + '</div>' +
                    '</div>';
                });
                document.getElementById('gitHistory').innerHTML = html || '暂无提交';
            } catch (e) {
                document.getElementById('gitHistory').innerHTML = '加载失败';
            }
        }

        async function gitAction(action) {
            if (!currentProjectId) return;
            try {
                let res, data;
                switch(action) {
                    case 'init':
                        res = await apiRequest('POST', '/api/projects/' + currentProjectId + '/git/init');
                        data = await res.json();
                        alert(data.message || '初始化完成');
                        loadGitStatus();
                        break;
                    case 'commit':
                        const msg = document.getElementById('commitMessage').value.trim();
                        res = await apiRequest('POST', '/api/projects/' + currentProjectId + '/git/commit', { message: msg || null });
                        data = await res.json();
                        alert(data.message);
                        document.getElementById('commitMessage').value = '';
                        loadGitStatus();
                        break;
                    case 'push':
                        res = await apiRequest('POST', '/api/projects/' + currentProjectId + '/git/push');
                        data = await res.json();
                        alert(data.message);
                        break;
                    case 'pull':
                        res = await apiRequest('POST', '/api/projects/' + currentProjectId + '/git/pull');
                        data = await res.json();
                        alert(data.message);
                        loadGitStatus();
                        break;
                    case 'saveConfig':
                        res = await apiRequest('POST', '/api/projects/' + currentProjectId + '/git/config', {
                            remote_url: document.getElementById('remoteUrl').value.trim() || null,
                            auto_commit: document.getElementById('autoCommit').checked,
                            auto_push: document.getElementById('autoPush').checked
                        });
                        alert('配置已保存');
                        loadGitStatus();
                        break;
                }
            } catch (e) {
                alert('操作失败: ' + e.message);
            }
        }

        // ============== 移动端输入 ==============
        // 修饰键状态
        var modState = { ctrl: false, shift: false, alt: false };

        function sendMobileInput() {
            var input = document.getElementById('mobileInput');
            var text = input.value;
            if (!text) return;

            if (ws && ws.readyState === WebSocket.OPEN) {
                // 分块发送长文本
                var chunkSize = 4096;
                for (var i = 0; i < text.length; i += chunkSize) {
                    ws.send(text.slice(i, i + chunkSize));
                }
                input.value = '';
                term.focus();
            }
        }

        function sendMobileEnter() {
            if (ws && ws.readyState === WebSocket.OPEN) {
                ws.send(String.fromCharCode(13));  // 发送回车符
            }
        }

        // 切换修饰键状态
        function toggleMod(mod) {
            modState[mod] = !modState[mod];
            var btn = document.getElementById('mod' + mod.charAt(0).toUpperCase() + mod.slice(1));
            if (btn) btn.classList.toggle('active', modState[mod]);
        }

        // 重置所有修饰键
        function resetMods() {
            modState = { ctrl: false, shift: false, alt: false };
            document.querySelectorAll('.mobile-key-btn.mod').forEach(function(btn) {
                btn.classList.remove('active');
            });
        }

        // 发送特殊键
        function sendKey(key) {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;

            var code = '';
            switch(key) {
                case 'up':    code = '\x1b[A'; break;
                case 'down':  code = '\x1b[B'; break;
                case 'right': code = '\x1b[C'; break;
                case 'left':  code = '\x1b[D'; break;
                case 'tab':   code = '\t'; break;
                case 'esc':   code = '\x1b'; break;
                case 'backspace': code = '\x7f'; break;
                case 'delete': code = '\x1b[3~'; break;
                case 'home':  code = '\x1b[H'; break;
                case 'end':   code = '\x1b[F'; break;
            }

            // 应用修饰键
            if (modState.shift) {
                switch(key) {
                    case 'up':    code = '\x1b[1;2A'; break;
                    case 'down':  code = '\x1b[1;2B'; break;
                    case 'right': code = '\x1b[1;2C'; break;
                    case 'left':  code = '\x1b[1;2D'; break;
                }
            }
            if (modState.ctrl) {
                switch(key) {
                    case 'up':    code = '\x1b[1;5A'; break;
                    case 'down':  code = '\x1b[1;5B'; break;
                    case 'right': code = '\x1b[1;5C'; break;
                    case 'left':  code = '\x1b[1;5D'; break;
                }
            }

            ws.send(code);
            resetMods();
            term.focus();
        }

        // 发送 Ctrl 组合键
        function sendCombo(char) {
            if (!ws || ws.readyState !== WebSocket.OPEN) return;
            var code = String.fromCharCode(char.charCodeAt(0) - 96);  // a=1, b=2, c=3...
            ws.send(code);
            term.focus();
        }

        // 移动端粘贴文本
        function mobilePasteText() {
            navigator.clipboard.readText().then(function(text) {
                if (text && ws && ws.readyState === WebSocket.OPEN) {
                    var chunkSize = 4096;
                    for (var i = 0; i < text.length; i += chunkSize) {
                        ws.send(text.slice(i, i + chunkSize));
                    }
                    term.focus();
                }
            }).catch(function(err) {
                alert('读取剪贴板失败，请检查权限');
            });
        }

        // 移动端粘贴图片
        function mobilePasteImage() {
            navigator.clipboard.read().then(function(items) {
                for (var item of items) {
                    for (var type of item.types) {
                        if (type.startsWith('image/')) {
                            item.getType(type).then(function(blob) {
                                // 转为 base64 发送
                                var reader = new FileReader();
                                reader.onload = function(e) {
                                    var base64 = e.target.result;
                                    // 图片数据发送给终端（Claude Code 会识别）
                                    if (ws && ws.readyState === WebSocket.OPEN) {
                                        // 发送特殊格式让后端识别这是图片
                                        ws.send('\\x1b]52;c;' + base64.split(',')[1] + '\\x07');
                                    }
                                };
                                reader.readAsDataURL(blob);
                            });
                            return;
                        }
                    }
                }
                alert('剪贴板中没有图片');
            }).catch(function(err) {
                alert('读取剪贴板失败: ' + err.message);
            });
        }

        // 移动端输入框回车发送
        document.getElementById('mobileInput').addEventListener('keydown', function(e) {
            if (e.key === 'Enter') {
                e.preventDefault();
                sendMobileInput();
                sendMobileEnter();
            }
        });

        let wsRetryCount = 0;
        const WS_MAX_RETRY = 3;

        function connectWS(projectName, mode) {
            // 关闭旧连接
            if (ws) {
                ws.onclose = null; // 防止触发旧的 onclose
                ws.close();
                ws = null;
            }

            const statusEl = document.getElementById('terminalStatus');
            statusEl.textContent = '连接中...';
            statusEl.className = 'terminal-status';

            const proto = location.protocol === 'https:' ? 'wss:' : 'ws:';
            let url = proto + '//' + location.host + '/ws/' + encodeURIComponent(projectName);
            url += '?token=' + encodeURIComponent(getToken());
            if (mode) url += '&mode=' + mode;

            console.log('[WS] 连接:', url);

            try {
                ws = new WebSocket(url);
                ws.binaryType = 'arraybuffer';

                // 连接超时处理
                const connectTimeout = setTimeout(function() {
                    if (ws && ws.readyState === WebSocket.CONNECTING) {
                        console.log('[WS] 连接超时');
                        ws.close();
                        if (wsRetryCount < WS_MAX_RETRY) {
                            wsRetryCount++;
                            statusEl.textContent = '重试连接 (' + wsRetryCount + '/' + WS_MAX_RETRY + ')...';
                            setTimeout(function() { connectWS(projectName, mode); }, 1000);
                        } else {
                            statusEl.textContent = '连接失败，请点击继续(-c)重试';
                            statusEl.className = 'terminal-status';
                        }
                    }
                }, 10000);

                ws.onopen = function() {
                    clearTimeout(connectTimeout);
                    wsRetryCount = 0;
                    console.log('[WS] 已连接');
                    statusEl.textContent = '已连接';
                    statusEl.className = 'terminal-status connected';
                    // 发送终端大小
                    if (term && term.rows && term.cols) {
                        ws.send('resize:' + term.rows + ':' + term.cols);
                    }
                };

                ws.onmessage = function(e) {
                    if (term) {
                        term.write(e.data instanceof ArrayBuffer ? new Uint8Array(e.data) : e.data);
                    }
                };

                ws.onclose = function(e) {
                    clearTimeout(connectTimeout);
                    console.log('[WS] 已断开:', e.code, e.reason);
                    statusEl.textContent = '已断开';
                    statusEl.className = 'terminal-status';
                };

                ws.onerror = function(e) {
                    clearTimeout(connectTimeout);
                    console.error('[WS] 连接错误:', e);
                    statusEl.textContent = '连接错误';
                    statusEl.className = 'terminal-status';
                };
            } catch (e) {
                console.error('[WS] 创建失败:', e);
                statusEl.textContent = '连接失败';
            }

            // 设置终端输入处理
            if (termDataHandler) termDataHandler.dispose();
            termDataHandler = term.onData(function(d) {
                if (ws && ws.readyState === WebSocket.OPEN) {
                    ws.send(d);
                }
            });
        }

        function showNewModal() { document.getElementById('newModal').classList.add('show'); }
        function closeNewModal() {
            document.getElementById('newModal').classList.remove('show');
            document.getElementById('newProjectName').value = '';
            document.getElementById('newProjectNote').value = '';
            // 重置 Butler 设置
            document.getElementById('butlerEnabled').checked = false;
            document.getElementById('butlerGoal').value = '';
            document.getElementById('butlerGoalSection').style.display = 'none';
        }

        function selectMode(el, mode) {
            document.querySelectorAll('.mode-btn').forEach(function(b) { b.classList.remove('active'); });
            el.classList.add('active');
            selectedMode = mode;
        }

        // Butler Agent 开关切换
        document.getElementById('butlerEnabled').addEventListener('change', function() {
            var goalSection = document.getElementById('butlerGoalSection');
            goalSection.style.display = this.checked ? 'block' : 'none';
        });

        async function createProject() {
            const name = document.getElementById('newProjectName').value.trim();
            const note = document.getElementById('newProjectNote').value.trim();
            if (!name) { alert('请输入项目名称'); return; }

            // Butler 设置
            const butlerEnabled = document.getElementById('butlerEnabled').checked;
            const butlerGoal = document.getElementById('butlerGoal').value.trim();

            if (butlerEnabled && !butlerGoal) {
                alert('请输入项目目标');
                return;
            }

            const res = await apiRequest('POST', '/api/projects', {
                name: name,
                display_name: name,
                note: note,
                mode: selectedMode
            });
            const data = await res.json();

            if (!res.ok) {
                alert(data.detail || '创建失败');
                return;
            }

            // 如果启用了 Butler，初始化它
            if (butlerEnabled && butlerGoal) {
                try {
                    await apiRequest('POST', '/api/projects/' + data.project_id + '/butler/init', {
                        goal: butlerGoal,
                        sub_goals: []
                    });
                    console.log('[Butler] 已启用，目标:', butlerGoal);
                } catch (e) {
                    console.error('[Butler] 初始化失败:', e);
                }
            }

            closeNewModal();
            await openProject(data.project_id, selectedMode);
        }

        let chatSending = false;

        async function sendChat() {
            if (chatSending) return;
            const input = document.getElementById('chatInput');
            const msg = input.value.trim();
            if (!msg) return;

            chatSending = true;
            const sendBtn = document.getElementById('chatSendBtn');
            sendBtn.textContent = '...';
            sendBtn.disabled = true;

            const messages = document.getElementById('chatMessages');
            messages.innerHTML += '<div class="message user">' + escapeHtml(msg) + '</div>';
            input.value = '';

            // 添加一个正在输入的占位符
            const assistantMsg = document.createElement('div');
            assistantMsg.className = 'message assistant typing';
            assistantMsg.innerHTML = '<span class="typing-indicator">思考中...</span>';
            messages.appendChild(assistantMsg);
            messages.scrollTop = messages.scrollHeight;

            try {
                const res = await apiRequest('POST', '/api/butler/chat', { message: msg });
                const data = await res.json();

                // 流式显示效果
                assistantMsg.classList.remove('typing');
                assistantMsg.innerHTML = '';
                const text = data.response || '抱歉，我无法处理这个请求。';
                await typeWriter(assistantMsg, text, 15);
            } catch (e) {
                assistantMsg.classList.remove('typing');
                assistantMsg.innerHTML = '网络错误，请重试。';
            }

            messages.scrollTop = messages.scrollHeight;
            chatSending = false;
            sendBtn.textContent = '↑';
            sendBtn.disabled = false;
        }

        async function typeWriter(element, text, delay) {
            for (let i = 0; i < text.length; i++) {
                element.innerHTML += escapeHtml(text[i]);
                if (i % 5 === 0) {
                    element.parentElement.scrollTop = element.parentElement.scrollHeight;
                    await new Promise(r => setTimeout(r, delay));
                }
            }
        }

        async function loadChatHistory() {
            try {
                const res = await apiRequest('GET', '/api/butler/history');
                const data = await res.json();
                const messages = document.getElementById('chatMessages');

                if (data.messages && data.messages.length > 0) {
                    messages.innerHTML = data.messages.map(m => {
                        return '<div class="message ' + m.role + '">' + escapeHtml(m.content) + '</div>';
                    }).join('');
                }
                messages.scrollTop = messages.scrollHeight;
            } catch (e) {
                console.error('加载聊天历史失败', e);
            }
        }

        async function clearChatHistory() {
            if (!confirm('确定清空聊天记录吗？')) return;
            try {
                await apiRequest('DELETE', '/api/butler/history');
                const messages = document.getElementById('chatMessages');
                messages.innerHTML = '<div class="message assistant">你好！我是小深，你的项目管家。可以帮你查看项目状态、记录计划、提醒待办。</div>';
            } catch (e) {
                alert('清空失败');
            }
        }

        document.getElementById('chatInput').addEventListener('keydown', function(e) {
            if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); sendChat(); }
        });

        window.addEventListener('resize', function() {
            if (fitAddon && term && document.getElementById('terminalPage').classList.contains('active')) {
                fitAddon.fit();
                if (ws && ws.readyState === 1) ws.send('resize:' + term.rows + ':' + term.cols);
            }
        });

        document.addEventListener('touchstart', function(e) {
            if (!e.target.closest('.project-card') && !e.target.closest('.project-actions')) {
                document.querySelectorAll('.project-card').forEach(function(card) {
                    card.style.transition = 'transform 0.2s ease-out';
                    card.style.transform = 'translateX(0)';
                });
            }
        });

        // 检查登录状态
        if (!getToken()) {
            window.location.href = '/';
        } else {
            loadProjects();
            loadUnreadCount();
            // 定时检查未读通知
            setInterval(loadUnreadCount, 30000);
        }

        // ============== 设置页面 ==============
        async function loadSettings() {
            try {
                const res = await apiRequest('GET', '/api/auth/me');
                const user = await res.json();

                document.getElementById('settingsEmail').textContent = user.email;
                document.getElementById('settingsDisplayName').value = user.display_name || '';
                document.getElementById('settingsExpires').textContent = new Date(user.auth_expires_at).toLocaleDateString('zh-CN');
                document.getElementById('settingsCreated').textContent = new Date(user.created_at).toLocaleDateString('zh-CN');
            } catch (e) {
                console.error('加载设置失败', e);
            }
        }

        async function saveProfile() {
            const displayName = document.getElementById('settingsDisplayName').value.trim();
            try {
                const res = await apiRequest('PATCH', '/api/auth/me', { display_name: displayName });
                if (res.ok) alert('保存成功');
                else alert('保存失败');
            } catch (e) {
                alert('网络错误');
            }
        }

        async function changePassword() {
            const oldPwd = document.getElementById('oldPassword').value;
            const newPwd = document.getElementById('newPassword').value;
            const confirmPwd = document.getElementById('confirmPassword').value;

            if (!oldPwd || !newPwd) { alert('请填写密码'); return; }
            if (newPwd.length < 6) { alert('新密码至少6位'); return; }
            if (newPwd !== confirmPwd) { alert('两次密码不一致'); return; }

            try {
                const res = await apiRequest('POST', '/api/auth/change-password', {
                    old_password: oldPwd,
                    new_password: newPwd
                });
                const data = await res.json();
                if (res.ok) {
                    alert('密码修改成功');
                    document.getElementById('oldPassword').value = '';
                    document.getElementById('newPassword').value = '';
                    document.getElementById('confirmPassword').value = '';
                } else {
                    alert(data.detail || '修改失败');
                }
            } catch (e) {
                alert('网络错误');
            }
        }

        async function logoutAll() {
            if (!confirm('确定要退出所有设备？')) return;
            try {
                await apiRequest('POST', '/api/auth/logout-all');
                logout();
            } catch (e) {
                logout();
            }
        }

        // ============== 项目设置 ==============
        let editingProjectId = null;

        function showProjectSettings(projectId, name, note) {
            editingProjectId = projectId;
            document.getElementById('editProjectName').value = name || '';
            document.getElementById('editProjectNote').value = note || '';
            document.getElementById('projectSettingsModal').classList.add('show');
        }

        function closeProjectSettings() {
            document.getElementById('projectSettingsModal').classList.remove('show');
            editingProjectId = null;
        }

        async function saveProjectSettings() {
            if (!editingProjectId) return;
            const name = document.getElementById('editProjectName').value.trim();
            const note = document.getElementById('editProjectNote').value.trim();

            try {
                const res = await apiRequest('PATCH', '/api/projects/' + editingProjectId, {
                    display_name: name || null,
                    note: note || null
                });
                if (res.ok) {
                    alert('保存成功');
                    closeProjectSettings();
                    loadProjects();
                } else {
                    alert('保存失败');
                }
            } catch (e) {
                alert('网络错误');
            }
        }

        async function confirmDeleteProject() {
            if (!editingProjectId) return;
            if (!confirm('确定要删除此项目？此操作不可恢复！')) return;
            if (!confirm('再次确认：删除后项目数据将丢失！')) return;

            try {
                const res = await apiRequest('DELETE', '/api/projects/' + editingProjectId);
                if (res.ok) {
                    alert('项目已删除');
                    closeProjectSettings();
                    loadProjects();
                } else {
                    alert('删除失败');
                }
            } catch (e) {
                alert('网络错误');
            }
        }

        // ============== 文件编辑器 ==============
        let editingFilePath = '';

        async function openEditor(path) {
            if (!currentProjectId) return;
            editingFilePath = path;
            document.getElementById('editorFilename').textContent = path;
            document.getElementById('editorModal').classList.add('show');

            try {
                const res = await apiRequest('GET', '/api/projects/' + currentProjectId + '/files/read?path=' + encodeURIComponent(path));
                const data = await res.json();

                if (data.is_binary) {
                    alert('二进制文件无法编辑');
                    closeEditor();
                    return;
                }

                const content = data.content || '';
                document.getElementById('editorContent').value = content;
                document.getElementById('editorFileSize').textContent = content.length + ' 字节';
                updateEditorLineInfo();
            } catch (e) {
                alert('无法打开文件');
                closeEditor();
            }
        }

        function closeEditor() {
            document.getElementById('editorModal').classList.remove('show');
            editingFilePath = '';
        }

        async function saveFile() {
            if (!currentProjectId || !editingFilePath) return;
            const content = document.getElementById('editorContent').value;

            try {
                const res = await apiRequest('POST', '/api/projects/' + currentProjectId + '/files/write', {
                    path: editingFilePath,
                    content: content
                });
                if (res.ok) {
                    alert('保存成功');
                    document.getElementById('editorFileSize').textContent = content.length + ' 字节';
                } else {
                    alert('保存失败');
                }
            } catch (e) {
                alert('网络错误');
            }
        }

        function updateEditorLineInfo() {
            const textarea = document.getElementById('editorContent');
            const pos = textarea.selectionStart;
            const text = textarea.value.substring(0, pos);
            const lines = text.split('\\n');
            const line = lines.length;
            const col = lines[lines.length - 1].length + 1;
            document.getElementById('editorLineInfo').textContent = '行: ' + line + ', 列: ' + col;
        }

        document.addEventListener('DOMContentLoaded', function() {
            const editor = document.getElementById('editorContent');
            if (editor) {
                editor.addEventListener('click', updateEditorLineInfo);
                editor.addEventListener('keyup', updateEditorLineInfo);
                // Tab 键支持
                editor.addEventListener('keydown', function(e) {
                    if (e.key === 'Tab') {
                        e.preventDefault();
                        const start = this.selectionStart;
                        const end = this.selectionEnd;
                        this.value = this.value.substring(0, start) + '    ' + this.value.substring(end);
                        this.selectionStart = this.selectionEnd = start + 4;
                    }
                });
            }
        });

        // ============== 代理状态详情 ==============
        async function showAgentDetail() {
            if (!currentProjectId) return;
            document.getElementById('agentDetailModal').classList.add('show');
            loadAgentDetail();
        }

        function closeAgentDetail() {
            document.getElementById('agentDetailModal').classList.remove('show');
        }

        async function loadAgentDetail() {
            try {
                // 加载代理状态
                const res = await apiRequest('GET', '/api/projects/' + currentProjectId + '/agent');
                const agent = await res.json();

                const phases = {
                    'pending_dispatch': '待分发',
                    'understanding': '理解规划中',
                    'pending_confirm': '待确认方案',
                    'implementing': '编码实现中',
                    'pending_acceptance': '待验收',
                    'pending_deploy': '待部署',
                    'completed': '已完成',
                    'error': '出错',
                    'idle': '空闲'
                };

                let statusHtml = '<div class="settings-item"><span>当前环节</span><strong>' + (phases[agent.current_phase] || agent.current_phase) + '</strong></div>';
                statusHtml += '<div class="settings-item"><span>需要介入</span><strong>' + (agent.needs_human ? '是' : '否') + '</strong></div>';
                if (agent.human_action_message) {
                    statusHtml += '<div class="settings-item"><span>消息</span><span style="color:var(--warning)">' + agent.human_action_message + '</span></div>';
                }
                statusHtml += '<div class="settings-item"><span>自动模式</span><strong>' + (agent.auto_mode ? '开启' : '关闭') + '</strong></div>';
                if (agent.last_heartbeat) {
                    statusHtml += '<div class="settings-item"><span>最后心跳</span><span>' + new Date(agent.last_heartbeat).toLocaleString('zh-CN') + '</span></div>';
                }
                document.getElementById('agentCurrentStatus').innerHTML = statusHtml;

                // 加载交互历史（如果有API）
                try {
                    const histRes = await apiRequest('GET', '/api/projects/' + currentProjectId + '/outputs?limit=20');
                    const histData = await histRes.json();
                    let timelineHtml = '';
                    (histData.outputs || []).slice(0, 10).forEach(function(item) {
                        timelineHtml += '<div class="timeline-item"><div class="timeline-title">输出记录</div><div class="timeline-time">' + new Date(item.created_at).toLocaleString('zh-CN') + '</div></div>';
                    });
                    document.getElementById('agentTimeline').innerHTML = timelineHtml || '暂无记录';
                } catch (e) {
                    document.getElementById('agentTimeline').innerHTML = '暂无记录';
                }
            } catch (e) {
                document.getElementById('agentCurrentStatus').innerHTML = '加载失败';
            }
        }

        // ============== 搜索功能 ==============
        let searchTimer = null;

        function showSearch() {
            document.getElementById('searchModal').classList.add('show');
            document.getElementById('searchInput').focus();
        }

        function closeSearch() {
            document.getElementById('searchModal').classList.remove('show');
            document.getElementById('searchInput').value = '';
            document.getElementById('searchResults').innerHTML = '<div class="empty-state"><span class="icon">🔍</span><p>输入关键词搜索</p></div>';
        }

        function debounceSearch() {
            if (searchTimer) clearTimeout(searchTimer);
            searchTimer = setTimeout(doSearch, 300);
        }

        async function doSearch() {
            const query = document.getElementById('searchInput').value.trim();
            if (!query) {
                document.getElementById('searchResults').innerHTML = '<div class="empty-state"><span class="icon">🔍</span><p>输入关键词搜索</p></div>';
                return;
            }

            document.getElementById('searchResults').innerHTML = '<div class="empty-state">搜索中...</div>';

            let results = [];

            // 搜索项目
            try {
                const res = await apiRequest('GET', '/api/projects');
                const projects = await res.json();
                projects.forEach(function(p) {
                    if ((p.name && p.name.includes(query)) || (p.display_name && p.display_name.includes(query)) || (p.note && p.note.includes(query))) {
                        results.push({
                            type: 'project',
                            title: p.display_name || p.name,
                            path: '项目',
                            id: p.id
                        });
                    }
                });
            } catch (e) {}

            // 如果当前有项目，搜索文件
            if (currentProjectId) {
                try {
                    const res = await apiRequest('GET', '/api/projects/' + currentProjectId + '/files/search?q=' + encodeURIComponent(query));
                    const data = await res.json();
                    (data.results || []).forEach(function(f) {
                        results.push({
                            type: 'file',
                            title: f.name,
                            path: f.path,
                            projectId: currentProjectId
                        });
                    });
                } catch (e) {}
            }

            // 渲染结果
            if (results.length === 0) {
                document.getElementById('searchResults').innerHTML = '<div class="empty-state"><span class="icon">😕</span><p>未找到结果</p></div>';
                return;
            }

            let html = '';
            results.forEach(function(r) {
                if (r.type === 'project') {
                    html += '<div class="search-result-item" onclick="closeSearch();openProject(' + r.id + ')">' +
                        '<div class="search-result-title">📁 ' + escapeHtml(r.title) + '</div>' +
                        '<div class="search-result-path">' + r.path + '</div>' +
                    '</div>';
                } else {
                    html += '<div class="search-result-item" onclick="closeSearch();openFileFromSearch(\\'' + escapeQuote(r.path) + '\\')">' +
                        '<div class="search-result-title">📄 ' + escapeHtml(r.title) + '</div>' +
                        '<div class="search-result-path">' + r.path + '</div>' +
                    '</div>';
                }
            });
            document.getElementById('searchResults').innerHTML = html;
        }

        async function openFileFromSearch(path) {
            if (!currentProjectId) return;
            switchView('files');
            // 导航到文件所在目录
            const parts = path.split('/');
            parts.pop();
            loadFiles(parts.join('/'));
        }

        // ============== 计划列表 ==============
        function showPlans() {
            document.getElementById('plansModal').classList.add('show');
            loadPlans();
        }

        function closePlans() {
            document.getElementById('plansModal').classList.remove('show');
        }

        async function loadPlans() {
            try {
                const res = await apiRequest('GET', '/api/butler/plans');
                const data = await res.json();

                let html = '';
                (data.plans || []).forEach(function(p) {
                    const doneClass = p.is_done ? 'done' : '';
                    html += '<div class="plan-item ' + doneClass + '">' +
                        '<div class="plan-content">' +
                            '<input type="checkbox" class="plan-checkbox" ' + (p.is_done ? 'checked' : '') + ' onchange="togglePlan(' + p.id + ', this.checked)">' +
                            '<span>' + escapeHtml(p.content) + '</span>' +
                        '</div>' +
                        '<div class="plan-time">' + new Date(p.created_at).toLocaleString('zh-CN') + '</div>' +
                        '<div class="plan-actions">' +
                            '<button class="settings-btn danger" onclick="deletePlan(' + p.id + ')">删除</button>' +
                        '</div>' +
                    '</div>';
                });

                document.getElementById('plansList').innerHTML = html || '<div class="empty-state">暂无计划</div>';
            } catch (e) {
                document.getElementById('plansList').innerHTML = '加载失败';
            }
        }

        async function addPlan() {
            const input = document.getElementById('newPlanInput');
            const content = input.value.trim();
            if (!content) return;

            try {
                await apiRequest('POST', '/api/butler/plans', { content });
                input.value = '';
                loadPlans();
            } catch (e) {
                alert('添加失败');
            }
        }

        async function togglePlan(id, done) {
            try {
                await apiRequest('PATCH', '/api/butler/plans/' + id, { is_done: done });
                loadPlans();
            } catch (e) {}
        }

        async function deletePlan(id) {
            if (!confirm('确定删除此计划？')) return;
            try {
                await apiRequest('DELETE', '/api/butler/plans/' + id);
                loadPlans();
            } catch (e) {
                alert('删除失败');
            }
        }

        // ============== 更新文件预览为编辑 ==============
        async function previewFile(path) {
            try {
                const res = await apiRequest('GET', '/api/projects/' + currentProjectId + '/files/read?path=' + encodeURIComponent(path));
                const data = await res.json();
                if (data.is_binary) {
                    if (confirm('这是二进制文件，是否下载？')) downloadFile(path);
                } else {
                    // 打开编辑器
                    openEditor(path);
                }
            } catch (e) {
                alert('无法打开文件');
            }
        }

        // 在项目列表添加设置按钮
        function addProjectSettingsBtn() {
            document.querySelectorAll('.project-wrapper').forEach(function(wrapper) {
                const actions = wrapper.querySelector('.project-actions');
                if (actions && !actions.querySelector('.btn-settings')) {
                    const id = wrapper.dataset.id;
                    const card = wrapper.querySelector('.project-card');
                    const name = card ? card.querySelector('.project-name')?.textContent : '';
                    const note = card ? card.querySelector('.project-note')?.textContent : '';
                    const btn = document.createElement('button');
                    btn.className = 'btn-restart btn-settings';
                    btn.innerHTML = '<span class="icon">⚙️</span>设置';
                    btn.onclick = function(e) {
                        e.stopPropagation();
                        showProjectSettings(id, name, note);
                    };
                    actions.insertBefore(btn, actions.firstChild);
                }
            });
        }

        // 监听项目列表更新
        const projectListObserver = new MutationObserver(addProjectSettingsBtn);
        document.addEventListener('DOMContentLoaded', function() {
            const projectList = document.getElementById('projectList');
            if (projectList) {
                projectListObserver.observe(projectList, { childList: true });
            }
        });
    </script>
</body>
</html>
"""

def get_admin_page():
    """管理员页面 HTML"""
    return """
<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Claude Mobile - 管理后台</title>
    <style>
        :root {
            --bg: #0d0d0d; --card: #1a1a1a; --border: #2a2a2a;
            --text: #fff; --text2: #888; --accent: #0066ff;
            --danger: #ef4444; --success: #22c55e;
        }
        * { margin: 0; padding: 0; box-sizing: border-box; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg); color: var(--text);
            min-height: 100vh; padding: 20px;
        }
        .header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 24px; }
        h1 { font-size: 24px; }
        .logout-btn { color: var(--text2); cursor: pointer; }
        .card { background: var(--card); padding: 20px; border-radius: 12px; margin-bottom: 16px; }
        .card-title { font-size: 16px; font-weight: 600; margin-bottom: 16px; display: flex; justify-content: space-between; align-items: center; }
        table { width: 100%; border-collapse: collapse; }
        th, td { padding: 12px; text-align: left; border-bottom: 1px solid var(--border); }
        th { color: var(--text2); font-weight: 500; font-size: 12px; }
        td { font-size: 14px; }
        .status-active { color: var(--success); }
        .status-inactive { color: var(--danger); }
        .actions button {
            padding: 6px 12px; margin-right: 8px; border: none; border-radius: 6px;
            font-size: 12px; cursor: pointer;
        }
        .btn-edit { background: var(--accent); color: #fff; }
        .btn-disable { background: var(--danger); color: #fff; }
        .btn-enable { background: var(--success); color: #fff; }
        .btn-add {
            padding: 8px 16px; background: var(--accent); color: #fff;
            border: none; border-radius: 8px; font-size: 14px; cursor: pointer;
        }
        .login-box {
            max-width: 400px; margin: 100px auto; background: var(--card);
            padding: 32px; border-radius: 16px;
        }
        .login-box h2 { margin-bottom: 24px; text-align: center; }
        .form-group { margin-bottom: 16px; }
        .form-group label { display: block; font-size: 12px; color: var(--text2); margin-bottom: 6px; }
        .form-group input {
            width: 100%; padding: 12px; background: var(--bg);
            border: 1px solid var(--border); border-radius: 8px;
            color: var(--text); font-size: 15px;
        }
        .error { color: var(--danger); font-size: 13px; margin-bottom: 12px; }
        .modal {
            display: none; position: fixed; inset: 0; background: rgba(0,0,0,0.7);
            justify-content: center; align-items: center; z-index: 100;
        }
        .modal.show { display: flex; }
        .modal-box { background: var(--card); padding: 24px; border-radius: 12px; width: 400px; max-width: 90%; }
        .modal-title { font-size: 18px; margin-bottom: 20px; }
        .modal-btns { display: flex; gap: 12px; margin-top: 20px; }
        .modal-btns button { flex: 1; padding: 12px; border: none; border-radius: 8px; cursor: pointer; }
        .btn-primary { background: var(--accent); color: #fff; }
        .btn-secondary { background: var(--border); color: var(--text); }
        .hidden { display: none; }
    </style>
</head>
<body>
    <div id="loginSection" class="login-box">
        <h2>管理员登录</h2>
        <div class="error" id="loginError" style="display:none"></div>
        <div class="form-group">
            <label>用户名</label>
            <input type="text" id="adminUsername" placeholder="admin">
        </div>
        <div class="form-group">
            <label>密码</label>
            <input type="password" id="adminPassword" placeholder="密码">
        </div>
        <button class="btn-add" style="width:100%" onclick="adminLogin()">登录</button>
    </div>

    <div id="adminSection" class="hidden">
        <div class="header">
            <h1>管理后台</h1>
            <span class="logout-btn" onclick="adminLogout()">退出</span>
        </div>

        <div class="card">
            <div class="card-title">
                <span>用户管理</span>
                <button class="btn-add" onclick="showAddUserModal()">+ 添加用户</button>
            </div>
            <table>
                <thead>
                    <tr>
                        <th>ID</th>
                        <th>邮箱</th>
                        <th>授权到期</th>
                        <th>状态</th>
                        <th>操作</th>
                    </tr>
                </thead>
                <tbody id="userTableBody"></tbody>
            </table>
        </div>

        <div class="card">
            <div class="card-title">系统状态</div>
            <div id="systemStatus">加载中...</div>
        </div>
    </div>

    <div class="modal" id="addUserModal">
        <div class="modal-box">
            <div class="modal-title">添加用户</div>
            <div class="form-group">
                <label>邮箱</label>
                <input type="email" id="newUserEmail" placeholder="user@example.com">
            </div>
            <div class="form-group">
                <label>密码</label>
                <input type="password" id="newUserPassword" placeholder="初始密码">
            </div>
            <div class="form-group">
                <label>授权天数</label>
                <input type="number" id="newUserDays" value="30" placeholder="30">
            </div>
            <div class="modal-btns">
                <button class="btn-secondary" onclick="closeAddUserModal()">取消</button>
                <button class="btn-primary" onclick="addUser()">添加</button>
            </div>
        </div>
    </div>

    <script>
        let adminToken = localStorage.getItem('admin_token');

        async function adminRequest(method, path, body = null) {
            const headers = { 'Content-Type': 'application/json' };
            if (adminToken) headers['Authorization'] = 'Bearer ' + adminToken;
            const options = { method, headers };
            if (body) options.body = JSON.stringify(body);
            return fetch(path, options);
        }

        async function adminLogin() {
            const username = document.getElementById('adminUsername').value.trim();
            const password = document.getElementById('adminPassword').value;
            const errorEl = document.getElementById('loginError');

            try {
                const res = await fetch('/api/admin/login', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ username, password })
                });
                const data = await res.json();

                if (!res.ok) {
                    errorEl.textContent = data.detail || '登录失败';
                    errorEl.style.display = 'block';
                    return;
                }

                adminToken = data.access_token;
                localStorage.setItem('admin_token', adminToken);
                showAdminPanel();
            } catch (e) {
                errorEl.textContent = '网络错误';
                errorEl.style.display = 'block';
            }
        }

        function adminLogout() {
            localStorage.removeItem('admin_token');
            adminToken = null;
            document.getElementById('adminSection').classList.add('hidden');
            document.getElementById('loginSection').classList.remove('hidden');
        }

        async function showAdminPanel() {
            document.getElementById('loginSection').classList.add('hidden');
            document.getElementById('adminSection').classList.remove('hidden');
            await loadUsers();
            await loadSystemStatus();
        }

        async function loadUsers() {
            try {
                const res = await adminRequest('GET', '/api/admin/users');
                const users = await res.json();
                const tbody = document.getElementById('userTableBody');

                tbody.innerHTML = users.map(u => {
                    const expires = new Date(u.auth_expires_at).toLocaleDateString('zh-CN');
                    const statusClass = u.is_active ? 'status-active' : 'status-inactive';
                    const statusText = u.is_active ? '正常' : '已禁用';

                    return '<tr>' +
                        '<td>' + u.id + '</td>' +
                        '<td>' + u.email + '</td>' +
                        '<td>' + expires + '</td>' +
                        '<td class="' + statusClass + '">' + statusText + '</td>' +
                        '<td class="actions">' +
                            '<button class="btn-edit" onclick="extendUser(' + u.id + ')">续期</button>' +
                            (u.is_active ?
                                '<button class="btn-disable" onclick="disableUser(' + u.id + ')">禁用</button>' :
                                '<button class="btn-enable" onclick="enableUser(' + u.id + ')">启用</button>'
                            ) +
                        '</td>' +
                    '</tr>';
                }).join('');
            } catch (e) {
                console.error(e);
            }
        }

        async function loadSystemStatus() {
            try {
                const res = await fetch('/api/supervisor/status');
                const status = await res.json();
                document.getElementById('systemStatus').innerHTML =
                    '<p>代理系统: ' + (status.running ? '运行中' : '已停止') + '</p>' +
                    '<p>活跃代理: ' + status.active_agents + ' 个</p>';
            } catch (e) {
                document.getElementById('systemStatus').textContent = '获取状态失败';
            }
        }

        function showAddUserModal() {
            document.getElementById('addUserModal').classList.add('show');
        }

        function closeAddUserModal() {
            document.getElementById('addUserModal').classList.remove('show');
            document.getElementById('newUserEmail').value = '';
            document.getElementById('newUserPassword').value = '';
            document.getElementById('newUserDays').value = '30';
        }

        async function addUser() {
            const email = document.getElementById('newUserEmail').value.trim();
            const password = document.getElementById('newUserPassword').value;
            const days = parseInt(document.getElementById('newUserDays').value) || 30;

            if (!email || !password) {
                alert('请填写邮箱和密码');
                return;
            }

            try {
                const res = await adminRequest('POST', '/api/admin/users', {
                    email, password, auth_days: days
                });

                if (!res.ok) {
                    const data = await res.json();
                    alert(data.detail || '添加失败');
                    return;
                }

                closeAddUserModal();
                await loadUsers();
            } catch (e) {
                alert('网络错误');
            }
        }

        async function extendUser(id) {
            const days = prompt('续期天数:', '30');
            if (!days) return;

            await adminRequest('POST', '/api/admin/users/' + id + '/extend', { days: parseInt(days) });
            await loadUsers();
        }

        async function disableUser(id) {
            if (!confirm('确定禁用此用户？')) return;
            await adminRequest('POST', '/api/admin/users/' + id + '/disable');
            await loadUsers();
        }

        async function enableUser(id) {
            await adminRequest('PATCH', '/api/admin/users/' + id, { is_active: true });
            await loadUsers();
        }

        // 检查登录状态
        if (adminToken) {
            showAdminPanel();
        }
    </script>
</body>
</html>
"""


if __name__ == "__main__":
    print("=" * 50)
    print("  Claude Mobile Gateway v10")
    print("=" * 50)
    print(f"  端口: {GATEWAY_PORT} (对外服务)")
    print(f"  Core: {CORE_URL}")
    print("=" * 50)
    uvicorn.run(app, host="0.0.0.0", port=GATEWAY_PORT)
