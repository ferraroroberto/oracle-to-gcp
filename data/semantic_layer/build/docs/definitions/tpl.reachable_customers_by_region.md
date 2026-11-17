# Campaign-reachable customers by region

`tpl.reachable_customers_by_region` · template · **reviewed** · v1 · owner **marketing** · steward **sam.ortiz**

Marketing's campaign-sizing query, parameterised by snapshot month.

## Lineage

Uses: [`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md), [`dimension.home_store`](../definitions/dimension.home_store.md), [`metric.customer_count`](../definitions/metric.customer_count.md)

## Provenance

- method: harvest
- source: queries/marketing_pilot.bigquery.sql
- confirmed by sam.ortiz on 2026-10-10
- ❓ open question: Should closed accounts (status C) be excluded? They cannot receive campaigns — CRM team to confirm.
- ❓ open question: Should staff accounts be excluded, as in customer.active_buyer?

Source YAML: [`domains/marketing/templates/tpl.reachable_customers_by_region.yaml`](../../../domains/marketing/templates/tpl.reachable_customers_by_region.yaml)
· review by 2026-11-10
