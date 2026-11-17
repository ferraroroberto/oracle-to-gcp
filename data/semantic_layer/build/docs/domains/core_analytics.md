# Domain: core_analytics

```mermaid
flowchart LR
  n1["customer.active_buyer"]
  n5["dimension.first_purchase_store"]
  n1 -->|many_to_one| n5
  n6["dimension.home_store"]
  n1 -->|many_to_one| n6
  n7["dimension.join_month"]
  n1 -->|many_to_one| n7
  n8["dimension.signup_month"]
  n1 -->|many_to_one| n8
  n2["customer.any_account"]
  n2 -->|many_to_one| n6
  n2 -->|many_to_one| n7
  n2 -->|many_to_one| n8
```

| definition | kind | status |
|---|---|---|
| [`anchor.monthly_pack.active_customers`](../definitions/anchor.monthly_pack.active_customers.md) | anchor | certified |
| [`customer.active_buyer`](../definitions/customer.active_buyer.md) | entity | certified |
| [`customer.any_account`](../definitions/customer.any_account.md) | entity | certified |
| [`dimension.first_purchase_store`](../definitions/dimension.first_purchase_store.md) | dimension | certified |
| [`dimension.home_store`](../definitions/dimension.home_store.md) | dimension | certified |
| [`dimension.join_month`](../definitions/dimension.join_month.md) | dimension | certified |
| [`dimension.signup_month`](../definitions/dimension.signup_month.md) | dimension | certified |
| [`metric.customer_count`](../definitions/metric.customer_count.md) | metric | certified |
| [`rel.active_buyer__first_purchase_store`](../definitions/rel.active_buyer__first_purchase_store.md) | relationship | certified |
| [`rel.active_buyer__home_store`](../definitions/rel.active_buyer__home_store.md) | relationship | certified |
| [`rel.active_buyer__join_month`](../definitions/rel.active_buyer__join_month.md) | relationship | certified |
| [`rel.active_buyer__signup_month`](../definitions/rel.active_buyer__signup_month.md) | relationship | certified |
| [`rel.any_account__home_store`](../definitions/rel.any_account__home_store.md) | relationship | certified |
| [`rel.any_account__join_month`](../definitions/rel.any_account__join_month.md) | relationship | certified |
| [`rel.any_account__signup_month`](../definitions/rel.any_account__signup_month.md) | relationship | certified |
| [`tpl.active_customers_by_store_and_join_month`](../definitions/tpl.active_customers_by_store_and_join_month.md) | template | certified |
