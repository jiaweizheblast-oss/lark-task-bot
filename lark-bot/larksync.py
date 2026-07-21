"""
Nexus Task <-> Lark Bitable 双向同步的「脑子」（纯函数，可脱离 DB / 真实 Lark 单测）。

设计：
- 字段/取值映射：Nexus 任务 <-> Bitable 记录（Task/Owner/Priority/Status/Deadline）。
- 变化检测用「内容哈希」而非时间戳：每对建立后存 synced_hash（上次同步时双方一致的内容哈希）。
  某侧 now_hash != synced_hash 即该侧有改动。这样天然防「我们自己写回去又被当成用户改动」的循环。
- 冲突（两侧都改）：按「最后修改时间为准」裁决（updated_ts vs modified_ts），赢家覆盖输家。
- 时间戳只用于冲突裁决，不用于变化检测（避免我方写入 bump 时间造成误判）。

真实 Lark HTTP 调用不在本文件（在 bitable_client 里，联调时接）；本文件只算「该做什么动作」。
"""
import json
import hashlib

# 状态映射：Nexus(pending/accepted/done) <-> Bitable(单选)
STATUS_N2B = {"pending": "Not Started", "accepted": "In Progress", "done": "Completed"}
STATUS_B2N = {"Not Started": "pending", "In Progress": "accepted", "Completed": "done",
              "To Do": "pending", "Done": "done"}
# 优先级：默认同名直传（Urgent/High/Medium/Low 双方一致；名字不同可在此加映射）
PRIORITY_B2N = {}   # 需要时： {"Urgent":"High"}
PRIORITY_N2B = {}

FIELDS = ("Task", "Owner", "Priority", "Status", "Deadline")


def map_task_to_b(task):
    """Nexus 任务 -> Bitable 字段值（统一成可比较的规范表示）。"""
    return {
        "Task": (task.get("title") or "").strip(),
        "Owner": (task.get("assignee_name") or "").strip(),
        "Priority": PRIORITY_N2B.get(task.get("priority") or "", task.get("priority") or ""),
        "Status": STATUS_N2B.get(task.get("status") or "pending", "Not Started"),
        "Deadline": _date_str(task.get("deadline")),
    }


def map_b_to_task(fields):
    """Bitable 字段值 -> Nexus 任务字段。"""
    return {
        "title": (fields.get("Task") or "").strip(),
        "assignee_name": (fields.get("Owner") or "").strip(),
        "priority": PRIORITY_B2N.get(fields.get("Priority") or "", fields.get("Priority") or ""),
        "status": STATUS_B2N.get((fields.get("Status") or "").strip(), "pending"),
        "deadline": _date_str(fields.get("Deadline")) or None,
    }


def _date_str(v):
    if not v:
        return ""
    s = str(v)
    return s[:10]  # YYYY-MM-DD


def _canon(b_fields):
    """把 Bitable 表示规范成稳定字符串（只取要同步的 5 个字段）。"""
    return json.dumps({k: (b_fields.get(k) or "") for k in FIELDS},
                      ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def content_hash(b_fields):
    return hashlib.sha256(_canon(b_fields).encode("utf-8")).hexdigest()


def record_b_fields(record):
    """从 bitable 记录里取出我们关心的 5 个字段（record['fields'] 是原始列->值）。"""
    f = record.get("fields", {})
    return {k: (f.get(k) or "") if not isinstance(f.get(k), dict) else _flatten(f.get(k)) for k in FIELDS}


def _flatten(v):
    """Bitable 有些字段是对象（人员/链接）；取可读文本。"""
    if isinstance(v, dict):
        return v.get("text") or v.get("name") or v.get("link") or ""
    if isinstance(v, list):
        return "，".join(_flatten(x) for x in v)
    return "" if v is None else str(v)


def reconcile(nexus_tasks, bitable_records, links):
    """
    输入：
      nexus_tasks: [{id, title, assignee_name, priority, status, deadline, updated_ts}]
      bitable_records: [{record_id, fields:{...}, modified_ts}]
      links: [{nexus_task_id, record_id, synced_hash}]
    返回：动作列表，交给调用方执行（再回写 link 的 synced_hash）。
      op ∈ {create_nexus, create_bitable, update_nexus, update_bitable, drop_link, noop}
    """
    tasks_by_id = {t["id"]: t for t in nexus_tasks}
    recs_by_id = {r["record_id"]: r for r in bitable_records}
    linked_task_ids = set()
    linked_rec_ids = set()
    actions = []

    for ln in links:
        tid, rid = ln.get("nexus_task_id"), ln.get("record_id")
        linked_task_ids.add(tid)
        linked_rec_ids.add(rid)
        task = tasks_by_id.get(tid)
        rec = recs_by_id.get(rid)
        if task is None and rec is None:
            actions.append({"op": "drop_link", "nexus_task_id": tid, "record_id": rid, "reason": "两侧都不在"})
            continue
        if task is None:
            actions.append({"op": "drop_link", "nexus_task_id": tid, "record_id": rid, "reason": "网站侧已删（保守：仅解绑，不删表）"})
            continue
        if rec is None:
            actions.append({"op": "drop_link", "nexus_task_id": tid, "record_id": rid, "reason": "表侧已删（保守：仅解绑）"})
            continue
        n_hash = content_hash(map_task_to_b(task))
        b_hash = content_hash(record_b_fields(rec))
        synced = ln.get("synced_hash")
        n_changed = n_hash != synced
        b_changed = b_hash != synced
        if not n_changed and not b_changed:
            actions.append({"op": "noop", "nexus_task_id": tid, "record_id": rid})
        elif n_changed and not b_changed:
            actions.append({"op": "update_bitable", "nexus_task_id": tid, "record_id": rid,
                            "fields": map_task_to_b(task), "new_hash": n_hash, "reason": "网站侧改动"})
        elif b_changed and not n_changed:
            actions.append({"op": "update_nexus", "nexus_task_id": tid, "record_id": rid,
                            "fields": map_b_to_task(record_b_fields(rec)), "new_hash": b_hash, "reason": "表侧改动"})
        else:
            # 两侧都改 -> 最后修改时间为准
            n_ts = task.get("updated_ts") or 0
            b_ts = rec.get("modified_ts") or 0
            if n_ts >= b_ts:
                actions.append({"op": "update_bitable", "nexus_task_id": tid, "record_id": rid,
                                "fields": map_task_to_b(task), "new_hash": n_hash,
                                "reason": "冲突：网站更晚（%s≥%s）" % (n_ts, b_ts)})
            else:
                actions.append({"op": "update_nexus", "nexus_task_id": tid, "record_id": rid,
                                "fields": map_b_to_task(record_b_fields(rec)), "new_hash": b_hash,
                                "reason": "冲突：表更晚（%s>%s）" % (b_ts, n_ts)})

    # 表里有、网站没有 -> 在网站建
    for r in bitable_records:
        if r["record_id"] not in linked_rec_ids:
            actions.append({"op": "create_nexus", "record_id": r["record_id"],
                            "fields": map_b_to_task(record_b_fields(r)),
                            "new_hash": content_hash(record_b_fields(r)), "reason": "表侧新增"})
    # 网站有、表里没有 -> 在表建
    for t in nexus_tasks:
        if t["id"] not in linked_task_ids:
            actions.append({"op": "create_bitable", "nexus_task_id": t["id"],
                            "fields": map_task_to_b(t),
                            "new_hash": content_hash(map_task_to_b(t)), "reason": "网站侧新增"})
    return actions
