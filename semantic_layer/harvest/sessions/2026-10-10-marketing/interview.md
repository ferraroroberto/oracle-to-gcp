# Harvest interview — marketing pilot query (second team)

> **Mock session.** Fictitious people and data. The second pilot team harvested its own query using only the README and the `semantic-harvest` skill; the core team watched but did not drive. The structured result is `answers.yaml`.

- **Query:** `queries/marketing_pilot.bigquery.sql` (cloud warehouse, BigQuery dialect)
- **Interviewee:** sam.ortiz (marketing analytics)
- **Date:** 2026-10-10, about 25 minutes

---

**Agent:** Before anything else: your query counts customers on `analytics.customer_monthly`, and the certified definition on that table, `customer.active_buyer` (owner: core-analytics, steward avery.lane), filters differently. You use a 6-month purchase window. The certified one uses 12 months, requires an open account (status A) and excludes staff accounts. Is yours a different concept, or should it use the certified one?

**Sam:** Different concept. For campaigns we want people who bought recently, and six months is our campaign rule. We're not trying to match the management pack.

**Agent:** Then it becomes its own definition. Two follow-ups, because the certified one excludes these on purpose:
1. Your query keeps closed accounts (8% of rows) and staff accounts (2%). Can a closed account receive a campaign?
2. You LEFT JOIN `loyalty_members` but never use it. Leftover, or a missing filter?

**Sam:** Hm. Closed accounts can't be contacted, so including them is probably a bug. I need to check with the CRM team whether the campaign tool drops them anyway. Staff, honestly, we never thought about. The loyalty join is a leftover from when we split the count by tier. It can go.

**Agent:** I'll record both as open questions on the draft, so it stays *reviewed*, not *certified*, until you confirm. One row of output is a region. Which published figure should it match?

**Sam:** None. It feeds the campaign planner; nothing is published.

**Agent:** Last ones: who's the steward, and what do people change?

**Sam:** Me. People change the month.

**Agent:** Summary: a new entity, `customer.campaign_reachable` (6-month buyers, no status or staff filter, with two open questions). It reuses the certified *home store* dimension and the certified customer count, and your query becomes a template with the period as a parameter. The difference from the management pack's definition will appear in the disagreement report with the size of the gap.
