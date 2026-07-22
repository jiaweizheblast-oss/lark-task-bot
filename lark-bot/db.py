"""
数据库操作封装。
所有对 PostgreSQL 的读写都放这里，bot.py 和 overdue.py 都从这里调。
连接地址从环境变量 DATABASE_URL 读（Railway 会自动提供）。
"""
import os
import datetime
import hashlib
import json
import secrets
import psycopg2
import psycopg2.extras

DATABASE_URL = os.environ.get("DATABASE_URL", "")
SCHEMA_MIGRATIONS = (
    ("20260722_recruiting_core_v1", "schema.sql"),
    ("20260722_recruiting_core_v2", "schema_20260722_recruiting_core_v2.sql"),
    ("20260722_talent_publication_queue_v1", "schema_20260722_talent_publication_queue_v1.sql"),
    ("20260722_talent_daily_publication_v2", "schema_20260722_talent_daily_publication_v2.sql"),
)


def get_conn():
    """开一个新的数据库连接。用完即关，简单稳妥。"""
    if not DATABASE_URL:
        raise RuntimeError("Missing DATABASE_URL environment variable — connect the database in Railway")
    conn = psycopg2.connect(DATABASE_URL)
    conn.autocommit = True
    return conn


def init_db():
    """Apply each immutable schema migration exactly once.

    Railway may briefly overlap old and new containers during a deploy. A
    transaction-scoped advisory lock prevents two current containers from
    migrating the same PostgreSQL database concurrently, while the explicit
    transaction guarantees that a failed migration cannot commit halfway.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("nexus_schema_migration",),
            )
            cur.execute("""CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                checksum_sha256 TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
            )""")
            for version, filename in SCHEMA_MIGRATIONS:
                with open(os.path.join(here, filename), "r", encoding="utf-8") as f:
                    sql = f.read()
                checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
                cur.execute(
                    "SELECT checksum_sha256 FROM schema_migrations WHERE version=%s",
                    (version,),
                )
                applied = cur.fetchone()
                if applied:
                    if applied[0] != checksum:
                        raise RuntimeError(
                            "Applied migration %s does not match the packaged checksum; "
                            "publish a new immutable migration version" % version
                        )
                    continue
                cur.execute(sql)
                cur.execute(
                    "INSERT INTO schema_migrations(version,checksum_sha256) VALUES(%s,%s)",
                    (version, checksum),
                )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
    print("[db] schema ready: %s" % SCHEMA_MIGRATIONS[-1][0])


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

OPERATIONAL_JOB_STATUSES = ("draft", "open", "paused", "closing", "closed")


def list_job_requests(only_open=True, include_search_profiles=False):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        where = []
        params = []
        if not include_search_profiles:
            where.append("record_type='operational'")
        if only_open:
            where.append("status='open'")
        sql = """SELECT j.*, COALESCE((SELECT array_agg(a.title ORDER BY a.created_at)
                  FROM job_request_title_alias a WHERE a.job_request_id=j.id),
                  ARRAY[]::text[]) AS title_aliases FROM job_requests j"""
        if where:
            sql += " WHERE " + " AND ".join("j." + item for item in where)
        sql += " ORDER BY CASE j.status WHEN 'open' THEN 0 WHEN 'paused' THEN 1 WHEN 'draft' THEN 2 WHEN 'closing' THEN 3 ELSE 4 END, j.id"
        cur.execute(sql, params)
        return cur.fetchall()


def list_search_profiles():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM job_requests WHERE record_type='search_profile' ORDER BY id")
        return cur.fetchall()


def seed_job_requests():
    """首次为空时给几个示例职位，方便直接试。已有职位则跳过。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT 1 FROM job_requests WHERE record_type='operational' LIMIT 1")
        if cur.fetchone():
            return
        # Production should start empty.  Real requisitions are created
        # explicitly by a manager and never seeded from search profiles.


def create_job_request(title, target_headcount=0, target_resume_count=0, owner="",
                       country="", location="", department="", status="draft",
                       search_profile_ref=None):
    status = str(status or "draft").strip().casefold()
    if status not in OPERATIONAL_JOB_STATUSES:
        raise ValueError("invalid job lifecycle status")
    job_ref = "REQ-" + datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%d") + "-" + secrets.token_hex(4).upper()
    conn = get_conn(); conn.autocommit = False
    try:
      with conn.cursor() as cur:
        cur.execute("""INSERT INTO job_requests
                       (job_ref,record_type,title,target_headcount,target_resume_count,
                        owner,country,location,department,status,search_profile_ref)
                       VALUES (%s,'operational',%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                    (job_ref, title, target_headcount, target_resume_count, owner,
                     country, location, department, status, search_profile_ref or None))
        job_id = cur.fetchone()[0]
        cur.execute("INSERT INTO job_request_title_alias(job_request_id,title) VALUES(%s,%s) ON CONFLICT DO NOTHING",
                    (job_id, title))
      conn.commit(); return job_id
    except Exception:
      conn.rollback(); raise
    finally:
      conn.close()


def update_job_request(jid, **fields):
    allowed = {"title", "target_headcount", "target_resume_count", "status", "owner",
               "country", "location", "department", "close_reason"}
    allowed.add("search_profile_ref")
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    conn = get_conn()
    conn.autocommit = False
    try:
      with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM job_requests WHERE id=%s FOR UPDATE", (jid,))
        current = cur.fetchone()
        if not current:
            raise ValueError("job not found")
        if current.get("record_type") != "operational":
            raise ValueError("search profiles are read-only in Job Reqs")
        requested_status = sets.get("status", current.get("status"))
        if requested_status not in OPERATIONAL_JOB_STATUSES:
            raise ValueError("invalid job lifecycle status")
        transitions = {
            "draft": {"draft", "open", "closed"},
            "open": {"open", "paused", "closing", "closed"},
            "paused": {"paused", "open", "closing", "closed"},
            "closing": {"closing", "closed"},
            "closed": {"closed"},
        }
        if requested_status not in transitions.get(current.get("status"), set()):
            raise ValueError("invalid job lifecycle transition")
        definition_keys = {"title", "country", "location", "department", "search_profile_ref"}
        operations_keys = {"owner", "target_headcount", "target_resume_count"}
        changed_definition = any(k in sets and sets[k] != current.get(k) for k in definition_keys)
        changed_operations = any(k in sets and sets[k] != current.get(k) for k in operations_keys)
        catalog_changed = changed_definition or requested_status != current.get("status")
        sets["definition_revision"] = int(current.get("definition_revision") or 1) + (1 if changed_definition else 0)
        sets["operations_revision"] = int(current.get("operations_revision") or 1) + (1 if changed_operations else 0)
        sets["catalog_revision"] = int(current.get("catalog_revision") or 1) + (1 if catalog_changed else 0)
        sets["record_version"] = int(current.get("record_version") or 1) + 1
        if requested_status == "closed" and current.get("status") != "closed":
            sets["closed_at"] = datetime.datetime.now(datetime.timezone.utc)
        if "title" in sets and sets["title"] != current.get("title"):
            cur.execute(
                """INSERT INTO job_request_title_alias(job_request_id,title)
                   VALUES(%s,%s),(%s,%s) ON CONFLICT DO NOTHING""",
                (jid, current.get("title"), jid, sets["title"]),
            )
        cols = ", ".join(f"{k}=%s" for k in sets)
        vals = list(sets.values()) + [jid]
        cur.execute(f"UPDATE job_requests SET {cols}, updated_at=now() WHERE id=%s", vals)
      conn.commit()
    except Exception:
      conn.rollback()
      raise
    finally:
      conn.close()


def operational_job_catalog(statuses=("open",)):
    statuses = tuple(statuses or ())
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT id,job_ref,title,status,country,location,department,
                      catalog_revision,record_version
               FROM job_requests
               WHERE record_type='operational' AND status = ANY(%s)
               ORDER BY title,job_ref""",
            (list(statuses),),
        )
        return cur.fetchall()


