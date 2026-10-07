# Wealth Management Semantic Model Assessment

**Inspected:** 2026-09-11. **Status:** offline metadata assessment, not a live query or security test.

## 1. Evidence and scope

Source: [../sm_wealth_mgmt_import.pbix](../sm_wealth_mgmt_import.pbix).

- Size: **163,996,355 bytes**.
- SHA-256: `e2ebaed09bde67982c1c2de368fcdd02f4ecdb7ce880bef35b684514111ee58a`.
- PBIX archive format version: `1.32`; includes an embedded DataModel and modern report-definition parts.
- Read with PBIXRay `0.15.5` in an isolated Python `3.11.15` x64 virtual environment.
- No business rows decoded, DAX executed, uploads performed, or original model/report changes made.
- The selected metadata properties extracted without reported errors. PBIXRay is a reverse-engineered third-party reader, not a Microsoft-supported export or query engine. Confirm security, expressions, and relationship semantics with the live model/TOM before release.
- Native metadata is not a complete round-trip model backup: connection strings, raw M, role memberships, and some native properties are intentionally excluded from these planning artifacts.

**Adversarial-review qualification (2026-09-11):** complete JSON parsing reconfirmed the inventory, all 53 nonblank DAX definitions, all 33 resolvable relationship endpoints, the 28/5 activity split and one extracted RLS filter record. “No extraction errors” means selected reader calls succeeded, not that every native property was extracted. The full role inventory, relationship security-filter behavior, and measure visibility/type/format/lineage remain unavailable from this selected output. The report walker finds direct references but does not resolve all source aliases, hierarchy-level bindings, report-level filters or textbox content; it cannot certify whole-report validity. No additional business rows or live queries were read during review.

Reproducible reader: [../scripts/inspect_pbix.py](../scripts/inspect_pbix.py).

Private, git-ignored evidence:

- [../artifacts/pbix-inspection/inspection-summary.json](../artifacts/pbix-inspection/inspection-summary.json)
- [../artifacts/pbix-inspection/model-metadata.json](../artifacts/pbix-inspection/model-metadata.json)
- [../artifacts/pbix-inspection/report-metadata.json](../artifacts/pbix-inspection/report-metadata.json)

## 2. Logical model inventory

**15 tables, 171 columns, 53 DAX measures, 33 relationships.**

| Table | Columns | Measures | Candidate business grain—not a verified unique key |
|---|---:|---:|---|
| `dim_account` | 10 | 0 | Account |
| `dim_advisor_primary` | 15 | 1 | Advisor in primary role |
| `dim_advisor_secondary` | 15 | 1 | Advisor in secondary role |
| `dim_client` | 16 | 5 | Client |
| `dim_date` | 16 | 0 | Calendar date |
| `dim_security` | 7 | 0 | Security |
| `fact_alert` | 10 | 4 | Alert |
| `fact_aum_daily` | 8 | 11 | Account/date AUM snapshot |
| `fact_client_interaction` | 11 | 5 | Client interaction |
| `fact_holding_snapshot` | 11 | 6 | Account/security/date holding snapshot |
| `fact_lifecycle_event` | 10 | 3 | Lifecycle event |
| `fact_opportunity` | 10 | 4 | Opportunity; historical snapshot behavior needs confirmation |
| `fact_performance_daily` | 9 | 4 | Account/date performance observation |
| `fact_recommendation` | 10 | 3 | Recommendation |
| `fact_transaction` | 13 | 6 | Transaction |

Additional observations:

- All 33 relationships are reported as many-to-one, single-direction: **28 active, 5 inactive**.
- Logical partitions: 15, with native mode value `0` (Import).
- The reader found no DAX calculated columns/tables, explicit hierarchies, calculation groups/items, or OLS definitions in the inspected surfaces.
- All 15 logical tables and 171 logical columns are reported visible. This is not a permission guarantee.
- No nonempty model, table, column, or measure descriptions were found.
- No logical column has `IsKey` or `IsUnique` set in this extraction. Do not auto-declare OSSIE primary keys from a `_key` suffix.
- Monetary fields such as `aum`, `market_value`, and `amount` are floating-point in the model; do not relabel them exact `Decimal` merely because they represent money.
- `is_active` is Boolean; several other flag fields are Integer. `load_date` is String, not a verified temporal type.
- Raw metadata also contained **360 internal storage columns and 204 internal partitions**. They were excluded by membership in the logical table inventory, not treated as extra business schema.

