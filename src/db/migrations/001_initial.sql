-- Claude Mobile v10 - 初始数据库结构
-- 创建时间: 2025-12-16

-- ============================================================
-- 管理员表 (独立管理员系统)
-- ============================================================
CREATE TABLE IF NOT EXISTS admins (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,
    is_active BOOLEAN DEFAULT 1
);

-- ============================================================
-- 用户表 (普通用户)
-- ============================================================
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    display_name TEXT,

    -- 授权时间管理
    auth_expires_at TIMESTAMP NOT NULL,

    -- 状态
    is_active BOOLEAN DEFAULT 1,
    is_verified BOOLEAN DEFAULT 0,

    -- 用户目录
    user_dir TEXT UNIQUE NOT NULL,

    -- 时间戳
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_login TIMESTAMP,

    -- 创建人
    created_by_admin INTEGER,
    FOREIGN KEY (created_by_admin) REFERENCES admins(id)
);

CREATE INDEX IF NOT EXISTS idx_users_email ON users(email);
CREATE INDEX IF NOT EXISTS idx_users_auth_expires ON users(auth_expires_at);

-- ============================================================
-- 邮箱验证码表
-- ============================================================
CREATE TABLE IF NOT EXISTS verification_codes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    email TEXT NOT NULL,
    code TEXT NOT NULL,
    purpose TEXT NOT NULL,  -- 'register', 'reset_password', 'login'
    expires_at TIMESTAMP NOT NULL,
    used BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_verification_email ON verification_codes(email, purpose);

-- ============================================================
-- Refresh Token 表
-- ============================================================
CREATE TABLE IF NOT EXISTS refresh_tokens (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    token_hash TEXT UNIQUE NOT NULL,
    device_info TEXT,
    expires_at TIMESTAMP NOT NULL,
    is_revoked BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_used_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_refresh_tokens_user ON refresh_tokens(user_id);

-- ============================================================
-- 项目表
-- ============================================================
CREATE TABLE IF NOT EXISTS projects (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    display_name TEXT,
    note TEXT,

    -- 项目目录
    project_dir TEXT NOT NULL,

    -- 状态
    status TEXT DEFAULT 'idle',
    current_phase TEXT DEFAULT 'idle',

    -- Claude Code 进程状态
    is_running BOOLEAN DEFAULT 0,
    pid INTEGER,
    mode TEXT DEFAULT 'new',

    -- 时间戳
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    last_active TIMESTAMP,

    UNIQUE(user_id, name),
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_projects_user ON projects(user_id);
CREATE INDEX IF NOT EXISTS idx_projects_status ON projects(status);

-- ============================================================
-- 代理状态表
-- ============================================================
CREATE TABLE IF NOT EXISTS project_agent_states (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER UNIQUE NOT NULL,

    -- 代理状态
    agent_active BOOLEAN DEFAULT 0,
    last_heartbeat TIMESTAMP,

    -- 当前环节
    current_phase TEXT DEFAULT 'pending_dispatch',

    -- 是否需要人介入
    needs_human BOOLEAN DEFAULT 0,
    human_action_type TEXT,
    human_action_message TEXT,

    -- 上下文管理
    context_summary TEXT,
    last_output_position INTEGER DEFAULT 0,

    -- 自动化控制
    auto_mode BOOLEAN DEFAULT 1,
    pending_command TEXT,

    -- 时间戳
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- ============================================================
-- 项目输出历史表
-- ============================================================
CREATE TABLE IF NOT EXISTS project_outputs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    content_type TEXT DEFAULT 'stdout',
    parsed_type TEXT,
    parsed_data TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_outputs_project ON project_outputs(project_id, created_at);

-- ============================================================
-- 项目交互历史表
-- ============================================================
CREATE TABLE IF NOT EXISTS project_interactions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    source TEXT NOT NULL,  -- 'user', 'agent', 'system'
    action_type TEXT NOT NULL,
    action_data TEXT,
    result TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_interactions_project ON project_interactions(project_id);

-- ============================================================
-- 任务队列表
-- ============================================================
CREATE TABLE IF NOT EXISTS task_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    project_id INTEGER,
    task_type TEXT NOT NULL,
    task_data TEXT NOT NULL,
    priority INTEGER DEFAULT 0,
    status TEXT DEFAULT 'pending',
    assigned_agent TEXT,
    result TEXT,
    error TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    started_at TIMESTAMP,
    completed_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_task_queue_status ON task_queue(status, priority DESC);
CREATE INDEX IF NOT EXISTS idx_task_queue_user ON task_queue(user_id);

-- ============================================================
-- 通知表
-- ============================================================
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    content TEXT NOT NULL,
    notification_type TEXT NOT NULL,
    project_id INTEGER,
    is_read BOOLEAN DEFAULT 0,
    is_sent_email BOOLEAN DEFAULT 0,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    read_at TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);

-- ============================================================
-- 系统配置表
-- ============================================================
CREATE TABLE IF NOT EXISTS system_config (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 初始配置
INSERT OR IGNORE INTO system_config (key, value) VALUES
    ('agent_heartbeat_interval', '10'),
    ('default_auth_days', '30'),
    ('max_projects_per_user', '10'),
    ('context_summary_max_tokens', '2000');

-- ============================================================
-- 创建默认管理员 (密码: admin123, 需要在首次使用时修改)
-- 密码哈希使用 argon2
-- ============================================================
-- 注意: 这里的哈希值需要在应用启动时通过代码生成
