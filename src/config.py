# -*- coding: utf-8 -*-
"""
WebCoder - 统一配置管理
支持多个AI提供商：Claude, Kimi, Codex
"""

import os
from pathlib import Path

# ============== 基础路径 ==============
BASE_DIR = Path(__file__).parent
DATA_DIR = Path(os.environ.get("DATA_DIR", os.path.expanduser("~/.webcoder")))
DATA_DIR.mkdir(parents=True, exist_ok=True)

PROJECTS_BASE_DIR = Path(os.environ.get("PROJECTS_BASE_DIR", "/var/lib/webcoder/projects"))
PROJECTS_BASE_DIR.mkdir(parents=True, exist_ok=True)

# ============== 服务端口 ==============
CORE_PORT = int(os.environ.get("CORE_PORT", "3001"))
GATEWAY_PORT = int(os.environ.get("GATEWAY_PORT", "3000"))

CORE_URL = os.environ.get("CORE_URL", f"http://127.0.0.1:{CORE_PORT}")
WEBHOOK_URL = os.environ.get("WEBHOOK_URL", f"http://127.0.0.1:{GATEWAY_PORT}/webhook")

# ============== JWT 配置 ==============
JWT_SECRET_KEY = os.environ.get("JWT_SECRET_KEY", "change-me-in-production")
JWT_ALGORITHM = "HS256"
JWT_ACCESS_TOKEN_EXPIRE_MINUTES = 15
JWT_REFRESH_TOKEN_EXPIRE_DAYS = 7

# ============== AI 提供商配置 ==============
CLAUDE_API_KEY = os.environ.get("CLAUDE_API_KEY", "")
CLAUDE_API_URL = os.environ.get("CLAUDE_API_URL", "https://api.anthropic.com")

KIMI_API_KEY = os.environ.get("KIMI_API_KEY", "")
KIMI_API_URL = os.environ.get("KIMI_API_URL", "https://api.moonshot.cn/v1")
KIMI_MODEL = os.environ.get("KIMI_MODEL", "kimi-latest")

CODEX_API_KEY = os.environ.get("CODEX_API_KEY", "")
CODEX_API_URL = os.environ.get("CODEX_API_URL", "https://api.openai.com/v1")

DEFAULT_AI_PROVIDER = os.environ.get("DEFAULT_AI_PROVIDER", "kimi")

PROVIDER_CONFIGS = {
    "claude": {
        "command": "claude",
        "env_vars": {
            "ANTHROPIC_API_KEY": CLAUDE_API_KEY,
            "ANTHROPIC_BASE_URL": CLAUDE_API_URL
        },
        "params": {
            "continue": "-c",
            "skip_permissions": "--dangerously-skip-permissions"
        },
        "display_name": "Claude Code",
        "description": "Anthropic Claude Code"
    },
    "kimi": {
        "command": "kimi",
        "env_vars": {
            "KIMI_API_KEY": KIMI_API_KEY,
            "OPENAI_API_KEY": KIMI_API_KEY,
            "OPENAI_BASE_URL": KIMI_API_URL
        },
        "params": {
            "continue": "--continue",
            "skip_permissions": "--yolo"
        },
        "display_name": "Kimi Code",
        "description": "Moonshot AI Kimi"
    },
    "codex": {
        "command": "codex",
        "env_vars": {
            "OPENAI_API_KEY": CODEX_API_KEY,
            "OPENAI_API_BASE": CODEX_API_URL
        },
        "params": {
            "continue": "--continue",
            "skip_permissions": "--auto-approve"
        },
        "display_name": "OpenAI Codex",
        "description": "OpenAI Codex"
    }
}