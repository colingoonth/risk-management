-- Phase 6 follow-up: enforce uniqueness on unavailability windows.
-- Prevents duplicate rows when the same member/semester/date range is added twice.
-- Option A from ADR: schema-enforced uniqueness is stronger than a pre-insert check.

CREATE UNIQUE INDEX IF NOT EXISTS unavailability_member_semester_range
  ON unavailability(member_id, semester_id, starts_on, ends_on);
