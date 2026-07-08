# liz-lu / ai-daily-report

## 仓库结构

```
├── daily-briefs/          ← AI 每日简报（自动生成）
│   ├── index.html         ← 简报首页（按日期浏览）
│   ├── latest.html        ← 跳转最新一期
│   └── AI简报-YYYY-MM-DD.*
├── duxiaoman-submission/  ← 度小满笔试提交
│   ├── 度小满笔试-A4提交版.html
│   └── 度小满笔试-MVP原型.html
├── scripts/               ← 生成脚本
├── index.html             ← 根导航页（GitHub Pages 入口）
└── .github/workflows/     ← 自动化
```

## AI Daily Brief 自动化

- Workflow: `.github/workflows/daily-ai-brief.yml`
- 脚本: `scripts/ai-daily-brief.py`
- 定时: 每天北京时间 10:30 自动运行
- 手动: Actions → `Daily AI Brief` → `Run workflow`

输出到 `daily-briefs/` 目录。

## Required repository settings

1. **Actions write permission**
   - Settings → Actions → General → Workflow permissions → Read and write

2. **Pages source**
   - Settings → Pages → Source: `Deploy from a branch` → Branch: `main`, folder `/ (root)`

## Optional secrets

- `AI_DAILY_BRIEF_API_KEY` / `OPENAI_API_KEY`
- `AI_DAILY_BRIEF_MODEL` / `OPENAI_MODEL`
- `AI_DAILY_BRIEF_BASE_URL` / `OPENAI_BASE_URL`

勿提交密钥到文件。本地环境变量放 `scripts/ai-daily-brief.env`（已 gitignore）。
