# Ad Ops Command Center

Local operating system for self-hosted paid-ads management. Eight modules:
Dashboard, Creative Insights, Ad Grader, Daily Pipeline, Brief Console,
Static Ad Generator (Cloner), Competitor Scan, Ads Spy. Multi-client, with a demo dataset


## Grader (pause/scale with confirmation)

Grades every ad A-F from synced metrics vs the client's CPL/ROAS targets
(clients.kpi_json), queues pause/scale recommendations in grader_actions, and
executes only after you click Apply (Meta only; other platforms report
"apply manually"). Recommendations auto-refresh after the daily sync.
Settings: grader_min_spend, grader_lookback_days, grader_scale_budget_pct.

## Daily Pipeline (awareness-stage creative batches)

Five ads per batch, one per Eugene Schwartz awareness stage, written from the
brand profile + personas + compliance rules, each audited (pass/warn/block).
Images per ad in 1:1, 4:5 and 9:16 (exact 1080px specs, disclaimer bar + logo
composited). Product images uploaded per client are passed to the image model
as references for compositing. Thumbs up/down feedback is embedded in future
prompts (learning loop). Scheduled daily at pipeline_daily_time for client ids
listed in pipeline_enabled_clients (comma-separated; empty = manual only).

Persona miner: Reddit (Apify actor `trudax/reddit-scraper-lite`) + review-page
URLs -> Gemini -> personas table (feeds Briefs + Pipeline).

Landing page intel: scans capture ad landing URLs and analyse up to
landing_max_per_competitor new pages per competitor (offer, hooks, CTA
structure, test ideas); shown on the scan detail view.

## Ads Spy platforms

Meta (`curious_coder/facebook-ads-library-scraper`), Google Transparency
Center (`scrapesage/google-ads-transparency-scraper`), LinkedIn Ad Library
(`s-r/linkedin-ads-library`) - actor ids swappable in Settings.

## Run

```
run.bat
```

First run installs Python deps, seeds a demo database, and opens
http://localhost:7480. Subsequent runs just start the server.

## Stack

FastAPI + SQLite (WAL) + vanilla JS/Chart.js frontend (no build step).
Python 3.14, port 7480. Data lives in `data/` (gitignored).

## Credentials

All credentials are optional — the app runs with a demo database and no
integrations. Add the ones you need. Nothing secret is stored in the repo;
secrets live in your OS Credential Manager or an untracked `~/.adops/.env`.

| Integration | Where | Notes |
|---|---|---|
| Meta Ads | Credential Manager `adops-meta-ads` (fallback `META_*` in `~/.adops/.env`) | Get app_id/app_secret from a Meta app; run `python tools/reauth_meta.py` with a short-lived token from Graph API Explorer to store a 60-day token |
| Google Ads | `~/google-ads.yaml` | Provide client_id/secret/dev-token, then `python tools/reauth_google.py` (opens browser, writes the refresh token) |
| Bing/Microsoft | `BING_*` in `~/.adops/.env` | Optional |
| GA4 | `~/.adops/ga4-service-account.json` | Service-account key with Viewer on the property |
| LinkedIn | keyring `linkedin:reporting` + `~/.adops/linkedin-tokens.json` | Optional |
| Apify (Spy/Scans) | `APIFY_TOKEN` in `~/.adops/.env` | Needed for competitor ad-library scans |
| Gemini (AI gen) | `GEMINI_API_KEY` in `~/.adops/.env` | Needed for AI copy/image generation |

### Restoring Meta / Google (one command each)

Both apps' base credentials are fine; only the user tokens expired. The `tools/`
helpers do everything except the human consent step:

- **Meta**: grab a short-lived token at https://developers.facebook.com/tools/explorer
  (pick the app → Generate Access Token → grant `ads_management` + `ads_read`), then
  `python tools/reauth_meta.py` — it exchanges it for a 60-day token and stores it in
  Credential Manager. Renew every ~60 days.
- **Google**: `python tools/reauth_google.py` — opens a browser for Google consent,
  then writes the new `refresh_token` into `~/google-ads.yaml` automatically.

## Sync

- Manual: "Sync now" in the top bar, or `POST /api/sync/{platform}`.
- Scheduled: daily 06:30 metrics sync, Monday 06:00 competitor scans
  (APScheduler inside the app process - the app must be running; times
  editable in Settings).
- First sync per account backfills 90 days; after that a rolling 14-day
  window (metrics restate).
- CSV fallback: Settings > CSV import (Google Ads Editor preset matches the
  Sample Client export files in the vault).

## Demo seed

`python -m seed.seed_vault` (idempotent; `--review` for a dry run). Seeds a
small fictional dataset — example clients, a brand profile, personas, a generic
advertising-standards ruleset, competitors, placeholder ad accounts, and a
couple of reference ads — so a fresh install has something to show. Edit
`seed/seed_vault.py` to add your own clients, or just point the app at real
accounts and let the sync jobs populate the database.

## Compliance guardrails

1. Rules embedded in every generation prompt (edit them per client in the app,
   or in the seed's `COMPLIANCE_RULES`).
2. Post-generation audit (Gemini) stored on each brief: pass / warn / block.
3. Deterministic Pillow overlay: a disclaimer bar + logo composited onto every
   generated image — never left to the image model. Output resized to the
   1080x1080 boosted-ad spec.

## Apify actors (Settings)

- Ad Library: `curious_coder/facebook-ads-library-scraper` (~$0.75/1000 ads,
  min 10 results per run; validated against real ZA output 2026-07-07)
- Organic posts: `apify/facebook-posts-scraper`
