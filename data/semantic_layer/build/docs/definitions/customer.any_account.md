# Registered account (open or pending)

`customer.any_account` · entity · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

Every registered account that is open (A) or pending verification (P), whether or not it has ever bought. The onboarding team's population; current state, not a monthly snapshot.

**Synonyms:** customer, client, account, registered account, registered customer

**Grain:** one row per customer account (current state)

## Not to be confused with

- [`customer.active_buyer`](../definitions/customer.active_buyer.md) — only open, non-staff accounts that bought in the last 12 months (management pack)
- [`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md) — marketing's 6-month buyers from the monthly snapshot

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| cloud (preferred) | [analytics.customers](../tables/cloud/analytics.customers.md) | customer_id | `{t}.account_status IN ('A', 'P')` | active |
| legacy | [DWH.CUSTOMERS](../tables/legacy/DWH.CUSTOMERS.md) | CUSTOMER_ID | `{t}.ACCOUNT_STATUS IN ('A', 'P')` | legacy |

## Caveats

- Includes staff accounts; filter them out if the question is about real customers.

## Lineage

Used by: [`metric.customer_count`](../definitions/metric.customer_count.md), [`rel.any_account__home_store`](../definitions/rel.any_account__home_store.md), [`rel.any_account__join_month`](../definitions/rel.any_account__join_month.md), [`rel.any_account__signup_month`](../definitions/rel.any_account__signup_month.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/entities/customer.any_account.yaml`](../../../domains/core_analytics/entities/customer.any_account.yaml)
· review by 2027-04-03
