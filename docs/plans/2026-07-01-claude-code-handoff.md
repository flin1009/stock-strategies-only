# GitHub Actions Telegram Handoff

> **For Claude Code:** REQUIRED SUB-SKILL: Use `superpowers:executing-plans` to execute this handoff task-by-task.

**Goal:** Finish the GitHub Actions + Telegram onboarding work so users can fork the repo, set secrets, run a Telegram smoke test, then rely on daily scheduled signal delivery.

**Architecture:** The production workflows live under `.github/workflows/`. Root-level `daily.yml` and `premarket.yml` remain as templates, but GitHub only executes workflow files under `.github/workflows/` on the default branch.

**Tech Stack:** GitHub Actions, Python 3.11, uv, FastAPI/Next.js repo, Telegram Bot API.

## Current State

The working tree already had unrelated local changes before this handoff. Do not revert them.

Known unrelated or pre-existing dirty files:
- `stock_strategies/evaluate.py`
- `web/app/page.tsx`
- `web/lib/api.ts`
- `.playwright-mcp/`
- `dashboard-idle.png`
- `frontend/`
- `web/tsconfig.tsbuildinfo`
- `wireframe-viewport.jpeg`

Changes made for this task:
- Added `.github/workflows/daily.yml`
- Added `.github/workflows/telegram-smoke-test.yml`
- Updated `README.md`

## Task 1: Verify Workflow Files

**Files:**
- Read: `.github/workflows/daily.yml`
- Read: `.github/workflows/premarket.yml`
- Read: `.github/workflows/telegram-smoke-test.yml`

**Steps:**
1. Run `git diff --check`.
2. Confirm all three workflow files exist under `.github/workflows/`.
3. Confirm `daily.yml` runs `uv run python main.py`.
4. Confirm `premarket.yml` runs `uv run python premarket.py`.
5. Confirm `telegram-smoke-test.yml` only requires `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`.

## Task 2: Verify README Onboarding

**Files:**
- Read: `README.md`

**Steps:**
1. Search README for stale copy instructions:
   `rg "cp .*workflow|cp daily.yml|cp premarket.yml" README.md`
2. Expected: no stale instruction telling users to copy workflows into `.github/workflows/`.
3. Confirm README has a top-level fast setup section.
4. Confirm Step 7 tells users to run `Telegram Smoke Test` first.
5. Confirm Step 7 includes common Telegram/GitHub Actions error mapping.
6. Confirm README explicitly tells users to put the five values under Actions **Secrets**, not Variables.

## Task 3: Run Local Verification

Run:

```bash
git diff --check
uv run pytest -q
cd web && npm run build
```

Expected:
- `git diff --check` exits 0
- `uv run pytest -q` reports all tests passing
- `npm run build` exits 0

## Task 4: Commit Only This Scope

Do not use `git add .`.

Commit only:

```bash
git add README.md .github/workflows/daily.yml .github/workflows/telegram-smoke-test.yml docs/plans/2026-07-01-claude-code-handoff.md
git commit -m "setup: enable daily signal workflow and telegram smoke test"
```

Leave unrelated dirty files untouched.

## Task 5: User-Facing Next Steps

Tell the user to push, then run GitHub Actions in this order:

1. Set repository Actions secrets under `Settings` → `Secrets and variables` → `Actions` → `Secrets`.
   Do not put these in Variables; the workflows read `secrets.*`.
   - `FINMIND_TOKEN`
   - `TELEGRAM_BOT_TOKEN`
   - `TELEGRAM_CHAT_ID`
   - `GOOGLE_SHEET_ID`
   - `GOOGLE_CREDS_JSON`
2. Run `Actions` → `Telegram Smoke Test` → `Run workflow`.
3. If Telegram receives the test message, run `V3 Daily Signal`.
4. If daily signal fails, inspect the failing Actions log before changing code.

## Optional Follow-Up Backlog

After the workflow setup is stable:

1. Add a demo mode so users can see the dashboard without secrets.
2. Change `/api/run` from a long synchronous request to job/progress/SSE.
3. Add a real CI workflow for `uv run pytest -q` and `cd web && npm run build`.
4. Expose watchlist add/remove API in the UI.
5. Add a Performance page using `stock_strategies/performance.py`.
