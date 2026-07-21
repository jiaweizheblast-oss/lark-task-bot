# Task Bot → RECRUITMENT BOT 安全迁移

目标：Talent Discovery 与 Channel Analytics 只对外使用一个 `RECRUITMENT BOT`。

## 安全边界

- 不删除候选人、职位、任务、招聘阶段历史或 ContactActivity。
- 当前 Task Bot 创建的 Channel Base 尚未同步时，只清除 Nexus 中的连接引用；不会删除该 Lark 文档。
- Recruitment Bot 通过 `NEXUS_TALENT_WORKER_TOKEN` 调用两个窄接口：查询在线表状态、提交在线表。
- 该 token 不能登录网站，也不能读取候选人集合、启动搜索或修改 Talent Discovery 数据库。

## 顺序

1. 部署本包并等待 Railway `Deployment successful`。
2. 打开 Channel Analytics，确认状态仍显示 `尚未同步`。
3. 展开 Lark 设置，点击“纠正错误机器人连接”，确认后输入 `RESET`。
4. 页面必须变成“Lark 在线表未配置”。旧文档未删除，但不再是 Nexus 数据源。
5. Railway Variables 修改：
   - `APP_ID` = 本地 AI-Talent-Discovery `.env` 中 RECRUITMENT BOT 的 `LARK_APP_ID`
   - `APP_SECRET` = 同一应用的 `LARK_APP_SECRET`
   - `BOT_NAME` = `RECRUITMENT BOT`
   - `NEXUS_TALENT_WORKER_TOKEN` = 与本地 AI-Talent-Discovery 完全相同的现有 token
6. 不要把真实变量值放进 GitHub、截图或聊天。
7. 等 Railway 再次部署成功，在网站点“测试机器人凭证”；页面应显示 RECRUITMENT BOT 和新的 app 尾号。
8. 点击“首次创建在线渠道表”，输入当前 Lark 账号实际绑定的邮箱，只创建一次。
9. 若表创建但打不开，使用“重新授予管理员权限”，不要重复创建。
10. 本地 AI-Talent-Discovery `.env` 必须同时有：
    - `NEXUS_RECRUITING_ENDPOINT=https://lark-task-bot-production.up.railway.app`
    - 与 Railway 相同的 `NEXUS_TALENT_WORKER_TOKEN`
11. 重启本地 `scripts/run_lark_manager_bot.py`。RECRUITMENT BOT 的事件接收继续由本地长连接负责；Railway 只提供网站、Base API 和签名服务接口。
12. 依次测试：
    - `/help`
    - `/channel_sheet`
    - 空表 `/submit_channel_sheet`
    - `/today_hr_sheet`（只检查既有 Talent 命令仍响应，不新建测试批次时不要执行）

## 成功标准

- `/channel_sheet` 由 RECRUITMENT BOT 返回新 Base 链接。
- 空表 `/submit_channel_sheet` 返回新增 0、更新 0、批量记录 0、问题行 0。
- Task Bot 不再作为日常入口。
- 网站和 Bot 打开的 Channel Base URL 完全一致。
