# Signup month

`dimension.signup_month` · dimension · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

Month the account was registered. Often long before the first purchase.

**Synonyms:** signup month, registration month, month joined, month they joined

## Not to be confused with

- [`dimension.join_month`](../definitions/dimension.join_month.md) — month of the first completed purchase ("joined" in the management pack)

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| cloud (preferred) | [analytics.customers](../tables/cloud/analytics.customers.md) | customer_id | `DATE_TRUNC({t}.signup_date, MONTH)` | active |
| legacy | [DWH.CUSTOMERS](../tables/legacy/DWH.CUSTOMERS.md) | CUSTOMER_ID | `TRUNC({t}.SIGNUP_DATE, 'MONTH')` | legacy |

## Lineage

Used by: [`rel.active_buyer__signup_month`](../definitions/rel.active_buyer__signup_month.md), [`rel.any_account__signup_month`](../definitions/rel.any_account__signup_month.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/dimensions/dimension.signup_month.yaml`](../../../domains/core_analytics/dimensions/dimension.signup_month.yaml)
· review by 2027-04-03
