-- Somewhere for the chair to write things the schedule cannot infer.
--
-- "Nico can't do Oct 10, family thing." "Don't put Mateus on cleanup, he opens
-- the house." Facts that decide assignments and exist nowhere in this database —
-- they live in a group chat, or in Colin's head, and by November nobody
-- remembers why a crew was built the way it was.
--
-- This is the INPUT side of the loop, which is what makes it different from
-- every other table here. Everything else is either reference data or something
-- the fill computed. These rows are the chair telling the system something, and
-- the record of what was done about it.
--
-- TWO KINDS, because they behave differently and a single list would blur them:
--
--   one_off   Action it once. "Nico is out Oct 10." `closed_at` means DONE.
--   standing  Applies to every rebuild, forever, until retired. "Never put two
--             Etas on the same setup crew." `closed_at` means RETIRED, not done
--             — a standing rule that gets "completed" would silently stop
--             applying on the next fill, which is the opposite of the point.
--
-- One nullable `closed_at` rather than two columns, because the question every
-- reader asks is the same one — "does this still apply?" — and splitting it
-- into applied_at/retired_at means every query has to know the kind first.
--
-- AUTHOR exists so the traffic can run both ways. Colin writes most of these;
-- some are mine, questions he needs to answer before I can act (the pledge-class
-- size, the Blur resident list). Those belong in the same place he already looks
-- rather than in a chat log he has to scroll back through, and they need to be
-- visually distinguishable so his own list does not fill up with my questions.

CREATE TABLE IF NOT EXISTS chair_notes (
  id INTEGER PRIMARY KEY,
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  kind TEXT NOT NULL DEFAULT 'one_off'
    CHECK (kind IN ('one_off', 'standing')),
  author TEXT NOT NULL DEFAULT 'chair'
    CHECK (author IN ('chair', 'claude')),
  body TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now')),
  -- NULL while the note still applies. Done, for a one-off; retired, for a
  -- standing rule.
  closed_at TEXT NULL,
  -- What was actually done about it. The note says what was asked; this says
  -- what happened, which is the half a chair needs three months later when
  -- somebody asks why they are on a crew.
  closed_note TEXT NULL,
  CHECK (length(trim(body)) > 0),
  CHECK (closed_at IS NULL OR length(closed_at) >= 10)
) STRICT;

-- Open notes are what every surface reads first, and they are a small minority
-- of the table by the end of a term. Partial, so the index stays the size of
-- the working set rather than the archive.
CREATE INDEX IF NOT EXISTS chair_notes_open
  ON chair_notes(semester_id, kind, created_at)
  WHERE closed_at IS NULL;

CREATE INDEX IF NOT EXISTS chair_notes_by_semester
  ON chair_notes(semester_id, created_at);
