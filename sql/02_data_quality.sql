WITH checks AS (
    SELECT
        'duplicate_assignment_users' AS check_name,
        COUNT(*) - COUNT(DISTINCT user_id) AS failed_rows,
        'error' AS severity
    FROM assignments

    UNION ALL

    SELECT
        'invalid_variants',
        COUNT(*),
        'error'
    FROM assignments
    WHERE variant NOT IN ('control', 'treatment')

    UNION ALL

    SELECT
        'orphan_events',
        COUNT(*),
        'error'
    FROM events AS e
    LEFT JOIN assignments AS a ON e.user_id = a.user_id
    WHERE a.user_id IS NULL

    UNION ALL

    SELECT
        'conflicting_event_ids',
        COUNT(*),
        'error'
    FROM (
        SELECT event_id
        FROM events
        GROUP BY event_id
        HAVING COUNT(DISTINCT user_id) > 1
            OR COUNT(DISTINCT event_name) > 1
            OR COUNT(DISTINCT occurred_at) > 1
    ) AS conflicts

    UNION ALL

    SELECT
        'analysis_base_row_mismatch',
        ABS(
            (SELECT COUNT(*) FROM assignments)
            - (SELECT COUNT(*) FROM v_analysis_base)
        ),
        'error'

    UNION ALL

    SELECT
        'duplicate_analysis_users',
        COUNT(*),
        'error'
    FROM (
        SELECT user_id
        FROM v_analysis_base
        GROUP BY user_id
        HAVING COUNT(*) <> 1
    ) AS duplicate_users

    UNION ALL

    SELECT
        'duplicate_ingest_rows_removed',
        COUNT(*) - COUNT(DISTINCT event_id),
        'info'
    FROM events
)
SELECT check_name, failed_rows, severity
FROM checks
ORDER BY check_name;