def operational_title_conflict(title, exclude_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT 1 FROM job_requests
               WHERE record_type='operational'
                 AND lower(btrim(title))=lower(btrim(%s))
                 AND status <> 'closed'
                 AND (%s IS NULL OR id <> %s)
               LIMIT 1""",
            (title, exclude_id, exclude_id),
        )
        return cur.fetchone() is not None


def store_talent_snapshot(envelope):
    """Append one verified mirror and sync core Job Refs atomically."""
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT pg_advisory_xact_lock(hashtext(%s))",
                ("talent_snapshot_ingest_v1",),
            )
            cur.execute(
                "SELECT content_sha256, generated_at FROM talent_snapshot "
                "ORDER BY generated_at DESC, received_at DESC LIMIT 1 FOR UPDATE"
            )
            latest = cur.fetchone()
            if latest and latest["content_sha256"] == envelope["content_sha256"]:
                conn.commit()
                return {"accepted": False, "idempotent": True}
            generated_at = datetime.datetime.fromisoformat(envelope["generated_at"])
            if latest and latest["generated_at"] > generated_at:
                raise ValueError("snapshot is older than the current mirror")
            cur.execute(
                """INSERT INTO talent_snapshot
                   (content_sha256, schema_version, source_system, generated_at, content, signature)
                   VALUES (%s,%s,%s,%s,%s::jsonb,%s)
                   ON CONFLICT (content_sha256) DO NOTHING""",
                (
                    envelope["content_sha256"], envelope["schema_version"],
                    envelope["source_system"], generated_at,
                    psycopg2.extras.Json(envelope["content"]), envelope["signature"],
                ),
            )
            inserted = cur.rowcount == 1
            for job in envelope["content"]["jobs"]:
                core_status = str(job.get("status") or "").casefold()
                local_status = "open" if core_status in {"active", "open"} else "closed"
                cur.execute(
                    """INSERT INTO job_requests
                       (job_ref,record_type,title,target_headcount,target_resume_count,
                        status,owner,core_job_ref,core_requested_contact_count,definition_sha256)
                       VALUES (%s,'search_profile',%s,0,0,%s,'',%s,%s,%s)
                       ON CONFLICT (core_job_ref) WHERE core_job_ref IS NOT NULL DO UPDATE SET
                         title=EXCLUDED.title,
                         core_requested_contact_count=EXCLUDED.core_requested_contact_count,
                         status=EXCLUDED.status,
                         definition_sha256=EXCLUDED.definition_sha256,
                         record_type='search_profile',
                         updated_at=now()""",
                    (
                        "SEARCH-" + str(job["core_job_ref"]).replace("-", ""),
                        str(job.get("title") or "Untitled"),
                        local_status,
                        job["core_job_ref"],
                        int(job.get("requested_contact_count") or 0),
                        str(job.get("definition_sha256") or ""),
                    ),
                )
        conn.commit()
        return {"accepted": inserted, "idempotent": not inserted}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_latest_talent_snapshot():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT content_sha256, schema_version, source_system,
                      generated_at, content, received_at
               FROM talent_snapshot
               ORDER BY generated_at DESC, received_at DESC LIMIT 1"""
        )
        return cur.fetchone()


def get_job_request_by_core_ref(core_job_ref):
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT * FROM job_requests WHERE core_job_ref=%s",
            (core_job_ref,),
        )
        return cur.fetchone()


def get_job_request_by_ref(job_ref):
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT * FROM job_requests WHERE job_ref=%s",
            (job_ref,),
        )
        return cur.fetchone()


def enqueue_talent_search_task(task):
    """Persist one immutable, idempotent read-only preview command."""
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            """INSERT INTO talent_search_task
               (task_id, schema_version, task_type, revision, status,
                core_job_ref, payload, payload_sha256, expires_at)
               VALUES (%s,%s,%s,%s,'pending',%s,%s::jsonb,%s,%s)
               ON CONFLICT (task_id) DO NOTHING""",
            (
                task["task_id"], task["schema_version"], task["task_type"],
                task["revision"], task["core_job_ref"],
                psycopg2.extras.Json(task), task["payload_sha256"],
                datetime.datetime.fromisoformat(task["expires_at"]),
            ),
        )
        inserted = cur.rowcount == 1
        cur.execute(
            "SELECT * FROM talent_search_task WHERE task_id=%s",
            (task["task_id"],),
        )
        row = cur.fetchone()
        if not row or row["payload_sha256"] != task["payload_sha256"]:
            raise ValueError("task_id already exists with another payload")
        return row, inserted


def list_talent_search_tasks(limit=50):
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            """SELECT * FROM talent_search_task
               ORDER BY created_at DESC LIMIT %s""",
            (limit,),
        )
        return cur.fetchall()


def get_talent_search_task(task_id):
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT * FROM talent_search_task WHERE task_id=%s",
            (task_id,),
        )
        return cur.fetchone()


def retry_failed_talent_search_task(task_id):
    """Requeue the exact immutable command after its failure is repaired."""
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT * FROM talent_search_task WHERE task_id=%s FOR UPDATE",
            (task_id,),
        )
        row = cur.fetchone()
        if not row:
            raise ValueError("Search task was not found")
        if row["status"] != "failed" and not (
            row["status"] == "pending" and int(row["attempt_count"] or 0) >= 3
        ):
            raise ValueError("Only a failed or exhausted search task can be retried")
        if row["expires_at"] <= datetime.datetime.now(datetime.timezone.utc):
            raise ValueError("Search task has expired; create a new search")
        cur.execute(
            """UPDATE talent_search_task
               SET status='pending', attempt_count=0,
                   worker_id=NULL, lease_token_sha256=NULL,
                   claimed_at=NULL, lease_expires_at=NULL,
                   last_error_code=NULL, result=NULL, result_sha256=NULL,
                   publication_status='not_ready', publication='{}'::jsonb,
                   published_at=NULL, updated_at=now()
               WHERE task_id=%s
               RETURNING *""",
            (task_id,),
        )
        return cur.fetchone()


def claim_talent_search_task(worker_id, lease_seconds):
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """UPDATE talent_search_task
                   SET status='pending', worker_id=NULL,
                       lease_token_sha256=NULL, claimed_at=NULL,
                       lease_expires_at=NULL, updated_at=now()
                   WHERE status='claimed' AND lease_expires_at < now()
                     AND attempt_count < 3 AND expires_at > now()"""
            )
            cur.execute(
                """UPDATE talent_search_task
                   SET status='failed', last_error_code='lease_attempts_exhausted',
                       lease_token_sha256=NULL, lease_expires_at=NULL,
                       updated_at=now()
                   WHERE status='claimed' AND lease_expires_at < now()
                     AND attempt_count >= 3"""
            )
            # Older workers could leave an exhausted command in pending,
            # which made it permanently invisible to both claim and retry.
            cur.execute(
                """UPDATE talent_search_task
                   SET status='failed', last_error_code='attempts_exhausted',
                       worker_id=NULL, lease_token_sha256=NULL,
                       claimed_at=NULL, lease_expires_at=NULL,
                       updated_at=now()
                   WHERE status='pending' AND attempt_count >= 3"""
            )
            cur.execute(
                """UPDATE talent_search_task
                   SET status='cancelled', last_error_code='task_expired',
                       updated_at=now()
                   WHERE status IN ('pending','claimed') AND expires_at <= now()"""
            )
            cur.execute(
                """SELECT * FROM talent_search_task
                   WHERE status='pending' AND expires_at > now()
                     AND attempt_count < 3
                   ORDER BY created_at
                   FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            lease_token = secrets.token_urlsafe(32)
            lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
            cur.execute(
                """UPDATE talent_search_task
                   SET status='claimed', worker_id=%s,
                       lease_token_sha256=%s, claimed_at=now(),
                       lease_expires_at=now() + (%s * interval '1 second'),
                       attempt_count=attempt_count+1, updated_at=now()
                   WHERE task_id=%s RETURNING *""",
                (worker_id, lease_hash, lease_seconds, row["task_id"]),
            )
            claimed = cur.fetchone()
        conn.commit()
        return claimed, lease_token
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def heartbeat_talent_search_task(task_id, worker_id, lease_token, lease_seconds):
    lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE talent_search_task
               SET lease_expires_at=now() + (%s * interval '1 second'),
                   updated_at=now()
               WHERE task_id=%s AND status='claimed' AND worker_id=%s
                 AND lease_token_sha256=%s AND lease_expires_at > now()
               RETURNING task_id""",
            (lease_seconds, task_id, worker_id, lease_hash),
        )
        return cur.fetchone() is not None


