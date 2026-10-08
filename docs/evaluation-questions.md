# Evaluation question set: 50 wealth-management prompts

**Created:** 2026-10-08. **Status:** reference answers computed live through the deployed MCP servers, then adversarially validated (section 5). All 50 of 50 questions were independently re-derived and agree with the answer key: 55 of 55 checks passed, run in the other engine or by a different method. 50 questions have agreeing SQL and DAX answers.

This set extends the roughly 30-question plan in [foundry-evaluation-plan.md](foundry-evaluation-plan.md). The prompts are worded as a business user would ask them. To answer, an agent must plan, run one or more DAX or SQL queries through its MCP server, and combine the results. Every question is answerable from both data paths:

- the semantic model, through `execute_dax_query`
- the lakehouse tables, through `execute_sql`

Row counts match across both paths. Column-level differences are listed in section 2, item 11; no question depends on them.

**Answer key:** [artifacts/evaluation/eval-questions.jsonl](../artifacts/evaluation/eval-questions.jsonl) and [answer-key.md](../artifacts/evaluation/answer-key.md). The `artifacts/` folder is gitignored, consistent with the plan's rule that answer keys stay out of the public repository. Each JSONL row contains:

- the question, tier, and plan
- the expected answer, as text and as structured values, with its tolerance
- the reference SQL and its result, and (where cross-checked) the reference DAX and its result
- a `validation` block listing every independent check and its outcome

Send agents the `question` field only.

## 1. Data profile (live, 2026-10-08)

| Table | Rows | Grain and cadence |
|---|---:|---|
| `dim_client` | 3,000 | Client. 4 segments, 6 client types, 5 risk profiles, 20 states; onboarded 2015-01-04 to 2024-06-30 |
| `dim_account` | 7,641 | Account. 12 account types; statuses Active, Closed, and Restricted. Only Active accounts appear in AUM, performance, and holdings |
| `dim_advisor_primary` / `_secondary` | 40 each | Advisor. The two tables are identical copies. Region, team, and title are populated only in the lakehouse (item 11) |
| `dim_security` | 400 | Security. 16 asset classes, 17 sectors |
| `dim_date` | 731 | Day, 2024-01-01 to 2025-12-31. Not marked as a date table (`DataCategory` = Regular) |
| `fact_aum_daily` | 531,440 | **Weekly** Friday snapshots: 104 dates × 5,110 accounts, 2024-01-05 to 2025-12-26. Unique per account and date |
| `fact_performance_daily` | 531,440 | Same weekly dates as AUM. excess = portfolio − benchmark; cumulative compounds the weekly returns from 2024-01-05 |
| `fact_holding_snapshot` | 980,760 | **Monthly** month-end snapshots: 24 dates × 40,865 positions, across 400 securities. unrealized = market value − cost basis; weights sum to 100% per account |
| `fact_transaction` | 1,200,000 | Transaction. Trades fall on 523 weekdays; 13 transaction types; signed USD amounts |
| `fact_lifecycle_event` | 20,000 | Event, daily |
| `fact_client_interaction` | 30,000 | Interaction. 5 channels; sentiment 0.9–10; every client has at least one |
| `fact_alert` | 15,000 | Alert. 15 types, 4 severities |
| `fact_recommendation` | 12,000 | Recommendation. 6 actions, 5 statuses |
| `fact_opportunity` | 8,000 | Opportunity. 15 types, 6 stages |

There are no nulls in any column the questions use, and no orphan keys on any of the 25 foreign-key paths checked.

## 2. Model behaviors the set deliberately exercises

These behaviors were verified live. They are why a semantic-model agent cannot simply call each measure without filters.

