DROP VIEW IF EXISTS v_analysis_base;
DROP VIEW IF EXISTS v_events_deduped;

CREATE VIEW v_events_deduped AS
WITH ranked_events AS (
    SELECT
        e.*,
        ROW_NUMBER() OVER (
            PARTITION BY event_id
            ORDER BY ingest_id
        ) AS ingest_rank
    FROM events AS e
)
SELECT ingest_id, event_id, user_id, event_name, occurred_at
FROM ranked_events
WHERE ingest_rank = 1;

CREATE VIEW v_analysis_base AS
WITH per_user AS (
    SELECT
        a.user_id,
        a.variant,
        a.assigned_at,
        a.device_type,
        MIN(
            CASE
                WHEN e.event_name = 'tutorial_complete'
                     AND e.occurred_at >= a.assigned_at
                THEN e.occurred_at
            END
        ) AS first_completion_at,
        MAX(
            CASE
                WHEN e.event_name = 'app_open'
                     AND e.occurred_at >= a.assigned_at + 86400
                     AND e.occurred_at < a.assigned_at + 172800
                THEN 1 ELSE 0
            END
        ) AS retained_d1,
        MAX(
            CASE
                WHEN e.event_name = 'crash'
                     AND e.occurred_at >= a.assigned_at
                     AND e.occurred_at < a.assigned_at + 86400
                THEN 1 ELSE 0
            END
        ) AS crashed_24h
    FROM assignments AS a
    LEFT JOIN v_events_deduped AS e
        ON a.user_id = e.user_id
    GROUP BY a.user_id, a.variant, a.assigned_at, a.device_type
)
SELECT
    user_id,
    variant,
    assigned_at,
    device_type,
    CASE
        WHEN first_completion_at < assigned_at + 86400 THEN 1
        ELSE 0
    END AS completed_24h,
    COALESCE(retained_d1, 0) AS retained_d1,
    COALESCE(crashed_24h, 0) AS crashed_24h
FROM per_user;
