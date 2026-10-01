export type Status = "claimed" | "uncertain" | "available" | "blocked" | "unknown" | "illegal";
export type Kind = "username" | "name" | "email";
export type SearchStatus = "queued" | "running" | "done" | "stopped" | "error";
export type Verdict = "confirmed" | "false_positive" | null;

export interface Fingerprint {
  status: number | null;
  length: number;
  title: string;
  final_url: string;
}

export interface Result {
  id: string;
  search_id: string;
  site: string;
  url_main: string;
  url: string;
  username: string;
  status: Status;
  confidence: number;
  reasons: string[];
  http_status: number | null;
  tags: string[];
  ids: Record<string, string>;
  evidence: { target?: Fingerprint; control?: Fingerprint; similarity?: number };
  feedback: Verdict;
  checked_at: string;
}

export interface Candidate {
  username: string;
  score: number;
  sources: string[];
  display_name?: string;
  profile_url?: string;
  avatar?: string;
  bio?: string;
  location?: string;
}

export interface SearchContext {
  location?: string;
  employer?: string;
  school?: string;
  email?: string;
  keywords?: string[];
}

export interface SearchOptions {
  top_sites: number;
  tags: string[];
  exclude_tags: string[];
  sites: string[];
  timeout: number;
  min_confidence: number;
  control_probe: boolean;
  include_nsfw: boolean;
  max_candidates: number;
  recursive: boolean;
}

export interface Counts {
  total: number;
  checked: number;
  claimed: number;
  uncertain: number;
}

export interface Search {
  id: string;
  kind: Kind;
  query: string;
  context: SearchContext;
  options: SearchOptions;
  status: SearchStatus;
  created_at: string;
  finished_at: string | null;
  counts: Counts;
  candidates: Candidate[];
}

export interface SiteRow {
  name: string;
  url_main: string;
  url: string;
  tags: string[];
  check_type: string;
  disabled: boolean;
  quarantined: boolean;
  rank: number;
  reliability: number | null;
  fp_reports: number;
  confirmations: number;
  last_verified: string | null;
  source: "maigret" | "wmn" | "custom";
}

export type SiteDetail = SiteRow & { raw: Record<string, unknown> };

export interface TagCount {
  tag: string;
  count: number;
}

export interface Settings {
  defaults: SearchOptions;
  proxy: string | null;
  tor_proxy: string | null;
}

export interface Stats {
  sites_total: number;
  sites_enabled: number;
  sites_quarantined: number;
  searches: number;
  results_claimed: number;
  feedback_fp: number;
  feedback_confirmed: number;
}

export interface Health {
  ok: boolean;
  version: string;
  sites_enabled: number;
}

export interface GraphNode {
  id: string;
  label: string;
  type: string;
}
export interface GraphEdge {
  source: string;
  target: string;
  label?: string;
}
export interface GraphData {
  nodes: GraphNode[];
  edges: GraphEdge[];
}

export interface SiteTestResult {
  claimed: Result;
  unclaimed: Result;
  healthy: boolean;
}

export const DEFAULT_OPTIONS: SearchOptions = {
  top_sites: 500,
  tags: [],
  exclude_tags: [],
  sites: [],
  timeout: 10,
  min_confidence: 60,
  control_probe: true,
  include_nsfw: false,
  max_candidates: 8,
  recursive: false,
};
