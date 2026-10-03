# Loyalty programme member

`customer.loyalty_member` · entity · **certified** · v1 · owner **marketing** · steward **sam.ortiz**

Customer enrolled in the loyalty programme, at any tier, regardless of recent purchases.

**Synonyms:** customer, loyalty member, member, programme member

**Grain:** one row per enrolled customer (current state)

## Not to be confused with

- [`customer.active_buyer`](../definitions/customer.active_buyer.md) — 12-month buyers with open accounts; membership is not required
- [`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md) — 6-month buyers; membership is not required
- [`customer.any_account`](../definitions/customer.any_account.md) — every open or pending account, enrolled or not

## Bindings

| platform | table | key | filter / expression | status |
|---|---|---|---|---|
| cloud (preferred) | [analytics.loyalty_members](../tables/cloud/analytics.loyalty_members.md) | customer_id | `` | active |
| legacy | [DWH.LOYALTY_MEMBERS](../tables/legacy/DWH.LOYALTY_MEMBERS.md) | CUSTOMER_ID | `` | legacy |

## Lineage

Used by: [`metric.customer_count`](../definitions/metric.customer_count.md)

## Provenance

- method: contribute
- source: conversation
- confirmed by sam.ortiz on 2026-10-10

Source YAML: [`domains/marketing/entities/customer.loyalty_member.yaml`](../../../domains/marketing/entities/customer.loyalty_member.yaml)
· review by 2027-04-10
