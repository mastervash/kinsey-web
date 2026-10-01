# kinsey-web API contract (v1)

Base: same origin, `/api/*`. Auth: if env `KW_TOKEN` set, every `/api` call needs
`Authorization: Bearer <token>` (SSE: `?token=<token>` query param). Frontend keeps token in localStorage, prompts on 401.

## Types

```ts
type Status = "claimed" | "uncertain" | "available" | "blocked" | "unknown" | "illegal";
type Kind = "username" | "name" | "email";

interface Fingerprint { status: number|null; length: number; title: string; final_url: string; }

interface Result {
  id: string;            // `${search_id}:${site}:${username}`
  search_id: string;
  site: string;
  url_main: string;
  url: string;           // profile url
  username: string;      // username actually checked (name search: candidate)
  status: Status;
  confidence: number;    // 0-100
  reasons: string[];     // human-readable scoring reasons, e.g. "control probe differs (title)"
  http_status: number|null;
  tags: string[];
  ids: Record<string, string>;   // extracted profile fields (fullname, bio, links, image, ...)
  evidence: { target?: Fingerprint; control?: Fingerprint; similarity?: number };
  feedback: "confirmed" | "false_positive" | null;
  checked_at: string;    // ISO
}

interface Candidate {   // name search
  username: string; score: number; sources: string[];   // e.g. ["permutation"], ["github:search"]
  display_name?: string; profile_url?: string; avatar?: string; bio?: string; location?: string;
}

interface Search {
  id: string; kind: Kind; query: string;
  context: { location?: string; employer?: string; school?: string; email?: string; keywords?: string[] };
  options: SearchOptions;
  status: "queued" | "running" | "done" | "stopped" | "error";
  created_at: string; finished_at: string|null;
  counts: { total: number; checked: number; claimed: number; uncertain: number };
  candidates: Candidate[];   // kind=name/email only
}

interface SearchOptions {
  top_sites: number;        // default 500, 0 = all enabled
  tags: string[]; exclude_tags: string[];
  sites: string[];          // explicit list overrides top_sites/tags
  timeout: number;          // seconds, default 10
  min_confidence: number;   // default 60, results below are status "uncertain"
  control_probe: boolean;   // default true
  include_nsfw: boolean;    // default false
  max_candidates: number;   // name search: usernames to scan, default 8
  recursive: boolean;       // follow extracted usernames, default false
}
```

## Endpoints

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | /api/health | | `{ok, version, sites_enabled}` |
| POST | /api/searches | `{kind, query, context?, options?}` | `Search` |
| GET | /api/searches | `?limit=50` | `Search[]` newest first |
| GET | /api/searches/{id} | | `{search: Search, results: Result[]}` (all non-available results) |
| DELETE | /api/searches/{id} | | `{ok}` |
| POST | /api/searches/{id}/stop | | `{ok}` |
| GET | /api/searches/{id}/events | SSE | events below |
| GET | /api/searches/{id}/export | `?format=json\|csv\|html` | file |
| GET | /api/searches/{id}/graph | | `{nodes:[{id,label,type}], edges:[{source,target,label}]}` |
| POST | /api/results/feedback | `{result_id, verdict: "confirmed"\|"false_positive"\|null}` | `{ok}` |
| GET | /api/sites | `?q=&tag=&state=enabled\|disabled\|quarantined&sort=rank\|name\|reliability&offset=&limit=` | `{total, items: SiteRow[]}` |
| GET | /api/sites/{name} | | `SiteRow & {raw: object}` |
| PATCH | /api/sites/{name} | partial `{disabled?, tags?, presenceStrs?, absenceStrs?, checkType?, url?}` | `SiteRow` |
| POST | /api/sites/{name}/test | `{username?}` | `{claimed: Result, unclaimed: Result, healthy: boolean}` |
| GET | /api/tags | | `{tag: string, count: number}[]` |
| GET/PUT | /api/settings | `{defaults: SearchOptions, proxy, tor_proxy}` | same |
| GET | /api/stats | | `{sites_total, sites_enabled, sites_quarantined, searches, results_claimed, feedback_fp, feedback_confirmed}` |

`SiteRow = {name, url_main, url, tags, check_type, disabled, quarantined, rank, reliability: number|null, fp_reports: number, confirmations: number, last_verified: string|null, source: "maigret"|"wmn"|"custom"}`

## SSE events (`event:` name, `data:` JSON)
- `start` `{total}`
- `candidates` `{candidates: Candidate[]}`  (name/email search, before scanning)
- `progress` `{checked, total}`
- `result` `Result` (only status != available/illegal)
- `done` `{status, counts}`
- `error` `{message}`
On connect, server replays all existing results for the search, then streams live.
