# Home store of each campaign-reachable customer

`rel.campaign_reachable__home_store` · relationship · **reviewed** · v1 · owner **marketing** · steward **sam.ortiz**

## Join

[`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md) → [`dimension.home_store`](../definitions/dimension.home_store.md) · many_to_one

- cloud: `{to}.store_id = {from}.home_store_id`
- legacy: `{to}.STORE_ID = {from}.HOME_STORE_ID`

## Lineage

Uses: [`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md), [`dimension.home_store`](../definitions/dimension.home_store.md)

## Provenance

- method: harvest
- source: queries/marketing_pilot.bigquery.sql
- confirmed by sam.ortiz on 2026-10-10
- ❓ open question: Should closed accounts (status C) be excluded? They cannot receive campaigns — CRM team to confirm.
- ❓ open question: Should staff accounts be excluded, as in customer.active_buyer?

Source YAML: [`domains/marketing/relationships/rel.campaign_reachable__home_store.yaml`](../../../domains/marketing/relationships/rel.campaign_reachable__home_store.yaml)
· review by 2026-11-10
