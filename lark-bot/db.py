"""
数据库操作封装。
所有对 PostgreSQL 的读写都放这里，bot.py 和 overdue.py 都从这里调。
连接地址从环境变量 DATABASE_URL 读（Railway 会自动提供）。
"""
import os
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")


def get_conn():
    """开一个新的数据库连接。用完即关，简单稳妥。"""
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL environment variable — connect the database in Railway")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def init_db():
    """第一次启动时建表（读取同目录 schema.sql）。已存在则跳过。"""
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "schema.sql"), "r", encoding="utf-8") as f:
        sql = f.read()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(sql)
    print("[db] tables ready")


# ---------------- 用户 ----------------

def upsert_user(open_id, display_name=None, union_id=None, kind=None,
                role=None, email=None, vendor_id=None, status=None):
    """新增或更新一个用户。只更新非空字段，不会把已有信息覆盖成空。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT open_id FROM users WHERE open_id=%s", (open_id,))
        exists = cur.fetchone()
        if exists:
            cur.execute("""
                UPDATE users SET
                    display_name = COALESCE(%s, display_name),
                    union_id     = COALESCE(%s, union_id),
                    kind         = COALESCE(%s, kind),
                    role         = COALESCE(%s, role),
                    email        = COALESCE(%s, email),
                    vendor_id    = COALESCE(%s, vendor_id),
                    status       = COALESCE(%s, status),
                    updated_at   = now()
                WHERE open_id=%s
            """, (display_name, union_id, kind, role, email, vendor_id, status, open_id))
        else:
            cur.execute("""
                INSERT INTO users (open_id, display_name, union_id, kind, role, email, vendor_id, status)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
            """, (open_id, display_name, union_id, kind,
                  role or "Unknown", email, vendor_id, status or "pending"))


def get_user(open_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE open_id=%s", (open_id,))
        return cur.fetchone()


def get_role(open_id):
    u = get_user(open_id)
    return u["role"] if u else "Unknown"


def is_admin(open_id):
    """Admin 或 HR 都算有管理权限。"""
    return get_role(open_id) in ("Admin", "HR")


def list_pending_in_group(chat_id):
    """列出某个群里还没绑定身份（Unknown/pending）的人。"""
    # 通过任务 / 成员关系比较麻烦，这里简单返回所有 pending 用户，配合 /pending 使用
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM users WHERE status='pending' ORDER BY created_at DESC LIMIT 50")
        return cur.fetchall()


def bind_user(open_id, role, vendor_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE users SET role=%s, vendor_id=COALESCE(%s, vendor_id),
                   status='bound', updated_at=now()
            WHERE open_id=%s
        """, (role, vendor_id, open_id))
        return cur.rowcount > 0


# ---------------- 群 ----------------

