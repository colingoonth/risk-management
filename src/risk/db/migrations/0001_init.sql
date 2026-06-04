-- Phase 0 schema: semesters only. Subsequent phases add the rest per the
-- canonical plan (Projects/risk-management/plan-r3-canonical, section 2).

CREATE TABLE IF NOT EXISTS semesters (
  id INTEGER PRIMARY KEY,
  name TEXT NOT NULL UNIQUE,
  starts_on TEXT NOT NULL,
  ends_on TEXT NOT NULL,
  pledge_takeover_starts_on TEXT NULL,
  is_current INTEGER NOT NULL DEFAULT 0 CHECK (is_current IN (0, 1)),
  archived_at TEXT NULL,
  CHECK (ends_on >= starts_on),
  CHECK (pledge_takeover_starts_on IS NULL
         OR (pledge_takeover_starts_on >= starts_on
             AND pledge_takeover_starts_on <= ends_on))
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS one_current_semester
  ON semesters(is_current) WHERE is_current = 1;
