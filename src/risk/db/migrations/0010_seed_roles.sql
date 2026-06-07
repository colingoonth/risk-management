-- Phase B (post-v1): seed the four real KS roles so `risk member set-role` works
-- out of the box without a manual `risk config role add` step.
--
-- - exec:          no exclusion. Tagging role for EC members (Google Form EC=yes).
-- - risk_chair:    hard exclude (chair coordinates assignments; not assigned themselves).
-- - dj:            soft exclude (override with --allow dj when a DJ also works a shift).
-- - pledge_chair:  soft exclude (override with --allow pledge-chair when relevant).
--
-- INSERT OR IGNORE so chair edits via `risk config role add/edit` survive re-migration.

INSERT OR IGNORE INTO roles (slug, display_name, automation_key, default_excluded_from_assignment, exclude_is_soft) VALUES
  ('exec',         'Exec',         'exec',          0, 0),
  ('risk_chair',   'Risk Chair',   'risk-chair',    1, 0),
  ('dj',           'DJ',           'dj',            1, 1),
  ('pledge_chair', 'Pledge Chair', 'pledge-chair',  1, 1);