def complete_talent_search_task(task_id, worker_id, lease_token, result, result_sha256):
    lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
    terminal_status = (
        "succeeded"
        if result.get("quota_fulfilled") is True and result.get("applicable") is True
        else "shortfall"
    )
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT status, result_sha256 FROM talent_search_task WHERE task_id=%s",
            (task_id,),
        )
        existing = cur.fetchone()
        if not existing:
            return None
        if existing["status"] in {"succeeded", "shortfall"}:
            if existing["result_sha256"] == result_sha256:
                return {"status": existing["status"], "idempotent": True}
            raise ValueError("terminal task already has a different result")
        cur.execute(
            """UPDATE talent_search_task
               SET status=%s, result=%s::jsonb, result_sha256=%s,
                    publication_status=%s,
                    lease_token_sha256=NULL, lease_expires_at=NULL,
                    updated_at=now()
               WHERE task_id=%s AND status='claimed' AND worker_id=%s
                 AND lease_token_sha256=%s AND lease_expires_at > now()
               RETURNING status""",
            (
                terminal_status, psycopg2.extras.Json(result), result_sha256,
                "ready" if terminal_status == "succeeded" else "not_ready",
                task_id, worker_id, lease_hash,
            ),
        )
        row = cur.fetchone()
        return (
            {"status": row["status"], "idempotent": False}
            if row else None
        )


def get_talent_daily_publication(publication_id):
    with get_conn() as conn, conn.cursor(
        cursor_factory=psycopg2.extras.RealDictCursor
    ) as cur:
        cur.execute(
            "SELECT * FROM talent_daily_publication WHERE publication_id=%s",
            (publication_id,),
        )
        return cur.fetchone()


def queue_talent_daily_publication(
    publication_id, business_date, publication, task_ids,
):
    """Atomically queue one immutable business-date multi-job publication."""
    ordered_ids = list(task_ids)
    if not ordered_ids or len(set(ordered_ids)) != len(ordered_ids):
        raise ValueError("publication task identities are empty or duplicated")
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """SELECT * FROM talent_daily_publication
                   WHERE business_date=%s FOR UPDATE""",
                (business_date,),
            )
            existing = cur.fetchone()
            if existing:
                if existing["payload_sha256"] != publication["payload_sha256"]:
                    raise ValueError(
                        "today already has a different immutable publication batch"
                    )
                conn.commit()
                return existing
            cur.execute(
                """SELECT task_id,status,publication_status
                   FROM talent_search_task
                   WHERE task_id = ANY(%s::uuid[])
                   FOR UPDATE""",
                (ordered_ids,),
            )
            rows = cur.fetchall()
            if len(rows) != len(ordered_ids):
                raise ValueError("one or more publication search tasks are missing")
            for row in rows:
                if (
                    row["status"] != "succeeded"
                    or row["publication_status"] not in {"ready", "failed"}
                ):
                    raise ValueError(
                        "every publication cohort must be successful and ready"
                    )
            cur.execute(
                """INSERT INTO talent_daily_publication
                   (publication_id,business_date,revision,status,payload,payload_sha256)
                   VALUES (%s,%s,%s,'queued',%s::jsonb,%s)
                   RETURNING *""",
                (
                    publication_id,
                    business_date,
                    int(publication.get("revision") or 1),
                    psycopg2.extras.Json(publication),
                    publication["payload_sha256"],
                ),
            )
            queued = cur.fetchone()
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO talent_daily_publication_item
                   (publication_id,task_id,cohort_order) VALUES %s""",
                [
                    (publication_id, task_id, index)
                    for index, task_id in enumerate(ordered_ids, start=1)
                ],
            )
            cur.execute(
                """UPDATE talent_search_task
                   SET publication_status='queued',
                       publication=%s::jsonb, updated_at=now()
                   WHERE task_id = ANY(%s::uuid[])""",
                (
                    psycopg2.extras.Json({
                        "publication_id": str(publication_id),
                        "business_date": str(business_date),
                        "payload_sha256": publication["payload_sha256"],
                    }),
                    ordered_ids,
                ),
            )
        conn.commit()
        return queued
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def reset_talent_daily_publication(publication_id):
    """Remove an unpublished publication command and release its cohorts.

    Frozen search receipts remain available for audit.  Their publication
    state is reset to ``not_ready`` so a cancelled cohort cannot be included
    in a later one-click publication accidentally.
    """
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """SELECT publication_id,status FROM talent_daily_publication
                   WHERE publication_id=%s FOR UPDATE""",
                (publication_id,),
            )
            publication = cur.fetchone()
            if not publication:
                conn.commit()
                return {"status": "absent", "reset": False, "task_count": 0}
            if publication["status"] == "published":
                raise ValueError("a published recruiting table cannot be reset")
            cur.execute(
                """SELECT task_id FROM talent_daily_publication_item
                   WHERE publication_id=%s ORDER BY cohort_order""",
                (publication_id,),
            )
            task_ids = [row["task_id"] for row in cur.fetchall()]
            if task_ids:
                cur.execute(
                    """UPDATE talent_search_task
                       SET status='cancelled', last_error_code='cancelled_by_manager',
                           publication_status='not_ready', publication='{}'::jsonb,
                           worker_id=NULL, lease_token_sha256=NULL, claimed_at=NULL,
                           lease_expires_at=NULL, published_at=NULL, updated_at=now()
                       WHERE task_id = ANY(%s::uuid[])
                         AND publication_status <> 'published'""",
                    (task_ids,),
                )
            cur.execute(
                "DELETE FROM talent_daily_publication_item WHERE publication_id=%s",
                (publication_id,),
            )
            cur.execute(
                "DELETE FROM talent_daily_publication WHERE publication_id=%s",
                (publication_id,),
            )
        conn.commit()
        return {
            "status": "reset",
            "reset": True,
            "task_count": len(task_ids),
        }
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def claim_talent_daily_publication(worker_id, lease_seconds):
    """Lease one manager-approved daily batch to the local Windows worker."""
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """UPDATE talent_daily_publication
                   SET status='queued', worker_id=NULL,
                       lease_token_sha256=NULL, claimed_at=NULL,
                       lease_expires_at=NULL, updated_at=now()
                   WHERE status='publishing' AND lease_expires_at < now()
                     AND attempt_count < 3"""
            )
            cur.execute(
                """UPDATE talent_daily_publication
                   SET status='failed',
                       last_error_code='publication_lease_attempts_exhausted',
                       lease_token_sha256=NULL, lease_expires_at=NULL,
                       updated_at=now()
                   WHERE status='publishing' AND lease_expires_at < now()
                     AND attempt_count >= 3"""
            )
            cur.execute(
                """UPDATE talent_search_task t
                   SET publication_status=p.status, updated_at=now()
                   FROM talent_daily_publication_item i
                   JOIN talent_daily_publication p
                     ON p.publication_id=i.publication_id
                   WHERE t.task_id=i.task_id
                     AND p.status IN ('queued','failed')
                     AND t.publication_status='publishing'"""
            )
            cur.execute(
                """SELECT * FROM talent_daily_publication
                   WHERE status='queued'
                   ORDER BY updated_at
                   FOR UPDATE SKIP LOCKED LIMIT 1"""
            )
            row = cur.fetchone()
            if not row:
                conn.commit()
                return None
            lease_token = secrets.token_urlsafe(32)
            lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
            cur.execute(
                """UPDATE talent_daily_publication
                   SET status='publishing', worker_id=%s,
                       lease_token_sha256=%s, claimed_at=now(),
                       lease_expires_at=now() + (%s * interval '1 second'),
                       attempt_count=attempt_count+1, updated_at=now()
                   WHERE publication_id=%s RETURNING *""",
                (worker_id, lease_hash, lease_seconds, row["publication_id"]),
            )
            claimed = cur.fetchone()
            cur.execute(
                """UPDATE talent_search_task t
                   SET publication_status='publishing', updated_at=now()
                   FROM talent_daily_publication_item i
                   WHERE i.publication_id=%s AND i.task_id=t.task_id""",
                (row["publication_id"],),
            )
        conn.commit()
        return claimed, lease_token
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def heartbeat_talent_daily_publication(
    publication_id, worker_id, lease_token, lease_seconds,
):
    lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE talent_daily_publication
               SET lease_expires_at=now() + (%s * interval '1 second'),
                   updated_at=now()
               WHERE publication_id=%s AND status='publishing' AND worker_id=%s
                 AND lease_token_sha256=%s AND lease_expires_at > now()
               RETURNING publication_id""",
            (lease_seconds, publication_id, worker_id, lease_hash),
        )
        return cur.fetchone() is not None


