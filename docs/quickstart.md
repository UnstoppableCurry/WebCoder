# 🚀 快速入门指南

## 5 分钟快速开始

### 1. 环境准备

```bash
# 确保已安装 Python 3.10+ 和 Node.js 16+
python3 --version
node --version
```

### 2. 安装 AI CLI 工具

```bash
# 安装 Kimi CLI（推荐）
pip install kimi-cli

# 安装 Claude Code
npm install -g @anthropic-ai/claude-code

# 配置 Kimi API 密钥
export KIMI_API_KEY="your-api-key"
```

### 3. 启动平台

```bash
# 克隆项目
git clone https://github.com/yourusername/webcoder.git
cd webcoder

# 创建虚拟环境
python3 -m venv venv
source venv/bin/activate

# 安装依赖
pip install -r requirements.txt

# 配置环境变量
cp .env.example .env
# 编辑 .env，填入你的 API 密钥

# 启动服务
./scripts/start.sh
```

### 4. 开始使用

打开浏览器访问 `http://localhost:3000`

默认账号：
- 用户名：`admin`
- 密码：`admin123`

## 创建第一个项目

1. 点击右下角 **+** 按钮
2. 输入项目名称
3. 选择 AI 提供商（Kimi/Claude/Codex）
4. 点击「创建并启动」

## 常用操作

### 在终端中工作

```bash
# 直接输入自然语言指令
帮我创建一个 Python Web 应用

# 或者使用传统命令
ls -la
python main.py
```

### 会话管理

- **新对话** - 开始全新的对话
- **继续** - 继续上次的对话
- **重启** - 重启当前项目

### 文件管理

点击工具栏的文件图标 📁 可以：
- 浏览项目文件
- 编辑代码文件
- 上传/下载文件

## 下一步

- [完整安装指南](./installation.md)
- [配置说明](./configuration.md)
- [部署教程](./deployment.md)

## 常见问题

**Q: 提示 "API Key 未设置"**  
A: 在 `.env` 文件中设置对应的 API 密钥

**Q: 无法连接到终端**  
A: 检查 Core 服务是否启动 (`curl http://localhost:3001/health`)

**Q: 如何切换 AI 提供商？**  
A: 创建项目时选择，或在环境变量中修改 `DEFAULT_AI_PROVIDER`