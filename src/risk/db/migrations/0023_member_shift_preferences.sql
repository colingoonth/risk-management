-- Per-member steers: "not rides at all", "door on a Tuesday, not a Friday",
-- "never a night post at a Krush".
--
-- The chapter tried the blanket version first and it failed publicly. Eleven
-- members on sports teams were pinned to setup and cleanup for the whole term
-- via season-long soft unavailability rows, and they were unhappy enough about
-- it that the chair reversed the whole thing on 2026-08-26 and said the steers
-- would be handled "with them individually on the biweekly schedule".
--
-- Individually is right, and this is where those live. Doing it as ad-hoc
-- unavailability rows the way the blanket steer did has two problems: an
-- unavailability row is a statement about TIME, so it cannot say "not rides"
-- without also saying "not door", which shares the same 20:00-23:59 window; and
-- it reads as "he is busy" in every surface that prints it, when the truth is
-- "he would rather not".
--
-- SOFT BY DEFAULT. A preference sorts the member behind everyone else for that
-- shift, exactly like soft unavailability and the senior night cap, so he is
-- taken only when the post would otherwise stand empty. That is the honest
-- reading of "try to avoid" and it can never leave a slot unstaffed. is_hard is
-- there for the ones that are not negotiable.
--
-- THREE OPTIONAL NARROWINGS, all nullable, all ANDed:
--   weekday        NULL = any day        (Python's Monday=0, matching
--                                         unavailability.repeats_weekday)
--   event_type_id  NULL = any event type
-- so one row says "no rides ever", another says "no door on a Friday", and a
-- third says "no door at a Krush". Expressing all three as separate rows rather
-- than one clever row keeps each one individually removable when the member's
-- situation changes, which is the whole point of handling people individually.
CREATE TABLE IF NOT EXISTS member_shift_preferences (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  shift_type_id INTEGER NOT NULL REFERENCES shift_types(id),
  weekday INTEGER NULL,
  event_type_id INTEGER NULL REFERENCES event_types(id),
  is_hard INTEGER NOT NULL DEFAULT 0 CHECK (is_hard IN (0, 1)),
  reason TEXT NULL,
  CHECK (weekday IS NULL OR weekday BETWEEN 0 AND 6)
) STRICT;

CREATE UNIQUE INDEX IF NOT EXISTS member_shift_preferences_unique
  ON member_shift_preferences(
    member_id, semester_id, shift_type_id,
    COALESCE(weekday, -1),
    COALESCE(event_type_id, -1)
  );

CREATE INDEX IF NOT EXISTS member_shift_preferences_lookup
  ON member_shift_preferences(semester_id, shift_type_id);
