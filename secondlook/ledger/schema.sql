-- Second Look ledger (design §2.2). One database per match; every row carries match_id.
CREATE TABLE IF NOT EXISTS events (
  event_id TEXT PRIMARY KEY, match_id TEXT NOT NULL, seq INTEGER, type TEXT NOT NULL,
  period INTEGER, match_clock_ms INTEGER, match_ms INTEGER NOT NULL,
  team_id TEXT, player_id TEXT, possession_id INTEGER,
  payload TEXT NOT NULL, arrival_order INTEGER, arrival_match_ms INTEGER,
  status TEXT NOT NULL DEFAULT 'active', corrected_by TEXT,
  rev INTEGER NOT NULL, corrected_rev INTEGER
);
CREATE INDEX IF NOT EXISTS ix_events_time ON events(match_ms, seq);
CREATE INDEX IF NOT EXISTS ix_events_type ON events(type, team_id);

CREATE TABLE IF NOT EXISTS revisions (
  rev INTEGER PRIMARY KEY, match_id TEXT NOT NULL, kind TEXT NOT NULL,
  event_id TEXT, at_match_ms INTEGER
);
CREATE TABLE IF NOT EXISTS feed_gaps (
  gap_id INTEGER PRIMARY KEY AUTOINCREMENT, match_id TEXT NOT NULL,
  from_seq INTEGER NOT NULL, to_seq INTEGER NOT NULL, opened_rev INTEGER NOT NULL,
  closed_rev INTEGER, opened_ms INTEGER
);
CREATE TABLE IF NOT EXISTS feed_stats (name TEXT PRIMARY KEY, value INTEGER NOT NULL);

-- Derived rows: key columns + the pydantic dump in `data`.
CREATE TABLE IF NOT EXISTS moments (
  id TEXT PRIMARY KEY, match_id TEXT, status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS candidates (
  id TEXT PRIMARY KEY, match_id TEXT, moment_id TEXT, status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claims (
  id TEXT PRIMARY KEY, match_id TEXT, owner_kind TEXT, owner_id TEXT, status TEXT,
  checked_rev INTEGER, reason TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS claim_event_deps (
  claim_id TEXT NOT NULL, event_id TEXT NOT NULL, PRIMARY KEY (claim_id, event_id));
CREATE INDEX IF NOT EXISTS ix_ced_event ON claim_event_deps(event_id);
CREATE TABLE IF NOT EXISTS claim_window_deps (
  claim_id TEXT NOT NULL, team_id TEXT, event_types TEXT NOT NULL, from_ms INTEGER NOT NULL,
  to_ms INTEGER NOT NULL);
CREATE INDEX IF NOT EXISTS ix_cwd_claim ON claim_window_deps(claim_id);
CREATE TABLE IF NOT EXISTS verdicts (
  id TEXT PRIMARY KEY, match_id TEXT, moment_id TEXT, status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS stories (
  id TEXT PRIMARY KEY, match_id TEXT, story_id TEXT, revision INTEGER, moment_id TEXT,
  status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS viewer_outputs (
  id TEXT PRIMARY KEY, match_id TEXT, story_id TEXT, story_revision INTEGER, profile_id TEXT,
  status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS overlays (
  cursor INTEGER PRIMARY KEY AUTOINCREMENT, id TEXT UNIQUE NOT NULL, match_id TEXT,
  story_id TEXT, revision INTEGER, profile_id TEXT, kind TEXT, status TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS deps (
  parent_kind TEXT NOT NULL, parent_id TEXT NOT NULL, child_kind TEXT NOT NULL,
  child_id TEXT NOT NULL, PRIMARY KEY (parent_kind, parent_id, child_kind, child_id));
CREATE INDEX IF NOT EXISTS ix_deps_child ON deps(child_kind, child_id);
CREATE TABLE IF NOT EXISTS viewer_profiles (
  id TEXT PRIMARY KEY, match_id TEXT, data TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS agent_calls (
  call_id TEXT PRIMARY KEY, match_id TEXT, moment_id TEXT, agent TEXT, model TEXT,
  started_wall TEXT, ms INTEGER, tokens_in INTEGER, tokens_out INTEGER, outcome TEXT,
  trace_id TEXT, cached INTEGER DEFAULT 0);
