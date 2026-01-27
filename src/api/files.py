# -*- coding: utf-8 -*-
"""
文件管理和 Git API
"""

import os
import shutil
import mimetypes
from fastapi import APIRouter, HTTPException, status, Depends, UploadFile, File, Form
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel
from typing import Optional, List
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).parent.parent))
from auth.dependencies import get_current_user
from db import crud
from services import file_service
from services.git_service import (
    init_git_repo,
    get_repo_status,
    auto_commit,
    set_remote,
    push_to_remote,
    pull_from_remote,
    get_commit_history,
    get_file_diff
)

router = APIRouter()

# ============================================================
# 请求模型
# ============================================================

class WriteFileRequest(BaseModel):
    path: str
    content: str

class CreateDirRequest(BaseModel):
    path: str

class RenameRequest(BaseModel):
    old_path: str
    new_name: str

class MoveRequest(BaseModel):
    src_path: str
    dest_path: str

class GitConfigRequest(BaseModel):
    remote_url: Optional[str] = None
    remote_type: Optional[str] = "github"
    branch: Optional[str] = "main"
    auto_commit: Optional[bool] = True
    auto_push: Optional[bool] = False
    commit_interval: Optional[int] = 300

class CommitRequest(BaseModel):
    message: Optional[str] = None

# ============================================================
# 文件管理 API
# ============================================================

@router.get("/{project_id}/files", summary="列出目录")
async def list_files(
    project_id: int,
    path: str = "",
    user: dict = Depends(get_current_user)
):
    """列出项目目录内容"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = file_service.list_directory(project["project_dir"], path)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="路径不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/files/read", summary="读取文件")
async def read_file(
    project_id: int,
    path: str,
    user: dict = Depends(get_current_user)
):
    """读取文件内容"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = file_service.read_file(project["project_dir"], path)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="文件不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/files/write", summary="写入文件")
async def write_file(
    project_id: int,
    req: WriteFileRequest,
    user: dict = Depends(get_current_user)
):
    """写入文件内容"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = file_service.write_file(project["project_dir"], req.path, req.content)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/files/mkdir", summary="创建目录")
async def create_directory(
    project_id: int,
    req: CreateDirRequest,
    user: dict = Depends(get_current_user)
):
    """创建目录"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = file_service.create_directory(project["project_dir"], req.path)
        return result
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.delete("/{project_id}/files", summary="删除文件/目录")
async def delete_file(
    project_id: int,
    path: str,
    user: dict = Depends(get_current_user)
):
    """删除文件或目录"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = file_service.delete_item(project["project_dir"], path)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="路径不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/files/rename", summary="重命名")
async def rename_file(
    project_id: int,
    req: RenameRequest,
    user: dict = Depends(get_current_user)
):
    """重命名文件或目录"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = file_service.rename_item(project["project_dir"], req.old_path, req.new_name)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="路径不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.post("/{project_id}/files/move", summary="移动文件")
async def move_file(
    project_id: int,
    req: MoveRequest,
    user: dict = Depends(get_current_user)
):
    """移动文件或目录"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    try:
        result = file_service.move_item(project["project_dir"], req.src_path, req.dest_path)
        return result
    except FileNotFoundError:
        raise HTTPException(status_code=404, detail="路径不存在")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@router.get("/{project_id}/files/search", summary="搜索文件")
async def search_files(
    project_id: int,
    q: str,
    user: dict = Depends(get_current_user)
):
    """搜索文件"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    results = file_service.search_files(project["project_dir"], q)
    return {"results": results}


