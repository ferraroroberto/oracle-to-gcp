/* core-analytics team — monthly management pack, "customer base" page. */
/* Active customers per store, by the month they first bought. */
/* Mock pilot query (fictitious retail domain): the starting point for the harvest interview. */
WITH active AS (
  SELECT
    cm.CUSTOMER_ID,
    cm.HOME_STORE_ID
  FROM DWH.CUSTOMER_MONTHLY cm
  WHERE
    cm.SNAPSHOT_DATE = TO_DATE('{{period_end}}', 'YYYY-MM-DD')
    AND cm.ACCOUNT_STATUS = 'A'
    AND cm.IS_STAFF = 0
    AND cm.LAST_PURCHASE_DATE >= ADD_MONTHS(cm.SNAPSHOT_DATE, -12)
)
SELECT
  s.REGION,
  s.STORE_NAME,
  TRUNC(c.FIRST_PURCHASE_DATE, 'MONTH') AS JOIN_MONTH,
  COUNT(DISTINCT a.CUSTOMER_ID) AS ACTIVE_CUSTOMERS
FROM active a
JOIN DWH.STORES s
  ON s.STORE_ID = a.HOME_STORE_ID
JOIN DWH.CUSTOMERS c
  ON c.CUSTOMER_ID = a.CUSTOMER_ID
GROUP BY
  s.REGION,
  s.STORE_NAME,
  TRUNC(c.FIRST_PURCHASE_DATE, 'MONTH')
ORDER BY
  s.REGION,
  s.STORE_NAME,
  JOIN_MONTH
