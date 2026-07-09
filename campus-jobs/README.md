# 27届 AI产品岗秋招追踪器

一个自动更新的 27届（2027届）AI产品/产品经理岗位追踪看板。每天由 GitHub Actions 抓取公开招聘信息，用 **DeepSeek API** 做结构化筛选，输出到可筛选的网页。

## 在线访问

启用 GitHub Pages 后：`https://liz-lu.github.io/ai-daily-report/campus-jobs/`

## 架构

```
GitHub Actions (每日 09:30 北京时间)
  → campus-jobs/scripts/fetch_jobs.py 抓取 data/sources.json 里的公开源
  → DeepSeek API 提取/筛选/结构化为岗位 JSON
  → 合并去重、按截止日期排序 → data/jobs.json
  → 自动 commit → GitHub Pages 展示 (index.html)
```

## 首次启用（3 步）

### 1. 添加 DeepSeek 密钥
仓库 → **Settings → Secrets and variables → Actions → New repository secret**
- Name: `DEEPSEEK_API_KEY`，Value: 你的 DeepSeek API key

### 2. 开启 Pages
**Settings → Pages → Source: Deploy from a branch → Branch: main / (root)**

### 3. 确认 Actions 写权限
**Settings → Actions → General → Workflow permissions → Read and write**

完成后：Actions 页手动点一次 **Run workflow** 测试，或等每天 09:30 自动跑。

## 维护数据源

编辑 `campus-jobs/data/sources.json` 的 `fetch_sources`，填入可 HTTP 抓取的公开页面。

## 说明

- 未配置 `DEEPSEEK_API_KEY` 时脚本不报错，保留现有数据。
- 岗位信息可能有延迟或变动，**投递前请以企业官方渠道为准**。
