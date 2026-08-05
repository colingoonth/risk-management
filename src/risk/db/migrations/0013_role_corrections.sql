-- Phase 8: role seed corrections for FA26.
--
-- Two defects in the 0010 seed, both of which left members assignable who must
-- never be assigned:
--
--   1. `exec` shipped with default_excluded_from_assignment = 0, so all five EC
--      members would have landed in the auto-assign pool. The chair's rule is
--      that EC never works a sober-monitor shift.
--   2. There was no `social_chair` role at all, so the two social chairs had
--      nothing to hold and therefore no way to be excluded.
--
-- 0010's seed values are corrected at the source as well, so a fresh database
-- is right from its first connect and the two files do not state different
-- things. The UPDATE below exists for databases ALREADY seeded by the old
-- 0010: INSERT OR IGNORE never revisits a row that exists, so the source fix
-- on its own would silently leave every existing database wrong.
--
-- REPLAY HAZARD — read this before adding another UPDATE to a migration.
-- ensure_schema() replays every migration on every connect; there is no
-- _schema_migrations table (see risk/db/schema.py). This UPDATE therefore runs
-- on every connection, not once. It is idempotent, because it re-asserts a
-- fixed value rather than deriving one. But it also PINS the flag: a later
-- `risk config role edit exec` will appear to succeed and then revert on the
-- next connect. That is tolerable here only because "EC never works" is a
-- domain invariant rather than something the chair tunes.
--
-- Do NOT copy this pattern for anything that IS tuned. Slot counts are the
-- counter-example: the FA26 recount was made at the source in 0004 precisely so
-- that no replayed UPDATE could clobber a count the chair had edited by hand.

UPDATE roles
SET default_excluded_from_assignment = 1,
    exclude_is_soft = 0
WHERE slug = 'exec';

-- New hard-exclude roles.
--
-- blur_resident is seeded with nobody attached on purpose: the list of who
-- lived in Blur has not arrived yet. Seeding the role now means applying the
-- names later is `risk member set-role <name> blur_resident`, not a migration.
INSERT OR IGNORE INTO roles
  (slug, display_name, automation_key, default_excluded_from_assignment, exclude_is_soft)
VALUES
  ('social_chair',  'Social Chair',  'social-chair',  1, 0),
  ('blur_resident', 'Blur Resident', 'blur-resident', 1, 0);

-- =========================================================================
-- NOTE on the `dj` role (seeded in 0010, deliberately left in place).
--
-- DJ is NO LONGER modelled as a role. 0012 makes it a shift TYPE plus a
-- QUALIFICATION (`qualifications` / `member_qualifications`), because a DJ
-- works the party rather than being excused from it.
--
-- Do not assign anyone the `dj` ROLE. It carries
-- default_excluded_from_assignment = 1 with exclude_is_soft = 1, so giving it
-- to a member soft-excludes them from assignment — the exact opposite of what
-- is wanted, and it would quietly shrink the pool. The row survives only so
-- that databases already referencing it keep their foreign keys intact.
--
-- The correct command is:  risk member qualify <member> dj
-- =========================================================================
