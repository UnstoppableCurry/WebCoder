# -*- coding: utf-8 -*-
"""
Claude Code Core Service - 能力层 (v10)
纯粹的 Claude Code 进程管理，无业务逻辑

v10 变更：
- 支持用户目录隔离
- 新增 /input 端点供代理发送指令
- 使用统一配置

职责：
- PTY 进程创建/停止/重启
- WebSocket 终端连接
- 项目目录管理
- 输出 buffer 管理
- 事件 webhook 回调

Port: 3001 (内部服务)
"""

import os
import pty
import select
import asyncio
import fcntl
import struct
import termios
import threading
import json
import httpx
from collections import deque
from datetime import datetime
from typing import Optional, List
from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from contextlib import asynccontextmanager
import uvicorn

from config import (
    PROJECTS_BASE_DIR,
    CLAUDE_API_KEY,
    CLAUDE_API_URL,
    WEBHOOK_URL,
    CORE_PORT,
    AI_PROVIDER,
    PROVIDER_CONFIGS
)

# 确保基础目录存在
os.makedirs(str(PROJECTS_BASE_DIR), exist_ok=True)

# ============== 数据模型 ==============
class ProcessInfo(BaseModel):
    name: str
    running: bool
    pid: Optional[int] = None
    mode: str = "new"
    created: Optional[str] = None
    connections: int = 0
    buffer_size: int = 0

class StartRequest(BaseModel):
    mode: str = "new"  # 新项目默认不带 -c，避免报错
    skip_permissions: bool = True
    user_dir: Optional[str] = None  # v10: 用户目录
    provider: str = AI_PROVIDER  # 新增：指定AI提供商，默认使用全局配置

class InputRequest(BaseModel):
    """v10: 输入请求"""
    input: str

# ============== 进程管理 ==============
processes = {}

def send_webhook(event: str, data: dict):
    """发送事件到 Gateway"""
    try:
        payload = {
            "event": event,
            "data": data,
            "timestamp": datetime.now().isoformat()
        }
        # 异步发送，不阻塞
        threading.Thread(
            target=_send_webhook_sync,
            args=(payload,),
            daemon=True
        ).start()
    except Exception as e:
        print(f"[Webhook] 准备失败: {e}")

def _send_webhook_sync(payload: dict):
    """同步发送 webhook"""
    try:
        with httpx.Client(timeout=5) as client:
            client.post(WEBHOOK_URL, json=payload)
        print(f"[Webhook] 已发送: {payload['event']}")
    except Exception as e:
        print(f"[Webhook] 发送失败: {e}")

