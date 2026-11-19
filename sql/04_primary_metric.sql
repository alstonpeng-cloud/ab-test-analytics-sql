WITH grouped AS (
    SELECT
        variant,
        COUNT(*) AS n,
        SUM(completed_24h) AS conversions
    FROM v_analysis_base
    GROUP BY variant
),
pivoted AS (
    SELECT
        COALESCE(SUM(CASE WHEN variant = 'control' THEN n END), 0) AS control_n,
        COALESCE(SUM(CASE WHEN variant = 'treatment' THEN n END), 0) AS treatment_n,
        COALESCE(SUM(CASE WHEN variant = 'control' THEN conversions END), 0)
            AS control_conversions,
        COALESCE(SUM(CASE WHEN variant = 'treatment' THEN conversions END), 0)
            AS treatment_conversions
    FROM grouped
),
rates AS (
    SELECT
        *,
        control_conversions / NULLIF(CAST(control_n AS REAL), 0) AS control_rate,
        treatment_conversions / NULLIF(CAST(treatment_n AS REAL), 0) AS treatment_rate
    FROM pivoted
),
statistics AS (
    SELECT
        *,
        treatment_rate - control_rate AS absolute_lift,
        (treatment_rate - control_rate) / NULLIF(control_rate, 0) AS relative_lift,
        (control_conversions + treatment_conversions)
            / NULLIF(CAST(control_n + treatment_n AS REAL), 0) AS pooled_rate,
        sqrt(
            control_rate * (1 - control_rate) / NULLIF(control_n, 0)
            + treatment_rate * (1 - treatment_rate) / NULLIF(treatment_n, 0)
        ) AS unpooled_se,
        sqrt(
            ((control_conversions + treatment_conversions)
             / NULLIF(CAST(control_n + treatment_n AS REAL), 0))
            * (1 - (control_conversions + treatment_conversions)
               / NULLIF(CAST(control_n + treatment_n AS REAL), 0))
            * (1.0 / NULLIF(control_n, 0) + 1.0 / NULLIF(treatment_n, 0))
        ) AS pooled_se
    FROM rates
)
SELECT
    control_n,
    treatment_n,
    control_conversions,
    treatment_conversions,
    control_rate,
    treatment_rate,
    absolute_lift,
    relative_lift,
    absolute_lift - 1.959963984540054 * unpooled_se AS ci_lower,
    absolute_lift + 1.959963984540054 * unpooled_se AS ci_upper,
    absolute_lift / NULLIF(pooled_se, 0) AS z_score,
    CASE
        WHEN pooled_se > 0 THEN erfc(abs(absolute_lift / pooled_se) / sqrt(2.0))
        WHEN absolute_lift = 0 THEN 1.0
        ELSE NULL
    END AS p_value
FROM statistics;