1. **AUM is a snapshot.** `is_active` is true only on the latest snapshot (2025-12-26), so `[Total Active AUM]` equals current AUM. With no date filter, `[Total AUM]` sums all 104 weekly snapshots (≈$2.21T). Q10, Q22, Q23, Q35, Q36, Q38–Q40, Q45, Q48, and Q50 depend on this.
2. **`[AUM Previous Year]` is blank on every one of the 104 snapshot dates.** `SAMEPERIODLASTYEAR` never lands on a Friday snapshot date. As a result:
   - `[AUM YoY Growth %]` returns 0.
   - `[AUM Growth %]` returns −100% for 2024.
   - `[AUM YTD]` equals current AUM for 2025 and is blank for 2024.

   Affects Q35 and Q50.
3. **No relationship from `fact_transaction` to `dim_date`.** `[Transaction Volume MTD]` and `[Transaction Volume QTD]`, and any transaction measure sliced by `dim_date`, return all-time totals; filtering to one month still returns every transaction. Filter `trade_date_key` directly. Affects Q25, Q26, Q43, Q49, and Q50.
4. **The five account-to-fact relationships are inactive.** Slicing by `dim_account` repeats the grand total unless the query uses `USERELATIONSHIP` or `TREATAS`. Affects Q22, Q47, Q48, and Q49.
5. **Period-axis YTD.** `dim_date` is not marked as a date table, so `[Interactions YTD]` with month or quarter on the axis returns only that period's count. Affects Q34.
6. **Calendar coverage.** `[New Clients (Period)]` is blank before 2024. Affects Q20.
7. **Measure scope:**
   - `[Weighted Pipeline]` includes Won and Lost stages, while `[Pipeline Value]` excludes them (Q13).
   - `[Critical Alerts]` includes resolved alerts (Q07, Q32).
   - `[Top 10 Concentration %]` and `[Unrealized Gain %]` span all 24 holdings snapshots unless date-filtered (Q27, Q28, Q37, Q42).
8. **`[Latest Cumulative Return]` returns a maximum, not a portfolio figure.** Ties on the latest date make it return the highest single-account value. Affects Q47.
9. **Advisor keys on facts identify the event owner.** Only 278 of 8,000 opportunities belong to the client's assigned advisor (215 primary, 63 secondary). Questions therefore ask about the advisor recorded on the opportunity or interaction (Q29, Q41).
10. **Known data issues the set avoids or pins down:**
    - **Invalid settlement dates.** 58,732 transactions have a `settle_date_key` that is not a real date (for example, 20240932). Settlement adds one or two to the integer key, so every invalid key falls after the 28th. 337,085 settlement dates fall on weekends.
    - **Holdings don't reconcile to AUM.** Holdings market value (≈$356M) is not reconciled to AUM (≈$22.4B).
    - **AUM net flows don't reconcile to transactions.** `fact_aum_daily.net_flow` does not match transaction-level contributions and withdrawals, so Q24, Q48, and Q50 name the AUM source explicitly.
    - **Amount is not quantity × price.** `amount` does not equal `quantity × price`.
    - **Probabilities aren't updated on close.** Opportunity probabilities are not updated when a deal closes; Won deals carry probabilities as low as 2%.
11. **The semantic model's content differs from the lakehouse in 39 of 171 columns.** None of these columns is used by a question:
    - **Advisor attributes are blank in the model.** In both advisor tables, eight attributes have values in the lakehouse but are blank in the model: region, team, title, hire date, UPN, manager email, and the CFP and CFA flags. Q41 was changed from advisor region to the team-lead flag for this reason.
    - **Client attributes are blank in the model.** `dim_client.client_age_band` and `client_tenure_years` are blank in the model.
    - **Performance returns are rounded.** The model rounds performance returns to 4 decimals, against 6 in the lakehouse. Q30 and Q47 differ in the fifth significant digit; the return tolerance absorbs this.
    - **Load metadata.** `load_date` is blank in the model, and `last_modified_date` has lower precision there.

## 3. Coverage

- **Complexity:** Tier 1: 10; Tier 2: 11; Tier 3: 13; Tier 4: 10; Tier 5: 6. Eighteen questions need three or more plan steps, and Tier 4–5 questions typically need two to six queries or a multi-stage query.
- **Data volume** (the **Vol.** column, by rows scanned):
  - S: under 10K rows (dimensions and the 8K-row opportunity fact). 13 questions.
  - M: 10K–100K rows. 13 questions.
  - L: one 531K–981K-row snapshot fact, or the 1.2M-row transaction fact alone. 19 questions.
  - XL: a large fact combined with another fact, or 1.2M transactions joined to dimensions. 5 questions.
