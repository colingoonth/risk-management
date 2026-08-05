-- Phase 1: seed event_type_shift_defaults with Colin's locked counts (min/target).
-- mixer:        driver 2/2, door 2/2, setup 4/4, cleanup 4/4
-- krush:        driver 3/3, door 2/2, setup 4/4, cleanup 4/4, bar 2/2
-- other_party:  driver 3/3, door 2/3, setup 4/4, cleanup 4/4, bar 2/2
-- philanthropy: setup 4/4, cleanup 4/4
--
-- mixer and krush additionally get dj 1/1 in 0012, which is where the `dj`
-- shift type is created. other_party is deliberately NOT given a dj slot: it
-- is the legacy catch-all, not one of the FA26 party types.
--
-- INSERT OR IGNORE so chair edits via `risk config event-type set-default` survive re-migration.
-- That is also why the FA26 recount of mixer/krush is made HERE, at the source,
-- rather than as an UPDATE in a later migration: migrations replay on every
-- connect, so an UPDATE would silently overwrite a count the chair had edited
-- by hand. The cost of that choice is that a database seeded before this edit
-- keeps the old numbers — it has to be rebuilt, or corrected via the CLI.

INSERT OR IGNORE INTO event_type_shift_defaults (event_type_id, shift_type_id, min_count, target_count)
SELECT et.id, st.id,
  CASE
    WHEN et.slug = 'mixer'        AND st.slug = 'driver'  THEN 2
    WHEN et.slug = 'mixer'        AND st.slug = 'door'    THEN 2
    WHEN et.slug = 'mixer'        AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'mixer'        AND st.slug = 'cleanup' THEN 4
    WHEN et.slug = 'krush'        AND st.slug = 'driver'  THEN 3
    WHEN et.slug = 'krush'        AND st.slug = 'door'    THEN 2
    WHEN et.slug = 'krush'        AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'krush'        AND st.slug = 'cleanup' THEN 4
    WHEN et.slug = 'krush'        AND st.slug = 'bar'     THEN 2
    WHEN et.slug = 'other_party'  AND st.slug = 'driver'  THEN 3
    WHEN et.slug = 'other_party'  AND st.slug = 'door'    THEN 2
    WHEN et.slug = 'other_party'  AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'other_party'  AND st.slug = 'cleanup' THEN 4
    WHEN et.slug = 'other_party'  AND st.slug = 'bar'     THEN 2
    WHEN et.slug = 'philanthropy' AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'philanthropy' AND st.slug = 'cleanup' THEN 4
  END AS min_count,
  CASE
    WHEN et.slug = 'mixer'        AND st.slug = 'driver'  THEN 2
    WHEN et.slug = 'mixer'        AND st.slug = 'door'    THEN 2
    WHEN et.slug = 'mixer'        AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'mixer'        AND st.slug = 'cleanup' THEN 4
    WHEN et.slug = 'krush'        AND st.slug = 'driver'  THEN 3
    WHEN et.slug = 'krush'        AND st.slug = 'door'    THEN 2
    WHEN et.slug = 'krush'        AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'krush'        AND st.slug = 'cleanup' THEN 4
    WHEN et.slug = 'krush'        AND st.slug = 'bar'     THEN 2
    WHEN et.slug = 'other_party'  AND st.slug = 'driver'  THEN 3
    WHEN et.slug = 'other_party'  AND st.slug = 'door'    THEN 3
    WHEN et.slug = 'other_party'  AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'other_party'  AND st.slug = 'cleanup' THEN 4
    WHEN et.slug = 'other_party'  AND st.slug = 'bar'     THEN 2
    WHEN et.slug = 'philanthropy' AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'philanthropy' AND st.slug = 'cleanup' THEN 4
  END AS target_count
FROM event_types et
CROSS JOIN shift_types st
JOIN event_type_shift_type_allowed allowed
  ON allowed.event_type_id = et.id AND allowed.shift_type_id = st.id;
