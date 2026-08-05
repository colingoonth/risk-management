-- Phase 7: time-aware assignment.
--
-- Three related changes, all additive except the unavailability rebuild:
--   1. shift_type_windows  — WHEN a shift type is worked, relative to the
--      event date. Drives both availability checking and the "can this member
--      hold two shifts at one event" rule.
--   2. qualifications      — gates which members may fill which shift types
--      (dj for the DJ slot). Replaces the earlier idea of modelling DJ as
--      a soft-excluded role.
--   3. unavailability v2   — time-of-day + weekly recurrence, so the Google
--      Form can capture "busy Thursdays 6-11pm" and "busy Oct 3, 2-6pm".
--
-- All STRICT, matching the rest of the schema.

-- =========================================================================
-- 1. DJ shift type + the FA26 party types (driver/door/setup/cleanup/bar and
--    mixer/krush/other_party/philanthropy already seeded in 0002).
--    Juice is deliberately NOT modelled — the chair does not track it.
--
--    A new event type is useless without an allow-list and default slot
--    counts: `event_shift_requirements` merges per-event override > per-house
--    preference > event-type default, so a type with no defaults row yields an
--    event with ZERO slots and auto-assign silently does nothing.
--    Allow-list seed mirrors 0002; defaults seed mirrors 0004 (CROSS JOIN
--    gated on event_type_shift_type_allowed). All INSERT OR IGNORE so chair
--    edits via `risk config event-type set-default` survive re-migration.
-- =========================================================================

INSERT INTO shift_types (slug, display_name)
SELECT 'dj', 'DJ'
WHERE NOT EXISTS (SELECT 1 FROM shift_types WHERE slug = 'dj');

-- Party types the chair actually schedules. mixer/krush already exist.
INSERT INTO event_types (slug, display_name)
SELECT v.slug, v.display_name
FROM (
            SELECT 'dage' AS slug, 'Dage'       AS display_name
  UNION ALL SELECT 'quad',         'Quad'
  UNION ALL SELECT 'open',         'Open'
  UNION ALL SELECT 'rush',         'Rush event'
) AS v
WHERE NOT EXISTS (SELECT 1 FROM event_types et WHERE et.slug = v.slug);

