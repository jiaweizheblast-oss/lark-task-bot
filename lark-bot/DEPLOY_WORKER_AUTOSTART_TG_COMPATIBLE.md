# Talent Worker 自动启动与在线状态

本包是在上一版 TG-compatible Nexus 完整包之上增加的最小改动：

- 保留现有 Telegram 自动化配置、路由和页面功能。
- Windows Worker 每 15 秒发送一次极小心跳，只报告在线状态和能力。
- Windows Worker 自动寻找并启动隔离的专用 Chrome，只绑定本机
  `127.0.0.1:9231`，不使用日常 Chrome 用户资料。
- Talent Discovery 页面分别显示 Worker 和搜索浏览器是否就绪。
- Worker 或搜索浏览器未就绪时，网站拒绝创建新的搜索任务；空白表发布仍可使用。
- 搜索参数、搜索预算、候选人评分和发布流程均未改变。

## Railway 部署

把本目录中的全部文件上传到 GitHub 的 `lark-bot` 应用目录。

不要上传任何 `__pycache__` 目录。不要删除或覆盖 Railway 现有环境变量。

Railway 部署成功后，数据库会自动应用：

`20260723_talent_worker_presence_v1`

这个迁移只增加 Worker 在线状态表，不修改候选人、活动、TG 或招聘历史。

## Windows 一次性安装

在 `AI-Talent-Discovery` 项目 PowerShell 中运行：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\manage_nexus_worker_startup.ps1 -Action Install
```

安装后无需每天运行命令。Windows 用户登录时 Worker 会在后台自动启动，
并在需要时自动启动专用 Chrome。

查看状态：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\manage_nexus_worker_startup.ps1 -Action Status
```

手动启动：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\manage_nexus_worker_startup.ps1 -Action Start
```

日志仅写入：

`data\runtime\worker-logs`

日志不输出 Worker token、Lark secret 或其他密钥。

## 日常使用

1. 登录 Windows。
2. 打开 Talent Discovery 页面，确认显示
   `Worker 在线 · 搜索浏览器就绪`。
3. 选择招聘职位和 HR 分配。
4. 只点击一次“开始搜索并生成今日表”。
5. Worker 完成搜索、冻结、导入和发布后，在网站或 Recruitment Bot 打开今日表。

心跳和浏览器就绪检查不搜索、不调用 AI、不访问 LinkedIn、不消耗搜索配额。
真实搜索次数与上一版完全相同。