def finish_talent_daily_publication(
    publication_id, worker_id, lease_token, status, publication,
):
    """Finish only the local worker lease that owns this publication."""
    if status not in {"published", "failed"}:
        raise ValueError("invalid terminal publication status")
    lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(
            cursor_factory=psycopg2.extras.RealDictCursor
        ) as cur:
            cur.execute(
                """UPDATE talent_daily_publication
                   SET status=%s, receipt=%s::jsonb,
                       published_at=CASE WHEN %s='published' THEN now() ELSE published_at END,
                       lease_token_sha256=NULL, lease_expires_at=NULL,
                       updated_at=now()
                   WHERE publication_id=%s AND status='publishing' AND worker_id=%s
                     AND lease_token_sha256=%s AND lease_expires_at > now()
                   RETURNING status,receipt,published_at""",
                (
                    status, psycopg2.extras.Json(publication or {}), status,
                    publication_id, worker_id, lease_hash,
                ),
            )
            completed = cur.fetchone()
            if completed:
                cur.execute(
                    """UPDATE talent_search_task t
                       SET publication_status=%s, publication=%s::jsonb,
                           published_at=CASE WHEN %s='published' THEN now()
                                             ELSE published_at END,
                           updated_at=now()
                       FROM talent_daily_publication_item i
                       WHERE i.publication_id=%s AND i.task_id=t.task_id""",
                    (
                        status,
                        psycopg2.extras.Json(publication or {}),
                        status,
                        publication_id,
                    ),
                )
        conn.commit()
        return completed
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def fail_talent_search_task(task_id, worker_id, lease_token, error_code):
    lease_hash = hashlib.sha256(lease_token.encode("utf-8")).hexdigest()
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """UPDATE talent_search_task
               SET status='failed', last_error_code=%s,
                   lease_token_sha256=NULL, lease_expires_at=NULL,
                   updated_at=now()
               WHERE task_id=%s AND status='claimed' AND worker_id=%s
                 AND lease_token_sha256=%s AND lease_expires_at > now()
               RETURNING task_id""",
            (error_code, task_id, worker_id, lease_hash),
        )
        return cur.fetchone() is not None


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


def list_channel_records_range(dfrom, dto):
    """[dfrom, dto] 区间内所有渠道记录（含职位名），供多粒度看板分析用。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT r.*, j.title AS job_title FROM channel_daily r
                       JOIN job_requests j ON j.id = r.job_request_id
                       WHERE r.record_date BETWEEN %s AND %s
                       ORDER BY r.record_date, r.channel""", (dfrom, dto))
        return cur.fetchall()


def earliest_channel_date():
    """最早一条渠道记录的日期（无数据返回 None）。用于推断上线日默认值。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT MIN(record_date) FROM channel_daily")
        return cur.fetchone()[0]


def channel_data_days():
    """所有有数据的日期（ISO 字符串列表），供日历标注「哪天真的上传过」。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT DISTINCT record_date FROM channel_daily ORDER BY 1")
        return [r[0].isoformat() for r in cur.fetchall()]


def get_channel_record(rid):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM channel_daily WHERE id=%s", (rid,))
        return cur.fetchone()