-- Allow-lists. Bar stays Krush-and-up (risk #M9): mixer and rush must NOT
-- gain it. DJ goes to the six FA26 party types only — mixer, krush, rush,
-- dage, quad, open. philanthropy has no party to DJ, and other_party is the
-- legacy catch-all rather than a type the chair actually schedules, so
-- neither is listed below.
INSERT OR IGNORE INTO event_type_shift_type_allowed (event_type_id, shift_type_id)
SELECT et.id, st.id FROM event_types et, shift_types st
WHERE
  (et.slug IN ('mixer', 'krush')  AND st.slug = 'dj')
  OR
  (et.slug = 'rush'               AND st.slug IN ('driver','door','setup','cleanup','dj'))
  OR
  (et.slug IN ('dage','quad','open')
                                  AND st.slug IN ('driver','door','bar','setup','cleanup','dj'));

-- Default slot counts for the new types. Two profiles:
--   mixer profile — 2 driver, 2 door, 0 bar, 4 setup, 4 cleanup, 1 dj (12 counted)
--   big profile   — 3 driver, 2 door, 2 bar, 4 setup, 4 cleanup, 1 dj (15 counted)
-- Rush is run as a mixer for now. DJ does not count toward the shift tally,
-- but it is still a slot that must be filled.
--
-- min = target here: these are the slots the chair actually staffs, and the
-- counted totals above depend on the target. (mixer/krush keep the min<target
-- spread 0004 gave them — OR IGNORE leaves those rows alone.)
--
-- The join on event_type_shift_type_allowed is the gate, exactly as in 0004:
-- a combination the chair has removed from the allow-list never regains a
-- default, and rush can never pick up a bar row.
INSERT OR IGNORE INTO event_type_shift_defaults (event_type_id, shift_type_id, min_count, target_count)
SELECT et.id, st.id, p.n, p.n
FROM (
            SELECT 'mixer_profile' AS profile, 'driver'  AS st_slug, 2 AS n
  UNION ALL SELECT 'mixer_profile',            'door',    2
  UNION ALL SELECT 'mixer_profile',            'setup',   4
  UNION ALL SELECT 'mixer_profile',            'cleanup', 4
  UNION ALL SELECT 'mixer_profile',            'dj',      1
  UNION ALL SELECT 'big_profile',              'driver',  3
  UNION ALL SELECT 'big_profile',              'door',    2
  UNION ALL SELECT 'big_profile',              'bar',     2
  UNION ALL SELECT 'big_profile',              'setup',   4
  UNION ALL SELECT 'big_profile',              'cleanup', 4
  UNION ALL SELECT 'big_profile',              'dj',      1
) AS p
JOIN (
            SELECT 'rush' AS et_slug, 'mixer_profile' AS profile
  UNION ALL SELECT 'dage',            'big_profile'
  UNION ALL SELECT 'quad',            'big_profile'
  UNION ALL SELECT 'open',            'big_profile'
) AS m ON m.profile = p.profile
JOIN event_types et ON et.slug = m.et_slug
JOIN shift_types st ON st.slug = p.st_slug
JOIN event_type_shift_type_allowed allowed
  ON allowed.event_type_id = et.id AND allowed.shift_type_id = st.id;

-- mixer and krush were seeded in 0004, before `dj` existed as a shift type.
INSERT OR IGNORE INTO event_type_shift_defaults (event_type_id, shift_type_id, min_count, target_count)
SELECT et.id, st.id, 1, 1
FROM event_types et
JOIN shift_types st ON st.slug = 'dj'
JOIN event_type_shift_type_allowed allowed
  ON allowed.event_type_id = et.id AND allowed.shift_type_id = st.id
WHERE et.slug IN ('mixer', 'krush');

-- =========================================================================
-- 2. Shift-type windows.
--
-- offset_days_* are relative to the event date: 0 = day of, -2 = two days
-- before, +1 = the following morning. The window is the span of wall-clock
-- time on those days during which the member must be free.
--
-- min_contiguous_minutes: NULL means "free for the whole window". Setup needs
-- only a 2-hour block somewhere inside a 3-day span, so it sets 120.
--
-- occupies_event_night is deliberately a FLAG rather than something inferred
-- from the times. Setup's window is ~72 hours wide and therefore numerically
-- overlaps the party, but doing setup at 2pm Thursday does not stop you
-- working door at 10pm Friday. Overlap arithmetic alone would wrongly block
-- that pairing. The flag records the real constraint: this shift pins you to
-- the party itself, so you cannot hold two of them at one event.
--
-- requires_group_overlap: setup and cleanup are done by the whole crew at
-- once, so the solver must find a window where ALL assignees are free
-- simultaneously — not merely each of them individually at some point.
-- =========================================================================

CREATE TABLE IF NOT EXISTS shift_type_windows (
  shift_type_id           INTEGER PRIMARY KEY REFERENCES shift_types(id),
  offset_days_start       INTEGER NOT NULL,
  offset_days_end         INTEGER NOT NULL,
  window_start_time       TEXT NOT NULL,
  window_end_time         TEXT NOT NULL,
  min_contiguous_minutes  INTEGER NULL,
  occupies_event_night    INTEGER NOT NULL DEFAULT 0,
  requires_group_overlap  INTEGER NOT NULL DEFAULT 0,
  CHECK (offset_days_end >= offset_days_start),
  CHECK (window_start_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  CHECK (window_end_time   GLOB '[0-2][0-9]:[0-5][0-9]'),
  CHECK (min_contiguous_minutes IS NULL OR min_contiguous_minutes > 0),
  CHECK (occupies_event_night   IN (0, 1)),
  CHECK (requires_group_overlap IN (0, 1))
) STRICT;

INSERT OR IGNORE INTO shift_type_windows (
  shift_type_id, offset_days_start, offset_days_end,
  window_start_time, window_end_time,
  min_contiguous_minutes, occupies_event_night, requires_group_overlap
)
SELECT st.id, v.o_start, v.o_end, v.w_start, v.w_end, v.min_min, v.night, v.grp
FROM (
  -- rides: at the house by 8pm, there for the night
            SELECT 'driver' AS slug, 0 AS o_start, 0 AS o_end,
                   '20:00' AS w_start, '23:59' AS w_end,
                   NULL AS min_min, 1 AS night, 0 AS grp
  -- door: same as rides. Lateness is tolerated occasionally in practice,
  -- but the schedule is planned as though everyone is there at 8pm.
  UNION ALL SELECT 'door',    0,  0, '20:00', '23:59', NULL, 1, 0
  -- bar: event night, on the parties that actually have a bar. No age gate —
  -- see the qualifications section below for why.
  UNION ALL SELECT 'bar',     0,  0, '20:00', '23:59', NULL, 1, 0
  -- dj: event night. Blocks door/rides/bar, but NOT setup or cleanup.
  UNION ALL SELECT 'dj',      0,  0, '20:00', '23:59', NULL, 1, 0
  -- setup: a 2-hour block any time in the 2 days before, or the day of
  UNION ALL SELECT 'setup',  -2,  0, '08:00', '23:59',  120, 0, 1
  -- cleanup: the next morning, free until noon
  UNION ALL SELECT 'cleanup', 1,  1, '00:00', '12:00', NULL, 0, 1
) AS v
JOIN shift_types st ON st.slug = v.slug;

-- =========================================================================
-- 3. Qualifications.
-- =========================================================================

CREATE TABLE IF NOT EXISTS qualifications (
  id           INTEGER PRIMARY KEY,
  slug         TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL
) STRICT;

-- `dj` GATES a shift type. `over-21` does NOT gate anything — it is retained as
-- informational data only (41 members carry it from the FA26 roster load).
-- Working the bar has no age requirement: the 21+ list is about who PURCHASES
-- alcohol, which belongs to the juice task the chair does not track. Do not
-- re-add a bar row to shift_type_required_qualification below.
INSERT OR IGNORE INTO qualifications (slug, display_name) VALUES
  ('over-21', 'Over 21'),
  ('dj',      'Qualified DJ');

-- Per-semester so a member turning 21 mid-year, or handing off the DJ job,
-- does not rewrite history.
CREATE TABLE IF NOT EXISTS member_qualifications (
  member_id        INTEGER NOT NULL REFERENCES members(id),
  qualification_id INTEGER NOT NULL REFERENCES qualifications(id),
  semester_id      INTEGER NOT NULL REFERENCES semesters(id),
  PRIMARY KEY (member_id, qualification_id, semester_id)
) STRICT;

CREATE INDEX IF NOT EXISTS member_qualifications_lookup
  ON member_qualifications(semester_id, qualification_id, member_id);

CREATE TABLE IF NOT EXISTS shift_type_required_qualification (
  shift_type_id    INTEGER NOT NULL REFERENCES shift_types(id),
  qualification_id INTEGER NOT NULL REFERENCES qualifications(id),
  PRIMARY KEY (shift_type_id, qualification_id)
) STRICT;

-- DJ is the ONLY gated shift type: two people in the chapter can actually DJ.
--
-- `bar` was gated on `over-21` here originally. That constraint was invented,
-- not asked for: the 21+ list tracks who can PURCHASE alcohol (the juice task,
-- which is deliberately not modelled), and has nothing to do with standing the
-- bar shift. Gating it cut the bar pool from 57 to 41 for no reason. Removed.
--
-- Note for anyone reading an older database: this seed cannot retract the row
-- it used to insert, because INSERT OR IGNORE only adds. Migration 0014 carries
-- the DELETE.
INSERT OR IGNORE INTO shift_type_required_qualification (shift_type_id, qualification_id)
SELECT st.id, q.id
FROM (SELECT 'dj' AS st_slug, 'dj' AS q_slug) AS v
JOIN shift_types st   ON st.slug = v.st_slug
JOIN qualifications q ON q.slug  = v.q_slug;

-- =========================================================================
-- 4. Unavailability v2 — time-of-day + weekly recurrence.
--
-- The three new columns are declared in 0009's CREATE TABLE (fresh DBs) and
-- back-filled onto existing DBs by ``_ensure_columns`` in risk/db/schema.py,
-- per the pattern documented there. Nothing to add here.
--
-- What DOES belong here: 0011's uniqueness index only covered
-- (member, semester, starts_on, ends_on), which would now wrongly reject a
-- second window on a day a member is already partly busy — e.g. "busy 09:00
-- to 11:00" plus "busy 18:00 to 22:00" on the same date. Swap it for one that
-- includes the time and recurrence columns.
--
-- DROP INDEX IF EXISTS + CREATE ... IF NOT EXISTS is safe to re-run, which
-- matters because ensure_schema replays every migration on every connect.
-- =========================================================================

DROP INDEX IF EXISTS unavailability_member_semester_range;

CREATE UNIQUE INDEX IF NOT EXISTS unavailability_member_semester_window
  ON unavailability(
    member_id, semester_id, starts_on, ends_on,
    COALESCE(starts_at_time, ''),
    COALESCE(ends_at_time, ''),
    COALESCE(repeats_weekday, -1)
  );
