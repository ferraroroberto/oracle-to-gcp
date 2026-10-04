/* core-analytics team — monthly management pack, "customer base" page. */
/* Active customers per store, by the month they first bought. */
/* Mock pilot query (fictitious retail domain): the starting point for the harvest interview. */
WITH active AS (
  SELECT
    cm.customer_id,
    cm.home_store_id
  FROM analytics.customer_monthly AS cm
  WHERE
    cm.snapshot_date = CAST('{{period_end}}' AS DATE)
    AND cm.account_status = 'A'
    AND cm.is_staff = FALSE
    AND cm.last_purchase_date >= DATE_SUB(cm.snapshot_date, INTERVAL 12 MONTH)
)
SELECT
  s.region,
  s.store_name,
  DATE_TRUNC(c.first_purchase_date, MONTH) AS JOIN_MONTH,
  COUNT(DISTINCT a.customer_id) AS ACTIVE_CUSTOMERS
FROM active AS a
JOIN analytics.stores AS s
  ON s.store_id = a.home_store_id
JOIN analytics.customers AS c
  ON c.customer_id = a.customer_id
GROUP BY
  s.region,
  s.store_name,
  JOIN_MONTH
ORDER BY
  s.region NULLS LAST,
  s.store_name NULLS LAST,
  join_month NULLS LAST
