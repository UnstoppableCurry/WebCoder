# 🌐 WebCoder

你的浏览器里的AI编程伙伴。支持多AI提供商（Claude、Kimi、Codex），让编程更高效。

[![Python 3.10+](https://img.shields.io/badge/python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.104+-green.svg)](https://fastapi.tiangolo.com/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## ✨ 功能特性

- 🎯 **多AI提供商支持** - 支持 Claude、Kimi、OpenAI Codex
- 🌐 **Web终端** - 基于 WebSocket 的实时终端交互
- 📱 **移动优先** - 针对移动端优化的界面设计
- 👥 **多用户支持** - 完整的用户认证和授权系统
- 🤖 **智能管家** - 自动任务推进和状态监控
- 📧 **邮件通知** - 关键节点自动邮件提醒
- 🔄 **会话管理** - 支持会话续传和多项目隔离
- 📁 **文件管理** - 内置文件浏览器和编辑器
- 🔒 **安全可靠** - JWT认证、权限控制、输入验证

## 🚀 快速开始

### 环境要求

- Python 3.10+
- Node.js 16+ (用于安装AI CLI工具)
- Linux/macOS/Windows WSL

### 安装AI CLI工具

```bash
# 安装 Claude Code
npm install -g @anthropic-ai/claude-code

# 安装 Kimi CLI
pip install kimi-cli

# 安装 OpenAI Codex (可选)
npm install -g @openai/codex
```

### 安装平台

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/webcoder.git
cd webcoder

# 2. 安装依赖
pip install -r requirements.txt

# 3. 配置环境变量
cp .env.example .env
# 编辑 .env 文件，填写你的API密钥

# 4. 启动服务
./scripts/start.sh
```

访问 http://localhost:3000 开始使用！

## 📖 文档

- [安装指南](docs/installation.md)
- [配置说明](docs/configuration.md)
- [API文档](docs/api.md)
- [部署教程](docs/deployment.md)
- [贡献指南](CONTRIBUTING.md)

## 🏗️ 项目结构

```
webcoder/
├── src/
│   ├── core/           # 核心服务 - AI进程管理
│   ├── gateway/        # 网关服务 - Web/API
│   ├── agents/         # 智能代理系统
│   ├── services/       # 业务服务
│   ├── api/            # API路由
│   ├── auth/           # 认证模块
│   ├── db/             # 数据库模块
│   └── config.py       # 配置文件
├── docs/               # 文档
├── scripts/            # 启动脚本
├── tests/              # 测试
├── docker/             # Docker配置
└── requirements.txt    # 依赖
```

## 🔧 配置说明

### 环境变量

| 变量名 | 说明 | 默认值 |
|--------|------|--------|
| `DEFAULT_AI_PROVIDER` | 默认AI提供商 | `kimi` |
| `KIMI_API_KEY` | Kimi API密钥 | - |
| `CLAUDE_API_KEY` | Claude API密钥 | - |
| `CODEX_API_KEY` | Codex API密钥 | - |
| `JWT_SECRET_KEY` | JWT密钥 | - |
| `EMAIL_ENABLED` | 启用邮件通知 | `false` |

### 配置文件示例

```bash
# .env 文件
DEFAULT_AI_PROVIDER=kimi
KIMI_API_KEY=your-kimi-api-key
CLAUDE_API_KEY=your-claude-api-key
JWT_SECRET_KEY=your-secret-key
```

## 🤝 贡献

欢迎提交Issue和Pull Request！

1. Fork 项目
2. 创建特性分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送分支 (`git push origin feature/AmazingFeature`)
5. 创建 Pull Request

## 📄 许可证

本项目采用 [MIT](LICENSE) 许可证。

## 🙏 致谢

- [Claude Code](https://github.com/anthropics/anthropic-cookbook)
- [Kimi](https://kimi.moonshot.cn/)
- [OpenAI Codex](https://github.com/openai/codex)
- [FastAPI](https://fastapi.tiangolo.com/)
- [xterm.js](https://xtermjs.org/)

---

⭐ 如果这个项目对你有帮助，请给它一个Star！