- **Semantic-model calculations:** 47 questions reference at least one stored measure, and 45 of the model's 53 measures appear somewhere in the set. 27 questions exercise at least one behavior from section 2, where using a measure without the correct filter or relationship gives a plausible but wrong number.
- **Tolerances:** counts are exact; currency ±0.01% relative; ratios ±0.05 percentage points; returns ±0.5% relative. A Sell volume may be reported as a positive outflow.

## 4. Questions

### Tier 1 — Simple lookup (10)

| ID | Prompt | Vol. | Semantic-model measures | Plan | What it tests |
|---|---|---|---|---|---|
| Q01 | How many clients do we have? | S | `[Total Clients]` | 1) Count rows in the client dimension. | Baseline single-measure lookup. |
| Q02 | How many advisors are on the team, and how many of them are team leads? | S | `[Primary Advisor Count]` | 1) Count advisors. 2) Count advisors where advisor_is_team_lead = 1. | Two values from one small dimension; integer flag interpretation. |
| Q03 | How many High Net Worth and Ultra High Net Worth clients do we have? | S | `[HNW Client Count]`, `[UHNW Client Count]` | 1) Count clients with segment 'High Net Worth' and 'Ultra High Net Worth'. | Maps business terms (HNW/UHNW) to exact segment values or the named measures. |
| Q04 | How many securities are in our security master, and which asset class has the most securities? | S | — | 1) Count securities. 2) Group by asset_class and take the largest group. | Count plus a simple top-1 grouping. |
| Q05 | On average, how many accounts does each client have, counting every account on file regardless of status? | S | `[Accounts per Client]` | 1) Count all accounts and all clients; divide. | Ratio across two dimensions; reuse of Accounts per Client. The status qualifier matters: counting only Active accounts gives a much lower figure. |
| Q06 | How many accounts are Active, Closed, and Restricted? | S | — | 1) Group accounts by status and count. | Simple categorical breakdown. |
| Q07 | How many alerts are currently open, and how many of those open alerts are Critical? | M | `[Open Alerts]`, `[Critical Alerts]` | 1) Count alerts with is_resolved = 0. 2) Count alerts with is_resolved = 0 and severity = 'Critical'. | Combining two filters. Critical Alerts alone counts resolved and unresolved alerts (1,537), so it must be intersected with the open filter. |
| Q08 | How many recommendations are still pending (status Proposed)? | M | `[Pending Recommendations]` | 1) Count recommendations with status = 'Proposed'. | Direct measure reuse. |
| Q09 | How many transactions have we recorded in total, and what is the average transaction amount in absolute dollars across all transaction types? | L | `[Transaction Count]`, `[Avg Trade Size]` | 1) Count transactions. 2) Average ABS(amount) over all transactions. | Large-table aggregate. Amounts are signed (sells, fees, and outflows are negative), so the average needs ABS. 'All transaction types' matters: a Buy/Sell-only average is higher, and amount does not equal quantity × price in this data. |
| Q10 | What is our total AUM right now? | L | `[Total Active AUM]`, `[Total AUM]` | 1) Find the latest AUM snapshot date (MAX date_key = 2025-12-26). 2) Sum aum for that date only. | Snapshot semantics. AUM is a weekly snapshot (104 Fridays). Summing all rows (Total AUM with no date filter) returns ≈$2.21T. Total Active AUM is correct because is_active is only true on the latest snapshot. |

### Tier 2 — Filtered / grouped (11)

