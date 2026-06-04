-- Phase 2: members + temporal junctions.
-- Schema per canonical plan §2.2. All STRICT.

CREATE TABLE IF NOT EXISTS member_statuses (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  excludes_from_assignment INTEGER NOT NULL DEFAULT 0
    CHECK (excludes_from_assignment IN (0, 1)),
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*')
) STRICT;

CREATE TABLE IF NOT EXISTS members (
  id INTEGER PRIMARY KEY,
  slug TEXT NOT NULL UNIQUE,
  display_name TEXT NOT NULL,
  status_id INTEGER NOT NULL REFERENCES member_statuses(id),
  class_year INTEGER NULL,
  notes TEXT NULL,
  CHECK (slug GLOB '[a-z]*' AND slug NOT GLOB '*[^a-z0-9_-]*'),
  CHECK (class_year IS NULL OR (class_year BETWEEN 2000 AND 2100))
) STRICT;

CREATE TABLE IF NOT EXISTS member_aliases (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  alias TEXT NOT NULL UNIQUE,
  source TEXT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
) STRICT;

CREATE TABLE IF NOT EXISTS member_roles (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  role_id INTEGER NOT NULL REFERENCES roles(id),
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  starts_on TEXT NULL,
  ends_on TEXT NULL,
  UNIQUE (member_id, role_id, semester_id)
) STRICT;

CREATE INDEX IF NOT EXISTS member_roles_by_semester
  ON member_roles(semester_id, role_id);

CREATE TABLE IF NOT EXISTS member_house_assignments (
  id INTEGER PRIMARY KEY,
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  house_id INTEGER NOT NULL REFERENCES houses(id),
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  starts_on TEXT NULL,
  ends_on TEXT NULL,
  UNIQUE (member_id, semester_id)
) STRICT;

CREATE INDEX IF NOT EXISTS mha_by_semester
  ON member_house_assignments(semester_id, house_id);

CREATE TABLE IF NOT EXISTS house_semester_status (
  id INTEGER PRIMARY KEY,
  house_id INTEGER NOT NULL REFERENCES houses(id),
  semester_id INTEGER NOT NULL REFERENCES semesters(id),
  pledge_mode_id INTEGER NOT NULL REFERENCES pledge_modes(id),
  UNIQUE (house_id, semester_id)
) STRICT;

-- =========================================================================
-- Seed member_statuses. Each row is editable via future config CLI,
-- but the closed-ish set covers Colin's documented chapter lifecycle.
-- =========================================================================

INSERT INTO member_statuses (slug, display_name, excludes_from_assignment)
SELECT 'active', 'Active', 0
WHERE NOT EXISTS (SELECT 1 FROM member_statuses WHERE slug = 'active');

INSERT INTO member_statuses (slug, display_name, excludes_from_assignment)
SELECT 'inactive', 'Inactive (excused)', 1
WHERE NOT EXISTS (SELECT 1 FROM member_statuses WHERE slug = 'inactive');

INSERT INTO member_statuses (slug, display_name, excludes_from_assignment)
SELECT 'exempt', 'Exempt (injured / accommodation)', 1
WHERE NOT EXISTS (SELECT 1 FROM member_statuses WHERE slug = 'exempt');

INSERT INTO member_statuses (slug, display_name, excludes_from_assignment)
SELECT 'abroad', 'Studying abroad', 1
WHERE NOT EXISTS (SELECT 1 FROM member_statuses WHERE slug = 'abroad');

INSERT INTO member_statuses (slug, display_name, excludes_from_assignment)
SELECT 'alumni', 'Alumni', 1
WHERE NOT EXISTS (SELECT 1 FROM member_statuses WHERE slug = 'alumni');

INSERT INTO member_statuses (slug, display_name, excludes_from_assignment)
SELECT 'transferred', 'Transferred / dropped', 1
WHERE NOT EXISTS (SELECT 1 FROM member_statuses WHERE slug = 'transferred');
