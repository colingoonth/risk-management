-- Phase 4: shifts + auto_assign_runs.
-- Schema per canonical plan §2.4. All STRICT.

CREATE TABLE IF NOT EXISTS shifts (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  slot_index INTEGER NOT NULL,
  assigned_member_id INTEGER NULL REFERENCES members(id),
  effective_pledge_mode_id INTEGER NULL REFERENCES pledge_modes(id),
  -- 'swapped' is reserved per amendment R3.2-D — see plan-r3-canonical.md.
  -- Shape B swaps (reassign-to-open at same event,shift_type) cannot use it
  -- because the shifts_one_assignment partial unique index forbids the
  -- terminal-marker pattern. Audit fact lives on swap_requests instead.
  -- The value is kept in the CHECK so a future write path (e.g. swap_history
  -- view, or shape-D row-level marker) can claim it without a table rebuild.
  status TEXT NOT NULL DEFAULT 'open'
    CHECK (status IN ('open', 'assigned', 'completed', 'no_show', 'swapped')),
  assigned_at TEXT NULL,
  -- The strike this shift WORKS OFF, if any. Added in 0017; see that file for
  -- why this is not `strikes.shift_id`, which records the opposite fact (the
  -- shift a member no-showed).
  --
  -- Declared without a REFERENCES clause on purpose. `strikes` is created in
  -- 0008, one file later, and more importantly ALTER TABLE — which is how
  -- databases that predate this column acquire it — cannot attach a foreign key
  -- at all. Declaring one here would leave a fresh database with a constraint
  -- an existing database does not have, and schema._ensure_columns is built on
  -- the two staying identical. The guarantee that actually matters, one make-up
  -- per strike, is the partial unique index in 0017 and applies to both.
  serves_strike_id INTEGER NULL,
  -- 1 when a human put this member here, rather than the fill. A rebuild must
  -- leave it alone: the chair knows things the solver does not, and cannot
  -- re-derive them. Added in 0021.
  chair_set INTEGER NOT NULL DEFAULT 0 CHECK (chair_set IN (0, 1)),
  CHECK (slot_index >= 0 AND slot_index < 50),
  CHECK ((assigned_member_id IS NULL AND status = 'open')
         OR (assigned_member_id IS NOT NULL AND status <> 'open')),
  UNIQUE (event_id, shift_type_id, slot_index)
) STRICT;

-- Partial unique index — a member may be assigned to at most one shift per
-- (event, shift_type) pair. NULL is excluded so multiple open slots coexist.
CREATE UNIQUE INDEX IF NOT EXISTS shifts_one_assignment
  ON shifts(event_id, shift_type_id, assigned_member_id)
  WHERE assigned_member_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS shifts_by_event ON shifts(event_id);
CREATE INDEX IF NOT EXISTS shifts_by_member
  ON shifts(assigned_member_id, assigned_at)
  WHERE assigned_member_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS auto_assign_runs (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL REFERENCES events(id),
  run_at TEXT NOT NULL DEFAULT (datetime('now')),
  resolved_mode_id INTEGER NOT NULL REFERENCES pledge_modes(id),
  eligible_pledges_at_run INTEGER NOT NULL,
  eligible_brothers_at_run INTEGER NOT NULL,
  seed INTEGER NOT NULL,
  payload_json TEXT
) STRICT;

CREATE INDEX IF NOT EXISTS auto_assign_runs_by_event
  ON auto_assign_runs(event_id, run_at DESC);