| ID | Prompt | Vol. | Semantic-model measures | Plan | What it tests |
|---|---|---|---|---|---|
| Q11 | What is our overall opportunity win rate (won ÷ (won + lost))? | S | `[Win Rate]` | 1) Count Won and Lost opportunities. 2) Compute Won / (Won + Lost). | Business-rule calculation; excludes open stages from the denominator. |
| Q12 | How much value is in the open pipeline (opportunities not yet Won or Lost), and how many open opportunities are there? | S | `[Pipeline Value]`, `[Open Opportunities]` | 1) Filter stage NOT IN (Won, Lost). 2) Sum estimated_value and count rows. | Stage-based filter; measure reuse. |
| Q13 | What is the probability-weighted value of the open pipeline (estimated value × probability, open opportunities only)? | S | `[Weighted Pipeline]` | 1) Filter stage NOT IN (Won, Lost). 2) Sum estimated_value × probability. | Measure gotcha: Weighted Pipeline sums every stage, including Won and Lost ($602,555,151.28). It must be filtered to open stages. |
| Q14 | Out of all advisor recommendations, including those still pending, what share were accepted or implemented? | M | `[Accepted Rate]` | 1) Count recommendations with status in (Accepted, Implemented). 2) Divide by all recommendations, including Proposed. | The definition includes two status values, and the denominator includes pending (Proposed) recommendations. Excluding pending recommendations gives a materially higher rate. |
| Q15 | What is the alert resolution rate for each severity level? Which severity has the highest and the lowest resolution rate? | M | `[Alert Resolution Rate]` | 1) Group alerts by severity. 2) Compute resolved / total for each group. 3) Rank the groups. | Grouped ratio; measure evaluated per group. |
| Q16 | What percentage of client interactions required a follow-up, by channel? Which channel has the highest follow-up rate? | M | `[Follow-Up Rate]` | 1) Group interactions by channel. 2) Compute follow_up_required = 1 share per channel. | Grouped ratio over 30K rows. |
| Q17 | Which interaction channel has the highest average client sentiment score, and what is that channel's average meeting duration? | M | `[Avg Sentiment Score]`, `[Avg Meeting Duration]` | 1) Average sentiment_score by channel and pick the max. 2) Report average duration_minutes for that channel. | Two measures in one grouped query; the top two channels are within 0.002 of each other. |
| Q18 | What are total Buy volume and total Sell volume across all transactions, and what is the buy/sell ratio? | L | `[Buy Volume]`, `[Sell Volume]`, `[Buy/Sell Ratio]` | 1) Sum amount for txn_type = 'Buy' and for 'Sell'. 2) Ratio = ABS(Buy) / ABS(Sell). | Sign convention: Sell amounts are negative. |
| Q19 | How many lifecycle events are still pending, and what is their total event amount? | M | `[Pending Events]`, `[Total Event Amount]` | 1) Filter status = 'Pending'. 2) Count rows and sum event_amount. | Applying a filter to a base measure (Total Event Amount is unfiltered). |
| Q20 | How many new clients did we onboard in 2024 compared with 2023? | S | `[New Clients (Period)]` | 1) Count clients by YEAR(onboard_date) for 2023 and 2024. 2) Compare. | Calendar-coverage gotcha: dim_date only covers 2024–2025, so New Clients (Period) is blank for 2023. Use onboard_date directly. 2024 onboarding stops at 2024-06-30. |
| Q21 | Which five states have the most clients, and how many clients are in each? | S | — | 1) Group clients by state. 2) Take the top 5 by count. | Top-N ranking; there is no tie at the cutoff. |

### Tier 3 — Multi-table / time-scoped (13)

