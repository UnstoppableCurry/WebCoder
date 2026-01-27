# 🚀 部署教程

## 部署方式

### 方式一：Docker Compose 部署（推荐）

#### 1. 准备服务器

```bash
# Ubuntu 22.04 LTS
sudo apt update && sudo apt upgrade -y
sudo apt install -y docker.io docker-compose git

# 启动 Docker
sudo systemctl enable docker
sudo systemctl start docker

# 添加用户到 docker 组
sudo usermod -aG docker $USER
# 重新登录生效
```

#### 2. 部署项目

```bash
# 克隆项目
git clone https://github.com/yourusername/webcoder.git
cd webcoder

# 配置环境变量
cp .env.example .env
nano .env  # 编辑配置

# 启动服务
docker-compose up -d

# 查看日志
docker-compose logs -f
```

#### 3. 配置 Nginx 反向代理

```nginx
# /etc/nginx/sites-available/webcoder
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:3000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection 'upgrade';
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_cache_bypass $http_upgrade;
        
        # WebSocket 支持
        proxy_read_timeout 86400;
    }
}
```

```bash
# 启用配置
sudo ln -s /etc/nginx/sites-available/webcoder /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl restart nginx

# 配置 SSL (Let's Encrypt)
sudo apt install certbot python3-certbot-nginx -y
sudo certbot --nginx -d your-domain.com
```

### 方式二：Systemd 服务部署

#### 1. 创建服务文件

```bash
# /etc/systemd/system/ai-code-core.service
[Unit]
Description=WebCoder Core Service
After=network.target

[Service]
Type=simple
User=ai-code
Group=ai-code
WorkingDirectory=/opt/webcoder
Environment=PATH=/opt/webcoder/venv/bin
Environment=PYTHONPATH=/opt/webcoder
EnvironmentFile=/opt/webcoder/.env
ExecStart=/opt/webcoder/venv/bin/python src/core.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

```bash
# /etc/systemd/system/ai-code-gateway.service
[Unit]
Description=WebCoder Gateway Service
After=network.target ai-code-core.service

[Service]
Type=simple
User=ai-code
Group=ai-code
WorkingDirectory=/opt/webcoder
Environment=PATH=/opt/webcoder/venv/bin
Environment=PYTHONPATH=/opt/webcoder
EnvironmentFile=/opt/webcoder/.env
ExecStart=/opt/webcoder/venv/bin/python src/gateway.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

#### 2. 部署项目

```bash
# 创建用户
sudo useradd -r -s /bin/false ai-code

# 部署代码
sudo mkdir -p /opt/webcoder
sudo cp -r webcoder/* /opt/webcoder/
sudo chown -R ai-code:ai-code /opt/webcoder

# 安装依赖
sudo -u ai-code python3 -m venv /opt/webcoder/venv
sudo -u ai-code /opt/webcoder/venv/bin/pip install -r /opt/webcoder/requirements.txt

# 复制服务文件
sudo cp ai-code-core.service /etc/systemd/system/
sudo cp ai-code-gateway.service /etc/systemd/system/

# 启动服务
sudo systemctl daemon-reload
sudo systemctl enable ai-code-core ai-code-gateway
sudo systemctl start ai-code-core ai-code-gateway
```

### 方式三：Kubernetes 部署

```yaml
# k8s-deployment.yaml
apiVersion: apps/v1
kind: Deployment
metadata:
  name: webcoder
  labels:
    app: webcoder
spec:
  replicas: 1
  selector:
    matchLabels:
      app: webcoder
  template:
    metadata:
      labels:
        app: webcoder
    spec:
      containers:
      - name: core
        image: your-registry/webcoder:latest
        ports:
        - containerPort: 3001
        env:
        - name: DEFAULT_AI_PROVIDER
          value: "kimi"
        - name: KIMI_API_KEY
          valueFrom:
            secretKeyRef:
              name: ai-code-secrets
              key: kimi-api-key
      - name: gateway
        image: your-registry/webcoder:latest
        ports:
        - containerPort: 3000
---
apiVersion: v1
kind: Service
metadata:
  name: webcoder
spec:
  selector:
    app: webcoder
  ports:
  - port: 3000
    targetPort: 3000
  type: LoadBalancer
```

## 生产环境配置

### 安全加固

```bash
# 1. 配置防火墙
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow 22/tcp    # SSH
sudo ufw allow 80/tcp    # HTTP
sudo ufw allow 443/tcp   # HTTPS
sudo ufw enable

# 2. 配置 Fail2Ban
sudo apt install fail2ban -y
sudo systemctl enable fail2ban

# 3. 配置日志轮转
sudo tee /etc/logrotate.d/webcoder << EOF
/opt/webcoder/logs/*.log {
    daily
    missingok
    rotate 14
    compress
    delaycompress
    notifempty
    create 0644 ai-code ai-code
    sharedscripts
    postrotate
        systemctl reload ai-code-core ai-code-gateway
    endscript
}
EOF
```

### 监控配置

```bash
# 安装 Prometheus Node Exporter
sudo apt install prometheus-node-exporter -y

# 配置告警规则
# 监控服务状态、资源使用等
```

### 备份策略

```bash
# 创建备份脚本
#!/bin/bash
BACKUP_DIR="/backup/webcoder"
DATE=$(date +%Y%m%d_%H%M%S)

# 备份数据库
cp ~/.webcoder/ai_code_platform.db "$BACKUP_DIR/db_$DATE.db"

# 备份项目文件
tar -czf "$BACKUP_DIR/projects_$DATE.tar.gz" /var/lib/webcoder/projects

# 保留最近30天的备份
find $BACKUP_DIR -name "*.db" -mtime +30 -delete
find $BACKUP_DIR -name "*.tar.gz" -mtime +30 -delete
```

## 更新维护

```bash
# 1. 备份数据
./scripts/backup.sh

# 2. 拉取最新代码
git pull origin main

# 3. 更新依赖
pip install -r requirements.txt --upgrade

# 4. 重启服务
sudo systemctl restart ai-code-core ai-code-gateway

# 或 Docker 方式
docker-compose pull
docker-compose up -d
```

## 故障排查

```bash
# 查看服务状态
sudo systemctl status ai-code-core
sudo systemctl status ai-code-gateway

# 查看日志
sudo journalctl -u ai-code-core -f
sudo journalctl -u ai-code-gateway -f

# 检查端口
ss -tlnp | grep -E '3000|3001'

# 检查资源使用
htop
df -h
```