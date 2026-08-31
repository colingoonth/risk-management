-- Who is who in GroupMe, and which chat is which.
--
-- Two tables, because they answer two different questions and neither one
-- should have to guess at the other's answer.
--
-- groupme_groups is the address book. The chapter's real GroupMe ids are
-- credentials-adjacent — this repository is public — so they live HERE, in the
-- chair's local database, seeded by `risk groupme seed` from argv or stdin, and
-- NEVER in a source file, a test fixture or a commit message. A slug is what
-- the code refers to; the id is what the HTTP client resolves it to at the last
-- possible moment.
--
-- WEEKDAY IS 1=Mon..7=Sun HERE, AND THAT DISAGREES WITH THE REST OF THIS SCHEMA
-- ON PURPOSE. `unavailability.repeats_weekday` and `member_shift_preferences.weekday`
-- are 0=Mon (Python's `date.weekday()`); this column is 1=Mon (`date.isoweekday()`).
--
-- The reason is that those columns are nullable and NULL means "any day", so 0
-- has to be allowed to mean Monday. This column is nullable too, but NULL here
-- means "not a day topic at all" — a parent group, or the roster source. With
-- 0=Mon a Monday topic and a non-topic would sit one keystroke apart in a table
-- a human seeds by hand, and reading `weekday=0` as "unset" is a mistake nobody
-- would notice until a Monday dage went to the wrong chat.
--
-- ONE convention, stated once: every read and write of this column goes through
-- `date.isoweekday()` (repos/groupme_groups.py). Do not mix in `weekday()`.
--
-- UNIQUE(parent_slug, weekday) is what makes "the Friday topic" a well-defined
-- phrase. Without it, seeding a second Friday row — a re-created chat, a typo,
-- a half-finished migration — leaves two, and the lookup is a `fetchone()` that
-- silently picks whichever the query planner reaches first. The announcement
-- then lands in an abandoned chat and reads to the chair as though it was never
-- sent. Partial (WHERE weekday IS NOT NULL) because the non-topic rows all
-- share a NULL weekday and are allowed to.
--
-- groupme_id is UNIQUE and non-blank for the same class of reason: two slugs
-- pointing at one chat double-post it, and a blank id turns every URL built
-- from it into a request against `/groups//messages`.
-- groupme_identities is the roster join, and its whole design is about refusing
-- to guess. One row per member (PRIMARY KEY (member_id)) and one row per
-- GroupMe account (ux_groupme_identity_user), so a mis-link is a constraint
-- violation rather than two brothers quietly sharing a mention.
--
-- `confidence` records HOW the link was made:
--   exact      the GroupMe nickname equalled the roster display name
--   alias      it equalled a registered member_aliases row
--   confirmed  a human said so
-- Nothing else links. There is no fuzzy tier on purpose: two similar names on
-- one roster are exactly the case where a similarity score is confident and
-- wrong, and the cost of being wrong is @-ing the wrong brother in front of the
-- chapter, or removing him from the group the night before he works.
--
-- `nickname` is stored as a copy of what GroupMe showed at link time. It is not
-- the key — people rename themselves mid-semester — but keeping it lets the
-- mapper report drift ("this account is now called something else") instead of
-- silently continuing to trust a stale match.
CREATE TABLE IF NOT EXISTS groupme_groups (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,          -- 'risk-parent' | 'risk-tuesday' | 'risk-friday'
                                      -- | 'risk-saturday' | 'roster-source'
  groupme_id TEXT NOT NULL,
  parent_slug TEXT,                   -- NULL for a parent group; else 'risk-parent'
  weekday INTEGER,                    -- 1=Mon..7=Sun for day topics, NULL otherwise
  label TEXT NOT NULL,
  CHECK (length(trim(groupme_id)) > 0),
  CHECK (length(trim(slug)) > 0),
  CHECK (length(trim(label)) > 0),
  CHECK (weekday IS NULL OR weekday BETWEEN 1 AND 7)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_groupme_group_remote
  ON groupme_groups(groupme_id);

CREATE UNIQUE INDEX IF NOT EXISTS ux_groupme_group_day
  ON groupme_groups(parent_slug, weekday)
  WHERE weekday IS NOT NULL;

CREATE TABLE IF NOT EXISTS groupme_identities (
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  groupme_user_id TEXT NOT NULL,
  nickname TEXT,                      -- as seen in GroupMe, for display + drift detection
  confidence TEXT NOT NULL,           -- 'exact' | 'alias' | 'confirmed'
  linked_at TEXT NOT NULL,
  PRIMARY KEY (member_id)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_groupme_identity_user
  ON groupme_identities(groupme_user_id);

-- =========================================================================
-- Outbound ledger. The thing standing between a crash and sixty brothers
-- reading the same announcement twice.
-- =========================================================================
--
-- A deterministic `source_guid` is NOT enough on its own. GroupMe de-duplicates
-- a repeated source_guid for roughly a minute; a process that dies after the
-- request leaves the socket and is restarted ten minutes later by a LaunchAgent
-- sails straight past that window and posts again. So does a double-click, and
-- so do two processes racing.
--
-- The ledger is therefore the guard and the guid is only the second line. A row
-- is RESERVED IN ITS OWN TRANSACTION BEFORE the HTTP call, never after: the
-- whole failure being defended against is the gap between "sent" and "recorded
-- as sent", and a row written afterwards lives entirely inside that gap.
--
-- KEY IS (event_id, destination_slug, content_version). content_version is a
-- hash of the exact message text and its mention tuples, which makes the two
-- cases behave differently on purpose:
--   same text, again      -> same key, row already exists, refuses to re-send
--   roster changed        -> new key, new row, sends (it is a new announcement)
--
-- FOUR STATES, and `unknown` is the important one:
--   pending  reserved, request not yet known to have completed
--   sent     GroupMe acknowledged it
--   failed   definitely not delivered (a 4xx, or a pre-flight refusal) — retryable
--   unknown  the request was in flight when the answer was lost: a timeout, a
--            reset connection, a 5xx. It may or may not have posted.
--
-- `unknown` NEVER retries on its own. Retrying is a coin flip whose losing side
-- is a duplicate announcement to the whole chapter, and no timeout heuristic can
-- tell the two apart from outside. It is resolved by `risk groupme reconcile`,
-- which reads the topic back and looks for the source_guid GroupMe echoes on
-- every message — evidence rather than a guess. A stale `pending` (a process
-- that died mid-call) is treated exactly like `unknown`, because it is.
CREATE TABLE IF NOT EXISTS groupme_outbound (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  destination_slug TEXT NOT NULL,
  content_version TEXT NOT NULL,
  source_guid TEXT NOT NULL UNIQUE,
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending', 'sent', 'unknown', 'failed')),
  attempts INTEGER NOT NULL DEFAULT 0,
  reserved_at TEXT NOT NULL,
  settled_at TEXT,
  message_id TEXT,
  last_error TEXT
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS ux_groupme_outbound_key
  ON groupme_outbound(event_id, destination_slug, content_version);

-- The reconcile queue: everything not settled. Partial, because by the end of a
-- term this table is almost entirely 'sent' and the queue is what gets read.
CREATE INDEX IF NOT EXISTS groupme_outbound_unsettled
  ON groupme_outbound(state, reserved_at)
  WHERE state IN ('pending', 'unknown');
