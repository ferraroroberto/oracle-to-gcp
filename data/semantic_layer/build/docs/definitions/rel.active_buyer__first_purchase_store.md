# First-purchase store of each active buyer

`rel.active_buyer__first_purchase_store` · relationship · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

## Join

[`customer.active_buyer`](../definitions/customer.active_buyer.md) → [`dimension.first_purchase_store`](../definitions/dimension.first_purchase_store.md) · many_to_one

- cloud: `{to}.store_id = {from}.first_purchase_store_id`
- legacy: `{to}.STORE_ID = {from}.FIRST_PURCHASE_STORE_ID`

## Caveats

- Active buyers always have a first purchase, so the inner join drops nobody.

## Lineage

Uses: [`customer.active_buyer`](../definitions/customer.active_buyer.md), [`dimension.first_purchase_store`](../definitions/dimension.first_purchase_store.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/relationships/rel.active_buyer__first_purchase_store.yaml`](../../../domains/core_analytics/relationships/rel.active_buyer__first_purchase_store.yaml)
· review by 2027-04-03
