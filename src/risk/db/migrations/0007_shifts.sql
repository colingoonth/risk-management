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
