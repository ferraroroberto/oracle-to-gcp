# Campaign-reachable customer (bought in the last 6 months)

`customer.campaign_reachable` · entity · **reviewed** · v1 · owner **marketing** · steward **sam.ortiz**

Customer with a completed purchase in the 6 months up to the snapshot date. Marketing's campaign rule. No account-status or staff filter (see open questions).

**Synonyms:** customer, client, reachable customer, campaign audience, recent buyer

**Grain:** one row per customer per month-end snapshot

## Not to be confused with

- [`customer.active_buyer`](../definitions/customer.active_buyer.md) — management-pack definition — 12 months, open accounts only, staff excluded
- [`customer.any_account`](../definitions/customer.any_account.md) — every open or pending account, whether or not it ever bought

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| cloud (preferred) | [analytics.customer_monthly](../tables/cloud/analytics.customer_monthly.md) | customer_id | `{t}.last_purchase_date >= DATE_SUB({t}.snapshot_date, INTERVAL '6' MONTH)` | active |
| legacy | [DWH.CUSTOMER_MONTHLY](../tables/legacy/DWH.CUSTOMER_MONTHLY.md) | CUSTOMER_ID | `{t}.LAST_PURCHASE_DATE >= ADD_MONTHS({t}.SNAPSHOT_DATE, -6)` | legacy |

## Caveats

- Includes closed and staff accounts — likely over-counts the reachable audience (open question).

## Lineage

Used by: [`metric.customer_count`](../definitions/metric.customer_count.md), [`rel.campaign_reachable__home_store`](../definitions/rel.campaign_reachable__home_store.md), [`tpl.reachable_customers_by_region`](../definitions/tpl.reachable_customers_by_region.md)

## Provenance

- method: harvest
- source: queries/marketing_pilot.bigquery.sql
- confirmed by sam.ortiz on 2026-10-10
- ❓ open question: Should closed accounts (status C) be excluded? They cannot receive campaigns — CRM team to confirm.
- ❓ open question: Should staff accounts be excluded, as in customer.active_buyer?

Source YAML: [`domains/marketing/entities/customer.campaign_reachable.yaml`](../../../domains/marketing/entities/customer.campaign_reachable.yaml)
· review by 2026-11-10