def create_process(project_name: str, mode: str = "new",
                   skip_permissions: bool = True, user_dir: str = None,
                   provider: str = AI_PROVIDER):
    """
    创建AI提供商进程 - 支持Claude, Kimi

    v10: 支持 user_dir 参数，实现用户目录隔离
    v11: 支持多AI提供商
    """
    if project_name in processes and processes[project_name]["running"]:
        return processes[project_name]

    # v10: 支持用户目录
    if user_dir:
        project_dir = os.path.join(user_dir, project_name)
    else:
        project_dir = os.path.join(str(PROJECTS_BASE_DIR), project_name)

    os.makedirs(project_dir, exist_ok=True)

    master_fd, slave_fd = pty.openpty()

    pid = os.fork()
    if pid == 0:
        # 子进程
        os.close(master_fd)
        os.setsid()
        fcntl.ioctl(slave_fd, termios.TIOCSCTTY, 0)
        os.dup2(slave_fd, 0)
        os.dup2(slave_fd, 1)
        os.dup2(slave_fd, 2)
        os.close(slave_fd)
        os.chdir(project_dir)

        # 获取AI提供商配置
        provider_config = PROVIDER_CONFIGS.get(provider, PROVIDER_CONFIGS[AI_PROVIDER])
        
        env = os.environ.copy()
        env["TERM"] = "xterm-256color"
        env["COLORTERM"] = "truecolor"
        
        # 设置提供商特定的环境变量
        for key, value in provider_config["env_vars"].items():
            env[key] = value

        # 构建命令参数
        args = [provider_config["command"]]
        
        # 添加模式参数
        if mode == "continue" and "continue" in provider_config["params"]:
            if provider == "kimi":
                # Kimi基于工作目录的MD5哈希值管理会话，每个目录有独立会话空间
                # 检查当前项目目录是否存在Kimi会话数据
                import hashlib
                work_dir_hash = hashlib.md5(project_dir.encode()).hexdigest()
                kimi_session_dir = os.path.join(os.path.expanduser("~"), ".kimi", "sessions", work_dir_hash)
                
                if os.path.exists(kimi_session_dir) and os.listdir(kimi_session_dir):
                    # 存在会话数据，可以安全使用continue
                    args.append(provider_config["params"]["continue"])
                    print(f"[Core] {project_name}: 找到Kimi会话数据，使用continue模式")
                else:
                    # 没有会话数据，回退到new模式
                    print(f"[Core] {project_name}: 未找到Kimi会话数据，自动切换到new模式")
                    # 注意：这里我们不添加continue参数，相当于使用new模式
            else:
                # 其他提供商直接使用continue
                args.append(provider_config["params"]["continue"])
        
        # 添加权限参数
        if skip_permissions and "skip_permissions" in provider_config["params"]:
            args.append(provider_config["params"]["skip_permissions"])
        
        # Kimi参数集成 - 极简配置，信任用户已有配置
        if provider == "kimi":
            # Kimi CLI已经全局配置好，不需要额外复杂参数
            # 完全依赖用户的全局配置，不添加任何额外参数
            pass  # 不需要任何额外参数
        elif provider == "claude":
            # Claude的特定参数处理（如果需要）
            pass
        
        os.execvpe(provider_config["command"], args, env)
    else:
        # 父进程
        os.close(slave_fd)
        flag = fcntl.fcntl(master_fd, fcntl.F_GETFL)
        fcntl.fcntl(master_fd, fcntl.F_SETFL, flag | os.O_NONBLOCK)

        # 创建进程对象
        process = {
            "fd": master_fd,
            "pid": pid,
            "buffer": deque(maxlen=50000),
            "websockets": set(),
            "running": True,
            "mode": mode,
            "user_dir": user_dir,
            "project_dir": project_dir,
            "created": datetime.now().isoformat(),
            "last_output": datetime.now().isoformat(),
            "provider": provider  # v11: 记录AI提供商
        }
        processes[project_name] = process

        # 启动读取线程
        thread = threading.Thread(
            target=read_loop,
            args=(project_name,),
            daemon=True
        )
        thread.start()

        # 确定实际使用的模式（可能被Kimi从continue回退到new）
        actual_mode = mode
        if provider == "kimi" and mode == "continue":
            import hashlib
            work_dir_hash = hashlib.md5(project_dir.encode()).hexdigest()
            kimi_session_dir = os.path.join(os.path.expanduser("~"), ".kimi", "sessions", work_dir_hash)
            if not (os.path.exists(kimi_session_dir) and os.listdir(kimi_session_dir)):
                actual_mode = "new"  # 回退到new模式
        
        # 更新进程的实际模式
        process["mode"] = actual_mode
        
        # 发送启动事件
        send_webhook("process_started", {
            "project": project_name,
            "mode": actual_mode,
            "pid": pid,
            "user_dir": user_dir,
            "provider": provider  # v11: 记录AI提供商
        })

        print(f"[Core] 进程已启动: {project_name} (PID: {pid}, Mode: {actual_mode}, Provider: {provider}, Dir: {project_dir})")
        return process

def stop_process(project_name: str):
    """停止进程"""
    if project_name not in processes:
        return False

    p = processes[project_name]
    p["running"] = False

    try:
        os.close(p["fd"])
    except:
        pass

    try:
        os.kill(p["pid"], 9)
    except:
        pass

    # 发送停止事件
    send_webhook("process_stopped", {
        "project": project_name,
        "pid": p.get("pid")
    })

    del processes[project_name]
    print(f"[Core] 进程已停止: {project_name}")
    return True

def read_loop(project_name: str):
    """后台读取 PTY 输出"""
    process = processes.get(project_name)
    if not process:
        return

    fd = process["fd"]

    while process.get("running", False):
        try:
            ready, _, _ = select.select([fd], [], [], 0.1)
            if ready:
                data = os.read(fd, 4096)
                if data:
                    process["buffer"].append(data)
                    process["last_output"] = datetime.now().isoformat()
                else:
                    # 进程结束
                    process["running"] = False
                    output = b''.join(process["buffer"]).decode('utf-8', errors='ignore')[-3000:]

                    send_webhook("process_finished", {
                        "project": project_name,
                        "output_tail": output
                    })
                    break
        except OSError:
            process["running"] = False
            send_webhook("process_error", {
                "project": project_name,
                "error": "OSError - 进程异常退出"
            })
            break
        except Exception as e:
            print(f"[Core] 读取错误 {project_name}: {e}")