@router.get("/{project_id}/files/tree", summary="目录树")
async def get_tree(
    project_id: int,
    depth: int = 3,
    user: dict = Depends(get_current_user)
):
    """获取目录树"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    tree = file_service.get_directory_tree(project["project_dir"], depth)
    return tree


@router.post("/{project_id}/files/upload", summary="上传文件")
async def upload_file(
    project_id: int,
    path: str = Form(""),
    file: UploadFile = File(...),
    user: dict = Depends(get_current_user)
):
    """
    上传文件到项目目录

    - path: 目标目录路径（相对于项目根目录），为空则上传到根目录
    - file: 要上传的文件
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    project_dir = project["project_dir"]

    # 安全检查：确保路径不逃逸
    if path:
        target_dir = os.path.normpath(os.path.join(project_dir, path))
        if not target_dir.startswith(os.path.normpath(project_dir)):
            raise HTTPException(status_code=400, detail="非法路径")
    else:
        target_dir = project_dir

    # 确保目标目录存在
    os.makedirs(target_dir, exist_ok=True)

    # 保存文件
    file_path = os.path.join(target_dir, file.filename)

    try:
        with open(file_path, "wb") as f:
            content = await file.read()
            f.write(content)

        return {
            "success": True,
            "filename": file.filename,
            "path": os.path.relpath(file_path, project_dir),
            "size": len(content)
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"上传失败: {str(e)}")


@router.post("/{project_id}/files/upload-multiple", summary="批量上传文件")
async def upload_multiple_files(
    project_id: int,
    path: str = Form(""),
    files: List[UploadFile] = File(...),
    user: dict = Depends(get_current_user)
):
    """
    批量上传多个文件到项目目录

    - path: 目标目录路径
    - files: 要上传的文件列表
    """
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    project_dir = project["project_dir"]

    # 安全检查
    if path:
        target_dir = os.path.normpath(os.path.join(project_dir, path))
        if not target_dir.startswith(os.path.normpath(project_dir)):
            raise HTTPException(status_code=400, detail="非法路径")
    else:
        target_dir = project_dir

    os.makedirs(target_dir, exist_ok=True)

    results = []
    for file in files:
        file_path = os.path.join(target_dir, file.filename)
        try:
            with open(file_path, "wb") as f:
                content = await file.read()
                f.write(content)
            results.append({
                "filename": file.filename,
                "success": True,
                "path": os.path.relpath(file_path, project_dir),
                "size": len(content)
            })
        except Exception as e:
            results.append({
                "filename": file.filename,
                "success": False,
                "error": str(e)
            })

    return {
        "total": len(files),
        "success_count": sum(1 for r in results if r["success"]),
        "results": results
    }


async def get_user_from_token_or_header(project_id: int, token: str = None, user: dict = None):
    """从 token 参数或 header 获取用户"""
    if user:
        return user

    if token:
        from auth.jwt_handler import decode_token
        try:
            payload = decode_token(token)
            user_id = payload.get("sub")
            user = await crud.get_user_by_id(int(user_id))
            if user:
                return user
        except:
            pass

    raise HTTPException(status_code=401, detail="认证失败")


@router.get("/{project_id}/files/download", summary="下载文件")
async def download_file(
    project_id: int,
    path: str,
    token: str = None
):
    """
    下载项目文件

    - path: 文件路径（相对于项目根目录）
    - token: 认证 token（用于浏览器直接下载）
    """
    user = await get_user_from_token_or_header(project_id, token)

    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    project_dir = project["project_dir"]

    # 安全检查
    file_path = os.path.normpath(os.path.join(project_dir, path))
    if not file_path.startswith(os.path.normpath(project_dir)):
        raise HTTPException(status_code=400, detail="非法路径")

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail="文件不存在")

    if os.path.isdir(file_path):
        raise HTTPException(status_code=400, detail="不能下载目录，请使用打包下载")

    # 获取文件名和 MIME 类型
    filename = os.path.basename(file_path)
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type:
        mime_type = "application/octet-stream"

    return FileResponse(
        path=file_path,
        filename=filename,
        media_type=mime_type
    )


