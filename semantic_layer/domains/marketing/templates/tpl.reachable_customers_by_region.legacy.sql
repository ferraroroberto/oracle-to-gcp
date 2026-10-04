/* marketing team — campaign sizing: how many customers can we reach per region? */
/* Mock pilot query for the second team (fictitious retail domain). */
SELECT
  s.REGION,
  COUNT(DISTINCT cm.CUSTOMER_ID) AS reachable_customers
FROM DWH.CUSTOMER_MONTHLY cm
JOIN DWH.STORES s
  ON s.STORE_ID = cm.HOME_STORE_ID
LEFT JOIN DWH.LOYALTY_MEMBERS lm
  ON lm.CUSTOMER_ID = cm.CUSTOMER_ID
WHERE
  cm.SNAPSHOT_DATE = DATE '{{period_end}}'
  AND cm.LAST_PURCHASE_DATE >= ADD_MONTHS(cm.SNAPSHOT_DATE, -6)
GROUP BY
  s.REGION
ORDER BY
  s.REGION NULLS FIRST
