# -*- coding: utf-8 -*-
"""
Git 版本管理服务
"""

import os
import asyncio
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from db import crud


async def run_git_command(project_dir: str, *args) -> tuple:
    """
    在项目目录运行 git 命令

    Returns:
        (success: bool, output: str)
    """
    try:
        process = await asyncio.create_subprocess_exec(
            "git", *args,
            cwd=project_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        output = stdout.decode('utf-8', errors='ignore') + stderr.decode('utf-8', errors='ignore')
        return process.returncode == 0, output.strip()
    except Exception as e:
        return False, str(e)


async def init_git_repo(project_dir: str) -> tuple:
    """初始化 Git 仓库"""
    git_dir = os.path.join(project_dir, ".git")
    if os.path.exists(git_dir):
        return True, "仓库已存在"

    success, output = await run_git_command(project_dir, "init")
    if success:
        # 创建 .gitignore
        gitignore_path = os.path.join(project_dir, ".gitignore")
        if not os.path.exists(gitignore_path):
            with open(gitignore_path, 'w') as f:
                f.write("""# Claude Mobile Auto Generated
__pycache__/
*.pyc
.env
.venv/
node_modules/
.DS_Store
*.log
""")
        # 初始提交
        await run_git_command(project_dir, "add", "-A")
        await run_git_command(project_dir, "commit", "-m", "Initial commit by Claude Mobile")

    return success, output


async def get_repo_status(project_dir: str) -> Dict:
    """获取仓库状态"""
    result = {
        "initialized": False,
        "has_changes": False,
        "branch": None,
        "remote": None,
        "ahead": 0,
        "behind": 0,
        "modified_files": [],
        "untracked_files": []
    }

    git_dir = os.path.join(project_dir, ".git")
    if not os.path.exists(git_dir):
        return result

    result["initialized"] = True

    # 获取当前分支
    success, branch = await run_git_command(project_dir, "branch", "--show-current")
    if success:
        result["branch"] = branch

    # 获取远程地址
    success, remote = await run_git_command(project_dir, "remote", "get-url", "origin")
    if success:
        result["remote"] = remote

    # 获取状态
    success, status = await run_git_command(project_dir, "status", "--porcelain")
    if success and status:
        lines = status.split('\n')
        for line in lines:
            if line:
                status_code = line[:2]
                file_path = line[3:]
                if '?' in status_code:
                    result["untracked_files"].append(file_path)
                else:
                    result["modified_files"].append(file_path)
        result["has_changes"] = len(result["modified_files"]) > 0 or len(result["untracked_files"]) > 0

    return result


async def auto_commit(project_id: int, project_dir: str, message: str = None) -> tuple:
    """
    自动提交变更

    Returns:
        (success: bool, message: str, commit_hash: str or None)
    """
    status = await get_repo_status(project_dir)

    if not status["initialized"]:
        # 初始化仓库
        success, output = await init_git_repo(project_dir)
        if not success:
            return False, f"初始化失败: {output}", None

    if not status["has_changes"]:
        return True, "没有变更需要提交", None

    # 添加所有变更
    success, output = await run_git_command(project_dir, "add", "-A")
    if not success:
        return False, f"添加文件失败: {output}", None

    # 生成提交信息
    if not message:
        now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        modified = len(status["modified_files"])
        untracked = len(status["untracked_files"])
        message = f"Auto commit by Claude Mobile [{now}]\n\nModified: {modified}, New: {untracked}"

    # 提交
    success, output = await run_git_command(project_dir, "commit", "-m", message)
    if not success:
        return False, f"提交失败: {output}", None

    # 获取 commit hash
    success, commit_hash = await run_git_command(project_dir, "rev-parse", "HEAD")

    # 更新数据库
    await crud.update_last_commit(project_id)

    return True, "提交成功", commit_hash.strip() if success else None


async def set_remote(project_dir: str, remote_url: str, remote_name: str = "origin") -> tuple:
    """设置远程仓库"""
    # 检查是否已有远程
    success, existing = await run_git_command(project_dir, "remote", "get-url", remote_name)
    if success:
        # 更新远程
        return await run_git_command(project_dir, "remote", "set-url", remote_name, remote_url)
    else:
        # 添加远程
        return await run_git_command(project_dir, "remote", "add", remote_name, remote_url)


async def push_to_remote(project_id: int, project_dir: str, branch: str = None, force: bool = False) -> tuple:
    """
    推送到远程仓库

    Returns:
        (success: bool, message: str)
    """
    # 获取当前分支
    if not branch:
        success, branch = await run_git_command(project_dir, "branch", "--show-current")
        if not success or not branch:
            branch = "main"

    args = ["push", "-u", "origin", branch]
    if force:
        args.insert(1, "--force")

    success, output = await run_git_command(project_dir, *args)

    if success:
        await crud.update_last_push(project_id)
        return True, "推送成功"
    else:
        return False, f"推送失败: {output}"


async def pull_from_remote(project_dir: str, branch: str = None) -> tuple:
    """从远程拉取"""
    if branch:
        return await run_git_command(project_dir, "pull", "origin", branch)
    return await run_git_command(project_dir, "pull")


async def get_commit_history(project_dir: str, limit: int = 20) -> List[Dict]:
    """获取提交历史"""
    success, output = await run_git_command(
        project_dir, "log",
        f"--max-count={limit}",
        "--pretty=format:%H|%an|%ae|%at|%s"
    )

    if not success or not output:
        return []

    commits = []
    for line in output.split('\n'):
        if line:
            parts = line.split('|')
            if len(parts) >= 5:
                commits.append({
                    "hash": parts[0],
                    "author": parts[1],
                    "email": parts[2],
                    "timestamp": int(parts[3]),
                    "message": parts[4]
                })
    return commits


async def get_file_diff(project_dir: str, file_path: str = None) -> str:
    """获取文件差异"""
    if file_path:
        success, output = await run_git_command(project_dir, "diff", "--", file_path)
    else:
        success, output = await run_git_command(project_dir, "diff")

    return output if success else ""


class GitAutoCommitWorker:
    """
    [已弃用] Git 自动提交服务

    ⚠️ 此类已弃用，Git 版本管理功能已集成到 ProjectAgent 心跳中。
    保留此类仅供参考，不再使用。

    新的 Git 管理逻辑在 agents/project_agent.py 的 _handle_git_operations 方法中，
    基于环节变化和文件变更综合判断是否提交。
    """

    def __init__(self):
        self.running = False
        self.task = None
        self.project_snapshots = {}  # project_id -> {files: set, last_change: datetime}
        self.debounce_seconds = 10   # 防抖时间：10秒无新变更后提交

    async def start(self):
        """启动后台任务"""
        if self.running:
            return
        self.running = True
        self.task = asyncio.create_task(self._worker_loop())
        print("[GitWorker] 自动提交服务已启动（基于文件变更检测）")

    async def stop(self):
        """停止后台任务"""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        print("[GitWorker] 自动提交服务已停止")

    def _get_file_snapshot(self, project_dir: str) -> dict:
        """获取目录文件快照（文件路径 -> 修改时间）"""
        snapshot = {}
        try:
            for root, dirs, files in os.walk(project_dir):
                # 跳过 .git 目录
                dirs[:] = [d for d in dirs if d != '.git']
                for name in files:
                    file_path = os.path.join(root, name)
                    try:
                        mtime = os.path.getmtime(file_path)
                        rel_path = os.path.relpath(file_path, project_dir)
                        snapshot[rel_path] = mtime
                    except:
                        pass
        except Exception as e:
            print(f"[GitWorker] 获取快照失败: {e}")
        return snapshot

    def _detect_changes(self, project_id: int, project_dir: str) -> bool:
        """检测文件是否有变更"""
        current_snapshot = self._get_file_snapshot(project_dir)

        if project_id not in self.project_snapshots:
            # 首次检测，记录快照，不视为变更
            self.project_snapshots[project_id] = {
                'snapshot': current_snapshot,
                'last_change': None,
                'pending_commit': False
            }
            return False

        old_data = self.project_snapshots[project_id]
        old_snapshot = old_data['snapshot']

        # 比较快照
        has_changes = False

        # 检查新增或修改的文件
        for path, mtime in current_snapshot.items():
            if path not in old_snapshot or old_snapshot[path] != mtime:
                has_changes = True
                break

        # 检查删除的文件
        if not has_changes:
            for path in old_snapshot:
                if path not in current_snapshot:
                    has_changes = True
                    break

        if has_changes:
            # 记录变更时间，标记待提交
            self.project_snapshots[project_id] = {
                'snapshot': current_snapshot,
                'last_change': datetime.now(),
                'pending_commit': True
            }

        return has_changes

    def _should_commit(self, project_id: int) -> bool:
        """判断是否应该提交（防抖检查）"""
        if project_id not in self.project_snapshots:
            return False

        data = self.project_snapshots[project_id]
        if not data.get('pending_commit') or not data.get('last_change'):
            return False

        # 检查是否过了防抖时间
        elapsed = (datetime.now() - data['last_change']).total_seconds()
        return elapsed >= self.debounce_seconds

    async def _worker_loop(self):
        """后台循环 - 每5秒检查一次"""
        while self.running:
            try:
                await self._check_and_commit()
            except Exception as e:
                print(f"[GitWorker] 错误: {e}")

            await asyncio.sleep(5)  # 每5秒检查一次变更

    async def _check_and_commit(self):
        """检查变更并提交"""
        projects = await crud.get_projects_with_auto_commit()

        for project in projects:
            try:
                project_dir = project.get("project_dir")
                project_id = project.get("id")

                if not project_dir or not os.path.exists(project_dir):
                    continue

                # 检测文件变更
                self._detect_changes(project_id, project_dir)

                # 检查是否应该提交（防抖后）
                if not self._should_commit(project_id):
                    continue

                # 执行自动提交
                success, message, commit_hash = await auto_commit(project_id, project_dir)

                if success and commit_hash:
                    print(f"[GitWorker] 项目 {project_id} 检测到变更，自动提交: {commit_hash[:8]}")

                    # 标记已提交
                    if project_id in self.project_snapshots:
                        self.project_snapshots[project_id]['pending_commit'] = False

                    # 检查是否需要自动推送
                    if project.get("auto_push") and project.get("remote_url"):
                        push_success, push_msg = await push_to_remote(project_id, project_dir)
                        if push_success:
                            print(f"[GitWorker] 项目 {project_id} 自动推送成功")
                        else:
                            print(f"[GitWorker] 项目 {project_id} 自动推送失败: {push_msg}")
                elif success:
                    # 没有变更需要提交
                    if project_id in self.project_snapshots:
                        self.project_snapshots[project_id]['pending_commit'] = False

            except Exception as e:
                print(f"[GitWorker] 处理项目 {project.get('id')} 失败: {e}")


# 全局实例
git_worker = GitAutoCommitWorker()
