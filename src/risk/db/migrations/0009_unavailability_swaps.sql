-- Phase 6: unavailability windows + swap requests.
-- Schema per canonical plan §2.5. All STRICT.

CREATE TABLE IF NOT EXISTS unavailability (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id),
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  starts_on TEXT NOT NULL,
  ends_on TEXT NOT NULL,
  reason TEXT NULL,
  CHECK (ends_on >= starts_on),
  CHECK (starts_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  CHECK (ends_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
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
