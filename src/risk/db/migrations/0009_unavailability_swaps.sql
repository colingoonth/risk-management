-- Phase 6: unavailability windows + swap requests.
-- Schema per canonical plan §2.5. All STRICT.

-- starts_at_time/ends_at_time added in Phase 7 (both NULL = all day, which is
-- what every pre-Phase-7 row means). repeats_weekday NULL = a one-off range;
-- 0-6 (Mon-Sun) = every such weekday between starts_on and ends_on.
-- Declared here so fresh DBs get the CHECKs; legacy DBs are back-filled by
-- ``_ensure_columns`` (see risk/db/schema.py).
CREATE TABLE IF NOT EXISTS unavailability (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id),
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  starts_on TEXT NOT NULL,
  ends_on TEXT NOT NULL,
  starts_at_time TEXT NULL,
  ends_at_time TEXT NULL,
  repeats_weekday INTEGER NULL,
  -- A PREFERENCE rather than a conflict. 0 means the member genuinely cannot
  -- work (a tournament, a flight); 1 means they would rather not, and should be
  -- picked only if the slot would otherwise go unfilled.
  --
  -- Added in 0019 for a cross-country runner's "I'd rather not be on party risk 2 days
  -- before a meet, but setup and cleanup is chill" — which is a real constraint
  -- worth honouring and a terrible one to treat as absolute, since it covers 9
  -- of 43 parties for one runner and would compound the moment anyone else
  -- asked for the same.
  is_soft INTEGER NOT NULL DEFAULT 0 CHECK (is_soft IN (0, 1)),
  reason TEXT NULL,
  CHECK (ends_on >= starts_on),
  CHECK (starts_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  CHECK (ends_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  CHECK (starts_at_time IS NULL OR starts_at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  CHECK (ends_at_time IS NULL OR ends_at_time GLOB '[0-2][0-9]:[0-5][0-9]'),
  -- both times or neither; a half-specified window is always a data bug
  CHECK ((starts_at_time IS NULL) = (ends_at_time IS NULL)),
  CHECK (starts_at_time IS NULL OR ends_at_time > starts_at_time),
  CHECK (repeats_weekday IS NULL OR repeats_weekday BETWEEN 0 AND 6)
) STRICT;

CREATE INDEX IF NOT EXISTS unavailability_member_semester
  ON unavailability(member_id, semester_id, starts_on, ends_on);

CREATE INDEX IF NOT EXISTS unavailability_semester_date
  ON unavailability(semester_id, starts_on, ends_on);

CREATE TABLE IF NOT EXISTS swap_requests (
  id INTEGER PRIMARY KEY,
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  from_shift_id INTEGER NOT NULL REFERENCES shifts(id),
  to_shift_id INTEGER NULL REFERENCES shifts(id),
  initiator_member_id INTEGER NOT NULL REFERENCES members(id),
  counterparty_member_id INTEGER NULL REFERENCES members(id),
  state TEXT NOT NULL DEFAULT 'open'
    CHECK (state IN ('open', 'accepted', 'rejected', 'cancelled')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT NULL
) STRICT;

CREATE INDEX IF NOT EXISTS swap_requests_open
  ON swap_requests(semester_id, state)
  WHERE state = 'open';

CREATE INDEX IF NOT EXISTS swap_requests_from_shift
  ON swap_requests(from_shift_id);

-- =========================================================================
-- Archive guards (defense-in-depth per ADR-008).
-- =========================================================================

CREATE TRIGGER IF NOT EXISTS trg_unavailability_block_archived_insert
BEFORE INSERT ON unavailability
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_unavailability_block_archived_update
BEFORE UPDATE ON unavailability
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = OLD.semester_id) IS NOT NULL
     OR (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_unavailability_block_archived_delete
BEFORE DELETE ON unavailability
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = OLD.semester_id) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_swap_requests_block_archived_insert
BEFORE INSERT ON swap_requests
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_swap_requests_block_archived_update
BEFORE UPDATE ON swap_requests
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = OLD.semester_id) IS NOT NULL
     OR (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;