def write_to_process(project_name: str, input_text: str) -> bool:
    """
    v10: 向进程写入输入

    供代理系统使用，发送指令给 Claude Code
    """
    if project_name not in processes:
        return False

    process = processes[project_name]
    if not process.get("running", False):
        return False

    try:
        fd = process["fd"]
        # 添加换行符确保命令被执行
        if not input_text.endswith("\n"):
            input_text += "\n"
        os.write(fd, input_text.encode('utf-8'))
        print(f"[Core] 已写入 {project_name}: {input_text[:50]}...")
        return True
    except Exception as e:
        print(f"[Core] 写入失败 {project_name}: {e}")
        return False

def set_pty_size(fd: int, rows: int, cols: int):
    """设置终端大小"""
    try:
        winsize = struct.pack("HHHH", rows, cols, 0, 0)
        fcntl.ioctl(fd, termios.TIOCSWINSZ, winsize)
    except:
        pass

def write_to_pty(fd: int, data: bytes, chunk_size: int = 1024):
    """
    分块写入数据到 PTY，处理大文本粘贴

    Args:
        fd: PTY 文件描述符
        data: 要写入的数据
        chunk_size: 每次写入的块大小（默认 1024 字节）
    """
    if not data:
        return

    offset = 0
    while offset < len(data):
        chunk = data[offset:offset + chunk_size]
        try:
            written = os.write(fd, chunk)
            offset += written
        except BlockingIOError:
            # 缓冲区满，等待一下再重试
            import time
            time.sleep(0.01)
        except Exception as e:
            print(f"[Core] PTY 写入错误: {e}")
            break

def get_all_projects() -> List[dict]:
    """获取所有运行中的项目"""
    result = []

    for name, p in processes.items():
        running = p.get("running", False)
        result.append({
            "name": name,
            "running": running,
            "pid": p.get("pid") if running else None,
            "mode": p.get("mode", "continue"),
            "user_dir": p.get("user_dir"),
            "project_dir": p.get("project_dir"),
            "created": p.get("created"),
            "connections": len(p.get("websockets", set())),
            "buffer_size": len(p.get("buffer", [])),
            "provider": p.get("provider", AI_PROVIDER)  # v11: 记录AI提供商
        })

    return result

# ============== FastAPI ==============
@asynccontextmanager
async def lifespan(app: FastAPI):
    print(f"[Core] 服务启动，基础目录: {PROJECTS_BASE_DIR}")
    yield
    # 清理所有进程
    for name in list(processes.keys()):
        stop_process(name)
    print("[Core] 服务已关闭")

app = FastAPI(title="Claude Code Core v10", lifespan=lifespan)

# 允许跨域（Gateway 调用）
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/health")
async def health():
    """健康检查"""
    return {"status": "ok", "service": "ai-code-core", "version": "v11", "default_provider": AI_PROVIDER}

@app.get("/providers")
async def list_providers():
    """获取支持的AI提供商列表"""
    return {
        "providers": list(PROVIDER_CONFIGS.keys()),
        "default": AI_PROVIDER,
        "configs": {
            name: {
                "command": config["command"],
                "display_name": config.get("display_name", name),
                "description": config.get("description", ""),
                "features": [k.replace("supports_", "") for k, v in config.items() 
                           if k.startswith("supports_") and v]
            }
            for name, config in PROVIDER_CONFIGS.items()
        }
    }

@app.get("/projects")
async def list_projects():
    """获取所有运行中的项目"""
    return get_all_projects()

@app.post("/project/{name}/start")
async def start_project(name: str, req: StartRequest = StartRequest()):
    """启动项目 - 支持指定AI提供商"""
    create_process(name, req.mode, req.skip_permissions, req.user_dir, req.provider)
    return {"status": "ok", "project": name, "provider": req.provider}

@app.post("/project/{name}/stop")
async def api_stop_project(name: str):
    """停止项目"""
    stop_process(name)
    return {"status": "ok"}

@app.post("/project/{name}/restart")
async def restart_project(name: str, req: StartRequest = StartRequest()):
    """重启项目 - 保持原有提供商"""
    # 获取原来的信息
    old_mode = req.mode
    old_user_dir = req.user_dir
    old_provider = req.provider
    if name in processes:
        old_mode = processes[name].get("mode", "continue")
        old_user_dir = processes[name].get("user_dir") or req.user_dir
        old_provider = processes[name].get("provider", AI_PROVIDER)

    stop_process(name)
    create_process(
        name,
        old_mode if req.mode == "continue" else req.mode,
        req.skip_permissions,
        old_user_dir,
        old_provider
    )
    return {"status": "ok", "provider": old_provider}

@app.post("/project/{name}/input")
async def send_input(name: str, req: InputRequest):
    """
    v10: 向项目发送输入

    供代理系统调用，发送指令给 Claude Code
    """
    if name not in processes:
        raise HTTPException(404, "Project not found or not running")

    success = write_to_process(name, req.input)
    if not success:
        raise HTTPException(500, "Failed to send input")

    return {"status": "ok", "message": "Input sent"}

