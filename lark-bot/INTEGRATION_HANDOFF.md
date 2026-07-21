# Nexus 交接与整合说明书（给 GPT / CODEX）

> 目的：把 **Nexus 运营控制台**（含网站）完整交接给 GPT，让 GPT 能读懂、部署、并与 CODEX（AI 招聘/候选人系统）整合上线。
> 读者：GPT/CODEX 工程侧。以下技术标识符（表名、字段、路由、env）都是可直接使用的真实名字。

---

## 0. 一句话概览

Nexus = 一套 **Flask + PostgreSQL + Lark** 的运营控制台，**从 GitHub 自动部署到 Railway**。
单一后端 `bot.py` 同时做三件事：① 跑 Lark 机器人（事件/卡片回调）② 提供网页控制台 `/panel` ③ 提供一整套 `/api/*`。
模块：**任务派发、考勤打卡（GPS 地理围栏）、招聘渠道看板、候选人跟进、职位管理、文档模板库、渠道成本**。

要 GPT 做的：把 Nexus 与 CODEX 整合、部署上线。**整合边界见第 8 节（务必读）。**

---

## 1. 技术栈 & 运行方式

- **语言/框架**：Python 3.13、Flask（开发）+ waitress（生产 WSGI）、psycopg2（**raw SQL**，不是 ORM）、lark-oapi（Lark SDK）、openpyxl（Excel）。
- **前端**：单文件 `panel.html`（原生 JS 单页应用，无框架），由 `bot.py` 直接返回；移动端打卡是 `checkin.html`。
- **进程**（`Procfile`）：
  - `web: python bot.py` —— 主进程（机器人 + 网站 + API），生产用 waitress 起在 `$PORT`。
  - `cron: python overdue.py` —— 逾期任务升级（可选，跟招聘无关）。
- **启动即迁移**：`bot.py` 启动时调用 `db.init_db()` → 执行 `schema.sql`。schema.sql 全是**幂等**语句（`CREATE TABLE IF NOT EXISTS` / `ALTER TABLE ADD COLUMN IF NOT EXISTS`）。
  → **直接部署新代码就会自动升级数据库、不丢老数据**，无需手动迁移。

---

## 2. 文件清单（每个文件干什么）

| 文件 | 作用 |
|---|---|
| `bot.py` | **主入口**：Lark 机器人 + 所有 Flask 路由（`/api/*`、`/panel`、`/webhook/*`、`/checkin/*`）。 |
| `db.py` | 所有数据库读写（raw SQL，psycopg2）。`init_db()` 在此。 |
| `schema.sql` | 建表 + 幂等迁移。由 `init_db()` 执行。 |
| `channel_report.py` | 招聘分析**纯函数引擎** + `PIPELINE_STATUS` + `candidates_to_daily()` 桥。 |
| `sheet_io.py` | Excel 模板生成/解析（通用引擎 + 候选人跟进表 + 分析导出）。 |
| `lark_bitable.py` | **Lark 多维表格客户端**：机器人建/读/写它**自己的**表；连接自检。 |
| `larksync.py` | 任务 ↔ Bitable 双向同步的纯函数"脑子"（reconcile）。**暂未接真实 Lark**，与候选人这套独立。 |
| `attend.py` | 考勤地理围栏 + 防作弊判定（纯函数）。 |
| `cards.py` | Lark 交互卡片构造。 |
| `overdue.py` | cron：逾期任务升级 @负责人。 |
| `parse.py` | 任务自然语言解析。 |
| `panel.html` | 网页控制台（单文件 SPA）。 |
| `checkin.html` | 移动端打卡页（公开，需 HTTPS + GPS）。 |
| `Procfile` / `requirements.txt` / `.env.example` | 部署配置。 |

---

## 3. 部署（GitHub + Railway）—— 具体步骤

**现有线上**：`https://lark-task-bot-production.up.railway.app`（Railway 项目 production，Python 3.13.x，含一个 PostgreSQL 插件）。

1. **GitHub**：整个 `lark-bot/` 文件夹就是**仓库根目录**。改代码 → `git push` → Railway 监听该仓库、**自动重新构建部署**。
2. **Railway**：一个 web service（跑 `python bot.py`）+ 一个 **PostgreSQL** 插件。`DATABASE_URL` 由 Railway **自动注入**，不用手填。
3. **依赖**（`requirements.txt`）：`lark-oapi>=1.7.1`、`psycopg2-binary>=2.9.9`、`flask>=3.0.0`、`waitress>=3.0.0`、`openpyxl>=3.1.0`。
4. **首次/每次部署**：`init_db()` 自动跑 `schema.sql`，库自动就位（日志里会看到 `[db] tables ready`）。
5. **环境变量**（Railway → Variables，**完整清单**）：

