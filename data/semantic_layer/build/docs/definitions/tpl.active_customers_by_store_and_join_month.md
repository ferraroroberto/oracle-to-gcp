# Active customers by store and join month

`tpl.active_customers_by_store_and_join_month` · template · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

The management-pack query, parameterised by snapshot month.

## Lineage

Uses: [`anchor.monthly_pack.active_customers`](../definitions/anchor.monthly_pack.active_customers.md), [`customer.active_buyer`](../definitions/customer.active_buyer.md), [`dimension.home_store`](../definitions/dimension.home_store.md), [`dimension.join_month`](../definitions/dimension.join_month.md), [`metric.customer_count`](../definitions/metric.customer_count.md)

## Provenance

- method: harvest
- source: queries/core_analytics_pilot.oracle.sql
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/templates/tpl.active_customers_by_store_and_join_month.yaml`](../../../domains/core_analytics/templates/tpl.active_customers_by_store_and_join_month.yaml)
· review by 2027-04-03
