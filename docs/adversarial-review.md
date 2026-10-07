# Adversarial Review: Wealth Management MCP Implementation Plan

**Date:** 2026-09-11  
**Reviewed scope:** accuracy, completeness, architectural boundaries, Python 3.11 compatibility, Microsoft tool alignment, OSSIE adaptation, and evidence from the supplied PBIX.  
**Disposition:** material design corrections applied to [implementation-plan.md](implementation-plan.md). **Conditionally ready for a feasibility implementation—not certified for live use or shared advisor deployment.**

> **Subsequent scope decision, 2026-09-11:** the user selected a trusted POC with **no inbound MCP authentication**, reuse of the PBIX IDs, MCAPS compute and associated Fabric hosting. This report retains the findings against the earlier multi-user design; its OAuth/OBO, individual caller-disclosure and two-user isolation gates now apply **only to a future authenticated multi-user expansion**, not to starting this POC. The current plan instead requires restricted ingress, a fixed POC exposure policy and one explicitly authenticated backend identity. Fabric/Power BI authentication and endpoint-specific RLS restrictions still apply. No model security is disabled, and all reachable clients share the same approved scope.

## 1. Clarified requirement and verdict

The governing boundary is now:

- **Microsoft Learn defines the MCP tool structure:** Execute Query, Get Semantic Model Schema, Get Report Metadata, Generate Query; preserve documented purposes and required input concepts.
- **OSSIE defines the semantic model structure:** model → datasets/fields, relationships, and metrics; adapt it explicitly for DAX and Fabric through a separately versioned derivative profile.
- **MCP defines protocol behavior:** discovery, tool schemas, result envelopes and transport/authentication requirements.
- **Power BI/Fabric defines execution and authorization:** no replacement DAX engine, fabricated relationships, or security-through-description.

The earlier plan conflated these responsibilities in two places. Its six custom tools were not Microsoft's tool surface, and moving every measure into an extension-only catalog unnecessarily abandoned OSSIE's first-class metrics. Both are corrected.

The revised target is **Microsoft-capability-aligned and OSSIE-derived**, not Microsoft wire-compatible or unmodified-OSSIE-compliant. Exact names and input schemas still require upstream discovery, and the profile's semantic response is an intentional output adaptation.

## 2. Review method and evidence

Three independent read-only review tracks challenged: (1) Microsoft contracts and feature parity, (2) OSSIE/Fabric semantic fidelity, and (3) Python architecture, identity and operational claims. Findings were reconciled against directly reread public sources and complete parsed local artifacts—not accepted solely because a reviewer reported them.

### Checks completed

| Evidence | Result and limitation |
|---|---|
| Microsoft get-started and dedicated tools reference | Four capability labels and required input concepts confirmed; no complete case-sensitive wire identifiers/input/output schemas published in these pages |
| Pinned OSSIE JSON Schema | Version `0.2.0.dev0`, DAX absent, expression dialect list requires at least one item, closed objects, extension `data` is String |
| OSSIE schema provenance | Commit `28365cd638f3833765c5b940ada5b8cbc65f1c42`; SHA-256 `22be177612ed665e0af244c586b9c0162f2a3706f8e9b061910f7a8e2a19b8e8` independently checked |
| Public Python SDK release metadata/source | `mcp` 2.2.0 non-yanked, Python `>=3.10`, `httpx2` dependency; v2 API/protocol claims are supported, not typos. Actual installation/runtime chain remains untested |
| Full local JSON assertions | 15 tables, 171 columns, 53 nonblank measure definitions, 33 relationships with valid endpoints, 28 active/5 inactive, one extracted RLS filter row, no transaction-to-date relationship |
| Source preservation | PBIX hash still `e2ebaed09bde67982c1c2de368fcdd02f4ecdb7ce880bef35b684514111ee58a` |
| Six memory-only schema probes | Minimal derivative accepts DAX; unmodified upstream rejects it even with upstream version restored; empty dialect list rejected; undeclared model property rejected; malformed extension JSON still passes outer String validation; `allOf` cannot widen the enum |