| 变量 | 说明 |
|---|---|
| `APP_ID` / `APP_SECRET` | Lark 应用凭证（开放平台 → 应用 → 凭证与基础信息）。**不要进 GitHub**。 |
| `LARK_DOMAIN` | 国际版 Lark = `https://open.larksuite.com`；国内飞书 = `https://open.feishu.cn`。 |
| `ENCRYPT_KEY` / `VERIFICATION_TOKEN` | Lark 事件订阅的两把校验钥匙（事件与回调 → 事件配置）。 |
| `DATABASE_URL` | Railway Postgres 自动注入。 |
| `ADMIN_PANEL_PASSWORD` | 网页 `/panel` 登录密码。 |
| `ADMIN_SETUP_CODE` | 首次把自己设为管理员的口令。 |
| `MODE` | `webhook`（国际版 Lark）。 |
| `BOT_NAME` | 应用名（用来把机器人从群成员里排除）。 |
| `OVERDUE_ESCALATE_DAYS` | 逾期升级天数（默认 2）。 |
| `ADMIN_NOTIFY_OPEN_ID` | 可选：网页派任务的反馈私聊通知谁。 |
| `PORT` | Railway 自动提供。 |

6. **Lark 事件订阅**（webhook 模式）：回调地址 = `https://<域名>/webhook/event`，卡片回调 = `https://<域名>/webhook/card`。

---

## 4. 数据库 schema（关键表）

- **任务派发**：`tasks`、`users`、`drafts`。
- `job_requests(id, title, target_headcount, target_resume_count, status, owner)` —— 职位。
- **`candidate`（本次重点，候选人级招聘跟踪）**：
  `id, apply_date, name, channel, job_request_id(FK job_requests, 可空), status, note, filled_by, source, ext_ref, lark_record_id, created_at, updated_at`
  - `status ∈ PIPELINE_STATUS = [新简历, 初筛通过, 已推荐面试, 已录用, 已拒绝]`
  - **`ext_ref`** = 预留给 **CODEX 候选人的不透明关联号**（两库不合并，只存对方 token）。
  - **`lark_record_id`** = 对应 Lark 多维表格记录号，导入去重用。
- `channel_daily` —— 老的"渠道人工汇总"表，**保留但前端已不用**（分析改由 `candidate` 派生）。
- `channel_cost(channel, ym, amount)` —— 渠道月度投入。
- `templates` —— 文档/模板库。
- `attendance_site` / `attendance_person` / `attendance_record` —— 考勤打卡。
- `settings(key, value)` —— 杂项配置。招聘相关键：`lark_cand_app_token`、`lark_cand_table_id`、`lark_cand_last_sync`、`channel_go_live`、`channel_roster`。

---

## 5. API 接口（分组，全部在 `bot.py`）

鉴权：除公开的 `/checkin/*`、`/webhook/*`、`/`，其余 `/api/*` 与 `/panel` 用 `_panel_auth()`（`X-Auth` 头或 `?pw=` 对 `ADMIN_PANEL_PASSWORD`）。

- **任务**：`/api/tasks`(GET/POST)、`/api/tasks/<id>`(PATCH/DELETE)、`/nudge`、`/comments`、`/api/groups`、`/api/external-groups*`。
- **招聘分析**：`/api/channel/meta`、`/analytics`、`/report`、`/report/push`、`/export.xlsx`、`/golive`、`/roster`、`/cost`、`/jobs*`。
- **候选人**：`/api/candidates`(GET/POST)、`/api/candidates/<id>`(PATCH/DELETE)。
- **候选人表 Excel（副路 B）**：`/api/channel/template`（下载跟进表）、`/api/channel/upload`（上传）。
- **Lark 同步（主路 A）**：`/api/lark/ping`（连接自检）、`/status`、`/table`（机器人建表）、`/push`（Nexus→Lark）、`/pull`（Lark→Nexus 导入）。
- **考勤**：`/api/att/*`、`/checkin/<token>`、`/api/checkin/<token>`。
- **其它**：`/calendar.ics`、`/t/<token>`（任务 token 页）、`/webhook/event`、`/webhook/card`。

---

## 6. 招聘/候选人模块（整合时最相关）

- **数据模型**：**每行一个候选人**（`candidate` 表）。渠道看板、漏斗、招聘速度**全部从候选人的 `status` 自动派生**，不用手填汇总数字。
- **核心桥**：`channel_report.candidates_to_daily(cands)` 把候选人行折算成"日×渠道×职位"的聚合行（`new/passed/recommended/rejected`），喂给现有 `analytics()` —— **分析引擎一行没改就能吃候选人数据**。折算规则：每人 1 份 `new`；`status ≥ 初筛通过 → passed`；`≥ 已推荐面试 → recommended`；`= 已拒绝 → rejected`。
- **前端**：招聘页有「候选人跟进」（待初筛 / 待跟进 / 已完成 视图 + **一键推进**按钮）+ 自动出图的看板。
- **两条录入路**：
  - **主路 A（Lark）**：机器人建一张**它自己拥有**的多维表格 → HR 在 Lark 里填/改状态 → 管理员在网站点「导入 HR 填好的」把它读回（带确认，作人工审核关）。
  - **副路 B（Excel）**：`/api/channel/template` 下载跟进表 → HR 填 → `/api/channel/upload` 上传。表里「记录ID」是灰色系统列（勿填）。
