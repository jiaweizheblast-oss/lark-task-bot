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
    external           BOOLEAN DEFAULT FALSE,   -- 是否外部群
    group_type         TEXT DEFAULT 'unknown',  -- internal_ops / vendor_group / candidate_group / unknown
    related_vendor_id  TEXT,
    default_owner_open_id TEXT,                 -- 升级时 @ 的内部负责人
    created_at         TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
ALTER TABLE groups ADD COLUMN IF NOT EXISTS external BOOLEAN DEFAULT FALSE;

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
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS unread   BOOLEAN DEFAULT FALSE;  -- 负责人有新留言待发布者查看（= 待沟通）
-- 迁移旧数据：过去的 "issue"(待沟通) 状态不再是真实状态，改成“有未读留言”叠加在“进行中”上
UPDATE tasks SET unread = TRUE  WHERE status = 'issue';
UPDATE tasks SET status = 'accepted' WHERE status = 'issue';

-- 草稿表：私聊终端派任务时，记住"某管理员正在给 X 群的 Y 派任务，等他输入内容"
CREATE TABLE IF NOT EXISTS drafts (
    admin_open_id     TEXT PRIMARY KEY,        -- 正在操作的管理员
    chat_id           TEXT,                    -- 选中的目标群
    chat_name         TEXT,
    assignee_open_id  TEXT,                    -- 选中的负责人
    assignee_name     TEXT,
    stage             TEXT,                    -- 逐步问答进度：title/detail/note/pridl
    title             TEXT,
    detail            TEXT,
    note              TEXT,
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- 已有部署补列（幂等）
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS stage  TEXT;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS title  TEXT;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS detail TEXT;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS note   TEXT;

-- 外部群（自定义机器人 webhook）配置
CREATE TABLE IF NOT EXISTS external_groups (
    id           SERIAL PRIMARY KEY,
    name         TEXT NOT NULL,               -- 外部群名字（你自己起）
    webhook_url  TEXT NOT NULL,               -- 该群 Custom Bot 的 webhook 网址
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- tasks 补外部相关列（幂等）
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS token             TEXT;    -- 外部状态汇报链接用的随机口令
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS is_external       BOOLEAN DEFAULT FALSE;
ALTER TABLE tasks ADD COLUMN IF NOT EXISTS external_group_id INTEGER;
-- 外部任务没有群 open_id / 负责人 open_id，放开非空约束
ALTER TABLE tasks ALTER COLUMN group_chat_id DROP NOT NULL;
ALTER TABLE tasks ALTER COLUMN assignee_open_id DROP NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tasks_token ON tasks (token);

-- 系统设置（键值对）：提醒开关、跳过周末、节假日等
CREATE TABLE IF NOT EXISTS settings (
    key    TEXT PRIMARY KEY,
    value  TEXT
);

-- 任务留言 / 沟通时间线：发布者 ↔ 负责人 来回商量，都留痕（内部外部通用）
CREATE TABLE IF NOT EXISTS task_comments (
    id           SERIAL PRIMARY KEY,
    task_id      INTEGER NOT NULL,
    author_side  TEXT NOT NULL DEFAULT 'system',  -- publisher(发布者) / assignee(负责人) / system(系统)
    author_name  TEXT,
    body         TEXT NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_comments_task ON task_comments (task_id, created_at);

-- 给定时任务扫描用的索引（按状态 + 截止日期查）
CREATE INDEX IF NOT EXISTS idx_tasks_status_deadline ON tasks (status, deadline);
CREATE INDEX IF NOT EXISTS idx_tasks_group ON tasks (group_chat_id);
CREATE INDEX IF NOT EXISTS idx_users_role ON users (role);

-- ============================================================
--  招聘渠道简历日报模块（新增，纯增量）
--  HR 每天每渠道每职位填一行；多个 HR 用 filled_by 区分，
--  汇总统计时按 (record_date, channel, job_request_id) GROUP BY——
--  行顺序无关，谁先填谁后填都不影响最终数字。
-- ============================================================

CREATE TABLE IF NOT EXISTS job_requests (
    id                  SERIAL PRIMARY KEY,
    title               TEXT NOT NULL,                 -- 职位名
    target_headcount    INTEGER NOT NULL DEFAULT 0,    -- 目标人数（要招几个人）
    target_resume_count INTEGER NOT NULL DEFAULT 0,    -- 目标简历量（可选；0=未设）
    status              TEXT NOT NULL DEFAULT 'open',  -- open / closed
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 人工渠道汇总表（manual_unidentified 空间）：只放未逐人建档的渠道人工汇总。
-- 单一 owner 键：同职位 + 同报告日 + 同渠道 只允许一行；填报人是受控 roster 选择、
-- 不进唯一键（自由文本换名不能再造重复行）。逐人建档的派生渠道指标由 AI-TD 核心
-- 按 attribution_source + 去重人头产出，不在本表、也不与本表相加。
CREATE TABLE IF NOT EXISTS channel_daily (
    id               SERIAL PRIMARY KEY,
    record_date      DATE NOT NULL,                    -- report_date
    channel          TEXT NOT NULL,                    -- 人工渠道标签（运营侧受控命名空间）
    job_request_id   INTEGER NOT NULL REFERENCES job_requests(id) ON DELETE CASCADE,
    filled_by        TEXT NOT NULL DEFAULT '',         -- 填报人（受控 roster 选择；仅声明/展示，非唯一键）
    new_resumes      INTEGER NOT NULL DEFAULT 0,
    passed_screening INTEGER NOT NULL DEFAULT 0,
    recommended      INTEGER NOT NULL DEFAULT 0,
    rejected         INTEGER NOT NULL DEFAULT 0,
    note             TEXT NOT NULL DEFAULT '',
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_channel_manual UNIQUE (record_date, channel, job_request_id)
);
CREATE INDEX IF NOT EXISTS idx_channel_daily_date ON channel_daily (record_date);
CREATE INDEX IF NOT EXISTS idx_channel_daily_ch ON channel_daily (channel, record_date);

-- 迁移（幂等）：从"含填写人的旧键"改成"单一 owner 键"。
-- 先按新键去重（每组保留一条），再删旧约束、加新约束，避免上新键失败。
DELETE FROM channel_daily a USING channel_daily b
  WHERE a.ctid < b.ctid
    AND a.record_date = b.record_date
    AND a.channel = b.channel
    AND a.job_request_id = b.job_request_id;
ALTER TABLE channel_daily DROP CONSTRAINT IF EXISTS channel_daily_record_date_channel_job_request_id_filled_by_key;
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname = 'uq_channel_manual') THEN
    ALTER TABLE channel_daily ADD CONSTRAINT uq_channel_manual UNIQUE (record_date, channel, job_request_id);
  END IF;
END $$;

-- ============================================================
--  候选人级招聘跟踪表（每行 = 一个候选人；渠道汇总/漏斗/速度由此自动统计）
--  · 取代"人工填汇总数字"：new/passed/recommended/rejected 改成数候选人行的状态。
--  · 独立于 CODEX(AI-TD)：ext_ref 只存对方不透明 token，两库永不合并、永不相加。
--  · channel_daily 汇总表保留（不删数据），但前端不再录入，分析改由本表派生。
-- ============================================================
CREATE TABLE IF NOT EXISTS candidate (
    id             SERIAL PRIMARY KEY,
    apply_date     DATE NOT NULL,                    -- 进入日期（简历到达/首次接触）
    name           TEXT NOT NULL DEFAULT '',         -- 候选人姓名
    channel        TEXT NOT NULL,                    -- 来源渠道（运营侧受控命名）
    job_request_id INTEGER REFERENCES job_requests(id) ON DELETE SET NULL,  -- 关联职位（可空）
    status         TEXT NOT NULL DEFAULT '新简历',    -- 新简历/初筛通过/已推荐面试/已录用/已拒绝
    note           TEXT NOT NULL DEFAULT '',
    filled_by      TEXT NOT NULL DEFAULT '',          -- 填报人（受控 roster；仅展示）
    source         TEXT NOT NULL DEFAULT '手动',       -- 手动 / CODEX（来源系统）
    ext_ref        TEXT NOT NULL DEFAULT '',           -- 外部关联：CODEX 候选人不透明 token（可空）
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_candidate_date ON candidate (apply_date);
CREATE INDEX IF NOT EXISTS idx_candidate_ch ON candidate (channel, apply_date);
CREATE INDEX IF NOT EXISTS idx_candidate_job ON candidate (job_request_id);
CREATE INDEX IF NOT EXISTS idx_candidate_extref ON candidate (ext_ref);
-- Lark 多维表格同步：记录本候选人对应的 Lark 记录 record_id（机器人自己那张表），拉取时按它更新去重
ALTER TABLE candidate ADD COLUMN IF NOT EXISTS lark_record_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_candidate_lark ON candidate (lark_record_id);

-- ============================================================
--  Nexus 运营模块（纯增量：职位 owner、文档模板库、渠道成本）
-- ============================================================

-- 职位补负责人列（幂等）
ALTER TABLE job_requests ADD COLUMN IF NOT EXISTS owner TEXT NOT NULL DEFAULT '';

-- 文档 / 模板库：招聘话术、JD、面试评估表、任务 SOP 等可复用模板
CREATE TABLE IF NOT EXISTS templates (
    id          SERIAL PRIMARY KEY,
    title       TEXT NOT NULL,
    category    TEXT NOT NULL DEFAULT '其他',       -- 招聘话术/JD/面试评估/任务SOP/其他
    content     TEXT NOT NULL DEFAULT '',
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_templates_cat ON templates (category);

-- 渠道月度投入（成本/ROI）：按 渠道 × 月(YYYY-MM) 记一笔当月投入
CREATE TABLE IF NOT EXISTS channel_cost (
    id          SERIAL PRIMARY KEY,
    channel     TEXT NOT NULL,
    ym          TEXT NOT NULL,                      -- YYYY-MM
    amount      NUMERIC NOT NULL DEFAULT 0,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    CONSTRAINT uq_channel_cost UNIQUE (channel, ym)
);

-- ============================================================
--  Nexus 考勤打卡（内部/外部；GPS + 地理围栏 + 防作弊留痕）
-- ============================================================
CREATE TABLE IF NOT EXISTS attendance_site (
    id             SERIAL PRIMARY KEY,
    name           TEXT NOT NULL,
    lat            DOUBLE PRECISION NOT NULL,
    lng            DOUBLE PRECISION NOT NULL,
    radius_m       INTEGER NOT NULL DEFAULT 200,     -- 允许打卡半径（米）
    require_selfie BOOLEAN NOT NULL DEFAULT FALSE,    -- 该点位是否要求自拍（按点位开关）
    created_at     TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attendance_person (
    id         SERIAL PRIMARY KEY,
    name       TEXT NOT NULL,
    kind       TEXT NOT NULL DEFAULT 'external',      -- internal / external
    token      TEXT UNIQUE NOT NULL,                  -- 免登录打卡链接口令
    site_id    INTEGER REFERENCES attendance_site(id) ON DELETE SET NULL,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS attendance_record (
    id           SERIAL PRIMARY KEY,
    person_id    INTEGER REFERENCES attendance_person(id) ON DELETE CASCADE,
    person_name  TEXT NOT NULL DEFAULT '',
    kind         TEXT NOT NULL DEFAULT 'external',
    punch_type   TEXT NOT NULL DEFAULT 'in',          -- in(上班) / out(下班)
    server_time  TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 服务器盖章，非手机时间
    lat          DOUBLE PRECISION,
    lng          DOUBLE PRECISION,
    accuracy     DOUBLE PRECISION,                    -- 定位精度（米）
    site_id      INTEGER,
    distance_m   INTEGER,                             -- 距点位距离（米）
    within_fence BOOLEAN,
    ip           TEXT NOT NULL DEFAULT '',
    photo        TEXT,                                -- base64（仅点位要求自拍时）
    flags        TEXT NOT NULL DEFAULT '',            -- 逗号分隔异常标记
    source       TEXT NOT NULL DEFAULT 'web',         -- web / lark
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS idx_att_rec_person ON attendance_record (person_id, server_time);
CREATE INDEX IF NOT EXISTS idx_att_rec_time ON attendance_record (server_time);
