-- FA26 scheduling: record which shift WORKS OFF a strike.
--
-- The chapter's rule is one unserved strike, one make-up shift. Twenty-one
-- active members carried one into FA26 from the spring strike sheet, and Colin's
-- instruction is that those shifts go on the first confirmed parties and do NOT
-- count toward the member's season total — the whole point of a penalty is that
-- it is work on top of a normal season, not instead of part of one.
--
-- WHY NOT `strikes.shift_id`, WHICH LOOKS LIKE EXACTLY THIS COLUMN.
-- It points the other way. `strikes.shift_id` is the shift the member NO-SHOWED
-- — the cause of the strike, not its remedy. strike_state.issue_strike leans on
-- that reading: it refuses a second open strike against the same
-- (member, shift) pair, on the grounds that you cannot miss one shift twice.
-- Storing a make-up there would make a served strike look like a missed shift,
-- and would then block issuing a real no-show strike for the very party the
-- member turned up to. Two opposite facts, two columns.
--
-- Cardinality runs one-to-one in both directions and the constraints say so: a
-- shift serves at most one strike (it is a single column), and a strike is
-- served by at most one shift (the partial unique index below). Working one
-- party does not clear two strikes.
--
-- The column is declared here rather than on 0007's CREATE TABLE because
-- `shifts` predates `strikes` in migration order, so a REFERENCES on the
-- original CREATE would point at a table that does not exist yet on a fresh
-- database. schema._ensure_columns back-fills it for every database, fresh or
-- otherwise, which is why there is no ALTER in this file to fail on the second
-- connect.

-- One shift per strike. Partial, because NULL is the overwhelmingly common case
-- — 543 of 543 rotation shifts serve no strike — and a full unique index would
-- treat every one of those NULLs as distinct anyway, which works but indexes the
-- entire table to no purpose.
CREATE UNIQUE INDEX IF NOT EXISTS shifts_one_makeup_per_strike
  ON shifts(serves_strike_id)
  WHERE serves_strike_id IS NOT NULL;

CREATE INDEX IF NOT EXISTS shifts_makeups
  ON shifts(serves_strike_id)
  WHERE serves_strike_id IS NOT NULL;
