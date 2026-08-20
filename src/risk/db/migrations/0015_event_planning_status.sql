-- FA26 scheduling: give "is this party actually happening?" a real column.
--
-- The FA26 load recorded it as free text in `events.notes` — `status=placeholder`,
-- `status=confirmed`, `status=potential`, sometimes followed by `; ` and a
-- sentence of provenance. `events.status` is 'created' on all 43 rows and
-- carries none of it.
--
-- That mattered the moment the fill order started depending on it. Colin's rule
-- is that placeholders get staffed LAST, so that cancelling one leaves the
-- confirmed calendar byte-identical to a calendar the placeholder was never on.
-- Reading a substring out of a notes field to decide assignment order would put
-- a free-text field on the correctness path, where one edited note silently
-- changes who works which party. 0006 now declares the column; this file
-- back-fills the databases that predate it.
--
-- REPLAY HAZARD — the reason both statements are guarded on IS NULL.
-- There is no `_schema_migrations` table; every migration in this directory
-- runs on EVERY connect. An unguarded `UPDATE ... FROM notes` would re-derive
-- the value forever and pin it, so a chair promoting a placeholder to confirmed
-- would see it revert on their next command. That is exactly the trap
-- documented in 0013. Guarding on `planning_status IS NULL` makes both
-- statements fire exactly once per row, ever: the column is NOT NULL on fresh
-- databases, and on a legacy one it is NULL only until the first connect after
-- this file lands. After that these are permanent no-ops.
--
-- Legacy databases get the column from schema._ensure_columns, which uses
-- ALTER TABLE ADD COLUMN and therefore carries neither the NOT NULL nor the
-- CHECK — SQLite cannot add either that way. Same accepted limitation as the
-- unavailability v2 columns, and the same mitigation: the service layer is
-- where the value is validated on write.

-- Parse `status=<word>` out of the notes prefix. The value runs to the first
-- ';' when there is provenance text after it, and to end-of-string when there
-- is not. Anything that does not start with `status=` is left for the default
-- below rather than guessed at.
UPDATE events
SET planning_status = CASE
      WHEN instr(notes, ';') > 0 THEN substr(notes, 8, instr(notes, ';') - 8)
      ELSE substr(notes, 8)
    END
WHERE planning_status IS NULL
  AND notes LIKE 'status=%'
  AND (CASE
         WHEN instr(notes, ';') > 0 THEN substr(notes, 8, instr(notes, ';') - 8)
         ELSE substr(notes, 8)
       END) IN ('confirmed', 'potential', 'placeholder');

-- Everything else is a confirmed party. Defaulting to 'confirmed' rather than
-- 'placeholder' is the safe direction: a real party wrongly marked placeholder
-- gets staffed last and may end up short, whereas a placeholder wrongly marked
-- confirmed just means it was staffed earlier than it needed to be.
UPDATE events SET planning_status = 'confirmed' WHERE planning_status IS NULL;