def create_channel_record(record_date, channel, job_request_id, filled_by,
                          new_resumes=0, passed_screening=0, recommended=0, rejected=0, note="",
                          source_detail=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO channel_daily
            (record_date, channel, source_detail, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (record_date, channel, source_detail, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note))
        return cur.fetchone()[0]


def update_channel_record(rid, **fields):
    allowed = {"record_date", "channel", "source_detail", "job_request_id", "filled_by",
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


def reset_channel_analytics_test_data():
    """Clear Channel Analytics test data while preserving all configuration.

    This deliberately keeps job requisitions, search profiles, channel
    catalogs, roster, Lark Base settings, and every Talent Discovery mirror.
    All database deletes commit together or roll back together.
    """
    conn = get_conn()
    conn.autocommit = False
    counts = {}
    try:
        with conn.cursor() as cur:
            for key, statement in (
                ("application_stage_events",
                 "DELETE FROM candidate_application_stage_event"),
                ("legacy_stage_events", "DELETE FROM candidate_stage_event"),
                ("submission_events", "DELETE FROM channel_submission_event"),
                ("applications", "DELETE FROM candidate_application"),
                ("candidates", "DELETE FROM candidate"),
                ("manual_batch_counts", "DELETE FROM channel_daily"),
            ):
                cur.execute(statement)
                counts[key] = cur.rowcount
            cur.execute(
                "UPDATE settings SET value='' WHERE key IN (%s,%s)",
                ("lark_channel_last_sync", "lark_channel_last_attempt"),
            )
        conn.commit()
        return counts
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def get_job_request(jid):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM job_requests WHERE id=%s", (jid,))
        return cur.fetchone()


def upsert_channel_record(record_date, channel, job_request_id, filled_by,
                          new_resumes=0, passed_screening=0, recommended=0, rejected=0, note="",
                          source_detail=""):
    """单一 owner 键 (报告日,渠道,职位) 覆盖写：同组重传/更正会更新同一条，不新增第二份数字。
    填报人(filled_by)是受控 roster 选择、非唯一键，随更新一起写。"""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO channel_daily
            (record_date, channel, source_detail, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            ON CONFLICT (record_date, channel, source_detail, job_request_id)
            DO UPDATE SET filled_by=EXCLUDED.filled_by,
                          new_resumes=EXCLUDED.new_resumes,
                          passed_screening=EXCLUDED.passed_screening,
                          recommended=EXCLUDED.recommended,
                          rejected=EXCLUDED.rejected,
                          note=EXCLUDED.note,
                          updated_at=now()""",
            (record_date, channel, source_detail, job_request_id, filled_by,
             new_resumes, passed_screening, recommended, rejected, note))


# ================= Nexus：候选人级招聘跟踪（每行一个候选人；渠道/漏斗/速度由此派生） =================
def list_candidates(day=None, limit=500):
    """Application list with candidate identity and requisition context."""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        sql = """SELECT a.id,a.application_ref,a.candidate_id,c.name,
                        a.entry_date AS apply_date,a.channel,a.source_detail,
                        a.job_request_id,a.current_stage AS status,a.note,
                        a.hr_owner AS filled_by,a.source,a.external_ref AS ext_ref,
                        a.candidate_url,a.cv_url,
                        a.lark_record_id,a.record_version,a.created_at,a.updated_at,
                        j.title AS job_title,e.effective_date AS stage_date,e.rejection_reason
                 FROM candidate_application a
                 JOIN candidate c ON c.id=a.candidate_id
                 LEFT JOIN job_requests j ON j.id=a.job_request_id
                 LEFT JOIN LATERAL (
                   SELECT effective_date,rejection_reason
                   FROM candidate_application_stage_event
                   WHERE application_id=a.id ORDER BY created_at DESC,id DESC LIMIT 1
                 ) e ON true"""
        if day:
            cur.execute(sql + " WHERE a.entry_date=%s ORDER BY a.id DESC", (day,))
        else:
            cur.execute(sql + " ORDER BY a.entry_date DESC,a.id DESC LIMIT %s", (limit,))
        return cur.fetchall()


def list_candidates_active():
    """在跟进中的候选人（状态未到终态：非 已录用/已拒绝），供下发「跟进表」预填。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT c.*, j.title AS job_title,
                               e.effective_date AS stage_date, e.rejection_reason
                       FROM candidate c
                       LEFT JOIN job_requests j ON j.id = c.job_request_id
                       LEFT JOIN LATERAL (
                         SELECT effective_date,rejection_reason FROM candidate_stage_event
                         WHERE candidate_id=c.id ORDER BY created_at DESC,id DESC LIMIT 1
                       ) e ON true
                       WHERE c.status NOT IN ('Hired','Rejected','Withdrawn','已录用','已拒绝')
                       ORDER BY c.apply_date DESC, c.id DESC""")
        return cur.fetchall()


def list_candidate_applications_active():
    """HR work queue: one row per candidate×requisition application."""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT a.application_ref,a.id AS application_id,
                              a.candidate_id,c.name,a.entry_date AS apply_date,
                              a.channel,a.source_detail,a.job_request_id,
                              a.current_stage AS status,a.note,a.hr_owner AS filled_by,
                              a.external_ref AS ext_ref,a.candidate_url,a.cv_url,
                              a.lark_record_id,a.record_version,
                              j.title AS job_title,j.job_ref,j.status AS job_status,
                              e.effective_date AS stage_date,e.rejection_reason
                       FROM candidate_application a
                       JOIN candidate c ON c.id=a.candidate_id
                       LEFT JOIN job_requests j ON j.id=a.job_request_id
                       LEFT JOIN LATERAL (
                         SELECT effective_date,rejection_reason
                         FROM candidate_application_stage_event
                         WHERE application_id=a.id ORDER BY created_at DESC,id DESC LIMIT 1
                       ) e ON true
                       WHERE a.current_stage NOT IN
                         ('Hired','Rejected','Withdrawn','Resigned','已录用','已拒绝')
                       ORDER BY a.entry_date DESC,a.id DESC""")
        return cur.fetchall()


def list_candidate_application_snapshot():
    """Current manager snapshot for every candidate × hiring requisition.

    Baseline rows are intentionally included. They describe today's portfolio
    even though events that happened before the recruiting-core go-live date
    cannot be reconstructed safely.
    """
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT a.application_ref,a.job_request_id,
                      a.current_stage AS status,a.channel,
                      a.hr_owner AS filled_by,a.baseline_import,
                      a.source_received_on,a.entry_date,
                      j.title AS job_title,j.status AS job_status
               FROM candidate_application a
               LEFT JOIN job_requests j ON j.id=a.job_request_id
               ORDER BY a.entry_date DESC,a.id DESC"""
        )
        return cur.fetchall()


def list_lark_referenced_job_request_ids():
    """Return only requisitions referenced by persistent Lark application rows.

    These IDs keep a historical value available in the Lark Job dropdown so an
    accidentally edited existing row can be restored.  They do not authorize a
    new application against a closed requisition.
    """
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(
            """SELECT DISTINCT job_request_id
               FROM candidate_application
               WHERE lark_record_id IS NOT NULL
                 AND btrim(lark_record_id) <> ''
                 AND job_request_id IS NOT NULL"""
        )
        return [int(row[0]) for row in cur.fetchall()]


def get_candidate_application_by_ref(application_ref):
    if not str(application_ref or "").strip():
        return None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT a.*,c.name FROM candidate_application a
                       JOIN candidate c ON c.id=a.candidate_id
                       WHERE a.application_ref=%s""", (application_ref,))
        return cur.fetchone()


def get_candidate_application(application_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT a.*,c.name FROM candidate_application a
                       JOIN candidate c ON c.id=a.candidate_id WHERE a.id=%s""",
                    (application_id,))
        return cur.fetchone()


def get_candidate_application_by_lark(record_id):
    if not str(record_id or "").strip():
        return None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT a.*,c.name FROM candidate_application a
                       JOIN candidate c ON c.id=a.candidate_id
                       WHERE a.lark_record_id=%s LIMIT 1""", (record_id,))
        return cur.fetchone()


_TERMINAL_APPLICATION_STAGES = {"Rejected", "Withdrawn", "Resigned"}
_APPLICATION_STAGE_ORDER = {
    "Pending": 0,
    "Contacted / Awaiting Reply": 1,
    "HR Screening": 2,
    "Interview": 3,
    "Offer": 4,
    "Hired": 5,
    "Resigned": 6,
}

_LEGACY_APPLICATION_STAGE = {
    "": "Pending", "New Lead": "Pending", "Interview 1": "Interview",
    "Interview 2 / Final": "Interview", "On Hold": "Withdrawn",
}


def _canonical_application_stage(value):
    stage = str(value or "").strip()
    return _LEGACY_APPLICATION_STAGE.get(stage, stage)


def _validate_application_stage_change(current_stage, next_stage):
    current = _canonical_application_stage(current_stage)
    target = _canonical_application_stage(next_stage)
    if current == target:
        return
    if current in _TERMINAL_APPLICATION_STAGES:
        raise ValueError(
            "%s is terminal; a manager correction event is required to reopen it" % current
        )
    if current == "Hired" and target != "Resigned":
        raise ValueError("A hired candidate can only move to Resigned")
    if target in {"Rejected", "Withdrawn"}:
        return
    if target not in _APPLICATION_STAGE_ORDER:
        raise ValueError("Current Stage is not approved")
    if _APPLICATION_STAGE_ORDER.get(target, -1) < _APPLICATION_STAGE_ORDER.get(current, -1):
        raise ValueError(
            "A backward stage change requires an audited manager correction"
        )


def apply_candidate_application_command(
    *, event_id, artifact_id, row_ref, payload_sha256, transport,
    entry_date, name, channel, source_detail, job_request_id, stage,
    note="", hr_owner="", rejection_reason="", application_ref="",
    expected_version=0, lark_record_id="", source="Excel", changed_by="",
    occurred_at=None, baseline_import=False, candidate_url="", cv_url="",
    stage_effective_date=None,
):
    """Apply one signed HR row as one serializable, idempotent transaction.

    Identity, requisition and first-touch attribution are immutable for an
    existing application.  A second application is required for another job;
    attribution corrections use a separate manager-only audit command.
    """
    event_id = str(event_id or "").strip()
    artifact_id = str(artifact_id or "").strip()
    row_ref = str(row_ref or "").strip()
    payload_sha256 = str(payload_sha256 or "").strip()
    if not all((event_id, artifact_id, row_ref, payload_sha256)):
        raise ValueError("The signed submission identity is incomplete")
    stage = _canonical_application_stage(stage)
    if stage not in _APPLICATION_STAGE_ORDER and stage not in {"Rejected", "Withdrawn"}:
        raise ValueError("Status is not approved")
    candidate_url = str(candidate_url or "").strip()
    cv_url = str(cv_url or "").strip()
    stage_effective_date = stage_effective_date or entry_date
    occurred_at = occurred_at or datetime.datetime.now(datetime.timezone.utc)

    conn = get_conn(); conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(
                "SELECT * FROM channel_submission_event WHERE event_id=%s FOR UPDATE",
                (event_id,),
            )
            receipt = cur.fetchone()
            if receipt:
                if receipt["payload_sha256"] != payload_sha256:
                    raise ValueError(
                        "This submission event was already accepted with different content"
                    )
                result = dict(receipt.get("result_json") or {})
                result["idempotent"] = True
                conn.commit()
                return result

            application = None
            if str(application_ref or "").strip():
                cur.execute(
                    """SELECT a.*,c.name FROM candidate_application a
                       JOIN candidate c ON c.id=a.candidate_id
                       WHERE a.application_ref=%s FOR UPDATE OF a,c""",
                    (application_ref,),
                )
                application = cur.fetchone()
                if not application:
                    raise ValueError("The candidate application no longer exists")
                if int(expected_version or 0) != int(application.get("record_version") or 0):
                    raise ValueError(
                        "This row is stale because the application changed after the workbook was downloaded"
                    )
                if str(application.get("name") or "").strip() != str(name or "").strip():
                    raise ValueError("Candidate is protected for an existing application")
                stored_candidate_url = str(application.get("candidate_url") or "").strip()
                if stored_candidate_url and candidate_url != stored_candidate_url:
                    raise ValueError("Candidate URL is protected for an existing application")
                if application.get("job_request_id") != job_request_id:
                    raise ValueError("Job is immutable; create a new application for another requisition")
                if (str(application.get("channel") or "").strip() != str(channel or "").strip()
                        or str(application.get("source_detail") or "").strip() !=
                        str(source_detail or "").strip()):
                    raise ValueError("Source attribution is immutable after the application is created")
                if lark_record_id:
                    cur.execute(
                        """SELECT application_ref FROM candidate_application
                           WHERE lark_record_id=%s AND application_ref<>%s LIMIT 1""",
                        (lark_record_id, application_ref),
                    )
                    if cur.fetchone():
                        raise ValueError("The Lark record is already bound to another application")
                _validate_application_stage_change(application.get("current_stage"), stage)
                stage_changed = str(application.get("current_stage") or "") != str(stage or "")
                metadata_changed = any((
                    str(application.get("note") or "") != str(note or ""),
                    str(application.get("hr_owner") or "") != str(hr_owner or ""),
                    (not stored_candidate_url and bool(candidate_url)),
                    str(application.get("cv_url") or "") != cv_url,
                    bool(lark_record_id) and str(application.get("lark_record_id") or "") != str(lark_record_id),
                ))
                if stage_changed:
                    cur.execute(
                        """INSERT INTO candidate_application_stage_event
                           (application_id,from_stage,to_stage,effective_date,rejection_reason,
                            note,changed_by,event_ref,occurred_at,baseline_import)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,FALSE)""",
                        (application["id"], application.get("current_stage") or "", stage,
                         stage_effective_date, rejection_reason, note, changed_by or hr_owner,
                         event_id, occurred_at),
                    )
                if stage_changed or metadata_changed:
                    cur.execute(
                        """UPDATE candidate_application
                           SET current_stage=%s,note=%s,hr_owner=%s,
                               candidate_url=CASE WHEN candidate_url='' THEN %s ELSE candidate_url END,
                               cv_url=%s,
                               lark_record_id=CASE WHEN %s='' THEN lark_record_id ELSE %s END,
                               stage_started_at=CASE WHEN %s THEN %s ELSE stage_started_at END,
                               record_version=record_version+1,updated_at=now()
                           WHERE id=%s RETURNING record_version""",
                        (stage, note, hr_owner, candidate_url, cv_url,
                         lark_record_id, lark_record_id,
                         stage_changed, occurred_at, application["id"]),
                    )
                    version = cur.fetchone()["record_version"]
                    if stage_changed:
                        cur.execute(
                            """UPDATE candidate SET status=%s,updated_at=now()
                               WHERE id=%s AND job_request_id IS NOT DISTINCT FROM %s""",
                            (stage, application["candidate_id"], application["job_request_id"]),
                        )
                else:
                    version = application["record_version"]
                result = {
                    "created": False, "updated": bool(stage_changed or metadata_changed),
                    "application_ref": application_ref, "record_version": int(version),
                    "application_id": int(application["id"]),
                    "candidate_id": int(application["candidate_id"]),
                    "idempotent": not (stage_changed or metadata_changed),
                }
            else:
                if int(expected_version or 0) != 0:
                    raise ValueError("A new row must have record version 0")
                cur.execute("SELECT id,status FROM job_requests WHERE id=%s FOR SHARE", (job_request_id,))
                job = cur.fetchone()
                if not job or str(job.get("status") or "").casefold() != "open":
                    raise ValueError("New candidates require an Open requisition")
                candidate_id = None
                cur.execute("SELECT id FROM candidate WHERE ext_ref=%s LIMIT 1 FOR UPDATE", (row_ref,))
                candidate = cur.fetchone()
                if candidate:
                    candidate_id = candidate["id"]
                    cur.execute(
                        """SELECT application_ref FROM candidate_application
                           WHERE candidate_id=%s AND job_request_id IS NOT DISTINCT FROM %s""",
                        (candidate_id, job_request_id),
                    )
                    if cur.fetchone():
                        raise ValueError("This Row Ref is already bound to an application")
                else:
                    cur.execute(
                        """INSERT INTO candidate
                           (apply_date,name,channel,source_detail,job_request_id,status,note,
                            filled_by,source,ext_ref,lark_record_id)
                           VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
                        (entry_date, name, channel, source_detail, job_request_id, stage,
                         note, hr_owner, source, row_ref, lark_record_id),
                    )
                    candidate_id = cur.fetchone()["id"]
                application_ref = "APP-" + secrets.token_hex(8).upper()
                # A newly accepted candidate is a new intake even when HR
                # first records them at a later stage (for example Interview).
                # Baseline is reserved for an explicit historical import.
                is_baseline = bool(baseline_import)
                cur.execute(
                    """INSERT INTO candidate_application
                       (application_ref,candidate_id,job_request_id,entry_date,channel,
                        source_detail,current_stage,note,hr_owner,source,external_ref,
                        candidate_url,cv_url,lark_record_id,record_version,
                        baseline_import,source_received_on,
                        stage_started_at,system_imported_at)
                       VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,%s,%s,%s,%s)
                       RETURNING id""",
                    (application_ref, candidate_id, job_request_id, entry_date, channel,
                     source_detail, stage, note, hr_owner, source, row_ref,
                     candidate_url, cv_url, lark_record_id,
                     is_baseline, None if is_baseline else entry_date, occurred_at, occurred_at),
                )
                application_id = cur.fetchone()["id"]
                cur.execute(
                    """INSERT INTO candidate_application_stage_event
                       (application_id,from_stage,to_stage,effective_date,rejection_reason,
                        note,changed_by,event_ref,occurred_at,baseline_import)
                       VALUES(%s,'',%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (application_id, stage, stage_effective_date, rejection_reason, note,
                     changed_by or hr_owner, event_id, occurred_at, is_baseline),
                )
                result = {
                    "created": True, "updated": False,
                    "application_ref": application_ref, "record_version": 1,
                    "application_id": int(application_id),
                    "candidate_id": int(candidate_id),
                    "idempotent": False, "baseline_import": is_baseline,
                }

            cur.execute(
                """INSERT INTO channel_submission_event
                   (event_id,artifact_id,row_ref,payload_sha256,transport,
                    application_ref,result_json)
                   VALUES(%s,%s,%s,%s,%s,%s,%s)""",
                (event_id, artifact_id, row_ref, payload_sha256, transport,
                 result.get("application_ref") or "", psycopg2.extras.Json(result)),
            )
        conn.commit()
        return result
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def create_candidate_application(entry_date, name, channel, job_request_id,
                                 note="", hr_owner="", source="manual",
                                 external_ref="", lark_record_id="",
                                 source_detail=""):
    """Create a person and application without name-based identity guessing.

    A trusted external_ref may reuse a person; the workflow remains a distinct
    candidate_application for each requisition.
    """
    conn = get_conn(); conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            candidate_id = None
            if str(external_ref or "").strip():
                cur.execute("SELECT id FROM candidate WHERE ext_ref=%s LIMIT 1 FOR UPDATE",
                            (external_ref,))
                row = cur.fetchone(); candidate_id = row["id"] if row else None
            if candidate_id is None:
                cur.execute("""INSERT INTO candidate
                    (apply_date,name,channel,source_detail,job_request_id,status,note,
                     filled_by,source,ext_ref,lark_record_id)
                    VALUES(%s,%s,%s,%s,%s,'New Lead',%s,%s,%s,%s,%s) RETURNING id""",
                    (entry_date,name,channel,source_detail,job_request_id,note,
                     hr_owner,source,external_ref,lark_record_id))
                candidate_id = cur.fetchone()["id"]
            # Serialize application creation per candidate. This protects the
            # nullable legacy-requisition case, for which a normal PostgreSQL
            # UNIQUE(candidate_id, job_request_id) constraint treats two NULL
            # values as distinct.
            cur.execute("SELECT pg_advisory_xact_lock(%s)", (candidate_id,))
            cur.execute("""SELECT * FROM candidate_application
                           WHERE candidate_id=%s
                             AND job_request_id IS NOT DISTINCT FROM %s
                           FOR UPDATE""",
                        (candidate_id,job_request_id))
            existing = cur.fetchone()
            if existing:
                conn.commit(); return existing, False
            application_ref = "APP-" + secrets.token_hex(8).upper()
            cur.execute("""INSERT INTO candidate_application
                (application_ref,candidate_id,job_request_id,entry_date,channel,
                 source_detail,current_stage,note,hr_owner,source,external_ref,
                 lark_record_id,record_version)
                VALUES(%s,%s,%s,%s,%s,%s,'New Lead',%s,%s,%s,%s,%s,1)
                RETURNING *""",
                (application_ref,candidate_id,job_request_id,entry_date,channel,
                 source_detail,note,hr_owner,source,external_ref,lark_record_id))
            application = cur.fetchone()
        conn.commit(); return application, True
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def update_candidate_application(application_ref, **fields):
    allowed = {"channel","source_detail","job_request_id","note","hr_owner",
               "source","external_ref","lark_record_id"}
    sets = {key:value for key,value in fields.items() if key in allowed}
    if not sets:
        return
    cols = ",".join(f"{key}=%s" for key in sets)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"""UPDATE candidate_application SET {cols},
                     record_version=record_version+1,updated_at=now()
                     WHERE application_ref=%s""",
                    list(sets.values()) + [application_ref])
        if cur.rowcount != 1:
            raise ValueError("application not found")


