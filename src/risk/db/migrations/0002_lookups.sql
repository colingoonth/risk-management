-- Phase 1: lookup tables + seed data.
-- Schema per canonical plan §2.1; seed per Phase 1 exit criteria.

CREATE TABLE IF NOT EXISTS houses (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE TABLE IF NOT EXISTS pledge_modes (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE TABLE IF NOT EXISTS roles (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  automation_key TEXT NULL UNIQUE,
  default_excluded_from_assignment INTEGER NOT NULL DEFAULT 0
    CHECK (default_excluded_from_assignment IN (0, 1)),
  exclude_is_soft INTEGER NOT NULL DEFAULT 0
    CHECK (exclude_is_soft IN (0, 1)),
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*'),
  CHECK (automation_key IS NULL
         OR (automation_key GLOB '[a-z]*'
             AND automation_key NOT GLOB '*[^a-z0-9-]*')),
  CHECK (automation_key IS NULL OR automation_key NOT IN
    ('help', 'version', 'verbose', 'quiet', 'dry-run', 'db', 'config', 'json', 'json-raw')),
  CHECK (NOT (exclude_is_soft = 1 AND default_excluded_from_assignment = 1
              AND automation_key IS NULL))
) STRICT;

CREATE TABLE IF NOT EXISTS shift_types (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  -- Whether working this post counts as risk work for fairness purposes.
  -- DJ does not: it is a different job, and counting it let two qualified
  -- members absorb all 43 DJ nights, price themselves out of every other pool,
  -- and then top the chapter's shift ledger having stood no risk shifts at all.
  -- Added in 0016, which also seeds dj to 0; see that file.
  counts_toward_tally INTEGER NOT NULL DEFAULT 1
    CHECK (counts_toward_tally IN (0, 1)),
  -- What a turn at this post costs the member, relative to standing a party
  -- night. Fairness is denominated in this rather than in a headcount, so a
  -- member doing the easier job works more of them to reach the same quota.
  -- Seeded in 0020; 1.0 for everything except setup.
  effort_weight REAL NOT NULL DEFAULT 1.0
    CHECK (effort_weight > 0.0 AND effort_weight <= 1.0),
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE TABLE IF NOT EXISTS event_types (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE TABLE IF NOT EXISTS event_type_shift_type_allowed (
  event_type_id INTEGER NOT NULL REFERENCES event_types(id) ON DELETE CASCADE,
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  PRIMARY KEY (event_type_id, shift_type_id)
) STRICT, WITHOUT ROWID;

-- ADR-007: `strike_categories` is the category a strike is issued under
-- (social_risk, car_wash, chapter_attend, other). Distinct from
-- `pending_consequences.kind` (extra_shift, probation, expulsion_review),
-- which is the chair-owed action triggered when active-strike count crosses
-- a threshold. Same English word, two domains — keep the table names apart.
CREATE TABLE IF NOT EXISTS strike_categories (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE TABLE IF NOT EXISTS serving_methods (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE TABLE IF NOT EXISTS removal_methods (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL,
  display_name TEXT NOT NULL,
  deleted_at TEXT NULL,
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS removal_methods_slug_active
  ON removal_methods(slug) WHERE deleted_at IS NULL;

-- =========================================================================
-- Seed data. INSERT OR IGNORE so re-running migrations doesn't clobber
-- chair edits (ADR-009 config-as-data — every value here is editable).
-- =========================================================================

INSERT OR IGNORE INTO pledge_modes (slug, display_name) VALUES
  ('normal', 'Normal (brothers only)'),
  ('pledge_takeover_full', 'Pledge takeover (full)'),
  ('pledge_takeover_partial', 'Pledge takeover (partial)');

INSERT OR IGNORE INTO shift_types (slug, display_name) VALUES
  ('driver', 'Driver'),
  ('door', 'Door'),
  ('setup', 'Setup'),
  ('cleanup', 'Cleanup (next day)'),
  ('bar', 'Bar (Krush only)');

INSERT OR IGNORE INTO event_types (slug, display_name) VALUES
  ('mixer', 'Mixer'),
  ('krush', 'Krush'),
  ('other_party', 'Other party'),
  ('philanthropy', 'Philanthropy');

-- Bar is Krush-and-up only (canonical plan §2.1 + risk #M9).
INSERT OR IGNORE INTO event_type_shift_type_allowed (event_type_id, shift_type_id)
SELECT et.id, st.id FROM event_types et, shift_types st
WHERE
  (et.slug = 'mixer'        AND st.slug IN ('driver','door','setup','cleanup'))
  OR
  (et.slug = 'krush'        AND st.slug IN ('driver','door','setup','cleanup','bar'))
  OR
  (et.slug = 'other_party'  AND st.slug IN ('driver','door','setup','cleanup','bar'))
  OR
  (et.slug = 'philanthropy' AND st.slug IN ('setup','cleanup'));

INSERT OR IGNORE INTO strike_categories (slug, display_name) VALUES
  ('social_risk', 'Social risk shift'),
  ('car_wash', 'Car wash'),
  ('chapter_attend', 'Chapter attendance'),
  ('other', 'Other');

INSERT OR IGNORE INTO serving_methods (slug, display_name) VALUES
  ('shift', 'Worked a shift'),
  ('event_attendance', 'Attended event'),
  ('chapter_attendance', 'Attended chapter'),
  ('car_wash', 'Car wash'),
  ('other', 'Other');

-- Removal methods: seeded once per slug. The partial unique index allows
-- a retired slug to be re-added; the seed must not re-insert if the slug
-- already exists in any state (active OR retired) — chair edits win.
INSERT INTO removal_methods (slug, display_name)
SELECT 'leadership_conf', 'Leadership conference (-2)'
WHERE NOT EXISTS (SELECT 1 FROM removal_methods WHERE slug = 'leadership_conf');

INSERT INTO removal_methods (slug, display_name)
SELECT 'donation', 'Donation ($50/strike, -1)'
WHERE NOT EXISTS (SELECT 1 FROM removal_methods WHERE slug = 'donation');

INSERT INTO removal_methods (slug, display_name)
SELECT 'ec_event', 'EC-decided event (varies)'
WHERE NOT EXISTS (SELECT 1 FROM removal_methods WHERE slug = 'ec_event');

INSERT INTO removal_methods (slug, display_name)
SELECT 'voluntary_social_risk', 'Voluntary social risk (-1)'
WHERE NOT EXISTS (SELECT 1 FROM removal_methods WHERE slug = 'voluntary_social_risk');
