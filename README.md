# AI Daily Report

This repository hosts a static Chinese AI daily brief site on GitHub Pages.

## Automation (GitHub-only, no local dependency)

Daily generation is handled by GitHub Actions:

- Workflow file: `.github/workflows/daily-ai-brief.yml`
- Script: `scripts/ai-daily-brief.py`
- Schedule: `30 2 * * *` (UTC), which is **10:30 Beijing time** every day
- Manual run: Actions -> `Daily AI Brief` -> `Run workflow`

The workflow runs the generator, updates:

- `AI简报-YYYY-MM-DD.html`
- `AI简报-YYYY-MM-DD.txt`
- `AI简报-YYYY-MM-DD.json`
- `index.html`
- `latest.html`

Then commits and pushes changes to `main`.

## Required repository settings

1. **Actions write permission**
   - GitHub -> Settings -> Actions -> General
   - Set **Workflow permissions** to **Read and write permissions**

2. **Pages source**
   - GitHub -> Settings -> Pages
   - Source: `Deploy from a branch`
   - Branch: `main` and folder `/ (root)`

## Optional secrets (for model summary block)

If not provided, the page still updates using RSS aggregation.

- `AI_DAILY_BRIEF_API_KEY` (or `OPENAI_API_KEY`)
- `AI_DAILY_BRIEF_MODEL` (or `OPENAI_MODEL`)
- `AI_DAILY_BRIEF_BASE_URL` (or `OPENAI_BASE_URL`) for OpenAI-compatible endpoints

Do **not** commit secrets into files. Local-only env can be placed in `scripts/ai-daily-brief.env` (ignored by `.gitignore`).
