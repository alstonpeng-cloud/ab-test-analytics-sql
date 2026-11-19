WITH metric_flags AS (
    SELECT
        variant,
        'd1_retention' AS metric_name,
        'secondary' AS metric_role,
        retained_d1 AS flag
    FROM v_analysis_base

    UNION ALL

    SELECT variant, 'crash_24h', 'guardrail', crashed_24h
    FROM v_analysis_base
),
grouped AS (
    SELECT
        metric_name,
        metric_role,
        variant,
        COUNT(*) AS n,
        SUM(flag) AS conversions
    FROM metric_flags
    GROUP BY metric_name, metric_role, variant
),
pivoted AS (
    SELECT
        metric_name,
        metric_role,
        COALESCE(SUM(CASE WHEN variant = 'control' THEN n END), 0) AS control_n,
        COALESCE(SUM(CASE WHEN variant = 'treatment' THEN n END), 0) AS treatment_n,
        COALESCE(SUM(CASE WHEN variant = 'control' THEN conversions END), 0)
            AS control_conversions,
        COALESCE(SUM(CASE WHEN variant = 'treatment' THEN conversions END), 0)
            AS treatment_conversions
    FROM grouped
    GROUP BY metric_name, metric_role
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
        ) AS unpooled_se,
        sqrt(
            ((control_conversions + treatment_conversions)
             / NULLIF(CAST(control_n + treatment_n AS REAL), 0))
            * (1 - (control_conversions + treatment_conversions)
               / NULLIF(CAST(control_n + treatment_n AS REAL), 0))
            * (1.0 / NULLIF(control_n, 0) + 1.0 / NULLIF(treatment_n, 0))
        ) AS pooled_se
    FROM pivoted
)
SELECT
    metric_name,
    metric_role,
    control_n,
    treatment_n,
    control_conversions,
    treatment_conversions,
    control_rate,
    treatment_rate,
    treatment_rate - control_rate AS absolute_lift,
    (treatment_rate - control_rate) / NULLIF(control_rate, 0) AS relative_lift,
    (treatment_rate - control_rate) - 1.959963984540054 * unpooled_se AS ci_lower,
    (treatment_rate - control_rate) + 1.959963984540054 * unpooled_se AS ci_upper,
    CASE
        WHEN metric_role = 'secondary'
        THEN (treatment_rate - control_rate) / NULLIF(pooled_se, 0)
    END AS z_score,
    CASE
        WHEN metric_role = 'secondary' AND pooled_se > 0
        THEN erfc(abs((treatment_rate - control_rate) / pooled_se) / sqrt(2.0))
        WHEN metric_role = 'secondary' AND treatment_rate = control_rate THEN 1.0
        ELSE NULL
    END AS p_value
FROM statistics
ORDER BY metric_name;
