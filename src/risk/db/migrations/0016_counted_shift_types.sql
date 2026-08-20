-- FA26 scheduling: not every shift is a risk shift.
--
-- The fairness tally counts rows in `shifts`, full stop. That was right while
-- every shift type was a sober-monitor post. It stopped being right when `dj`
-- was added in 0012, because DJing is a different job: the chapter does not
-- consider it risk work, and HANDOFF is explicit that "DJ does NOT count toward
-- the shift tally".
--
-- Leaving it counted produced a visible absurdity in the FA26 fill. Two members
-- hold the dj qualification. Between them they took all 43 DJ slots, each one
-- inflating their tally, so by roughly the sixth party both sat at the bottom
-- of every pool and were never picked for anything else again. The chapter's
-- own ledger ended up reporting the two hardest-worked brothers in the chapter
-- — 22 and 21 shifts against a chapter average near 9 — as having stood zero
-- risk shifts between them. Both halves of that reading are wrong.
--
-- Modelled as a column rather than a `slug <> 'dj'` test inside the count
-- query, for the same reason removal_methods and strike_categories are lookup
-- tables: this is chapter policy, and chapter policy belongs in data the chair
-- can read, not spelled into a WHERE clause where changing it needs a
-- developer. The column is declared on the CREATE TABLE in 0002 and back-filled
-- onto existing databases by schema._ensure_columns, so there is no ALTER here
-- to fail on the second connect.

-- A tombstone for "the chair decided; do not re-seed me".
--
-- Created BEFORE the UPDATE below, which references it — executescript runs
-- statements in file order, and on a fresh database the table would not exist
-- yet otherwise.
--
-- It is needed because guarding the seed on `counts_toward_tally = 1` is not
-- sufficient by itself. A chair who decides DJ should count after all sets the
-- value back to 1 — which is precisely the state the guard fires on, so the
-- next connect would flip it to 0 again and their change would look like it had
-- never happened. One row here makes the decision stick, while leaving the
-- INSERT OR IGNORE doctrine intact for every other seeded value. This is the
-- shape item C of the overnight report recommends for seeded config in general;
-- it lands here because this is the first seeded value a chair has a concrete
-- reason to overturn.
CREATE TABLE IF NOT EXISTS shift_type_tally_overrides (
  shift_type_id INTEGER PRIMARY KEY REFERENCES shift_types(id) ON DELETE CASCADE,
  set_at TEXT NOT NULL DEFAULT (datetime('now')),
  note TEXT NULL
) STRICT;

-- DJ is the one uncounted type today.
UPDATE shift_types
SET counts_toward_tally = 0
WHERE slug = 'dj'
  AND counts_toward_tally = 1
  AND NOT EXISTS (
        SELECT 1 FROM shift_type_tally_overrides o
        WHERE o.shift_type_id = shift_types.id
      );
