# Fork roadmap

Baseline (upstream `soxoj/maigret` @ b664274, 2026-09-25): 6206 sites, 698 disabled.
Active checks: 1231 `status_code`, 771 `message`, 74 `response_url`, 3432 inherit from engine.
1228 of 1231 active `status_code` sites have no `absenceStrs`, so any 200 page counts as "found".
That is the main false-positive source.

## 0. Repo surgery
- Private copy (GitHub forks of public repos cannot be private, so duplicate-push). Keep `upstream` remote for cherry-picks of site fixes.
- Drop CLI: `maigret/maigret.py` arg parsing, `__main__`, `pyinstaller/`, `snap/`, `Installer.bat`, `wizard.py`, `cloudshell-tutorial.md`, `example.ipynb`, translated READMEs, the `alive_progress`/`colorama` console deps, 1 MB generated `sites.md`.
- Keep engine as library: `checking.py`, `sites.py`, `result.py`, `errors.py`, `executors.py`, `activation.py`, `socid-extractor` integration.
- Split `checking.py` (74 KB) into transport / detection / extraction modules.

## 1. False positives (top priority)
1. **Calibration probe per site.** Before trusting a hit, request 1-2 random never-existing usernames on the same site in the same run. If the response for the target matches the random one (status, length within N%, same `<title>`, same simhash), mark `UNCERTAIN` not `CLAIMED`. Cache per site per day.
2. **Response fingerprint diff.** Compare target vs. control on: status, final URL, title, body length, simhash of visible text, presence of username in body/title/canonical/og:url. Score instead of boolean.
3. **Confidence score 0-100** per result, derived from: check type, control-diff strength, username echoed in profile fields, extracted ids (socid-extractor), site reliability history. UI default filter: >= 70.
4. **Global soft-404 / WAF detectors:** Cloudflare/Akamai/DDoS-Guard challenge pages, captcha walls, login walls, parked domains, "search results for X" pages, homepage redirects. Classify as `BLOCKED`/`UNKNOWN`, never `CLAIMED`.
5. **Username-reflection guard:** pages that echo any input ("No user X found" embedded in 200) caught by control probe and by negative-phrase dictionary (multi-language).
6. **Auto-quarantine:** nightly job runs every site with a known-good (`usernameClaimed`) and known-bad (`usernameUnclaimed`) name; sites failing either get disabled automatically with reason + date.
7. **User feedback loop:** "false positive" / "confirmed" buttons in UI feed a per-site reliability stat; sites under threshold auto-demote.
8. **Dedupe:** collapse mirrors/alias domains and same-engine clones (thousands of forum/XenForo/vBulletin/uCoz entries under `forum`/`discussion` tags).
9. Default site set = reliability-ranked, not "top N by Alexa rank" (Alexa is dead; rank data is stale).

## 2. Site database refresh
- Rerun all 698 disabled sites; re-enable or delete.
- Delete dead domains (DNS NXDOMAIN, parked, expired TLS).
- Replace Alexa rank with Tranco list rank.
- Import/merge candidates from other projects' lists: Sherlock, WhatsMyName (`wmn-data.json`, has explicit `e_string`/`m_string` = good FP behavior), Blackbird, social-analyzer. Dedupe by domain.
- Add modern platforms missing or broken: Bluesky, Threads, Mastodon (multi-instance via WebFinger), Lemmy/Kbin, Pixelfed, Substack, Kick, Linktree-alikes, Ko-fi/Patreon, Cara, Letterboxd, Strava, Discord lookups via public bot directories, etc.
- Prefer official/public JSON APIs over HTML scraping where available (fewer FPs, structured data).
- Schema v2: required `presenceStrs` OR `absenceStrs` OR API check; `lastVerified`, `reliability`, `category`, `nsfw`, `region`, `requiresAuth`.
- CI: site DB lint + weekly verification run with diff report.

## 3. Real-name / person search
- **Name to username candidates:** permutator upgrade: `first.last`, `firstlast`, `flast`, `firstl`, `lastfirst`, with digits/years, nickname table (Robert to bob/rob/bobby), diacritic folding, middle initial. Ranked by likelihood, capped.
- **Name-searchable sources** (sites that support search by display name, not username): GitHub user search API, GitLab, Gravatar, Keybase, Bluesky actor search, Mastodon account search, Reddit, ORCID, Google Scholar, Wikipedia/Wikidata, OpenCorporates, Hacker News (Algolia), npm/PyPI maintainers, Medium, Behance/Dribbble, YouTube channel search.
- **Disambiguation:** optional context fields (location, employer, school, age range, known email/domain, photo). Score candidates against context.
- **Pivoting graph:** from any confirmed profile, extract linked accounts/usernames/emails (socid-extractor + bio link parsing) and queue them; show as entity graph.
- Optional: email to account checks (holehe-style), phone format normalization. Gate behind settings.
- Search-engine dorks module (SearXNG self-hosted backend) for `"First Last" site:` queries.

## 4. Web UI (primary product)
- Backend: FastAPI + asyncio (engine already async; drop Flask + thread/queue bridge). WebSocket/SSE live results.
- Frontend: SPA (React or Svelte), dark-only, black base, red/purple neon accents, glass cards.
- Views: new search (username | real name | email), live results grid with confidence filter, per-result evidence drawer (target vs. control diff, screenshot, extracted fields), entity graph, case/investigation history, site DB browser/editor with test button, settings (proxies, Tor, concurrency, timeouts).
- Persistence: SQLite/Postgres for cases, results, site reliability stats.
- Exports: JSON, CSV, HTML, PDF report, graph (GEXF).
- Optional headless screenshot of each hit (Playwright) for quick visual triage.
- Auth (single-user token or OIDC) since it will be self-hosted.
- Docker compose: app + worker + SearXNG + optional Tor/proxy.

## 5. Engine/perf
- Per-domain rate limiting and retry with jitter; proxy rotation; Tor per-site option.
- curl_cffi / TLS-fingerprint impersonation for sites that block aiohttp.
- Job queue (arq/Redis or in-process) so multiple searches run concurrently.
- Result caching per (site, username, day).

## Suggested order
1. Repo surgery + FastAPI skeleton running the existing engine.
2. Control-probe + confidence scoring (biggest FP win).
3. Nightly verification/quarantine + site DB refresh/merge.
4. Web UI v1.
5. Real-name search + pivot graph.
