DECLARE
  v_run_date DATE;
BEGIN
  SELECT MAX(load_date) INTO v_run_date FROM sales_orders;
END;
/

CREATE TABLE daily_revenue AS
SELECT
  o.customer_id,
  c.segment,
  TRUNC(o.order_date) AS order_day,
  SUM(NVL(o.amount, 0)) AS revenue,
  COUNT(*) AS order_count
FROM sales_orders o
JOIN customer_segments c ON c.customer_id = o.customer_id
WHERE o.load_date = v_run_date
GROUP BY o.customer_id, c.segment, TRUNC(o.order_date);

SELECT
  segment,
  SUM(revenue) AS revenue,
  SUM(order_count) AS order_count
FROM daily_revenue
GROUP BY segment;
