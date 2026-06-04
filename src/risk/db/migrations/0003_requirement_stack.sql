-- Phase 1: three-layer shift requirement stack + history triggers.
-- See canonical plan §2.3 and §2.7.

CREATE TABLE IF NOT EXISTS event_type_shift_defaults (
  event_type_id INTEGER NOT NULL REFERENCES event_types(id) ON DELETE CASCADE,
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  min_count INTEGER NOT NULL CHECK (min_count >= 0 AND min_count <= 50),
  target_count INTEGER NOT NULL CHECK (target_count >= 0 AND target_count <= 50),
  CHECK (target_count >= min_count),
  PRIMARY KEY (event_type_id, shift_type_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS house_shift_preferences (
  house_id INTEGER NOT NULL REFERENCES houses(id) ON DELETE CASCADE,
  event_type_id INTEGER NOT NULL REFERENCES event_types(id) ON DELETE CASCADE,
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  min_count INTEGER NOT NULL CHECK (min_count >= 0 AND min_count <= 50),
  target_count INTEGER NOT NULL CHECK (target_count >= 0 AND target_count <= 50),
  CHECK (target_count >= min_count),
  PRIMARY KEY (house_id, event_type_id, shift_type_id)
) STRICT, WITHOUT ROWID;

CREATE TABLE IF NOT EXISTS house_shift_preferences_history (
  id INTEGER PRIMARY KEY,
  house_id INTEGER NOT NULL,
  event_type_id INTEGER NOT NULL,
  shift_type_id INTEGER NOT NULL,
  min_count INTEGER NULL,
  target_count INTEGER NULL,
  changed_at TEXT NOT NULL DEFAULT (datetime('now')),
  change_kind TEXT NOT NULL CHECK (change_kind IN ('insert', 'update', 'delete'))
) STRICT;

CREATE INDEX IF NOT EXISTS hsp_history_lookup
  ON house_shift_preferences_history(house_id, event_type_id, shift_type_id, changed_at DESC);

-- =========================================================================
-- Audit triggers per canonical plan §2.7.
-- =========================================================================

CREATE TRIGGER IF NOT EXISTS trg_hsp_history_insert
AFTER INSERT ON house_shift_preferences
BEGIN
  INSERT INTO house_shift_preferences_history
    (house_id, event_type_id, shift_type_id, min_count, target_count, change_kind)
  VALUES (NEW.house_id, NEW.event_type_id, NEW.shift_type_id,
          NEW.min_count, NEW.target_count, 'insert');
END;

CREATE TRIGGER IF NOT EXISTS trg_hsp_history_update
AFTER UPDATE ON house_shift_preferences
BEGIN
  INSERT INTO house_shift_preferences_history
    (house_id, event_type_id, shift_type_id, min_count, target_count, change_kind)
  VALUES (NEW.house_id, NEW.event_type_id, NEW.shift_type_id,
          NEW.min_count, NEW.target_count, 'update');
END;

CREATE TRIGGER IF NOT EXISTS trg_hsp_history_delete
AFTER DELETE ON house_shift_preferences
BEGIN
  INSERT INTO house_shift_preferences_history
    (house_id, event_type_id, shift_type_id, min_count, target_count, change_kind)
  VALUES (OLD.house_id, OLD.event_type_id, OLD.shift_type_id,
          NULL, NULL, 'delete');
END;
