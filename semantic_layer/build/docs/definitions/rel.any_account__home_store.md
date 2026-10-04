# Home store of each registered account

`rel.any_account__home_store` · relationship · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

## Join

[`customer.any_account`](../definitions/customer.any_account.md) → [`dimension.home_store`](../definitions/dimension.home_store.md) · many_to_one

- cloud: `{to}.store_id = {from}.home_store_id`
- legacy: `{to}.STORE_ID = {from}.HOME_STORE_ID`

## Lineage

Uses: [`customer.any_account`](../definitions/customer.any_account.md), [`dimension.home_store`](../definitions/dimension.home_store.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/relationships/rel.any_account__home_store.yaml`](../../../domains/core_analytics/relationships/rel.any_account__home_store.yaml)
· review by 2027-04-03
