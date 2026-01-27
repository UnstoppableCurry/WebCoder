#!/bin/bash
# WebCoder 启动脚本

echo "========================================"
echo "  WebCoder"
echo "  多AI提供商 · 在线编程助手"
echo "========================================"

# 切换到脚本所在目录
cd "$(dirname "$0")/.."

# 检查虚拟环境
if [ ! -d "venv" ]; then
    echo "[Setup] 创建虚拟环境..."
    python3 -m venv venv
fi

# 安装依赖
echo "[Setup] 检查依赖..."
./venv/bin/pip install -q -r requirements.txt

# 检查静态文件
if [ ! -f "static/xterm.min.js" ]; then
    echo "[Warning] xterm.js 文件不存在，请按 static/README.md 说明下载"
fi

# 启动服务
echo ""
echo "[Starting] Core 服务 (端口 3001)..."
./venv/bin/python src/core.py &
CORE_PID=$!
sleep 2

echo "[Starting] Gateway 服务 (端口 3000)..."
./venv/bin/python src/gateway.py &
GATEWAY_PID=$!

echo ""
echo "========================================"
echo "  服务已启动"
echo "  Gateway: http://localhost:3000"
echo "  Core: http://localhost:3001 (内部)"
echo ""
echo "  默认管理员: admin / admin123"
echo "  请登录后立即修改密码！"
echo "========================================"
echo ""
echo "按 Ctrl+C 停止服务..."

# 等待中断
trap "kill $CORE_PID $GATEWAY_PID 2>/dev/null; exit" INT TERM
wait