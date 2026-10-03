# Join month of each registered account

`rel.any_account__join_month` · relationship · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

## Join

[`customer.any_account`](../definitions/customer.any_account.md) → [`dimension.join_month`](../definitions/dimension.join_month.md) · many_to_one

- cloud: `{to}.customer_id = {from}.customer_id`
- legacy: `{to}.CUSTOMER_ID = {from}.CUSTOMER_ID`

## Lineage

Uses: [`customer.any_account`](../definitions/customer.any_account.md), [`dimension.join_month`](../definitions/dimension.join_month.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/relationships/rel.any_account__join_month.yaml`](../../../domains/core_analytics/relationships/rel.any_account__join_month.yaml)
· review by 2027-04-03