@router.get("/{project_id}/files/download-zip", summary="打包下载目录")
async def download_zip(
    project_id: int,
    path: str = "",
    token: str = None
):
    """
    将目录打包为 ZIP 下载

    - path: 目录路径，为空则打包整个项目
    - token: 认证 token
    """
    import zipfile
    import io

    user = await get_user_from_token_or_header(project_id, token)

    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    project_dir = project["project_dir"]

    # 确定要打包的目录
    if path:
        target_dir = os.path.normpath(os.path.join(project_dir, path))
        if not target_dir.startswith(os.path.normpath(project_dir)):
            raise HTTPException(status_code=400, detail="非法路径")
    else:
        target_dir = project_dir

    if not os.path.exists(target_dir):
        raise HTTPException(status_code=404, detail="目录不存在")

    if not os.path.isdir(target_dir):
        raise HTTPException(status_code=400, detail="路径不是目录")

    # 创建 ZIP 文件到内存
    zip_buffer = io.BytesIO()

    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zip_file:
        for root, dirs, files in os.walk(target_dir):
            # 跳过 .git 目录
            dirs[:] = [d for d in dirs if d != '.git']

            for file in files:
                file_path = os.path.join(root, file)
                arcname = os.path.relpath(file_path, target_dir)
                zip_file.write(file_path, arcname)

    zip_buffer.seek(0)

    # 生成文件名
    zip_filename = f"{project['name']}.zip" if not path else f"{os.path.basename(path)}.zip"

    return StreamingResponse(
        zip_buffer,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="{zip_filename}"'
        }
    )


# ============================================================
# Git API
# ============================================================

@router.get("/{project_id}/git/status", summary="Git 状态")
async def git_status(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """获取 Git 仓库状态"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    status = await get_repo_status(project["project_dir"])
    git_config = await crud.get_git_config(project_id)

    return {
        "status": status,
        "config": git_config
    }


@router.post("/{project_id}/git/init", summary="初始化 Git")
async def git_init(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """初始化 Git 仓库"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    success, message = await init_git_repo(project["project_dir"])

    if success:
        # 创建默认 Git 配置
        existing = await crud.get_git_config(project_id)
        if not existing:
            await crud.create_git_config(project_id)

    return {"success": success, "message": message}


@router.post("/{project_id}/git/config", summary="配置 Git")
async def configure_git(
    project_id: int,
    req: GitConfigRequest,
    user: dict = Depends(get_current_user)
):
    """配置 Git 远程仓库和自动提交"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    # 更新或创建配置
    existing = await crud.get_git_config(project_id)
    config_data = req.dict(exclude_none=True)

    if existing:
        await crud.update_git_config(project_id, **config_data)
    else:
        await crud.create_git_config(project_id, **config_data)

    # 如果有远程地址，设置远程
    if req.remote_url:
        await set_remote(project["project_dir"], req.remote_url)

    return {"success": True}


@router.post("/{project_id}/git/commit", summary="提交更改")
async def git_commit(
    project_id: int,
    req: CommitRequest = CommitRequest(),
    user: dict = Depends(get_current_user)
):
    """提交当前更改"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    success, message, commit_hash = await auto_commit(
        project_id,
        project["project_dir"],
        req.message
    )

    return {
        "success": success,
        "message": message,
        "commit_hash": commit_hash
    }


@router.post("/{project_id}/git/push", summary="推送到远程")
async def git_push(
    project_id: int,
    force: bool = False,
    user: dict = Depends(get_current_user)
):
    """推送到远程仓库"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    git_config = await crud.get_git_config(project_id)
    if not git_config or not git_config.get("remote_url"):
        raise HTTPException(status_code=400, detail="未配置远程仓库")

    success, message = await push_to_remote(
        project_id,
        project["project_dir"],
        git_config.get("branch"),
        force
    )

    return {"success": success, "message": message}


@router.post("/{project_id}/git/pull", summary="从远程拉取")
async def git_pull(
    project_id: int,
    user: dict = Depends(get_current_user)
):
    """从远程拉取更新"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    git_config = await crud.get_git_config(project_id)
    branch = git_config.get("branch") if git_config else None

    success, message = await pull_from_remote(project["project_dir"], branch)

    return {"success": success, "message": message}


@router.get("/{project_id}/git/history", summary="提交历史")
async def git_history(
    project_id: int,
    limit: int = 20,
    user: dict = Depends(get_current_user)
):
    """获取提交历史"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    commits = await get_commit_history(project["project_dir"], limit)

    return {"commits": commits}


@router.get("/{project_id}/git/diff", summary="查看差异")
async def git_diff(
    project_id: int,
    path: str = None,
    user: dict = Depends(get_current_user)
):
    """查看文件差异"""
    project = await crud.get_project_by_id(project_id)
    if not project or project["user_id"] != user["id"]:
        raise HTTPException(status_code=404, detail="项目不存在")

    diff = await get_file_diff(project["project_dir"], path)

    return {"diff": diff}