def transition_candidate_application(application_ref, to_stage, effective_date,
                                     changed_by="", rejection_reason="", note="",
                                     event_ref=""):
    ref = str(event_ref or "").strip() or secrets.token_urlsafe(18)
    conn = get_conn(); conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT * FROM candidate_application WHERE application_ref=%s FOR UPDATE",
                        (application_ref,))
            current = cur.fetchone()
            if not current:
                raise ValueError("application not found")
            cur.execute("""INSERT INTO candidate_application_stage_event
                (application_id,from_stage,to_stage,effective_date,rejection_reason,
                 note,changed_by,event_ref)
                VALUES(%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT(event_ref) DO NOTHING RETURNING id""",
                (current["id"],current.get("current_stage") or "",to_stage,
                 effective_date,rejection_reason,note,changed_by,ref))
            inserted = cur.fetchone()
            if inserted:
                cur.execute("""UPDATE candidate_application SET current_stage=%s,
                            record_version=record_version+1,updated_at=now() WHERE id=%s""",
                            (to_stage,current["id"]))
                # Keep the legacy projection coherent only for its original job.
                cur.execute("""UPDATE candidate SET status=%s,updated_at=now()
                               WHERE id=%s AND job_request_id IS NOT DISTINCT FROM %s""",
                            (to_stage,current["candidate_id"],current["job_request_id"]))
        conn.commit()
        return {"event_ref":ref,"idempotent":inserted is None,
                "from_stage":current.get("current_stage") or "","to_stage":to_stage}
    except Exception:
        conn.rollback(); raise
    finally:
        conn.close()


