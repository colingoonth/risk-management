-- Not every shift costs the same, and counting them as if they do is what makes
-- "give them extra in exchange for setup" impossible to express.
--
-- Setup is a two-hour block the member picks, anywhere inside 08:00-23:59
-- across the three days up to and including the party. Door, rides and bar are
-- 20:00-23:59 on the night, sober, at the party, non-negotiable. Cleanup is the
-- next morning. Treating a chosen weekday afternoon as equal to a Saturday
-- night is the thing everybody already knows is untrue, which is exactly why
-- the chair was being asked to hand-adjust for it.
--
-- 0.7 is Colin's call. It means roughly three setups where two night shifts
-- would do, so a member working mostly setup lands around 16 turns against a
-- 13-turn quota. Visible as extra, not punitive.
--
-- WEIGHTED BY THE JOB, NOT BY THE PERSON. This is load-bearing. The chapter can
-- be told "setup counts 0.7 because it is two hours in daylight that you
-- choose", and it applies to whoever takes one. "These eleven get a bigger
-- quota because they play sports" is the same schedule and an indefensible
-- sentence, and it is the version that starts an argument at chapter.
--
-- Guarded on the current value being the default, and tombstoned like 0016, so
-- a chair who retunes this keeps their number across the next connect.

CREATE TABLE IF NOT EXISTS shift_type_effort_overrides (
  shift_type_id INTEGER PRIMARY KEY REFERENCES shift_types(id) ON DELETE CASCADE,
  set_at TEXT NOT NULL DEFAULT (datetime('now')),
  note TEXT NULL
) STRICT;

UPDATE shift_types
SET effort_weight = 0.7
WHERE slug = 'setup'
  AND effort_weight = 1.0
  AND NOT EXISTS (
        SELECT 1 FROM shift_type_effort_overrides o
        WHERE o.shift_type_id = shift_types.id
      );