def upsert_group(chat_id, name=None, group_type=None,
                 related_vendor_id=None, default_owner_open_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT chat_id FROM groups WHERE chat_id=%s", (chat_id,))
        if cur.fetchone():
            cur.execute("""
                UPDATE groups SET
                    name = COALESCE(%s, name),
                    group_type = COALESCE(%s, group_type),
                    related_vendor_id = COALESCE(%s, related_vendor_id),
                    default_owner_open_id = COALESCE(%s, default_owner_open_id),
                    updated_at = now()
                WHERE chat_id=%s
            """, (name, group_type, related_vendor_id, default_owner_open_id, chat_id))
        else:
            cur.execute("""
                INSERT INTO groups (chat_id, name, group_type, related_vendor_id, default_owner_open_id)
                VALUES (%s,%s,%s,%s,%s)
            """, (chat_id, name, group_type or "unknown", related_vendor_id, default_owner_open_id))


def get_group(chat_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM groups WHERE chat_id=%s", (chat_id,))
        return cur.fetchone()


# ---------------- 任务 ----------------

def create_task(title, assignee_open_id, group_chat_id, deadline=None,
                owner_open_id=None, created_by_open_id=None, assignee_name=None,
                detail=None, note=None, priority=None,
                token=None, is_external=False, external_group_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO tasks (title, detail, note, priority, assignee_open_id, assignee_name,
                               group_chat_id, deadline, owner_open_id, created_by_open_id,
                               token, is_external, external_group_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id
        """, (title, detail, note, priority, assignee_open_id, assignee_name,
              group_chat_id, deadline, owner_open_id, created_by_open_id,
              token, is_external, external_group_id))
        return cur.fetchone()[0]


def get_task_by_token(token):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM tasks WHERE token=%s", (token,))
        return cur.fetchone()


# ---------------- 任务留言 / 沟通时间线 ----------------

def add_comment(task_id, body, author_side="system", author_name=None):
    """给某个任务加一条留言。author_side: publisher / assignee / system。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO task_comments (task_id, author_side, author_name, body)
            VALUES (%s,%s,%s,%s) RETURNING id
        """, (task_id, author_side, author_name, body))
        return cur.fetchone()[0]


def list_comments(task_id):
    """按时间顺序列出某任务的全部留言。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM task_comments WHERE task_id=%s ORDER BY created_at ASC, id ASC", (task_id,))
        return cur.fetchall()


# ---------------- 系统设置（键值对） ----------------

def get_settings():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT key, value FROM settings")
        return {r["key"]: r["value"] for r in cur.fetchall()}


def set_setting(key, value):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO settings (key, value) VALUES (%s,%s)
                       ON CONFLICT (key) DO UPDATE SET value=EXCLUDED.value""", (key, str(value)))


# ---------------- 外部群（webhook）配置 ----------------

def list_external_groups():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM external_groups ORDER BY created_at DESC")
        return cur.fetchall()


def add_external_group(name, webhook_url):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO external_groups (name, webhook_url) VALUES (%s,%s) RETURNING id",
                    (name, webhook_url))
        return cur.fetchone()[0]


def get_external_group(eg_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM external_groups WHERE id=%s", (eg_id,))
        return cur.fetchone()


def delete_external_group(eg_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM external_groups WHERE id=%s", (eg_id,))


def set_task_card(task_id, card_message_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET card_message_id=%s, updated_at=now() WHERE id=%s",
                    (card_message_id, task_id))


def get_task(task_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM tasks WHERE id=%s", (task_id,))
        return cur.fetchone()


def update_task_status(task_id, status, result=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            UPDATE tasks SET status=%s, result=COALESCE(%s, result), updated_at=now()
            WHERE id=%s
        """, (status, result, task_id))
        return cur.rowcount > 0


def set_reminder_stage(task_id, stage):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("UPDATE tasks SET last_reminder_stage=%s, updated_at=now() WHERE id=%s",
                    (stage, task_id))


def delete_task(task_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM tasks WHERE id=%s", (task_id,))
        return cur.rowcount > 0


def update_task_fields(task_id, **fields):
    """更新任务的若干字段（截止/优先级/负责人/状态/卡片ID）。"""
    allowed = {"deadline", "priority", "assignee_open_id", "assignee_name", "status", "card_message_id", "unread", "result"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=%s" for k in sets)
    vals = list(sets.values()) + [task_id]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE tasks SET {cols}, updated_at=now() WHERE id=%s", vals)


def list_tasks(limit=300):
    """列出最近的任务（网页看板用）。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM tasks ORDER BY created_at DESC LIMIT %s", (limit,))
        return cur.fetchall()


def tasks_still_open():
    """所有还没完成的任务（用于每日超期扫描）。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""
            SELECT * FROM tasks
            WHERE status IN ('pending','accepted') AND deadline IS NOT NULL
            ORDER BY deadline ASC
        """)
        return cur.fetchall()


# ---------------- 草稿（私聊派任务的中间状态） ----------------

def set_draft(admin_open_id, chat_id, chat_name, assignee_open_id, assignee_name, stage="title"):
    """开始一个新草稿（选好群+人，准备逐步问答）。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""
            INSERT INTO drafts (admin_open_id, chat_id, chat_name, assignee_open_id, assignee_name,
                                stage, title, detail, note, updated_at)
            VALUES (%s,%s,%s,%s,%s,%s, NULL, NULL, NULL, now())
            ON CONFLICT (admin_open_id) DO UPDATE SET
                chat_id=EXCLUDED.chat_id, chat_name=EXCLUDED.chat_name,
                assignee_open_id=EXCLUDED.assignee_open_id, assignee_name=EXCLUDED.assignee_name,
                stage=EXCLUDED.stage, title=NULL, detail=NULL, note=NULL, updated_at=now()
        """, (admin_open_id, chat_id, chat_name, assignee_open_id, assignee_name, stage))


def update_draft(admin_open_id, **fields):
    """更新草稿的某些字段（stage/title/detail/note）。"""
    allowed = {"stage", "title", "detail", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=%s" for k in sets)
    vals = list(sets.values()) + [admin_open_id]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE drafts SET {cols}, updated_at=now() WHERE admin_open_id=%s", vals)


def get_draft(admin_open_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM drafts WHERE admin_open_id=%s", (admin_open_id,))
        return cur.fetchone()


def clear_draft(admin_open_id):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM drafts WHERE admin_open_id=%s", (admin_open_id,))


# ---------------- 招聘渠道日报模块 ----------------

def list_job_requests(only_open=True):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if only_open:
            cur.execute("SELECT * FROM job_requests WHERE status='open' ORDER BY id")
        else:
            cur.execute("SELECT * FROM job_requests ORDER BY id")
        return cur.fetchall()


def seed_job_requests():
    """首次为空时给几个示例职位，方便直接试。已有职位则跳过。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM job_requests LIMIT 1")
        if cur.fetchone():
            return
        cur.execute("""INSERT INTO job_requests (title, target_headcount, target_resume_count)
                       VALUES ('后端工程师',5,300),('产品经理',3,250),('HRBP',2,150)""")


def create_job_request(title, target_headcount=0, target_resume_count=0):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO job_requests (title, target_headcount, target_resume_count)
                       VALUES (%s,%s,%s) RETURNING id""",
                    (title, target_headcount, target_resume_count))
        return cur.fetchone()[0]


def list_channel_records(day=None):
    """某日（或最近）渠道记录，带职位名。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        if day:
            cur.execute("""SELECT r.*, j.title AS job_title FROM channel_daily r
                           JOIN job_requests j ON j.id = r.job_request_id
                           WHERE r.record_date=%s ORDER BY r.channel, j.title""", (day,))
        else:
            cur.execute("""SELECT r.*, j.title AS job_title FROM channel_daily r
                           JOIN job_requests j ON j.id = r.job_request_id
                           ORDER BY r.record_date DESC, r.channel LIMIT 500""")
        return cur.fetchall()


def channel_rows_upto(day):
    """截止到 day（含）的所有渠道记录，供分析（滚动/累计/环比）用。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM channel_daily WHERE record_date<=%s", (day,))
        return cur.fetchall()


def get_channel_record(rid):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM channel_daily WHERE id=%s", (rid,))
        return cur.fetchone()


def create_channel_record(record_date, channel, job_request_id, filled_by,
                          new_resumes=0, passed_screening=0, recommended=0, rejected=0, note=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO channel_daily
            (record_date, channel, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (record_date, channel, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note))
        return cur.fetchone()[0]


def update_channel_record(rid, **fields):
    allowed = {"record_date", "channel", "job_request_id", "filled_by",
               "new_resumes", "passed_screening", "recommended", "rejected", "note"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=%s" for k in sets)
    vals = list(sets.values()) + [rid]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE channel_daily SET {cols}, updated_at=now() WHERE id=%s", vals)


def delete_channel_record(rid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM channel_daily WHERE id=%s", (rid,))
        return cur.rowcount > 0


def get_job_request(jid):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM job_requests WHERE id=%s", (jid,))
        return cur.fetchone()


def upsert_channel_record(record_date, channel, job_request_id, filled_by,
                          new_resumes=0, passed_screening=0, recommended=0, rejected=0, note=""):
    """单一 owner 键 (报告日,渠道,职位) 覆盖写：同组重传/更正会更新同一条，不新增第二份数字。
    填报人(filled_by)是受控 roster 选择、非唯一键，随更新一起写。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO channel_daily
            (record_date, channel, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (record_date, channel, job_request_id)
            DO UPDATE SET filled_by=EXCLUDED.filled_by,
                          new_resumes=EXCLUDED.new_resumes,
                          passed_screening=EXCLUDED.passed_screening,
                          recommended=EXCLUDED.recommended,
                          rejected=EXCLUDED.rejected,
                          note=EXCLUDED.note,
                          updated_at=now()""",
            (record_date, channel, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note))