def list_candidate_application_stage_events(application_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM candidate_application_stage_event
                       WHERE application_id=%s ORDER BY created_at,id""",
                    (application_id,))
        return cur.fetchall()


def list_candidates_range(dfrom, dto):
    """[dfrom,dto] 内候选人（apply_date 取别名 record_date，直接喂 candidates_to_daily→analytics）。"""
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT id,entry_date AS record_date,channel,job_request_id,
                              current_stage AS status
                       FROM candidate_application WHERE entry_date BETWEEN %s AND %s
                       ORDER BY entry_date""", (dfrom, dto))
        return cur.fetchall()


def list_candidate_metric_rows_range(dfrom, dto):
    """Immutable identity-derived funnel events for Channel Analytics.

    A candidate is counted as a new resume once, on ``source_received_on``.
    Screening/recommendation/rejection are counted only when an immutable stage
    event crosses the relevant boundary.  Baseline imports are deliberately
    excluded because importing work that already happened is not new activity.
    """
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute(
            """SELECT a.source_received_on AS record_date,a.channel,a.job_request_id,
                      1::integer AS new_resumes,0::integer AS passed_screening,
                      0::integer AS recommended,0::integer AS rejected
               FROM candidate_application a
               WHERE a.baseline_import=FALSE
                 AND a.source_received_on BETWEEN %s AND %s
               UNION ALL
               SELECT e.effective_date AS record_date,a.channel,a.job_request_id,
                      0::integer AS new_resumes,
                      CASE WHEN e.to_stage IN ('Interview','Offer','Hired')
                                  AND e.from_stage NOT IN ('Interview','Offer','Hired')
                           THEN 1 ELSE 0 END AS passed_screening,
                      CASE WHEN e.to_stage IN ('Offer','Hired')
                                  AND e.from_stage NOT IN ('Offer','Hired')
                           THEN 1 ELSE 0 END AS recommended,
                      CASE WHEN e.to_stage='Rejected' AND e.from_stage<>'Rejected'
                           THEN 1 ELSE 0 END AS rejected
               FROM candidate_application_stage_event e
               JOIN candidate_application a ON a.id=e.application_id
               WHERE e.baseline_import=FALSE
                 AND e.effective_date BETWEEN %s AND %s
                 AND (
                   (e.to_stage IN ('Interview','Offer','Hired')
                    AND e.from_stage NOT IN ('Interview','Offer','Hired'))
                   OR (e.to_stage IN ('Offer','Hired')
                    AND e.from_stage NOT IN ('Offer','Hired'))
                   OR (e.to_stage='Rejected' AND e.from_stage<>'Rejected')
                 )
               ORDER BY record_date""",
            (dfrom, dto, dfrom, dto),
        )
        return cur.fetchall()


def earliest_candidate_date():
    """Earliest immutable candidate metric date (None when there is no data)."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT MIN(metric_date) FROM (
                         SELECT source_received_on AS metric_date
                         FROM candidate_application
                         WHERE baseline_import=FALSE AND source_received_on IS NOT NULL
                         UNION ALL
                         SELECT effective_date AS metric_date
                         FROM candidate_application_stage_event
                         WHERE baseline_import=FALSE
                       ) q""")
        return cur.fetchone()[0]


def candidate_data_days():
    """Dates containing an immutable identity-derived funnel event."""
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""SELECT DISTINCT metric_date FROM (
                         SELECT source_received_on AS metric_date
                         FROM candidate_application
                         WHERE baseline_import=FALSE AND source_received_on IS NOT NULL
                         UNION ALL
                         SELECT effective_date AS metric_date
                         FROM candidate_application_stage_event
                         WHERE baseline_import=FALSE
                       ) q WHERE metric_date IS NOT NULL ORDER BY 1""")
        return [r[0].isoformat() for r in cur.fetchall()]


def get_candidate(cid):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM candidate WHERE id=%s", (cid,))
        return cur.fetchone()


def create_candidate(apply_date, name, channel, job_request_id=None,
                     status="New Lead", note="", filled_by="", source="手动", ext_ref="",
                     lark_record_id="", source_detail=""):
    conn = get_conn()
    conn.autocommit = False
    try:
      with conn.cursor() as cur:
        cur.execute("""INSERT INTO candidate
            (apply_date, name, channel, source_detail, job_request_id, status, note, filled_by, source, ext_ref, lark_record_id)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (apply_date, name, channel, source_detail, job_request_id, status, note, filled_by, source, ext_ref, lark_record_id))
        candidate_id = cur.fetchone()[0]
        cur.execute("""INSERT INTO candidate_application
            (application_ref,candidate_id,job_request_id,entry_date,channel,
             source_detail,current_stage,note,hr_owner,source,external_ref,
             lark_record_id,record_version)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1)""",
            ("APP-" + secrets.token_hex(8).upper(), candidate_id, job_request_id,
             apply_date, channel, source_detail, status, note, filled_by, source,
             ext_ref, lark_record_id))
      conn.commit()
      return candidate_id
    except Exception:
      conn.rollback()
      raise
    finally:
      conn.close()


def get_candidate_by_lark(record_id):
    """按 Lark 记录 record_id 找候选人（同步拉取时去重/更新用）。"""
    if not record_id:
        return None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM candidate WHERE lark_record_id=%s LIMIT 1", (record_id,))
        return cur.fetchone()


def get_candidate_by_ext_ref(ext_ref):
    if not (ext_ref or "").strip():
        return None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM candidate WHERE ext_ref=%s LIMIT 1", (ext_ref,))
        return cur.fetchone()


