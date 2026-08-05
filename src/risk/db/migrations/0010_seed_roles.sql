-- Phase B (post-v1): seed the four real KS roles so `risk member set-role` works
-- out of the box without a manual `risk config role add` step.
--
-- - exec:          hard exclude. EC never works a sober-monitor shift.
-- - risk_chair:    hard exclude (chair coordinates assignments; not assigned themselves).
-- - dj:            soft exclude. SUPERSEDED — DJ became a qualification in 0012;
--                  do not assign this role to anyone. See 0013 for why.
-- - pledge_chair:  soft exclude (override with --allow pledge-chair when relevant).
--
-- INSERT OR IGNORE so chair edits via `risk config role add/edit` survive re-migration.
--
-- `exec` originally shipped here as (0, 0) — no exclusion — which would have put
-- all five EC members in the auto-assign pool. Corrected at the source so fresh
-- databases are right immediately; 0013 carries the matching UPDATE for
-- databases this seed had already run against.

INSERT OR IGNORE INTO roles (slug, display_name, automation_key, default_excluded_from_assignment, exclude_is_soft) VALUES
  ('exec',         'Exec',         'exec',          1, 0),
  ('risk_chair',   'Risk Chair',   'risk-chair',    1, 0),
  ('dj',           'DJ',           'dj',            1, 1),
  ('pledge_chair', 'Pledge Chair', 'pledge-chair',  1, 1);
