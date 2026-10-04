# Signup month of each active buyer

`rel.active_buyer__signup_month` · relationship · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

## Join

[`customer.active_buyer`](../definitions/customer.active_buyer.md) → [`dimension.signup_month`](../definitions/dimension.signup_month.md) · many_to_one

- cloud: `{to}.customer_id = {from}.customer_id`
- legacy: `{to}.CUSTOMER_ID = {from}.CUSTOMER_ID`

## Lineage

Uses: [`customer.active_buyer`](../definitions/customer.active_buyer.md), [`dimension.signup_month`](../definitions/dimension.signup_month.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/relationships/rel.active_buyer__signup_month.yaml`](../../../domains/core_analytics/relationships/rel.active_buyer__signup_month.yaml)
· review by 2027-04-03
