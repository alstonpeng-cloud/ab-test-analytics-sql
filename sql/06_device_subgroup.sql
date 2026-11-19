WITH grouped AS (
    SELECT
        device_type,
        variant,
        COUNT(*) AS n,
        SUM(completed_24h) AS conversions
    FROM v_analysis_base
    GROUP BY device_type, variant
),
pivoted AS (
    SELECT
        device_type,
        COALESCE(SUM(CASE WHEN variant = 'control' THEN n END), 0) AS control_n,
        COALESCE(SUM(CASE WHEN variant = 'treatment' THEN n END), 0) AS treatment_n,
        COALESCE(SUM(CASE WHEN variant = 'control' THEN conversions END), 0)
            AS control_conversions,
        COALESCE(SUM(CASE WHEN variant = 'treatment' THEN conversions END), 0)
            AS treatment_conversions
    FROM grouped
    GROUP BY device_type
),
statistics AS (
    SELECT
        *,
        control_conversions / NULLIF(CAST(control_n AS REAL), 0) AS control_rate,
        treatment_conversions / NULLIF(CAST(treatment_n AS REAL), 0) AS treatment_rate,
        sqrt(
            (control_conversions / NULLIF(CAST(control_n AS REAL), 0))
            * (1 - control_conversions / NULLIF(CAST(control_n AS REAL), 0))
            / NULLIF(control_n, 0)
            +
            (treatment_conversions / NULLIF(CAST(treatment_n AS REAL), 0))
            * (1 - treatment_conversions / NULLIF(CAST(treatment_n AS REAL), 0))
            / NULLIF(treatment_n, 0)
        ) AS unpooled_se
    FROM pivoted
)
SELECT
    device_type,
    'exploratory' AS analysis_role,
    control_n,
    treatment_n,
    control_conversions,
    treatment_conversions,
    control_rate,
    treatment_rate,
    treatment_rate - control_rate AS absolute_lift,
    (treatment_rate - control_rate) / NULLIF(control_rate, 0) AS relative_lift,
    (treatment_rate - control_rate) - 1.959963984540054 * unpooled_se AS ci_lower,
    (treatment_rate - control_rate) + 1.959963984540054 * unpooled_se AS ci_upper
FROM statistics
ORDER BY device_type;
