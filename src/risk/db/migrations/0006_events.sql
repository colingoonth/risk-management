-- Phase 3: events + 3-layer requirement snapshot + audit table + host-change trigger.
-- Schema per canonical plan §2.4. ADR-010: source_layer lives in writes, not state.

CREATE TABLE IF NOT EXISTS events (
  id INTEGER PRIMARY KEY,
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  event_type_id INTEGER NOT NULL REFERENCES event_types(id),
  host_house_id INTEGER NULL REFERENCES houses(id),
  display_name TEXT NOT NULL,
  date TEXT NOT NULL,
  start_time TEXT NULL,
  end_time TEXT NULL,
  status TEXT NOT NULL DEFAULT 'created'
    CHECK (status IN ('created', 'assigned', 'completed', 'cancelled')),
  -- How real the event is, which is a DIFFERENT axis from `status` above.
  -- `status` is the lifecycle of our work on the event (created -> assigned ->
  -- completed); `planning_status` is whether the party is actually happening.
  -- A placeholder is a date the social chair is holding, fully staffed, that
  -- may never become a party. The fill order depends on the distinction —
  -- placeholders are staffed LAST so that cancelling one leaves the confirmed
  -- calendar exactly as it would have been if the placeholder never existed.
  -- Added in 0015; see that file for the back-fill and its replay hazard.
  planning_status TEXT NOT NULL DEFAULT 'confirmed'
    CHECK (planning_status IN ('confirmed', 'potential', 'placeholder')),
  resync_pending INTEGER NOT NULL DEFAULT 0 CHECK (resync_pending IN (0, 1)),
  notes TEXT NULL,
  UNIQUE (semester_id, display_name),
  CHECK (
    (start_time IS NULL AND end_time IS NULL)
    OR (start_time IS NOT NULL AND end_time IS NOT NULL)
  )
) STRICT;

CREATE INDEX IF NOT EXISTS events_by_date ON events(date);
CREATE INDEX IF NOT EXISTS events_by_semester ON events(semester_id, date);

CREATE TABLE IF NOT EXISTS event_shift_requirements (
  event_id INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  min_count INTEGER NOT NULL CHECK (min_count >= 0 AND min_count <= 50),
  target_count INTEGER NOT NULL CHECK (target_count >= 0 AND target_count <= 50),
  CHECK (target_count >= min_count),
  PRIMARY KEY (event_id, shift_type_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS event_shift_requirement_writes (
  id INTEGER PRIMARY KEY,
  event_id INTEGER NOT NULL,
  shift_type_id INTEGER NOT NULL,
  written_at TEXT NOT NULL DEFAULT (datetime('now')),
  source_layer TEXT NOT NULL
    CHECK (source_layer IN ('event_type_default', 'house_preference', 'manual_override', 'resync')),
  min_count INTEGER NOT NULL,
  target_count INTEGER NOT NULL,
  FOREIGN KEY (event_id, shift_type_id)
    REFERENCES event_shift_requirements(event_id, shift_type_id) ON DELETE CASCADE
) STRICT;

CREATE INDEX IF NOT EXISTS esrw_lookup
  ON event_shift_requirement_writes(event_id, shift_type_id, written_at DESC);

-- =========================================================================
-- Host-change trigger: changing events.host_house_id flips resync_pending.
-- Chair runs `risk event resync-shift-reqs <event>` to pull latest prefs.
-- =========================================================================

CREATE TRIGGER IF NOT EXISTS trg_events_host_change_resync
AFTER UPDATE OF host_house_id ON events
WHEN
  (NEW.host_house_id IS NOT OLD.host_house_id)
BEGIN
  UPDATE events SET resync_pending = 1 WHERE id = NEW.id;
END;
