# First-purchase store

`dimension.first_purchase_store` · dimension · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

Store where the customer completed their first purchase. Empty for customers who never bought.

**Synonyms:** store, first store, first purchase store, store of first purchase

## Not to be confused with

- [`dimension.home_store`](../definitions/dimension.home_store.md) — store assigned at signup (always present); the management pack's "store"

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| cloud (preferred) | [analytics.stores](../tables/cloud/analytics.stores.md) | store_id | `` | active |
| legacy | [DWH.STORES](../tables/legacy/DWH.STORES.md) | STORE_ID | `` | legacy |

## Lineage

Used by: [`rel.active_buyer__first_purchase_store`](../definitions/rel.active_buyer__first_purchase_store.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/dimensions/dimension.first_purchase_store.yaml`](../../../domains/core_analytics/dimensions/dimension.first_purchase_store.yaml)
· review by 2027-04-03
