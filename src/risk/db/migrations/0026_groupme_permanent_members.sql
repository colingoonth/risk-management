-- Members who belong in a particular GroupMe chat even when they have no shift.
--
-- Permanence is scoped to a group slug on purpose. A member who must remain in
-- the standalone setup/cleanup chat does not thereby belong permanently in the
-- Risk parent group, or in any other chat registered later.
--
-- The standalone setup/cleanup chat's canonical slug is `setup-cleanup`. Seed
-- that group with both parent_slug and weekday NULL: it is a group in its own
-- right, not a weekday topic under `risk-parent`.
CREATE TABLE IF NOT EXISTS groupme_permanent_members (
  group_slug TEXT NOT NULL REFERENCES groupme_groups(slug) ON DELETE CASCADE,
  member_id INTEGER NOT NULL REFERENCES members(id) ON DELETE CASCADE,
  PRIMARY KEY (group_slug, member_id)
) STRICT;

CREATE INDEX IF NOT EXISTS ix_groupme_permanent_members_member
  ON groupme_permanent_members(member_id);
