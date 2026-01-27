# -*- coding: utf-8 -*-
"""
文件管理服务
"""

import os
import shutil
import mimetypes
from datetime import datetime
from pathlib import Path
from typing import List, Dict, Optional


def get_file_info(path: str) -> Dict:
    """获取文件/目录信息"""
    stat = os.stat(path)
    name = os.path.basename(path)
    is_dir = os.path.isdir(path)

    info = {
        "name": name,
        "path": path,
        "is_dir": is_dir,
        "size": stat.st_size if not is_dir else 0,
        "modified_at": datetime.fromtimestamp(stat.st_mtime).isoformat(),
        "created_at": datetime.fromtimestamp(stat.st_ctime).isoformat()
    }

    if not is_dir:
        info["extension"] = os.path.splitext(name)[1].lower()
        info["mime_type"] = mimetypes.guess_type(path)[0]

    return info


def list_directory(project_dir: str, sub_path: str = "") -> Dict:
    """
    列出目录内容

    Args:
        project_dir: 项目根目录
        sub_path: 子路径

    Returns:
        {
            "path": 当前路径,
            "items": [文件/目录列表],
            "parent": 父目录路径或 None
        }
    """
    # 安全检查：确保路径在项目目录内
    full_path = os.path.normpath(os.path.join(project_dir, sub_path))
    if not full_path.startswith(os.path.normpath(project_dir)):
        raise ValueError("路径越界")

    if not os.path.exists(full_path):
        raise FileNotFoundError("路径不存在")

    if not os.path.isdir(full_path):
        raise ValueError("路径不是目录")

    items = []
    for name in os.listdir(full_path):
        # 跳过隐藏文件（可选）
        if name.startswith('.'):
            continue

        item_path = os.path.join(full_path, name)
        try:
            items.append(get_file_info(item_path))
        except:
            continue

    # 排序：目录在前，然后按名称
    items.sort(key=lambda x: (not x["is_dir"], x["name"].lower()))

    # 计算父目录
    parent = None
    if sub_path:
        parent = os.path.dirname(sub_path)

    return {
        "path": sub_path,
        "items": items,
        "parent": parent
    }


def read_file(project_dir: str, file_path: str, max_size: int = 1024 * 1024) -> Dict:
    """
    读取文件内容

    Args:
        project_dir: 项目根目录
        file_path: 相对路径
        max_size: 最大读取大小 (默认 1MB)

    Returns:
        {
            "content": 文件内容,
            "encoding": 编码,
            "truncated": 是否截断,
            "size": 文件大小
        }
    """
    full_path = os.path.normpath(os.path.join(project_dir, file_path))
    if not full_path.startswith(os.path.normpath(project_dir)):
        raise ValueError("路径越界")

    if not os.path.exists(full_path):
        raise FileNotFoundError("文件不存在")

    if os.path.isdir(full_path):
        raise ValueError("路径是目录，不是文件")

    file_size = os.path.getsize(full_path)
    truncated = file_size > max_size

    # 尝试不同编码读取
    encodings = ['utf-8', 'gbk', 'gb2312', 'latin-1']
    content = None

    for encoding in encodings:
        try:
            with open(full_path, 'r', encoding=encoding) as f:
                if truncated:
                    content = f.read(max_size)
                else:
                    content = f.read()
            return {
                "content": content,
                "encoding": encoding,
                "truncated": truncated,
                "size": file_size
            }
        except UnicodeDecodeError:
            continue

    # 如果都失败，按二进制读取
    with open(full_path, 'rb') as f:
        if truncated:
            binary = f.read(max_size)
        else:
            binary = f.read()

    return {
        "content": binary.hex(),
        "encoding": "binary",
        "truncated": truncated,
        "size": file_size
    }


def write_file(project_dir: str, file_path: str, content: str) -> Dict:
    """
    写入文件

    Args:
        project_dir: 项目根目录
        file_path: 相对路径
        content: 文件内容

    Returns:
        {"success": bool, "size": int}
    """
    full_path = os.path.normpath(os.path.join(project_dir, file_path))
    if not full_path.startswith(os.path.normpath(project_dir)):
        raise ValueError("路径越界")

    # 确保目录存在
    os.makedirs(os.path.dirname(full_path), exist_ok=True)

    with open(full_path, 'w', encoding='utf-8') as f:
        f.write(content)

    return {
        "success": True,
        "size": len(content.encode('utf-8'))
    }


def create_directory(project_dir: str, dir_path: str) -> Dict:
    """创建目录"""
    full_path = os.path.normpath(os.path.join(project_dir, dir_path))
    if not full_path.startswith(os.path.normpath(project_dir)):
        raise ValueError("路径越界")

    os.makedirs(full_path, exist_ok=True)
    return {"success": True, "path": dir_path}