The schema probes used an in-memory design candidate, **not a delivered production profile or full model-validation implementation**. No Python package was installed, no server started, no authenticated Power BI call made, no business rows decoded, and no model/permission changes performed by this review.

## 3. Findings and corrections

Severity is relative to the requested design. **Critical** means a direct mismatch with the clarified requirement; **High** means correctness/security claims would be unsafe or misleading; **Medium** means a material completeness/maintainability gap. “Corrected” below means **corrected in the plan**, not implemented or proven in a live system.

| ID | Severity | Original issue | Evidence / failure mode | Correction and remaining gate |
|---|---|---|---|---|
| AR-01 | Critical | Six custom `wealth_*` tools replaced Microsoft's surface | Metric list/detail are not among the four documented tools; model aliases replaced required IDs | Four-role surface restored; metric services internal; exact names/input schemas must be captured before public registration |
| AR-02 | Critical | All 53 measures were absent from primary `metrics[]` | Valid unmodified-core export, but poorly matched the requested OSSIE definition tailored for DAX | Primary Fabric/DAX derivative retains all approved metrics with `DAX` definitions; own schema ID/version and no upstream-conformance claim |
| AR-03 | High | Inactive relationships were outside the main relationship inventory | Representation and automatic filtering graph were conflated | All 33 relationships represented once, with required activity/native behavior; derived default graph has 28 active edges |
| AR-04 | High | Structure alignment could be mistaken for wire compatibility | Public pages provide conceptual inputs, not full wire signatures; adapted schema changes output | Contract manifest, observed schema samples and intentional-output diff required; explicitly no drop-in claim |
| AR-05 | High | Report/model IDs and generation context were weakened | Report requires Report ID; Generate Query requires question plus table/column/measure context | Distinct ID validation and report binding check; preserve required agent-selected context and translate only to supported native upstream input |
| AR-06 | High | Model access risked becoming formula/metadata disclosure permission | Native measure metadata APIs can require Write for formula values; OLS protects object names/metadata | Separate per-object/property disclosure from execution; no privileged snapshot backfill, including hashes/dependencies/warnings |
| AR-07 | High | Local sign-in could satisfy a supposedly hosted feasibility gate | Public-client success does not prove custom audience → OBO → downstream user chain | Hosted exit requires actual intended client and same-user delegation; consent/Conditional Access failures cannot broaden identity |
| AR-08 | High | Ordinary relationship graph could be called an RLS graph | Extracted cross-filter behavior does not include security-filter behavior; role inventory also incomplete | Assessment qualified; effective roles/UPN and two actual restricted identities remain release gates |
| AR-09 | High | Cached hash comparison implied live model-version safety | Unchanged name with changed DAX bypasses a cache-only comparison; arbitrary DAX can override a measure | Separate catalog/live fingerprints; model-level readiness gate; no unsupported selective dependency analysis or atomic version assertion |
| AR-10 | High | Boolean completeness/native type guarantees exceeded backend evidence | Remote contract unknown; REST can return nested errors/partial rows and no native typed schema | `complete/partial/unknown` internally, explicit type provenance, null preservation, no fabricated totals/cursors; output deviations documented |
| AR-11 | High | Report allowlisting alone did not cover rebinding or independent access | A report can change datasets; model access is not report access; hidden text/fields can be returned | Verify current report/model relationship, report authorization and disclosure; do not assume same workspace |
| AR-12 | Medium | Schema freshness stood in for imported-data freshness | Schema can match while imported balances are stale | Separate refresh/as-of evidence from extraction/query time; report unknown rather than infer from `load_date` |
| AR-13 | Medium | Extraction success could be interpreted as full Fabric/report coverage | Selected properties omit measure formats/visibility, security filtering, full roles; report walker misses alias/nested cases | Explicit availability states and parser limitations; planning utility not designated runtime schema/report implementation |
| AR-14 | Medium | Runtime limits/cancellation were underspecified | Trimming rows after buffering is not a byte bound; local cancellation need not stop engine work | Server-owned deadlines/byte/row settings, transport-bound verification, liveness/readiness split and honest cancellation outcomes |
| AR-15 | Medium | Wrapping Microsoft's remote MCP could appear mandatory | The article explains use of a hosted endpoint, not how a custom server must be built | Remote adapter remains a conditional design choice; REST fallback does not magically provide report/Copilot capabilities |

