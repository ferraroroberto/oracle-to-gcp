# Number of customers

`metric.customer_count` · metric · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

Distinct customers in the chosen customer population.

**Synonyms:** number of customers, customer count, how many customers, headcount

## Lineage

Uses: [`customer.active_buyer`](../definitions/customer.active_buyer.md), [`customer.any_account`](../definitions/customer.any_account.md), [`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md), [`customer.loyalty_member`](../definitions/customer.loyalty_member.md)
Used by: [`anchor.monthly_pack.active_customers`](../definitions/anchor.monthly_pack.active_customers.md), [`tpl.active_customers_by_store_and_join_month`](../definitions/tpl.active_customers_by_store_and_join_month.md), [`tpl.reachable_customers_by_region`](../definitions/tpl.reachable_customers_by_region.md)

## Provenance

- method: harvest
- source: queries/core_analytics_pilot.oracle.sql
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/metrics/metric.customer_count.yaml`](../../../domains/core_analytics/metrics/metric.customer_count.yaml)
· review by 2027-04-03
