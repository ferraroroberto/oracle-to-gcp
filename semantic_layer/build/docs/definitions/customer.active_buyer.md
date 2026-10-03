# Active customer (bought in the last 12 months)

`customer.active_buyer` · entity · **certified** · v1 · owner **core_analytics** · steward **avery.lane**

Open account (status A) that is not a staff account and completed at least one purchase in the 12 months up to the snapshot date. Pending accounts (P) cannot buy yet and closed accounts (C) are excluded.

**Synonyms:** customer, client, active customer, active client, buyer

**Grain:** one row per customer per month-end snapshot

## Not to be confused with

- [`customer.any_account`](../definitions/customer.any_account.md) — every open or pending registered account, whether or not it ever bought
- [`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md) — marketing's 6-month window, with no account-status or staff filter

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| legacy | [DWH.CUSTOMER_MONTHLY](../tables/legacy/DWH.CUSTOMER_MONTHLY.md) | CUSTOMER_ID | `{t}.ACCOUNT_STATUS = 'A' AND {t}.IS_STAFF = 0 AND {t}.LAST_PURCHASE_DATE >= ADD_MONTHS({t}.SNAPSHOT_DATE, -12)` | legacy |
| cloud (preferred) | [analytics.customer_monthly](../tables/cloud/analytics.customer_monthly.md) | customer_id | `{t}.account_status = 'A' AND {t}.is_staff = FALSE AND {t}.last_purchase_date >= DATE_SUB({t}.snapshot_date, INTERVAL 12 MONTH)` | active |

## Caveats

- Snapshot table: always pin one snapshot_date; summing across months counts each customer up to 12 times.
- The 12-month window is the leadership-approved definition, shared with finance.

## Lineage

Used by: [`anchor.monthly_pack.active_customers`](../definitions/anchor.monthly_pack.active_customers.md), [`metric.customer_count`](../definitions/metric.customer_count.md), [`rel.active_buyer__first_purchase_store`](../definitions/rel.active_buyer__first_purchase_store.md), [`rel.active_buyer__home_store`](../definitions/rel.active_buyer__home_store.md), [`rel.active_buyer__join_month`](../definitions/rel.active_buyer__join_month.md), [`rel.active_buyer__signup_month`](../definitions/rel.active_buyer__signup_month.md), [`tpl.active_customers_by_store_and_join_month`](../definitions/tpl.active_customers_by_store_and_join_month.md)

## Provenance

- method: harvest
- source: queries/core_analytics_pilot.oracle.sql
- confirmed by avery.lane on 2026-10-03

Source YAML: [`domains/core_analytics/entities/customer.active_buyer.yaml`](../../../domains/core_analytics/entities/customer.active_buyer.yaml)
· review by 2027-04-03