## 4. Microsoft contract findings

| Documented label | Required concepts | Not established by public prose |
|---|---|---|
| Execute Query | Semantic model ID, DAX expression | Exact tool name, JSON keys/types, response schema, remote-specific limits |
| Get Semantic Model Schema | Semantic model ID | Exact callable signature, complete formula disclosure, byte-exact definition export |
| Get Report Metadata | **Report ID** | Exact signature, numeric metadata-size limit, generic permission equivalence to model access |
| Generate Query | Semantic model ID, question, relevant table/column/measure context | Exact context format, OSSIE input support, eligibility for this tenant/user |

The tools-reference generic sentence “each tool requires a semantic model ID” conflicts with the specific report-input section. The plan uses the specific Report ID requirement and leaves exact signature resolution to discovery.

Follow all four roles in the target design. Generation may be disabled as Microsoft explicitly permits, but client-side LLM generation is not implementation of Microsoft's Copilot tool. The schema/execution-only starting slice is deliberately incomplete; a reduced backend cannot be called full parity.

## 5. Semantic profile decisions

The project profile deliberately changes the upstream dialect enum and document version under its **own** schema identity. It keeps OSSIE object shapes and uses object-local JSON-string extensions for Fabric details.

- **Metrics:** full DAX formula in `metrics[].expression.dialects` with `dialect: DAX`; canonical names and native invocation bindings are separate.
- **Fields:** DAX row-context column references; calculated-column author definitions are separate from materialized-column references.
- **Relationships:** all 33 in the primary inventory; activity/filter/cardinality required in validated metadata. Single-direction filtering runs one-side → many-side, not the textual `from` → `to` direction.
- **Security:** ordinary cross-filtering never substitutes for native security-filtering. Unknown security properties are not copied or defaulted into asserted facts.
- **Scope:** Import/many-to-one baseline only; no tested-all-Fabric-models or universal SQL-portability claim.
- **Availability:** complete author baseline and caller-visible views are distinct. A non-disclosable definition is not replaced with a placeholder to satisfy the schema.
- **Validation:** structural schema, decoded extension, semantic references/coverage, and publication permissions are independent checks. Schema validation does not validate DAX semantics or RLS.

The reusable profile schema must not hardcode this PBIX's object counts. Counts belong in the private baseline acceptance fixture. Retain upstream license/notice and a readable derivative diff.

## 6. Architectural assessment

| Principle | Revised design assessment |
|---|---|
| Separation of concerns | Public Microsoft tool contract, project semantic profile, native backend and auth/disclosure are explicit separate boundaries |
| Dependency inversion | Core services depend on typed protocols; MCP/MSAL live at adapters/composition boundary |
| Single calculation authority | Existing Power BI model remains authoritative; DAX copied, not reimplemented |
| Contract-first integration | Captured names/signatures and approved output adaptations precede public handler registration |
| Least privilege | Author extraction separate from reader runtime; formula/report access not inferred from query access |
| Fail-safe behavior | Unknown identity/security/required-definition state blocks affected shared capabilities, not silently replaced by an owner identity |
| Honest fidelity | Profile states, metadata/data freshness and result completeness distinguish known, absent and unknown |
| Python conventions | 3.11 root venv, package layout, dependency lock, typed models, async I/O and focused pytest/Ruff/mypy retained |
| Scope control | One backend and two-role vertical slice first; full four-role target remains visible; no extra agents/query engines or blanket hardening project |

