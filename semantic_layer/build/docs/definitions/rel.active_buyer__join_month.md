# Join month (first purchase) of each active buyer

`rel.active_buyer__join_month` · relationship · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

## Join

[`customer.active_buyer`](../definitions/customer.active_buyer.md) → [`dimension.join_month`](../definitions/dimension.join_month.md) · many_to_one

- legacy: `{to}.CUSTOMER_ID = {from}.CUSTOMER_ID`
- cloud: `{to}.customer_id = {from}.customer_id`

## Lineage

Uses: [`customer.active_buyer`](../definitions/customer.active_buyer.md), [`dimension.join_month`](../definitions/dimension.join_month.md)

## Provenance

- method: harvest
- source: queries/core_analytics_pilot.oracle.sql
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/relationships/rel.active_buyer__join_month.yaml`](../../../domains/core_analytics/relationships/rel.active_buyer__join_month.yaml)
· review by 2027-04-03