| ID | Prompt | Vol. | Semantic-model measures | Plan | What it tests |
|---|---|---|---|---|---|
| Q22 | What is AUM by account type as of the latest snapshot? Which account type holds the most and the least AUM? | L | `[Total Active AUM]` | 1) Restrict to the latest snapshot (2025-12-26). 2) Join AUM to dim_account on account_key (in DAX, USERELATIONSHIP or TREATAS). 3) Sum aum by account_type. | Inactive relationship: fact_aum_daily→dim_account is inactive. Slicing by account_type without activating it repeats $22.44B on every row. |
| Q23 | Break down current AUM (latest snapshot) by client segment. | L | `[Total Active AUM]` | 1) Restrict to the latest snapshot. 2) Sum aum by dim_client.segment (active relationship). | Snapshot plus an active dimension relationship. |
| Q24 | What were total AUM net flows (the net flow reported with each weekly AUM snapshot) in 2025 versus 2024? | L | `[Net Flows]` | 1) Sum fact_aum_daily.net_flow by calendar year. | Flow (additive over time), unlike AUM balances. The AUM net flow does not reconcile with transaction-level contributions and withdrawals, so the question names the source. |
| Q25 | How many transactions were traded in December 2025, and what were the total Buy and Sell volumes that month? | L | `[Transaction Count]`, `[Buy Volume]`, `[Sell Volume]`, `[Transaction Volume MTD]` | 1) Filter trade_date_key between 20251201 and 20251231 (in DAX, directly or with TREATAS from dim_date). 2) Count rows; sum amount for Buy and for Sell. | No transaction-date relationship: dim_date filters do not reach fact_transaction, so slicing by month returns all-time totals, and Transaction Volume MTD returns the all-time total for any date. Filter trade_date_key directly. |
| Q26 | How much dividend and interest income did clients receive in 2025 (by trade date)? | L | `[Total Transaction Volume]` | 1) Filter txn_type in (Dividend, Interest) and trade_date_key in 2025. 2) Sum amount by type, then total. | Date filter on a fact with no date relationship, plus a type filter. |
| Q27 | What are the top 5 securities by total market value in the most recent holdings snapshot? | L | `[Total Market Value]` | 1) Find the latest holdings date (2025-12-31; monthly snapshots). 2) Sum market_value by security; top 5. | Monthly snapshot (different cadence from weekly AUM) plus top-N over a 981K-row fact. |
| Q28 | What is the unrealized gain percentage (unrealized gain ÷ cost basis) by asset class in the latest holdings snapshot? Which asset class is highest and lowest? | L | `[Unrealized Gain %]`, `[Unrealized Gain/Loss]`, `[Total Cost Basis]` | 1) Restrict to 2025-12-31. 2) By asset_class, divide SUM(unrealized_gain_loss) by SUM(cost_basis). 3) Rank. | Ratio of sums (not an average of row ratios) under a snapshot filter. |
| Q29 | Which advisor owns the largest open pipeline (the advisor recorded on each opportunity), how large is it, and what is that advisor's win rate? | S | `[Pipeline Value]`, `[Win Rate]` | 1) Sum open estimated_value by owning advisor (opportunity advisor_key). 2) Take the top advisor. 3) Compute that advisor's Won / (Won + Lost). | Two measures evaluated for one ranked entity. The opportunity advisor_key is the owner, not the client's assigned advisor; attributing through dim_client.advisor_key_primary gives a different leader. |
| Q30 | What were the simple (equal-weighted) average weekly portfolio return, benchmark return, and excess return across all accounts in 2025? | L | `[Avg Portfolio Return]`, `[Avg Benchmark Return]`, `[Avg Excess Return]` | 1) Filter performance rows to 2025 (265,720 weekly observations). 2) Average each return column. | Year filter over a 531K-row fact. Simple averages, not compounded or AUM-weighted returns. The semantic model stores returns rounded to 4 decimals, so the two data paths differ in the fifth significant digit. |
| Q31 | How many client interactions happened in Q4 2025, and how many unique clients did we contact? | M | `[Interaction Count]`, `[Unique Clients Contacted]` | 1) Filter interactions to 2025-10-01 through 2025-12-31. 2) Count rows and distinct client_key. | Quarter filter plus distinct count. |
| Q32 | Which five alert types have the most unresolved Critical alerts? | M | `[Critical Alerts]`, `[Open Alerts]` | 1) Filter severity = 'Critical' and is_resolved = 0. 2) Count by alert_type; top 5. | Tie handling: three types share third place, so a strict top 3 would be ambiguous; top 5 is not. |
| Q33 | How many Bond Maturity and CD Maturity events dated in Q1 2025 are still pending, and what is their total amount? | M | `[Pending Events]`, `[Total Event Amount]` | 1) Filter event_type in (Bond Maturity, CD Maturity), status = 'Pending', and date in Q1 2025. 2) Count and sum by type, then total. | Three simultaneous filters. |
| Q34 | Show the cumulative year-to-date number of client interactions at the end of each quarter of 2025. | M | `[Interactions YTD]`, `[Interaction Count]` | 1) Count interactions per quarter of 2025. 2) Accumulate quarter by quarter (or evaluate YTD at each quarter-end date). | Time-intelligence gotcha: dim_date does not behave as a marked date table, so Interactions YTD with quarter or month on the axis returns only that period's count. Evaluate it at each quarter-end full_date, or accumulate manually. |