def delete_item(project_dir: str, item_path: str) -> Dict:
    """
    删除文件或目录

    Args:
        project_dir: 项目根目录
        item_path: 相对路径

    Returns:
        {"success": bool}
    """
    full_path = os.path.normpath(os.path.join(project_dir, item_path))
    if not full_path.startswith(os.path.normpath(project_dir)):
        raise ValueError("路径越界")

    if not os.path.exists(full_path):
        raise FileNotFoundError("路径不存在")

    if os.path.isdir(full_path):
        shutil.rmtree(full_path)
    else:
        os.remove(full_path)

    return {"success": True}


def rename_item(project_dir: str, old_path: str, new_name: str) -> Dict:
    """重命名文件或目录"""
    old_full = os.path.normpath(os.path.join(project_dir, old_path))
    if not old_full.startswith(os.path.normpath(project_dir)):
        raise ValueError("路径越界")

    if not os.path.exists(old_full):
        raise FileNotFoundError("路径不存在")

    new_full = os.path.join(os.path.dirname(old_full), new_name)
    if not new_full.startswith(os.path.normpath(project_dir)):
        raise ValueError("路径越界")

    os.rename(old_full, new_full)

    return {
        "success": True,
        "new_path": os.path.relpath(new_full, project_dir)
    }


def copy_item(project_dir: str, src_path: str, dest_path: str) -> Dict:
    """复制文件或目录"""
    src_full = os.path.normpath(os.path.join(project_dir, src_path))
    dest_full = os.path.normpath(os.path.join(project_dir, dest_path))

    if not src_full.startswith(os.path.normpath(project_dir)):
        raise ValueError("源路径越界")
    if not dest_full.startswith(os.path.normpath(project_dir)):
        raise ValueError("目标路径越界")

    if not os.path.exists(src_full):
        raise FileNotFoundError("源路径不存在")

    if os.path.isdir(src_full):
        shutil.copytree(src_full, dest_full)
    else:
        os.makedirs(os.path.dirname(dest_full), exist_ok=True)
        shutil.copy2(src_full, dest_full)

    return {"success": True, "dest_path": dest_path}


def move_item(project_dir: str, src_path: str, dest_path: str) -> Dict:
    """移动文件或目录"""
    src_full = os.path.normpath(os.path.join(project_dir, src_path))
    dest_full = os.path.normpath(os.path.join(project_dir, dest_path))

    if not src_full.startswith(os.path.normpath(project_dir)):
        raise ValueError("源路径越界")
    if not dest_full.startswith(os.path.normpath(project_dir)):
        raise ValueError("目标路径越界")

    if not os.path.exists(src_full):
        raise FileNotFoundError("源路径不存在")

    os.makedirs(os.path.dirname(dest_full), exist_ok=True)
    shutil.move(src_full, dest_full)

    return {"success": True, "dest_path": dest_path}


def search_files(project_dir: str, pattern: str, max_results: int = 100) -> List[Dict]:
    """
    搜索文件

    Args:
        project_dir: 项目根目录
        pattern: 搜索模式（文件名包含）
        max_results: 最大结果数

    Returns:
        匹配的文件列表
    """
    results = []
    pattern_lower = pattern.lower()

    for root, dirs, files in os.walk(project_dir):
        # 跳过隐藏目录
        dirs[:] = [d for d in dirs if not d.startswith('.')]

        for name in files:
            if pattern_lower in name.lower():
                full_path = os.path.join(root, name)
                rel_path = os.path.relpath(full_path, project_dir)
                results.append({
                    "name": name,
                    "path": rel_path,
                    "size": os.path.getsize(full_path)
                })

                if len(results) >= max_results:
                    return results

    return results


def get_directory_tree(project_dir: str, max_depth: int = 3) -> Dict:
    """
    获取目录树

    Args:
        project_dir: 项目根目录
        max_depth: 最大深度

    Returns:
        目录树结构
    """
    def build_tree(path: str, depth: int) -> Dict:
        name = os.path.basename(path) or path
        node = {
            "name": name,
            "is_dir": os.path.isdir(path),
            "children": []
        }

        if node["is_dir"] and depth < max_depth:
            try:
                for item in sorted(os.listdir(path)):
                    if item.startswith('.'):
                        continue
                    item_path = os.path.join(path, item)
                    node["children"].append(build_tree(item_path, depth + 1))
            except PermissionError:
                pass

        return node

    return build_tree(project_dir, 0)
