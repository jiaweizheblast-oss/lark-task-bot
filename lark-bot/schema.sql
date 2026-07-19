-- ============================================================
--  Lark Task Bot 数据库表结构
--  第一次部署时会自动执行这个文件建表（见 db.py 的 init_db）
--  你不用手动跑它，放着就好
-- ============================================================

-- 用户表：唯一权威身份表（不依赖飞书通讯录）
CREATE TABLE IF NOT EXISTS users (
    id            SERIAL PRIMARY KEY,
    open_id       TEXT UNIQUE NOT NULL,        -- 飞书用户唯一 ID
    union_id      TEXT,
    display_name  TEXT,
    role          TEXT NOT NULL DEFAULT 'Unknown',  -- Admin / HR / Vendor / Unknown
    kind          TEXT,                         -- internal / external
    email         TEXT,
    vendor_id     TEXT,                         -- 属于哪个供应商（外部人员用）
    status        TEXT NOT NULL DEFAULT 'pending',  -- pending(待确认) / bound(已绑定)
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 群表
CREATE TABLE IF NOT EXISTS groups (
    chat_id            TEXT PRIMARY KEY,        -- 飞书群唯一 ID
    name               TEXT,
    group_type         TEXT DEFAULT 'unknown',  -- internal_ops / vendor_group / candidate_group / unknown
    related_vendor_id  TEXT,
    default_owner_open_id TEXT,                 -- 升级时 @ 的内部负责人
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 任务表
CREATE TABLE IF NOT EXISTS tasks (
    id                 SERIAL PRIMARY KEY,
    title              TEXT NOT NULL,
    detail             TEXT,                    -- 任务详情/安排
    note               TEXT,                    -- 注意事项
    priority           TEXT,                    -- 优先级 高/中/低
    assignee_open_id   TEXT NOT NULL,           -- 负责人
    assignee_name      TEXT,                    -- 负责人名字（通知发布者时用）
    owner_open_id      TEXT,                    -- 升级对象（一般取所在群的 default_owner）
    group_chat_id      TEXT NOT NULL,           -- 卡片发到哪个群
    status             TEXT NOT NULL DEFAULT 'pending',  -- pending / accepted / done / issue
    deadline           DATE,
    card_message_id    TEXT,                    -- 卡片消息 ID（用于点完后更新卡片）
    result             TEXT,                    -- 反馈结果 / 备注
    created_by_open_id TEXT,
    last_reminder_stage TEXT NOT NULL DEFAULT '', -- ''/due_tomorrow/due_today/escalated 防重复提醒
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 已有部署补列（幂等，第一次部署时表已存在也能安全加上新列）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS assignee_name TEXT;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS detail   TEXT;   -- 任务详情/安排
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS note     TEXT;   -- 注意事项
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS priority TEXT;   -- 优先级 高/中/低

-- 草稿表：私聊终端派任务时，记住"某管理员正在给 X 群的 Y 派任务，等他输入内容"
CREATE TABLE IF NOT EXISTS drafts (
    admin_open_id     TEXT PRIMARY KEY,        -- 正在操作的管理员
    chat_id           TEXT,                    -- 选中的目标群
    chat_name         TEXT,
    assignee_open_id  TEXT,                    -- 选中的负责人
    assignee_name     TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 给定时任务扫描用的索引（按状态 + 截止日期查）
CREATE INDEX IF NOT EXISTS idx_tasks_status_deadline ON tasks (status, deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_group ON tasks (group_chat_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);