def find_candidate(name, channel, job_request_id):
    """按 姓名+渠道+职位 认亲（跨门兜底去重）：Excel 门进来的没 lark_record_id，
    Lark 门来同一个人时用它认出、避免建重复行。name 空则不认（无法可靠去重）。"""
    if not (name or "").strip():
        return None
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM candidate WHERE name=%s AND channel=%s
                       AND COALESCE(job_request_id,0)=COALESCE(%s,0) ORDER BY id LIMIT 1""",
                    (name, channel, job_request_id))
        return cur.fetchone()


def update_candidate(cid, **fields):
    allowed = {"apply_date", "name", "channel", "source_detail", "job_request_id",
               "status", "note", "filled_by", "source", "ext_ref", "lark_record_id"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=%s" for k in sets)
    vals = list(sets.values()) + [cid]
    conn = get_conn()
    conn.autocommit = False
    try:
      with conn.cursor() as cur:
        cur.execute(f"UPDATE candidate SET {cols}, updated_at=now() WHERE id=%s", vals)
        app_map = {
            "apply_date": "entry_date", "channel": "channel",
            "source_detail": "source_detail", "job_request_id": "job_request_id",
            "status": "current_stage", "note": "note", "filled_by": "hr_owner",
            "source": "source", "ext_ref": "external_ref",
            "lark_record_id": "lark_record_id",
        }
        app_sets = {app_map[k]: v for k, v in sets.items() if k in app_map}
        if app_sets:
            app_cols = ", ".join(f"{k}=%s" for k in app_sets)
            cur.execute(
                f"""UPDATE candidate_application SET {app_cols},
                    record_version=record_version+1,updated_at=now()
                    WHERE candidate_id=%s AND application_ref='APP-' || lpad(candidate_id::text,10,'0')""",
                list(app_sets.values()) + [cid],
            )
      conn.commit()
    except Exception:
      conn.rollback()
      raise
    finally:
      conn.close()


def delete_candidate(cid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM candidate WHERE id=%s", (cid,))
        return cur.rowcount > 0


def upsert_candidate(apply_date, name, channel, job_request_id=None,
                     status="New Lead", note="", filled_by="", source="手动", ext_ref="",
                     source_detail=""):
    """上传去重：优先按 ext_ref，其次按 (name,channel,职位) 命中则更新、否则新增；
    name 为空且无 ext_ref 一律新增（无法可靠去重）。返回 id。"""
    with get_conn() as conn, conn.cursor() as cur:
        found = None
        if (ext_ref or "").strip():
            cur.execute("SELECT id FROM candidate WHERE ext_ref=%s LIMIT 1", (ext_ref,))
            found = cur.fetchone()
        elif (name or "").strip():
            cur.execute("""SELECT id FROM candidate
                           WHERE name=%s AND channel=%s AND COALESCE(job_request_id,0)=COALESCE(%s,0)
                           ORDER BY id LIMIT 1""", (name, channel, job_request_id))
            found = cur.fetchone()
        if found:
            cur.execute("""UPDATE candidate SET apply_date=%s, name=%s, channel=%s, source_detail=%s, job_request_id=%s,
                             status=%s, note=%s, filled_by=%s, source=%s, ext_ref=%s, updated_at=now()
                           WHERE id=%s""",
                        (apply_date, name, channel, source_detail, job_request_id, status, note,
                         filled_by, source, ext_ref, found[0]))
            return found[0]
        cur.execute("""INSERT INTO candidate
            (apply_date, name, channel, source_detail, job_request_id, status, note, filled_by, source, ext_ref)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING id""",
            (apply_date, name, channel, source_detail, job_request_id, status, note, filled_by, source, ext_ref))
        return cur.fetchone()[0]


def transition_candidate_stage(candidate_id, to_stage, effective_date, changed_by="",
                               rejection_reason="", note="", event_ref=""):
    """Atomically append a stage event and update the current-stage projection."""
    ref = (event_ref or "").strip() or secrets.token_urlsafe(18)
    conn = get_conn()
    conn.autocommit = False
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute("SELECT id,status FROM candidate WHERE id=%s FOR UPDATE", (candidate_id,))
            current = cur.fetchone()
            if not current:
                raise ValueError("候选人不存在")
            cur.execute("""INSERT INTO candidate_stage_event
                (candidate_id,from_stage,to_stage,effective_date,rejection_reason,note,changed_by,event_ref)
                VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                ON CONFLICT (event_ref) DO NOTHING RETURNING id""",
                (candidate_id, current.get("status") or "", to_stage, effective_date,
                 rejection_reason, note, changed_by, ref))
            inserted = cur.fetchone()
            if inserted:
                cur.execute("UPDATE candidate SET status=%s,updated_at=now() WHERE id=%s",
                            (to_stage, candidate_id))
                cur.execute(
                    """SELECT id,current_stage FROM candidate_application
                       WHERE candidate_id=%s ORDER BY id LIMIT 1 FOR UPDATE""",
                    (candidate_id,),
                )
                application = cur.fetchone()
                if application:
                    cur.execute(
                        """INSERT INTO candidate_application_stage_event
                           (application_id,from_stage,to_stage,effective_date,
                            rejection_reason,note,changed_by,event_ref)
                           VALUES (%s,%s,%s,%s,%s,%s,%s,%s)
                           ON CONFLICT (event_ref) DO NOTHING""",
                        (application["id"], application.get("current_stage") or "",
                         to_stage, effective_date, rejection_reason, note,
                         changed_by, "APP-" + ref),
                    )
                    cur.execute(
                        """UPDATE candidate_application
                           SET current_stage=%s,record_version=record_version+1,
                               updated_at=now() WHERE id=%s""",
                        (to_stage, application["id"]),
                    )
        conn.commit()
        return {"event_ref": ref, "idempotent": inserted is None,
                "from_stage": current.get("status") or "", "to_stage": to_stage}
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def list_candidate_stage_events(candidate_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT * FROM candidate_stage_event WHERE candidate_id=%s
                       ORDER BY created_at,id""", (candidate_id,))
        return cur.fetchall()


# ================= Nexus：文档/模板库 =================
def list_templates():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM templates ORDER BY category, updated_at DESC")
        rows = cur.fetchall()
        for r in rows:
            for k in ("created_at", "updated_at"):
                if r.get(k) is not None and hasattr(r[k], "isoformat"):
                    r[k] = r[k].isoformat()
        return rows


def create_template(title, category="其他", content=""):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("INSERT INTO templates (title, category, content) VALUES (%s,%s,%s) RETURNING id",
                    (title, category, content))
        return cur.fetchone()[0]


def update_template(tid, **fields):
    allowed = {"title", "category", "content"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=%s" for k in sets)
    vals = list(sets.values()) + [tid]
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE templates SET {cols}, updated_at=now() WHERE id=%s", vals)


def delete_template(tid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM templates WHERE id=%s", (tid,))
        return cur.rowcount > 0


# ================= Nexus：渠道成本 =================
def list_channel_costs():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT channel, ym, amount FROM channel_cost ORDER BY ym, channel")
        rows = cur.fetchall()
        for r in rows:
            r["amount"] = float(r["amount"]) if r["amount"] is not None else 0.0
        return rows


def upsert_channel_cost(channel, ym, amount):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO channel_cost (channel, ym, amount) VALUES (%s,%s,%s)
                       ON CONFLICT (channel, ym) DO UPDATE SET amount=EXCLUDED.amount, updated_at=now()""",
                    (channel, ym, amount))


# ================= Nexus：考勤打卡 =================
def create_attendance_site(name, lat, lng, radius_m=200, require_selfie=False):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO attendance_site (name, lat, lng, radius_m, require_selfie)
                       VALUES (%s,%s,%s,%s,%s) RETURNING id""",
                    (name, lat, lng, radius_m, require_selfie))
        return cur.fetchone()[0]


def list_attendance_sites():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM attendance_site ORDER BY id")
        rows = cur.fetchall()
        for r in rows:
            if r.get("created_at") is not None and hasattr(r["created_at"], "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
        return rows


def get_attendance_site(sid):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM attendance_site WHERE id=%s", (sid,))
        return cur.fetchone()


def update_attendance_site(sid, **fields):
    allowed = {"name", "lat", "lng", "radius_m", "require_selfie"}
    sets = {k: v for k, v in fields.items() if k in allowed}
    if not sets:
        return
    cols = ", ".join(f"{k}=%s" for k in sets)
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute(f"UPDATE attendance_site SET {cols} WHERE id=%s", list(sets.values()) + [sid])


def delete_attendance_site(sid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM attendance_site WHERE id=%s", (sid,))
        return cur.rowcount > 0


def create_attendance_person(name, kind, token, site_id=None):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("""INSERT INTO attendance_person (name, kind, token, site_id)
                       VALUES (%s,%s,%s,%s) RETURNING id""", (name, kind, token, site_id))
        return cur.fetchone()[0]


def list_attendance_persons():
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""SELECT p.*, s.name AS site_name FROM attendance_person p
                       LEFT JOIN attendance_site s ON s.id = p.site_id ORDER BY p.id""")
        rows = cur.fetchall()
        for r in rows:
            if r.get("created_at") is not None and hasattr(r["created_at"], "isoformat"):
                r["created_at"] = r["created_at"].isoformat()
        return rows


def get_attendance_person_by_token(token):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM attendance_person WHERE token=%s", (token,))
        return cur.fetchone()


def delete_attendance_person(pid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("DELETE FROM attendance_person WHERE id=%s", (pid,))
        return cur.rowcount > 0


def last_attendance_record(person_id):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("SELECT * FROM attendance_record WHERE person_id=%s ORDER BY server_time DESC LIMIT 1",
                    (person_id,))
        return cur.fetchone()


def add_attendance_record(person_id, person_name, kind, punch_type, lat, lng, accuracy,
                          site_id, distance_m, within_fence, ip, photo, flags, source="web"):
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("""INSERT INTO attendance_record
            (person_id, person_name, kind, punch_type, lat, lng, accuracy, site_id,
             distance_m, within_fence, ip, photo, flags, source)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
            RETURNING id, server_time""",
            (person_id, person_name, kind, punch_type, lat, lng, accuracy, site_id,
             distance_m, within_fence, ip, photo, flags, source))
        return cur.fetchone()


def list_attendance_records(dfrom=None, dto=None, person_id=None, limit=500):
    q = ["SELECT id, person_id, person_name, kind, punch_type, server_time, lat, lng, accuracy,",
         "       site_id, distance_m, within_fence, ip, flags, source,",
         "       (photo IS NOT NULL) AS has_photo",
         "FROM attendance_record WHERE 1=1"]
    args = []
    if dfrom:
        q.append("AND server_time >= %s"); args.append(dfrom)
    if dto:
        q.append("AND server_time < (%s::date + 1)"); args.append(dto)
    if person_id:
        q.append("AND person_id = %s"); args.append(person_id)
    q.append("ORDER BY server_time DESC LIMIT %s"); args.append(limit)
    with get_conn() as conn, conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
        cur.execute("\n".join(q), args)
        rows = cur.fetchall()
        for r in rows:
            if r.get("server_time") is not None and hasattr(r["server_time"], "isoformat"):
                r["server_time"] = r["server_time"].isoformat()
        return rows


def get_attendance_photo(rid):
    with get_conn() as conn, conn.cursor() as cur:
        cur.execute("SELECT photo FROM attendance_record WHERE id=%s", (rid,))
        r = cur.fetchone()
        return r[0] if r else None