- **去重（关键，杜绝重复行）**：导入时 **① 按 `lark_record_id` / 记录ID 命中 → 原地更新；② 找不到再按 姓名+渠道+职位 认亲；③ 都没有才新增。** 所以同一张表多次提交、或"机器人一次 + 网站一次"都**不会产生重复**。这条去重逻辑集中在 `db.upsert_candidate()` 与 `/api/lark/pull`。

---

## 7. Lark 集成现状

- `lark_bitable.py`：机器人以**应用身份**（tenant_access_token，用 `APP_ID/APP_SECRET`）建/读/写**自己的**多维表格。**机器人自己建的表它就是主人**，不用加协作者、不用 OAuth —— 绕开了"知识库授权"那一整套麻烦。
- **已联调**：`① 连接自检`（`/api/lark/ping`）线上通过 —— 机器人能拿到 Lark token（app 尾号 `78deea`）。
- **待联调**：`② 生成表`（`create_base`，可能需要 `folder_token` 或给应用加 `drive` 权限来把表分享给管理员）、`③ push`、`④ pull` —— 这几个真实 Lark 接口**尚未在线上验证过**。
- `larksync.py` 是另一套（任务 ↔ Bitable 的 reconcile），目前**未接真实 Lark**，与候选人这套独立，留给将来任务同步。

---

## 8. 与 CODEX 整合 —— 边界、发现、建议（务必读）

**现场发现**：那个 `RECRUITMENT BOT` 已经支持 `/submit_today_hr_sheet <id>`，回执带"已完成/未完成/待选择/待审核 Review/联系任务"，且明写"**继续填写后可安全重复提交**"。→ 这是 **CODEX（GPT）那套候选人联系/审核流程**，而且**已经实现了"机器人收 HR 表、幂等导入"**。这跟 Nexus 正要做的"机器人收表"高度重叠。

**建议分工（避免重复造轮子 + 避免 HR 面对两套提交打架）**：

- **CODEX**：候选人**来源 / 联系 / 审核**（sourcing、contact tasks、review pool）。已有 `/submit_today_hr_sheet`，保留。
- **Nexus**：**渠道看板 + 招聘速度/漏斗分析 + 运营**（任务派发、考勤、文档模板、渠道成本）。候选人明细可从 CODEX 取，也可 Nexus 自录（两者靠去重共存不冲突）。

**数据边界（重要，请遵守）**：设计上两套是**各自独立的数据库、不合并、不互相 import 代码**。候选人交换走**签名不透明 token（v3）**：**CODEX 签发 token + 行签名；运营(Nexus)侧只投递 / 读回 / 原样往返，不持 HMAC 密钥、不重算签名**。Nexus 的 `candidate.ext_ref` 就是留来存 CODEX 候选人不透明号的。

**整合成"一个机器人"时**：两套后端**不要抢同一个 webhook/命令**。给 Nexus 的机器人命令用不同前缀（如 `/nexus_*`），CODEX 保留 `/submit_today_hr_sheet` 等；或加一个网关按命令分流到两个后端。

**候选人数据互通（推荐做法）**：
- 若以 CODEX 为候选人真相源：CODEX 把（已联系/已推荐/录用等）候选人按签名 token 推给 Nexus 的 `POST /api/candidates`（建议新开 `/api/candidates/ingest` 带签名校验）；Nexus 落库时把 token 存进 `ext_ref`、按 `ext_ref` 去重更新。→ Nexus 看板即可统计 CODEX 的候选人，**两库仍独立**。
- 反向（Nexus→CODEX）同理，走 token 往返。

---

## 9. 当前状态 & TODO

- ✅ **已上线跑通**：任务派发、考勤、招聘看板（候选人级）、候选人跟进（一键推进、Excel 主/副路去重）、`① Lark 连接自检`。
- ⏳ **待联调**：`② 生成 Lark 表` / `③ push` / `④ pull`（真实 Lark 接口未在线上验证；② 可能要 `drive` 权限或 `folder_token`）。
- 🔜 **未做**：机器人"私信下载/上传"那道门（需先与 CODEX 的 `/submit_today_hr_sheet` 明确不撞车）；`larksync` 接真实 Lark；Nexus↔CODEX 的签名 token 通道（`/api/candidates/ingest`）。

---

*本说明书随代码包一起交付。代码根目录 = `lark-bot/`。任何"这块怎么跑"的问题，先看对应文件顶部的中文 docstring，注释很全。*