M-navigation lineage points to `wm`, `silver`, and `gold` schema objects. This is provenance, not permission to bypass Power BI and query the underlying source. Transformations and refresh state can make the imported model differ from its source.

## 3. Existing service identifiers

The embedded connection manifest records:

| Identifier | Value |
|---|---|
| Original workspace | `<pbix-workspace-id>` |
| Semantic model | `<pbix-model-id>` |
| Report | `<pbix-report-id>` |

**These are discovery hints only.** They do not establish that the items still exist, are accessible, or match this PBIX revision. Validate them before publishing a duplicate or using them in configuration. The tenant ID is not established by these values.

## 4. Findings that affect implementation

### F01 — Advisor RLS does not visibly cover the whole model

The single extracted table RLS rule belongs to role `Advisor`, filters `dim_advisor_primary`, and uses `[advisor_email] == USERPRINCIPALNAME()`.

The visible active relationship paths from that table reach only:

- `fact_client_interaction`
- `fact_opportunity`
- `fact_recommendation`

There is no extracted relationship from either advisor dimension to `dim_client`, despite client fields `advisor_key_primary` and `advisor_key_secondary`. The extracted **ordinary** single-direction graph therefore supplies no evidence of an advisor-security path to clients, accounts, AUM, holdings, transactions, performance, alerts, or lifecycle events. Ordinary cross-filtering and security-filtering are separate properties; the latter was not extracted, so this is not a complete RLS propagation graph.

**Consequence:** merely using delegated authentication and saying “Power BI enforces RLS” does not establish advisor isolation for those tables. This is a potential data-exposure defect, not a formatting issue.

**Required gate:** compare the live relationship/security metadata; test two distinct actual Viewer identities with Build permission against both permitted and forbidden clients/accounts. Resolve primary-only versus primary-or-secondary access with the owner. If this is an unrestricted synthetic-data POC, explicitly label it as such; do not claim multi-user advisor isolation. Do not repair RLS using an LLM instruction or application-side filter alone.

Verify actual role membership (including additive membership in multiple roles) and the UPN-to-`advisor_email` mapping. UPN is not necessarily the email value. One extracted table-filter row is not proof that the model has exactly one role.

### F02 — Account-to-fact relationships are inactive

The `account_key` relationships to `dim_account` are inactive for transactions, AUM, performance, holdings, and lifecycle events.

**Consequence:** an account dimension filter cannot be assumed to filter these measures. Do not activate these edges in an OSSIE projection just because the keys match.

**Required gate:** compare a known account selection with the report/live engine; obtain owner-approved measure/model changes if necessary. `USERELATIONSHIP` is not an RLS repair and can be restricted in RLS scenarios.

### F03 — Transaction date filtering is not represented by a relationship

`fact_transaction` contains `trade_date_key` and `settle_date_key`, but neither appears in the extracted relationships to `dim_date`. Nevertheless, measures `Transaction Volume MTD` and `Transaction Volume QTD` use `dim_date[full_date]`.

**Consequence:** the extracted model provides no active date-filter path to transactions; these time-intelligence measures require live verification and likely model-owner attention. Ask whether “transaction date” means trade or settlement date. Do not silently invent that relationship in a query generator.

### F04 — Advisor-role filtering needs explicit semantics

Both advisor dimensions actively relate to the same `advisor_key` on interactions, opportunities, and recommendations. They do not connect to the client's primary/secondary assignment columns.

**Consequence:** simultaneous primary/secondary advisor selections can intersect filters on the same event-owner key. The names alone do not prove primary/secondary client-ownership behavior.

### F05 — Balance, return, and pipeline labels can mislead

