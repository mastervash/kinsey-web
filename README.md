# maigret-web

Private, web-first fork of [soxoj/maigret](https://github.com/soxoj/maigret). Search for a person by
**username**, **real name**, or **email** across ~6k sites, with false-positive scoring on every hit.

No CLI. FastAPI backend (`maigret/server`) + React/Vite/TS frontend (`web/`).

## What differs from upstream

- **Control probe + confidence score.** Every hit is re-checked against a random never-existing username
  on the same site. If both responses look alike (status, title, simhash of visible text, length), the hit
  is demoted. Each result carries `confidence` 0-100, scoring `reasons`, and target/control evidence.
- **Soft-404 / wall detection**: not-found phrases (multi-language), login walls, parked domains,
  redirects to homepage/login.
- **Feedback loop**: mark results confirmed / false positive; per-site reliability feeds future scores.
- **Person search**: name permutations + nickname table, and directory lookups (GitHub, GitLab, Bluesky,
  Mastodon, Keybase, Hacker News, Gravatar for email) ranked against optional context (location,
  employer, school, keywords). Top candidates are then scanned.
- **Site DB**: WhatsMyName import, quarantine flags from `tools/verify_sites.py`, NSFW excluded by default.
- Entity graph, history (SQLite), JSON/CSV/HTML export, optional bearer-token auth.

## Run

```bash
python3 -m venv .venv && .venv/bin/pip install -e . pytest pytest-asyncio pytest-httpserver httpx
cd web && npm ci && npm run build && cd ..
MW_HOST=127.0.0.1 MW_PORT=7580 .venv/bin/maigret-web     # serves UI + /api
```

Dev: `make dev-api` and `make dev-web` (vite proxies `/api` to :7580).

Env: `MW_HOST`, `MW_PORT`, `MW_DATA_DIR` (sqlite), `MW_SITES_DB`, `MW_DIST`, `MW_TOKEN` (enables auth).

Deploy on oci: `deploy/maigret-web.service` (tailnet-only bind). API contract: `docs/API.md`.
Plan: `ROADMAP.md`.

## Upstream sync

`git fetch upstream && git log upstream/main -- maigret/resources/data.json` then cherry-pick site fixes.
Engine files (`checking.py`, `sites.py`) are modified; expect conflicts there.
