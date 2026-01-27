-- 添加 last_notify_position 字段用于追踪最后通知位置
-- 避免重复发送通知

ALTER TABLE project_agent_states ADD COLUMN last_notify_position INTEGER DEFAULT 0;
