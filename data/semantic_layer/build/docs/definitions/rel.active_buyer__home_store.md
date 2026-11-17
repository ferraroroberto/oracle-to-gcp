# Home store of each active buyer

`rel.active_buyer__home_store` · relationship · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

## Join

[`customer.active_buyer`](../definitions/customer.active_buyer.md) → [`dimension.home_store`](../definitions/dimension.home_store.md) · many_to_one

- legacy: `{to}.STORE_ID = {from}.HOME_STORE_ID`
- cloud: `{to}.store_id = {from}.home_store_id`

## Lineage

Uses: [`customer.active_buyer`](../definitions/customer.active_buyer.md), [`dimension.home_store`](../definitions/dimension.home_store.md)

## Provenance

- method: harvest
- source: queries/core_analytics_pilot.oracle.sql
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/relationships/rel.active_buyer__home_store.yaml`](../../../domains/core_analytics/relationships/rel.active_buyer__home_store.yaml)
· review by 2027-04-03
