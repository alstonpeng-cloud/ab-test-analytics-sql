WITH variant_counts AS (
    SELECT variant, COUNT(*) AS n
    FROM assignments
    GROUP BY variant
),
pivoted AS (
    SELECT
        COALESCE(SUM(CASE WHEN variant = 'control' THEN n END), 0) AS control_n,
        COALESCE(SUM(CASE WHEN variant = 'treatment' THEN n END), 0) AS treatment_n
    FROM variant_counts
),
statistics AS (
    SELECT
        control_n,
        treatment_n,
        (control_n + treatment_n) / 2.0 AS expected_n,
        CASE
            WHEN control_n + treatment_n > 0 THEN
                (control_n - (control_n + treatment_n) / 2.0)
                * (control_n - (control_n + treatment_n) / 2.0)
                / ((control_n + treatment_n) / 2.0)
                +
                (treatment_n - (control_n + treatment_n) / 2.0)
                * (treatment_n - (control_n + treatment_n) / 2.0)
                / ((control_n + treatment_n) / 2.0)
        END AS chi_square
    FROM pivoted
),
with_p_value AS (
    SELECT
        *,
        CASE
            WHEN chi_square IS NOT NULL THEN erfc(sqrt(chi_square / 2.0))
        END AS p_value
    FROM statistics
)
SELECT
    control_n,
    treatment_n,
    expected_n,
    chi_square,
    1 AS degrees_of_freedom,
    p_value,
    CASE
        WHEN p_value IS NULL THEN 'INVALID'
        WHEN p_value < 0.01 THEN 'FAIL'
        ELSE 'PASS'
    END AS status
FROM with_p_value;