### Tier 4 — Multi-step / combined (10)

| ID | Prompt | Vol. | Semantic-model measures | Plan | What it tests |
|---|---|---|---|---|---|
| Q35 | How much did total AUM grow year over year, comparing the last snapshot of 2025 with the last snapshot of 2024? | L | `[Total AUM]`, `[AUM YoY Growth %]`, `[AUM Previous Year]` | 1) Find the last snapshot date in each year (2025-12-26 and 2024-12-27). 2) Sum AUM at each date. 3) Compute the difference and percent change. | Broken measure: AUM Previous Year shifts to the same calendar date one year earlier, which never lands on a Friday snapshot (for example, 2024-12-26 is a Thursday). It is always blank, so AUM YoY Growth % returns 0. Compute from the two snapshots. |
| Q36 | Using the last AUM snapshot of each month, which month in 2025 had the largest month-over-month AUM increase, and by how much? | L | `[Total AUM]` | 1) For Dec 2024–Dec 2025, find each month's last snapshot date. 2) Sum AUM at each of those dates. 3) Compute month-over-month differences and take the max. | Month-end snapshot selection (Fridays, not calendar month-end) plus a window calculation. Summing every snapshot in a month inflates months with five Fridays. |
| Q37 | What share of total holdings market value is concentrated in the top 10 securities, as of the most recent holdings snapshot? | L | `[Top 10 Concentration %]`, `[Total Market Value]` | 1) Restrict to 2025-12-31. 2) Sum market_value by security and take the top 10. 3) Divide by total market value. | Snapshot gotcha: without a date filter, Top 10 Concentration % ranks across all 24 snapshots and returns 3.79%. |
| Q38 | As of the latest AUM snapshot, which client segment has the highest average AUM per account, counting only accounts that hold AUM on that date? | L | `[Avg AUM per Account]` | 1) Restrict to the latest snapshot. 2) By segment, divide AUM by distinct accounts in the snapshot. 3) Rank. | Per-entity average (AVERAGEX over accounts) rather than an average of rows. Dividing by every account on file, including closed ones, gives different values. |
| Q39 | Who are our top 10 clients by AUM at the latest snapshot? Show each client's segment, number of accounts on file, and AUM. | L | `[Total Active AUM]` | 1) Sum latest-snapshot AUM by client; top 10. 2) Look up segment from dim_client. 3) Count accounts per client in dim_account (includes closed and restricted accounts). | Top-N, then enrichment from two dimensions (a second query or a nested lookup). |
| Q40 | Compare High Net Worth and Ultra High Net Worth clients: AUM per client at the latest snapshot, interactions per client in 2025, and average 2025 sentiment. Divide by all clients in each segment, including those with no AUM balance. | XL | `[Total Active AUM]`, `[HNW Client Count]`, `[UHNW Client Count]`, `[Interaction Count]`, `[Avg Sentiment Score]` | 1) Count all clients per segment. 2) Sum latest-snapshot AUM per segment and divide by clients. 3) Count 2025 interactions per segment and divide by clients. 4) Average 2025 sentiment per segment. 5) Combine into one comparison. | Combining three facts at one segment grain. 370 clients have no AUM on the latest snapshot, so the denominator has to be stated. |
| Q41 | Among advisors who are team leads, who are the top 3 by open pipeline value? Include each one's win rate and total number of client interactions. | M | `[Pipeline Value]`, `[Win Rate]`, `[Interaction Count]` | 1) Filter advisors to advisor_is_team_lead = 1 (10 advisors). 2) Rank by open pipeline value; top 3. 3) Add each advisor's win rate and interaction count. | Dimension filter propagated to two different facts via advisor_key. Advisor region, team, and title are blank in the semantic model, so the question uses the team-lead flag, which both data paths carry. |
| Q42 | Which asset class had the largest increase, and which the largest decrease, in total holdings market value between the first (Jan 2024) and last (Dec 2025) monthly holdings snapshots? | L | `[Total Market Value]` | 1) Find the first (2024-01-31) and last (2025-12-31) snapshot dates. 2) Sum market value by asset class at each date. 3) Difference and rank. | Two snapshot evaluations combined into a change calculation. |
| Q43 | What was the average Buy trade size (absolute amount) in 2025 for each client segment? | XL | `[Avg Trade Size]` | 1) Filter txn_type = 'Buy' and trade_date_key in 2025 (no date relationship). 2) Average ABS(amount) by client segment. | Large fact with a type filter, a direct date-key filter, and a dimension grouping. |
| Q44 | Which opportunity type has the highest win rate and which has the lowest? | S | `[Win Rate]` | 1) Compute Won / (Won + Lost) per opportunity_type (15 types). 2) Pick max and min. | Measure evaluated per group, then ranked. |

