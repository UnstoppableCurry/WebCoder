# 📦 安装指南

## 系统要求

### 最低配置
- CPU: 2核+
- 内存: 4GB+
- 磁盘: 20GB+
- 系统: Ubuntu 20.04+ / macOS 12+ / Windows WSL2

### 推荐配置
- CPU: 4核+
- 内存: 8GB+
- 磁盘: 50GB+
- 网络: 稳定的外网连接

## 环境准备

### 1. 安装 Python 3.10+

```bash
# Ubuntu/Debian
sudo apt update
sudo apt install python3.10 python3.10-pip python3.10-venv -y

# macOS
brew install python@3.10

# 验证安装
python3 --version
```

### 2. 安装 Node.js 16+

```bash
# Ubuntu/Debian
curl -fsSL https://deb.nodesource.com/setup_18.x | sudo -E bash -
sudo apt install -y nodejs

# macOS
brew install node@18

# 验证安装
node --version
npm --version
```

### 3. 安装 AI CLI 工具

#### Claude Code
```bash
npm install -g @anthropic-ai/claude-code
```

#### Kimi CLI
```bash
pip install kimi-cli
```

#### OpenAI Codex (可选)
```bash
npm install -g @openai/codex
```

## 项目安装

### 方式一：直接安装

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/webcoder.git
cd webcoder

# 2. 创建虚拟环境
python3 -m venv venv
source venv/bin/activate  # Linux/macOS
# 或 venv\Scripts\activate  # Windows

# 3. 安装依赖
pip install -r requirements.txt

# 4. 配置环境变量
cp .env.example .env
nano .env  # 编辑配置文件

# 5. 初始化数据库
python -c "from src.db.database import init_db; init_db()"

# 6. 启动服务
./scripts/start.sh
```

### 方式二：Docker 安装

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/webcoder.git
cd webcoder

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 文件

# 3. 构建并启动
docker-compose up -d
```

### 方式三：开发模式安装

```bash
# 1. 克隆项目
git clone https://github.com/yourusername/webcoder.git
cd webcoder

# 2. 安装开发依赖
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 3. 安装 pre-commit
pre-commit install

# 4. 启动开发服务器
./scripts/start-dev.sh
```

## 配置说明

### 1. 创建 .env 文件

```bash
cp .env.example .env
```

### 2. 编辑配置文件

```env
# ========== 基础配置 ==========
DEFAULT_AI_PROVIDER=kimi

# ========== Kimi 配置 ==========
KIMI_API_KEY=your-kimi-api-key-here
KIMI_API_URL=https://api.moonshot.cn/v1

# ========== Claude 配置 ==========
CLAUDE_API_KEY=your-claude-api-key-here
CLAUDE_API_URL=https://api.anthropic.com

# ========== JWT 配置 ==========
JWT_SECRET_KEY=your-super-secret-jwt-key

# ========== 邮件配置 (可选) ==========
EMAIL_ENABLED=false
EMAIL_SMTP_SERVER=smtp.gmail.com
EMAIL_SMTP_PORT=587
EMAIL_SENDER=your-email@gmail.com
EMAIL_PASSWORD=your-app-password
```

### 3. 获取 API 密钥

#### Kimi API 密钥
1. 访问 [Moonshot AI 开放平台](https://platform.moonshot.cn/)
2. 注册账号并登录
3. 进入「API 密钥管理」
4. 创建新的 API 密钥

#### Claude API 密钥
1. 访问 [Anthropic Console](https://console.anthropic.com/)
2. 注册账号并登录
3. 进入「API Keys」
4. 创建新的 API 密钥

## 验证安装

```bash
# 检查核心服务
curl http://localhost:3001/health

# 检查网关服务
curl http://localhost:3000/api/health

# 检查 AI CLI 工具
claude --version
kimi --version
```

## 常见问题

### 问题1: `command not found: kimi`

**解决方案:**
```bash
# 确保 pip bin 目录在 PATH 中
export PATH="$HOME/.local/bin:$PATH"

# 或者重新安装
pip install --user kimi-cli
```

### 问题2: `Permission denied`

**解决方案:**
```bash
# 修复权限
chmod +x scripts/*.sh

# 创建必要的目录
mkdir -p ~/.webcoder
chmod 755 ~/.webcoder
```

### 问题3: 端口被占用

**解决方案:**
```bash
# 查找占用端口的进程
lsof -i:3000
lsof -i:3001

# 停止占用端口的进程
kill -9 <PID>

# 或修改配置使用其他端口
export GATEWAY_PORT=8080
export CORE_PORT=8081
```

## 下一步

- [配置说明](./configuration.md)
- [使用教程](./usage.md)
- [API文档](./api.md)