| Measure | Formula observation | Required guidance |
|---|---|---|
| `Total AUM` | Sums `fact_aum_daily[aum]` | Requires explicit as-of/date scope; not automatically an end-of-period balance |
| `Total Active AUM` | Sums AUM where `is_active` is true | Verify what “active” means and whether multiple snapshot dates qualify |
| `AUM YTD` | Applies `TOTALYTD` to `Total Active AUM` | Do not describe as YTD investment return or automatically as latest AUM |
| `AUM Growth %` | Net flows divided by active AUM minus flows | Not equivalent to a general portfolio-growth/return calculation |
| `AUM Previous Year` | Selects previous-year maximum date and removes `is_active` filtering | Preserve this deliberate asymmetry in comparisons |
| `Avg Portfolio Return` | Arithmetic `AVERAGE` | Not a weighted or compounded return |
| `Latest Cumulative Return` | Latest date via `TOPN`, then `MAXX` | Across multiple accounts, date ties can produce a maximum rather than portfolio aggregation |
| `Weighted Pipeline` | Sums value times probability without excluding stages | Unlike `Pipeline Value`, not explicitly restricted to open opportunities |
| `Holdings Count` | Distinct security keys | Not count of accounts or holding rows |
| `Top 10 Concentration %` | `TOPN` over security market values | DAX tie behavior can include more than ten securities |

These are static formula observations, not numerical accuracy findings. Keep native definitions unchanged until reviewed. Descriptions must explain implemented behavior rather than an assumed business meaning.

### F06 — AI grounding is effectively unconfigured

- Embedded Copilot schema contains `tables: []`.
- Example prompts contain `prompts: []`.
- Indexing setting is enabled.
- The entire author instruction is “You are a very helpful analyst!”

The empty AI selection is not an empty semantic model. Build a reviewed domain glossary, measure descriptions, explicit date/advisor guidance, and a few verified question/query pairs. Do not advertise these pairs as Power BI “verified answers” until configured and verified there.

### F07 — Stale report references

Report metadata references `dim_advisor_primary[team]` and `dim_advisor_secondary[team]`; those columns are absent. Both tables instead contain `advisor_team`.

Eleven visual-definition files contain a directly detected `team` reference, across Account AUM, Advisor Pipeline, Recommendations, Opportunities, and Transactions pages. Treat these as stale references pending report-owner verification, **not an exhaustive inventory of report defects**. Do not automatically rename model fields or import invalid bindings as trusted query context.

### F08 — Personal and free-text fields need deliberate exposure

Potentially sensitive fields include client name/email/phone; advisor email, UPN, and manager email; interaction subjects; alert descriptions/recommended actions; and recommendation rationale. No rows were read to classify actual values. Decide whether these are needed by the MCP user population. Hiding them from a catalog alone does not prevent raw DAX from accessing them; enforce true restrictions in Power BI permissions/OLS or restrict the public query interface.

## 5. Report inventory

| Page | Visibility | Raw visual definitions |
|---|---|---:|
| Account AUM | Default | 19 |
| Advisor Pipeline | Default | 18 |
| Portfolio & Performance | Hidden | 13 |
| Recommendations | Default | 18 |
| Opportunities | Default | 19 |
| Transactions | Default | 23 |
| Executive Overview | Hidden | 13 |

Total: **7 pages / 123 visual definitions**, including textboxes, shapes, and buttons. This is not the official remote MCP service's count of valid, data-bound visuals. Its documented report filtering rules differ.

## 6. Recommended first query scenarios

**Current POC scope:** MCP clients will not authenticate, and all calls use one configured backend identity through restricted compute access. The two-identity security scenario below is deferred until multi-user support is requested. These static model findings remain valid caveats; the POC does not remove RLS/OLS or claim separate advisor access for each caller.

1. Explain `Total Active AUM`, including the snapshot/date caveat, without running a query.
2. Execute a known date-scoped AUM query and compare with the live model under the same identity/filter context.
3. List open opportunities by the event-owning advisor; verify primary/secondary semantics.
4. Compare trade-date and settlement-date transaction questions; reject unsupported ambiguity rather than invent an active date path.
5. Prove account slicing behavior on one approved account before exposing account-level analytics generally.
6. Run an actual restricted-user cross-client negative test before any shared advisor deployment.

See [implementation-plan.md](implementation-plan.md) for architecture, contracts, standards, and implementation phases.