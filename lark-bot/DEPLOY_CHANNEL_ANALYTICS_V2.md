# Channel Analytics v2 部署与首次验证

本包替代旧的 `Nexus-Channel-Analytics-Unified-20260721.zip`。不要把两个包混合上传。

## 部署

1. 解压本 ZIP。
2. 把解压目录内的全部文件上传到 GitHub 仓库现有 `lark-bot/` 目录，允许覆盖同名文件。
3. 等 Railway 对这次 GitHub commit 显示 `Deployment successful`。
4. 打开 `/panel`，进入 `Channel Analytics`。

启动时会运行幂等 PostgreSQL schema 迁移：新增来源详情、阶段历史和 v2 配置所需结构，
不会删除候选人、职位、任务或历史活动。

## 首次验证顺序

1. 先确认网站上的既有 Candidate Pipeline 和职位仍可见。
2. 在 Channel Analytics 的“高级设置”中运行 Lark 连接自检。
3. 创建一次新的 `channel-analytics-v2` Lark Base。它应包含：
   - `Candidate Pipeline`
   - `未建档批量统计（特殊情况）`
4. 在网站下载 Candidate Pipeline Excel，填写一条安全测试候选人后上传；再上传同一文件，确认不重复。
5. 在 Lark Bot 使用 `/channel_sheet` 获取表；填写一条安全记录后使用 `/submit_channel_sheet`，确认网站同步。
6. 重复提交同一内容，确认候选人和阶段事件不重复。

## 关键规则

- Source Channel 由 HR 选择；系统明确知道来源时才预填。
- 选择 `Other` 必须填写“其他来源说明”；其他渠道可留空。
- 候选人可以从任意合法招聘阶段开始；`Rejected` 必须填写原因。
- 网站只接收 `.xlsx`，候选人路径禁用 CSV。
- 隐藏 `Row Ref`、Lark `System ID` 和记录 ID 不得修改。
- Talent Discovery 的搜索评分、联系任务、Review 和 ContactActivity 不由 Channel Analytics 改写。

本 ZIP 不包含 `.env`、生产密钥、数据库、备份、候选人导出、日志或 Lark 会话。
