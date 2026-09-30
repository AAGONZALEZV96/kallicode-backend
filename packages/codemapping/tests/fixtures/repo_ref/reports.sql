WITH monthly_revenue AS (
    SELECT
        date_trunc('month', created_at) AS month,
        SUM(amount) AS total_amount
    FROM invoices
    WHERE paid = true
    GROUP BY 1
),
ranked_months AS (
    SELECT
        month,
        total_amount,
        RANK() OVER (ORDER BY total_amount DESC) AS rank
    FROM monthly_revenue
)
SELECT month, total_amount
FROM ranked_months
WHERE rank <= 5;