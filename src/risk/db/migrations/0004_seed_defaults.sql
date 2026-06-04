-- Phase 1: seed event_type_shift_defaults with Colin's locked counts.
-- mixer:        driver 2/3, door 2/3, setup 4/4, cleanup 4/4
-- krush:        driver 3/3, door 2/3, setup 4/4, cleanup 4/4, bar 2/2
-- other_party:  same as krush
-- philanthropy: setup 4/4, cleanup 4/4
-- INSERT OR IGNORE so chair edits via `risk config event-type set-default` survive re-migration.

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
    WHEN et.slug = 'mixer'        AND st.slug = 'driver'  THEN 3
    WHEN et.slug = 'mixer'        AND st.slug = 'door'    THEN 3
    WHEN et.slug = 'mixer'        AND st.slug = 'setup'   THEN 4
    WHEN et.slug = 'mixer'        AND st.slug = 'cleanup' THEN 4
    WHEN et.slug = 'krush'        AND st.slug = 'driver'  THEN 3
    WHEN et.slug = 'krush'        AND st.slug = 'door'    THEN 3
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
