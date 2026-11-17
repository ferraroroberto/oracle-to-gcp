# Join month (first purchase)

`dimension.join_month` · dimension · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

Month of the customer's first completed purchase. "Joined" means first purchase, not signup.

**Synonyms:** join month, month joined, month they joined, cohort month, cohort

## Not to be confused with

- [`dimension.signup_month`](../definitions/dimension.signup_month.md) — month the account was registered, which may be long before any purchase

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| legacy | [DWH.CUSTOMERS](../tables/legacy/DWH.CUSTOMERS.md) | CUSTOMER_ID | `TRUNC({t}.FIRST_PURCHASE_DATE, 'MONTH')` |  |
| cloud | [analytics.customers](../tables/cloud/analytics.customers.md) | customer_id | `DATE_TRUNC({t}.first_purchase_date, MONTH)` | active |

## Lineage

Used by: [`rel.active_buyer__join_month`](../definitions/rel.active_buyer__join_month.md), [`rel.any_account__join_month`](../definitions/rel.any_account__join_month.md), [`tpl.active_customers_by_store_and_join_month`](../definitions/tpl.active_customers_by_store_and_join_month.md)

## Provenance

- method: harvest
- source: queries/core_analytics_pilot.oracle.sql
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/dimensions/dimension.join_month.yaml`](../../../domains/core_analytics/dimensions/dimension.join_month.yaml)
· review by 2027-04-03
