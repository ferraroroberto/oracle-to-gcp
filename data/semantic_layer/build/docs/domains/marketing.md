# Domain: marketing

```mermaid
flowchart LR
  n3["customer.campaign_reachable"]
  n6["dimension.home_store"]
  n3 -->|many_to_one| n6
```

| definition | kind | status |
|---|---|---|
| [`customer.campaign_reachable`](../definitions/customer.campaign_reachable.md) | entity | reviewed |
| [`customer.loyalty_member`](../definitions/customer.loyalty_member.md) | entity | certified |
| [`rel.campaign_reachable__home_store`](../definitions/rel.campaign_reachable__home_store.md) | relationship | reviewed |
| [`tpl.reachable_customers_by_region`](../definitions/tpl.reachable_customers_by_region.md) | template | reviewed |
