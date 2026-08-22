-- Mark a shift the chair placed by hand, so a rebuild stops throwing it away.
--
-- The fill preserves whatever already holds a slot, so a PER-EVENT re-run has
-- always kept manual assignments. A full clear-and-rebuild does not, because it
-- deletes every shift row first — and that is the operation actually used when
-- anything upstream changes: a new constraint, a roster correction, a tuned
-- weight. Every one of those quietly discarded the chair's overrides, and
-- nothing in the output said so.
--
-- That already happened once in FA26. Wednesday 26 August had a driver swapped
-- by hand; the only thing standing between that decision and the next rebuild
-- was a note reminding somebody to put it back.
--
-- An override is a DECISION, made with information the database does not have —
-- who is reliable, who is falling out with whom, who asked for a favour. The
-- solver cannot re-derive it, so it must not be allowed to overwrite it.
--
-- Deliberately NOT inferred from status or from a timestamp. "Assigned by a
-- human" is not visible in either: an auto-assigned shift and a chair-assigned
-- one are identical rows apart from this flag.

CREATE INDEX IF NOT EXISTS shifts_chair_set
  ON shifts(event_id)
  WHERE chair_set = 1;