These are architectural design checks, **not certification against an external architectural standard or a completed security assessment**.

## 7. Gates that a document review cannot close

1. **Real tool discovery:** exact names, input schemas, outputs/errors and protocol version under the intended user/client.
2. **Python dependency/runtime proof:** locked 3.11 install, one schema/query workflow, and actual transport/auth interoperability.
3. **Published identity match:** embedded PBIX IDs still exist, correct tenant/workspace/report binding, metadata baseline reconciled.
4. **Native definition/disclosure:** which formulas/properties are actually available and permitted for each audience; no invisible author-export bypass.
5. **Hosted delegation:** actual client token for façade → separate user-delegated downstream access, including denial/consent paths.
6. **RLS/OLS correctness:** current roles, UPN mapping, ordinary/security relationship behavior and two real Viewer identities tested against forbidden clients/accounts.
7. **Business semantics:** account inactive edges, missing transaction-date path, advisor role meaning and snapshot/return measures owner-reviewed.
8. **Report parity:** valid role bindings, filters, textboxes/hidden content, stale references and live dataset rebinding handled.
9. **Generation eligibility:** correct Microsoft generation engine with required context and actual tenant/user eligibility; no claim when disabled.
10. **Output/freshness bounds:** nested/partial errors, unknown types/completeness, actual byte/deadline enforcement and schema-versus-data staleness verified.

The revised [implementation-plan.md](implementation-plan.md) carries these as explicit phase exits and acceptance checks. The existing [model-assessment.md](model-assessment.md) retains static findings with their evidence limits.

## 8. Key sources

- [Microsoft Power BI MCP get started](https://learn.microsoft.com/en-us/power-bi/developer/mcp/remote-mcp-server-get-started) and [dedicated tools reference](https://learn.microsoft.com/en-us/power-bi/developer/mcp/remote-mcp-server-tools).
- [Pinned OSSIE core specification](https://github.com/apache/ossie/blob/28365cd638f3833765c5b940ada5b8cbc65f1c42/core-spec/spec.md) and [JSON Schema](https://github.com/apache/ossie/blob/28365cd638f3833765c5b940ada5b8cbc65f1c42/core-spec/ossie-schema.json).
- [MCP tools specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools), [Python SDK v2](https://py.sdk.modelcontextprotocol.io/whats-new/), and [mcp 2.2.0 release metadata](https://pypi.org/pypi/mcp/2.2.0/json).
- [Remote MCP client registration](https://learn.microsoft.com/en-us/power-bi/developer/mcp/remote-mcp-server-external-clients), [Entra OBO](https://learn.microsoft.com/en-us/entra/identity-platform/v2-oauth2-on-behalf-of-flow), and [SDK HTTP authorization](https://py.sdk.modelcontextprotocol.io/run/authorization/).
- [Fabric definition export](https://learn.microsoft.com/en-us/rest/api/fabric/semanticmodel/items/get-semantic-model-definition), [measure formula visibility](https://learn.microsoft.com/en-us/dax/info-view-measures-function-dax), and [OLS](https://learn.microsoft.com/en-us/fabric/security/service-admin-object-level-security).
- [Native relationship behavior](https://learn.microsoft.com/en-us/analysis-services/tmsl/relationships-object-tmsl), [inactive relationships/RLS](https://learn.microsoft.com/en-us/power-bi/guidance/relationships-active-inactive), and [DAX query definitions](https://learn.microsoft.com/en-us/dax/dax-queries).
- [Execute Queries REST](https://learn.microsoft.com/en-us/rest/api/power-bi/datasets/execute-queries), [report binding REST](https://learn.microsoft.com/en-us/rest/api/power-bi/reports/get-report-in-group), and [closed-schema extension rules](https://json-schema.org/understanding-json-schema/reference/object#extending-closed-schemas).

The full source register is in [implementation-plan.md](implementation-plan.md). Public documentation checks are not authenticated endpoint observations. Reference traversal was task-focused, not an unlimited crawl of every transitive link.