### Tier 5 — Advanced analytical (6)

| ID | Prompt | Vol. | Semantic-model measures | Plan | What it tests |
|---|---|---|---|---|---|
| Q45 | How many clients with an Aggressive risk profile have at least one unresolved Critical alert, how many such alerts do they have, and what is their combined AUM at the latest snapshot? | XL | `[Critical Alerts]`, `[Open Alerts]`, `[Total Active AUM]` | 1) Find Aggressive clients with any open Critical alert. 2) Count those clients and their open Critical alerts. 3) Sum their latest-snapshot AUM. | Set-based filter (semi-join) from one fact applied to another fact. |
| Q46 | Do more engaged clients convert better? Group clients by their total number of interactions on record (7 or fewer, 8–12, 13 or more) and report the number of clients and the opportunity win rate in each group. | M | `[Interaction Count]`, `[Win Rate]` | 1) Count interactions per client. 2) Assign each client to a bucket. 3) Count Won and Lost opportunities per bucket. 4) Compute win rate per bucket and compare. | Client-level derived attribute used to segment a different fact (dynamic segmentation). |
| Q47 | By account type, what was the average weekly excess return in 2025, and what is the average cumulative return across accounts as of the latest performance date? Which account type leads on each? | L | `[Avg Excess Return]`, `[Latest Cumulative Return]` | 1) Join performance to dim_account (inactive relationship). 2) Average excess_return for 2025 by account_type. 3) Average cumulative_return on 2025-12-26 by account_type. 4) Identify the leaders. | Inactive relationship plus a measure gotcha: Latest Cumulative Return returns the maximum single-account value on the latest date (for example, 1.6405 for Endowment), not an average. |
| Q48 | Which 5 accounts had the largest absolute AUM net flows in 2025 (the net flow reported with the weekly AUM snapshots)? Show account type, client segment, 2025 net flow, and the account's AUM at the latest snapshot. | L | `[Net Flows]`, `[Total Active AUM]` | 1) Sum 2025 net_flow per account; rank by absolute value; top 5. 2) Look up account type (inactive relationship) and client segment. 3) Look up each account's latest-snapshot AUM. | Ranking by absolute value (mixed signs), then multi-dimension enrichment. |
| Q49 | For 2025 (by trade date), what was the net money movement by account type from contributions, wires in and transfers in, minus distributions, wires out and transfers out? Which account type had the largest and smallest net inflow? | XL | `[Total Transaction Volume]` | 1) Filter the six named txn_types and 2025 trade dates. 2) Join to dim_account (inactive relationship). 3) Sum signed amount by account_type; rank. | Explicit business definition over six transaction types, an inactive relationship, a date filter with no relationship, and signed amounts. Excluding transfers changes the ranking. |
| Q50 | Give me a 2025 executive summary: current AUM, year-over-year AUM growth versus year-end 2024, 2025 AUM net flows, number of transactions traded in 2025, open pipeline value, overall win rate, and number of unresolved Critical alerts. | XL | `[Total Active AUM]`, `[Net Flows]`, `[Transaction Count]`, `[Pipeline Value]`, `[Win Rate]`, `[Critical Alerts]`, `[Open Alerts]` | 1) Latest-snapshot AUM and YoY versus 2024-12-27 (not the broken YoY measure). 2) 2025 net flows. 3) 2025 transaction count by trade_date_key. 4) Open pipeline value and win rate. 5) Unresolved Critical alerts. 6) Assemble into one summary. | Many independent queries combined. Combines the snapshot, YoY, no-date-relationship, and filter-on-measure gotchas. |