@app.delete("/project/{name}")
async def delete_project(name: str):
    """删除项目目录"""
    stop_process(name)
    # v10: 不再自动删除目录，由 Gateway 控制
    return {"status": "ok"}

@app.get("/project/{name}/status")
async def project_status(name: str):
    """获取项目状态"""
    if name not in processes:
        return {"running": False, "exists": False}

    p = processes[name]
    return {
        "running": p.get("running", False),
        "pid": p.get("pid"),
        "mode": p.get("mode"),
        "user_dir": p.get("user_dir"),
        "project_dir": p.get("project_dir"),
        "connections": len(p.get("websockets", set())),
        "buffer_size": len(p.get("buffer", [])),
        "last_output": p.get("last_output"),
        "exists": True,
        "provider": p.get("provider", AI_PROVIDER)  # v11: 记录AI提供商
    }

@app.get("/project/{name}/output")
async def get_output(name: str, tail: int = 100):
    """
    获取项目输出

    tail: 获取最后 N 条 buffer 块
    返回合并后的完整输出文本
    """
    if name not in processes:
        # 进程不存在时返回空输出，而不是 404
        # 这样代理可以正常处理这种情况
        return {"output": "", "buffer_count": 0, "running": False}

    process = processes[name]
    buffer = list(process["buffer"])[-tail:]
    output = b''.join(buffer).decode('utf-8', errors='ignore')
    return {"output": output, "buffer_count": len(buffer), "running": process.get("running", False)}

@app.websocket("/ws/{project_name}")
async def websocket_endpoint(websocket: WebSocket, project_name: str,
                             mode: str = "new", user_dir: str = None,
                             provider: str = AI_PROVIDER):
    """
    WebSocket 终端连接

    v10: 支持 user_dir 参数
    v11: 支持 provider 参数
    """
    await websocket.accept()

    # 如果进程不存在，创建
    if project_name not in processes or not processes[project_name].get("running", False):
        create_process(project_name, mode, user_dir=user_dir, provider=provider)

    process = processes.get(project_name)
    if not process:
        await websocket.close()
        return
    
    # 记录provider信息到进程数据中
    process["provider"] = provider

    fd = process["fd"]
    process["websockets"].add(websocket)

    # 发送历史 buffer
    try:
        buffer_snapshot = list(process["buffer"])
        if buffer_snapshot:
            history = b''.join(buffer_snapshot)
            await websocket.send_bytes(history)
    except Exception as e:
        print(f"[Core] 发送历史失败: {e}")

    last_seen_idx = len(list(process["buffer"]))
    connected = True

    async def read_from_buffer():
        nonlocal last_seen_idx, connected
        while connected and process.get("running", False):
            await asyncio.sleep(0.02)
            try:
                current_buffer = list(process["buffer"])
                current_len = len(current_buffer)

                if current_len < last_seen_idx:
                    last_seen_idx = 0

                if current_len > last_seen_idx:
                    for chunk in current_buffer[last_seen_idx:]:
                        await websocket.send_bytes(chunk)
                    last_seen_idx = current_len
            except WebSocketDisconnect:
                connected = False
                break
            except Exception:
                connected = False
                break

    async def handle_input():
        nonlocal connected
        while connected and process.get("running", False):
            try:
                data = await asyncio.wait_for(websocket.receive(), timeout=0.1)
                if data["type"] == "websocket.disconnect":
                    connected = False
                    break
                if data["type"] == "websocket.receive":
                    if "bytes" in data:
                        write_to_pty(fd, data["bytes"])
                    elif "text" in data:
                        text = data["text"]
                        if text.startswith("resize:"):
                            parts = text.split(":")
                            if len(parts) == 3:
                                set_pty_size(fd, int(parts[1]), int(parts[2]))
                        else:
                            write_to_pty(fd, text.encode())
            except asyncio.TimeoutError:
                continue
            except WebSocketDisconnect:
                connected = False
                break
            except Exception:
                pass

    try:
        await asyncio.gather(read_from_buffer(), handle_input())
    except:
        pass
    finally:
        connected = False
        process["websockets"].discard(websocket)


if __name__ == "__main__":
    print("=" * 50)
    print(f"  AI Code Core Service v11")
    print(f"  提供商: {AI_PROVIDER}")
    print("=" * 50)
    print(f"  端口: {CORE_PORT} (内部服务)")
    print(f"  基础目录: {PROJECTS_BASE_DIR}")
    print(f"  Webhook: {WEBHOOK_URL}")
    print("=" * 50)
    uvicorn.run(app, host="127.0.0.1", port=CORE_PORT)
