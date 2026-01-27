-- 002_butler_and_git.sql
-- 管家数据 + Git 配置

-- 管家对话历史
CREATE TABLE IF NOT EXISTS butler_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    role TEXT NOT NULL,  -- 'user' or 'assistant'
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 管家事件记录
CREATE TABLE IF NOT EXISTS butler_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 用户计划
CREATE TABLE IF NOT EXISTS butler_plans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    content TEXT NOT NULL,
    is_done BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 项目 Git 配置
CREATE TABLE IF NOT EXISTS project_git_config (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER UNIQUE NOT NULL,
    remote_url TEXT,                      -- Git 远程地址
    remote_type TEXT DEFAULT 'github',    -- github/gitee/gitlab
    branch TEXT DEFAULT 'main',           -- 默认分支
    auto_commit BOOLEAN DEFAULT TRUE,     -- 自动提交
    auto_push BOOLEAN DEFAULT FALSE,      -- 自动推送
    commit_interval INTEGER DEFAULT 300,  -- 自动提交间隔（秒）
    last_commit_at TIMESTAMP,
    last_push_at TIMESTAMP,
    ssh_key_path TEXT,                    -- SSH 密钥路径
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- 项目文件变更记录
CREATE TABLE IF NOT EXISTS project_file_changes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    project_id INTEGER NOT NULL,
    file_path TEXT NOT NULL,
    change_type TEXT NOT NULL,  -- 'add', 'modify', 'delete'
    commit_hash TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (project_id) REFERENCES projects(id) ON DELETE CASCADE
);

-- 索引
CREATE INDEX IF NOT EXISTS idx_butler_messages_user ON butler_messages(user_id);
CREATE INDEX IF NOT EXISTS idx_butler_events_user ON butler_events(user_id);
CREATE INDEX IF NOT EXISTS idx_butler_plans_user ON butler_plans(user_id);
CREATE INDEX IF NOT EXISTS idx_project_git_config_project ON project_git_config(project_id);