## 5. Adversarial validation (2026-10-08)

| Check | Method | Result |
|---|---|---|
| Live model vs catalog | `INFO.VIEW.MEASURES`, `INFO.VIEW.RELATIONSHIPS`, and `INFO.VIEW.TABLES` against the author catalog | All 53 measure expressions are identical. The 33 relationships match (28 active, 5 inactive), with no `fact_transaction`→`dim_date` relationship. `dim_date` is a Regular table, not a marked date table |
| Data fidelity | Row counts and sums of every numeric fact column, in both engines | All match except performance returns, which the model rounds to 4 decimals |
| Column fidelity | Blank and distinct counts for all 171 columns, in both engines | 39 columns differ (section 2, item 11); none is used by a question |
| Integrity | Nulls, orphan keys on 25 paths, snapshot-grain uniqueness, cadence, and accounting identities | Clean. Every cadence claim and identity in section 1 holds |
| Re-derivation | 55 independent checks covering all 50 questions. Each used the other engine or a different method: dim_date joins instead of key ranges, TREATAS instead of USERELATIONSHIP, the secondary advisor dimension, raw iterators instead of measures, and window functions instead of TOP | 55/55 agree within tolerance |
| Ranking robustness | Gap between the last included and first excluded item, for every top-N, highest, or lowest question | Every cutoff is strictly separated. Q32 has a three-way tie inside the top 5, but none at the cutoff |
| Ambiguity probes | Each plausible alternative reading of a question was computed | 14 questions were reworded, listed below |

**Questions changed by the validation:**

- **Q05:** Counting only Active accounts gives a much lower ratio. Now specifies "every account on file regardless of status".
- **Q09:** "Average trade size" had three readings: all types, Buy/Sell only, or quantity × price. Now asks for the average transaction amount across all types.
- **Q14:** Excluding pending recommendations from the denominator gives a materially higher rate. Now specifies that pending recommendations are included.
- **Q24, Q48, Q50:** AUM net flows and transaction-based flows differ by more than an order of magnitude. Now name the AUM net flow.
- **Q25:** The "net dollar amount" of all transaction types mixes buys, sells, fees, and income, so it has no business meaning. Now asks for December Buy and Sell volumes.
- **Q29:** Attributing opportunities through the client's assigned advisor gives a different leader. Now names the advisor recorded on the opportunity.
- **Q30:** Now says simple (equal-weighted) average.
- **Q38:** Dividing by all accounts on file changes the values. Now counts only accounts that hold AUM.
- **Q40:** 370 clients have no AUM. Now states the denominator: all clients in the segment.
- **Q41:** Advisor region is blank in the semantic model, so the question was unanswerable through DAX. Replaced with the team-lead filter.
- **Q46:** Now says "on record", to fix the time window.
- **Q49:** Removed "external", because transfers may be internal. The six types are listed explicitly.
