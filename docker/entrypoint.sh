#!/bin/bash
set -e

echo "========================================"
echo "  WebCoder - Docker Entrypoint"
echo "========================================"

# 初始化数据库
echo "[Setup] 初始化数据库..."
python3 -c "from src.db.database import init_db; init_db()" || true

# 启动 Core 服务
echo "[Start] 启动 Core 服务 (端口 3001)..."
python3 src/core.py &
CORE_PID=$!

# 等待 Core 服务启动
sleep 3

# 启动 Gateway 服务
echo "[Start] 启动 Gateway 服务 (端口 3000)..."
python3 src/gateway.py &
GATEWAY_PID=$!

echo ""
echo "========================================"
echo "  服务已启动"
echo "  Gateway: http://localhost:3000"
echo "  Core: http://localhost:3001"
echo "========================================"

# 等待进程
wait $CORE_PID $GATEWAY_PID