# Home store

`dimension.home_store` · dimension · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

Store assigned to the customer at signup (always present). Rolls up to district and region.

**Synonyms:** store, shop, location, home store, region, district

## Not to be confused with

- [`dimension.first_purchase_store`](../definitions/dimension.first_purchase_store.md) — store where the customer completed their first purchase

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| legacy | [DWH.STORES](../tables/legacy/DWH.STORES.md) | STORE_ID | `` |  |
| cloud | [analytics.stores](../tables/cloud/analytics.stores.md) | store_id | `` | active |

## Lineage

Used by: [`rel.active_buyer__home_store`](../definitions/rel.active_buyer__home_store.md), [`rel.any_account__home_store`](../definitions/rel.any_account__home_store.md), [`rel.campaign_reachable__home_store`](../definitions/rel.campaign_reachable__home_store.md), [`tpl.active_customers_by_store_and_join_month`](../definitions/tpl.active_customers_by_store_and_join_month.md), [`tpl.reachable_customers_by_region`](../definitions/tpl.reachable_customers_by_region.md)

## Provenance

- method: harvest
- source: queries/core_analytics_pilot.oracle.sql
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/dimensions/dimension.home_store.yaml`](../../../domains/core_analytics/dimensions/dimension.home_store.yaml)
· review by 2027-04-03
