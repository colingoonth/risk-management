-- Phase 5: strikes, threshold-consequences, removals.
-- Schema per canonical plan §2.6. All STRICT.
--
-- ADR-007 vocabulary note: this file deals with THRESHOLD-CONSEQUENCES
-- (`pending_consequences.kind`: extra_shift, probation, expulsion_review).
-- Issuance reasons live in the `strike_categories` lookup (Phase 1).

CREATE TABLE IF NOT EXISTS strikes (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id),
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  shift_id INTEGER NULL REFERENCES shifts(id),
  issued_on TEXT NOT NULL,
  reason TEXT NOT NULL,
  carried_from_strike_id INTEGER NULL REFERENCES strikes(id),
  closed_at TEXT NULL,
  CHECK (issued_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]'),
  CHECK (closed_at IS NULL OR length(closed_at) >= 10)
) STRICT;

CREATE INDEX IF NOT EXISTS strikes_member_semester
  ON strikes(member_id, semester_id, issued_on, id);

CREATE INDEX IF NOT EXISTS strikes_open
  ON strikes(member_id, semester_id)
  WHERE closed_at IS NULL;

-- ADR-006: strike numbers are computed, never stored. The view re-derives
-- on every read; only OPEN strikes (closed_at IS NULL) count.
CREATE VIEW IF NOT EXISTS v_strike_numbers AS
  SELECT
    id,
    member_id,
    semester_id,
    ROW_NUMBER() OVER (
      PARTITION BY member_id, semester_id
      ORDER BY issued_on, id
    ) AS strike_number,
    issued_on,
    reason
  FROM strikes
  WHERE closed_at IS NULL;

CREATE TABLE IF NOT EXISTS strike_removals (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id),
  removal_method_id INTEGER NOT NULL REFERENCES removal_methods(id),
  performed_on TEXT NOT NULL,
  performed_by_member_id INTEGER NULL REFERENCES members(id),
  notes TEXT NULL,
  CHECK (performed_on GLOB '[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]')
) STRICT;

CREATE TABLE IF NOT EXISTS strike_removal_links (
  strike_removal_id INTEGER NOT NULL REFERENCES strike_removals(id) ON DELETE CASCADE,
  strike_id INTEGER NOT NULL REFERENCES strikes(id),
  PRIMARY KEY (strike_removal_id, strike_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS pending_consequences (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id),
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  triggering_strike_id INTEGER NOT NULL REFERENCES strikes(id),
  kind TEXT NOT NULL CHECK (kind IN ('extra_shift', 'probation', 'expulsion_review')),
  state TEXT NOT NULL DEFAULT 'pending'
    CHECK (state IN ('pending', 'served', 'waived', 'carried_forward')),
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_at TEXT NULL,
  -- Idempotency invariant: at most one row per (member, semester, kind).
  -- Threshold-crossing fires exactly once; re-derivation is a no-op.
  UNIQUE (member_id, semester_id, kind)
) STRICT;

CREATE INDEX IF NOT EXISTS pending_consequences_open
  ON pending_consequences(member_id, semester_id)
  WHERE state = 'pending';

-- =========================================================================
-- Triggers
-- =========================================================================

-- Monotonicity: a fresh strike cannot be back-dated before any existing
-- strike in the same (member, semester). Carry-forward strikes (linked from
-- a prior semester at archive time) are exempt because they may legitimately
-- be issued at the start of the new semester.
CREATE TRIGGER IF NOT EXISTS trg_strikes_monotonic
BEFORE INSERT ON strikes
WHEN NEW.carried_from_strike_id IS NULL
BEGIN
  SELECT RAISE(ABORT, 'strike issued_on precedes latest existing strike for member/semester')
  WHERE EXISTS (
    SELECT 1 FROM strikes
    WHERE member_id = NEW.member_id
      AND semester_id = NEW.semester_id
      AND issued_on > NEW.issued_on
  );
END;

-- Archive guards on `strikes` (defense-in-depth alongside service layer).
CREATE TRIGGER IF NOT EXISTS trg_strikes_block_archived_insert
BEFORE INSERT ON strikes
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_strikes_block_archived_update
BEFORE UPDATE ON strikes
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = OLD.semester_id) IS NOT NULL
     OR (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_strikes_block_archived_delete
BEFORE DELETE ON strikes
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = OLD.semester_id) IS NOT NULL;
END;

-- Archive guards on `pending_consequences`.
CREATE TRIGGER IF NOT EXISTS trg_pending_consequences_block_archived_insert
BEFORE INSERT ON pending_consequences
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;

CREATE TRIGGER IF NOT EXISTS trg_pending_consequences_block_archived_update
BEFORE UPDATE ON pending_consequences
BEGIN
  SELECT RAISE(ABORT, 'cannot write to archived semester')
  WHERE (SELECT archived_at FROM semesters WHERE id = OLD.semester_id) IS NOT NULL
     OR (SELECT archived_at FROM semesters WHERE id = NEW.semester_id) IS NOT NULL;
END;
