-- marketing team — campaign sizing: how many customers can we reach per region?
-- Mock pilot query for the second team (fictitious retail domain).
SELECT s.region,
       COUNT(DISTINCT cm.customer_id) AS reachable_customers
FROM analytics.customer_monthly AS cm
JOIN analytics.stores AS s ON s.store_id = cm.home_store_id
LEFT JOIN analytics.loyalty_members AS lm ON lm.customer_id = cm.customer_id
WHERE cm.snapshot_date = DATE '2026-08-31'
  AND cm.last_purchase_date >= DATE_SUB(cm.snapshot_date, INTERVAL 6 MONTH)
GROUP BY s.region
ORDER BY s.region
