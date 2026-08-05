-- Phase 8: retract the bar → over-21 requirement.
--
-- 0012 seeded shift_type_required_qualification with two rows: ('dj','dj') and
-- ('bar','over-21'). The bar one was never a real rule. The 21+ list tracks who
-- can PURCHASE alcohol — part of the juice task the chair deliberately does not
-- model — and has nothing to do with standing the bar shift. Gating on it would
-- have cut the bar pool from 57 eligible members to 41.
--
-- 0012's seed is corrected at the source so fresh databases never get the row.
-- This DELETE exists for databases the old seed already ran against: INSERT OR
-- IGNORE only ever adds, so it cannot retract its own row.
--
-- SCOPE: this deletes the (bar, over-21) PAIR, not every qualification on bar.
-- Deleting by shift_type alone would be a standing trap — a replayed migration
-- silently removing any bar requirement a future chair adds. Same replay
-- reasoning as the exec UPDATE in 0013: this statement runs on EVERY connect,
-- so it must retract exactly the mistake and nothing more.
--
-- The `over-21` qualification itself and the 41 member_qualifications rows are
-- deliberately LEFT IN PLACE. They are harmless informational data now, and
-- dropping the qualification would break the foreign key those rows hold.

DELETE FROM shift_type_required_qualification
WHERE shift_type_id    = (SELECT id FROM shift_types   WHERE slug = 'bar')
  AND qualification_id = (SELECT id FROM qualifications WHERE slug = 'over-21');
