# Foundry evaluation plan: raw data vs. semantic context vs. predefined measures

**Updated:** 2026-09-18  
**Status:** research-backed plan only; no agents, model copies, evaluators, datasets, or evaluation runs created.  
**Decisions:** three agents run in Microsoft Foundry, each with only its corresponding dataset's MCP connection; no Code Interpreter, generic calculator, or other agent tools; scoring runs in the Foundry cloud evaluation framework.

## 1. Experiment and existing project assets

Test two hypotheses separately: semantic context improves answer accuracy, and predefined business calculations further improve accuracy/consistency while reducing agent tokens and execution steps. Report observed results, including failures; do not assume either hypothesis is true.

| Arm | Data access, always through MCP | Responsibility left to the agent |
| --- | --- | --- |
| A — Raw | Source-table schema discovery and read-only SQL; preserve actual source names, types, and declared keys | Interpret the schema, construct joins and calculations in SQL, and apply filters |
| B — Semantic, no measures | Fabric facts, dimensions, domain names/descriptions, and relationships; DAX execution against a measure-free model copy | Implement calculations and filters in generated DAX |
| C — Semantic, with measures | The same semantic structure and data as B, plus approved predefined DAX measures | Select/reuse measures and apply appropriate filters |

All calculations execute in the underlying database or semantic engine through the assigned MCP connection. Agents author queries and explain returned results; they do not use a code interpreter or a separate calculation tool.

The project already has 15 tables, 171 columns, 53 DAX measures, and 33 relationships; draft semantic descriptions; and a project-owned MCP server with schema and DAX tools. The [package README](../semantic-model-mcp/README.md) records live execution through the `fabric_rest` backend. Microsoft's native preview MCP endpoint is not required for this experiment. Existing implementation tests are not agent-quality evaluations.

The [model assessment](model-assessment.md) and [semantic overlay](../semantic-model-mcp/semantic-models/sm_wealth_mgmt_import/semantic-overlay.yaml) identify unverified business definitions, inactive account relationships, missing transaction-date relationships, and snapshot aggregation caveats. Validate the scenarios we use before creating answer keys. The MCP schema is an author snapshot, not automatic live metadata synchronization.

## 2. Foundry evaluation approach

- Use the **current Foundry cloud evaluation service**, with the Azure AI Projects SDK and its project-scoped Evals client—not a local-only evaluator with a portal upload. Current setup documentation uses `azure-ai-projects>=2.2.0`; verify and pin a compatible release during implementation. [S1]
- Create **three Foundry agents from the same base definition**, pinning each agent version and changing only its corresponding dataset's MCP connection. Expose only that connection's approved schema/query tools; do not attach Code Interpreter, calculator, web/search, or unrelated tools. Prompt agents are sufficient initially; a custom hosted runtime is not required merely to call MCP.
- Use **agent-target batch evaluations**: Foundry sends the shared questions to each pinned agent version and evaluates the actual generated response and tool interaction. The documented target is `azure_ai_agent`, using `azure_ai_target_completions`. [S2]
- Reuse the same dataset version, evaluator versions, thresholds, and judge deployment across arms. Keep all runs in a comparable evaluation group and label them by arm and repetition.
- Supply full interaction output and actual MCP tool definitions to process evaluators, not only final prose. The SDK distinguishes `sample.output_items` from `sample.output_text`. Confirm mappings and tool-result capture in the pilot. [S2, S3]
- Register a **custom deterministic code evaluator** for numerical correctness alongside the built-in evaluators. It runs inside Foundry. Expected answers must already be in the evaluation data because the grading sandbox has no network access. Custom evaluators and some agent evaluators are preview features. [S3, S4]
- The code evaluator is **evaluation-only scoring logic**, not Code Interpreter and not a tool available to any agent. It compares completed answers with the answer key; it cannot help an agent answer a question.

## 3. Selected quality metrics

| Metric | Foundry implementation | Scoring and role |
| --- | --- | --- |
| **Business-answer accuracy — primary** | Custom code evaluator, proposed name `business_answer_accuracy` | Return 1.0 only when the structured answer matches the reviewed expected result: values within per-case tolerance, correct entities/grouping, units/currency, and requested reporting scope; otherwise 0.0. Aggregate as percent correct on answerable cases. |
| **Task completion** | `builtin.task_completion` (preview) | Pass/fail: did the agent deliver all requested parts, or appropriately handle a question requiring clarification? Secondary outcome signal, not a numerical oracle. |
| **Tool-call accuracy** | `builtin.tool_call_accuracy` | 1–5 plus pass/fail: appropriate MCP tools and arguments, without unnecessary calls. Proposed initial threshold: 4; calibrate on pilot examples and freeze before comparison. |
| **Tool-output utilization** | `builtin.tool_output_utilization` | Pass/fail: did the agent correctly use the returned SQL/DAX query results rather than ignore, misread, or contradict them? |
| **Task adherence** | `builtin.task_adherence` (preview) | Pass/fail against shared instructions: use only the assigned dataset MCP tools, obtain calculated values from SQL/DAX results, preserve requested dates/filters/units, do not invent data, and clarify material ambiguity. Include the instructions in evaluator context. |

For deterministic scoring, ask every arm for the same compact structured answer containing results, units, reporting scope, and an optional explanation. Normalize row order unless ranking is requested; define null/empty-result behavior and numeric tolerances in advance. Invalid structured output fails the answer check and is reported separately as a formatting failure. A claimed filter in the answer does not prove the query applied it: inspect executed SQL/DAX in failure analysis and representative spot-checks.

Keep ambiguous/clarification cases separately tagged and report their behavior scores separately from numerical accuracy. Do not count a fluent clarification as a correct numerical answer to an otherwise answerable question.

**Not headline metrics:** BLEU/ROUGE/text similarity and fluency do not establish financial correctness. Groundedness can be useful diagnostically, but a response can faithfully repeat a wrong query result; tool-output utilization plus the independent answer check is the initial choice. Task Navigation Efficiency requires expected action sequences, so it is not a fair primary score across three arms with different valid query paths. [S3]

## 4. Efficiency and consistency measurements

These are **observed agent/runtime measurements and result aggregations**, not interchangeable with Foundry's LLM-judge quality scores. Join them to evaluation results using question, arm, repetition, agent version, response/trace ID, and evaluation run ID. Foundry tracing captures tool calls/results, retries, token consumption, and duration in Application Insights. [S5]

- **Agent input, output, and total tokens per question:** include all model calls, schema context, tool results, and retries. Exclude evaluator/judge usage. Report cached-input and reasoning-token details where available without adding subsets twice. Missing telemetry is unknown, not zero.
- **Tokens per correct answer:** total agent tokens across all attempts divided by correct answers; report alongside ordinary tokens/question so cheap failures do not look efficient. Report undefined if there are no correct answers.
- **Execution effort:** MCP schema/query calls, model turns, query revisions, retries, and tool failures. Distinguish schema discovery from query execution. There are no separate calculation-tool calls; savings, if any, come from simpler query construction and fewer attempts.
- **Latency:** median and p95 agent end-to-end time, with MCP query duration as a diagnostic. Exclude evaluation queue and judge time. Report p95 as descriptive given the small sample.
- **Repeatability:** per-question correctness across repeated runs and the percentage correct in all repetitions. Consistently wrong is not success.
- **Measure reuse in C:** use captured DAX to identify whether the intended stored measure was actually invoked or the agent recomputed the formula. This is explanatory evidence, not a requirement imposed on A/B.

Do not assume the evaluation report's token total is agent-only; confirm attribution in the pilot. Current documentation describes automatic estimated cost specifically for **model-target** evaluations, not a guaranteed full agent/MCP bill. Use agent usage/traces for this comparison; any later cost estimate must be labeled and separate from evaluation costs. [S6]

## 5. High-level delivery plan

1. **Freeze and validate the data/model baseline.** Obtain read-only source access, align the imported data snapshot and relevant transformations, and validate a small set of calculations such as average absolute trade size, probability-weighted opportunity value, ratios, and an explicitly dated AUM balance. Apply approved relationship/model corrections equally to B and C.
2. **Prepare isolated MCP data configurations.** Reuse the existing Fabric/DAX server for B/C and add a read-only raw SQL/schema adapter for A. Separate server configurations/endpoints can reuse one codebase while keeping backend/model allowlists fixed. Physically remove stored measures from B's model copy; scrub measure definitions from its catalog, overlays, and report metadata. Do not merely hide measures. Exclude report-specific tools from the benchmark unless equivalent context is deliberately available to every arm.
3. **Create the three Foundry agents and prove connectivity.** Pin model/version, instructions, reasoning/sampling settings, output format, and budgets. Each agent gets only its assigned dataset's MCP connection and approved schema/query tools. Verify remote discovery, protocol compatibility, metadata retrieval, and one query from Foundry—not just from the local smoke client. Inspect each agent's configured tools to confirm no Code Interpreter, generic calculator, or unrelated tool is enabled, and verify its MCP allowlist prevents cross-arm dataset access. Approve only the intended read-only tools for unattended evaluation.
4. **Build a reviewed shared dataset of about 30 questions.** Include simple controls, dimensional joins/filters, domain terminology, and calculation-heavy questions, plus a few clarification cases. Each row records a stable ID, category, question, approved business rule where applicable, reporting dates, expected result/behavior, units/tolerance, snapshot version, and answer-key provenance. Cross-check expected values with independently reviewed source calculations rather than treating C as the sole oracle.
5. **Configure the Foundry evaluation and traces.** Register the answer grader; select the four built-ins above; attach the shared dataset and capture MCP definitions, arguments/results, errors, and agent usage. Send only question/allowed business-rule fields to the agent; answer keys and scoring rubrics remain evaluator-only.
6. **Run a small pilot, then the comparison.** Pilot a few known cases across all three arms to check scoring, MCP trace coverage, token attribution, and connectivity. Freeze the setup, then run 30 questions × 3 arms × 3 repetitions: **270 agent attempts**. Start each question in a fresh conversation; interleave arm order and use comparable concurrency. Do not tune an arm on the scored dataset; use separate pilot questions.
7. **Publish three result sets and paired comparisons.** Show A→B for semantic context, B→C for predefined calculations, and A→C for the combined approach. Report accuracy overall and by category, correctness consistency, tokens, MCP query calls, query revisions, retries, and latency. Include all attempted questions and separately label infrastructure/evaluator errors; do not silently drop failures. Pair token comparisons on the same questions, including a subset where both arms answered correctly.
8. **Build the blog evidence.** Export Foundry scorecards and a small set of representative traces showing correct answers, wrong SQL/DAX logic, repeated query revisions versus measure reuse, and exceptions. Preserve dataset/evaluator/agent/model/catalog versions and sanitized reproducibility notes. Keep private rows, personal data, credentials, and answer-key artifacts out of the public repository unless explicitly approved.

## 6. Controls and practical constraints

- **Separate business-rule knowledge from executable reuse.** For the calculation-reuse subset, include the same business definition in every arm's question/context, but provide its stored implementation only in C. Keep naturally phrased domain questions separately tagged. B/C retain the same non-measure semantic descriptions. This prevents missing definitions from masquerading as a benefit of precomputation.
- **Dataset MCP only.** A is not a direct SQL connection from the agent; the MCP server owns database access. B/C use the existing project-owned DAX tools. Each agent can access only its assigned dataset's schema/query tools. No Code Interpreter, standalone or MCP-hosted generic calculator, Fabric Data Agent, web/search tool, RAG source, or hidden formula helper is in scope for any arm.
- **Engine-side computation only.** A expresses calculations in SQL, B expresses them in generated DAX, and C can reference stored DAX measures. Do not force row downloads or external computation. Shared instructions require numerical conclusions to be backed by query results; agents explain the results rather than substitute their own arithmetic.
- **Connectivity remains a prerequisite.** The existing server is on VNet-integrated App Service; outbound VNet integration alone does not establish Foundry-to-MCP reachability. Confirm the approved access path without making the current unauthenticated POC endpoint public. Private MCP has additional Foundry networking requirements; Microsoft's tested private hosting configuration is Container Apps, while private App Service hosting is not internally validated. Decide only after a connectivity pilot. [S7]
- **C does not remove all reasoning.** It still has to select measures and filter context. SQL and DAX execution are deterministic once correctly specified; the hypothesized advantage is avoiding repeated LLM implementation of business logic, not making arithmetic newly deterministic. Larger measure metadata can also increase tokens, so savings must be measured. This experiment makes no claim about saving Code Interpreter calls because none are available in any arm.
- **Attribution is bounded.** A→B includes differences in schema, modeling, and SQL/DAX execution; it is not proof that naming alone caused an improvement. B→C is the cleaner calculation-reuse comparison. One small wealth-management dataset supports a case study, not a universal claim.

## 7. Remaining inputs and next step

- Raw source location/access and agreement on the snapshot and business definitions.
- Selected Foundry project, agent deployment, fixed judge deployment, and permissions for cloud evaluation and telemetry.
- Approved model copies/catalogs and a reachable, appropriately restricted MCP connection for each arm.
- New evaluation dataset, deterministic grader, cloud-run configuration, and trace-to-results export. Existing protocol tests and smoke scripts remain useful prerequisites but do not replace these.

**Next implementation milestone:** validate a handful of model calculations and create the three-arm Foundry connectivity/scoring pilot. No deployment or source-data changes are authorized by this planning document.

## Sources

Official Microsoft documentation reviewed on 2026-09-18. Verify preview support and evaluator schemas again when implementing.

- **S1:** [Introduction to cloud evaluation with Microsoft Foundry SDK](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation)
- **S2:** [Evaluate model and agent targets](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-targets)
- **S3:** [Agent evaluators: metrics, inputs, MCP support, and tool limitations](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/agent-evaluators)
- **S4:** [Custom evaluators: code-based grading and sandbox limits](https://learn.microsoft.com/azure/foundry/concepts/evaluation-evaluators/custom-evaluators)
- **S5:** [Agent tracing overview](https://learn.microsoft.com/azure/foundry/observability/concepts/trace-agent-concept)
- **S6:** [Evaluation results, latency, and model-target cost availability](https://learn.microsoft.com/azure/foundry/observability/how-to/cloud-evaluation-results)
- **S7:** [Connect agents to MCP servers: connectivity and known limitations](https://learn.microsoft.com/azure/foundry/agents/how-to/tools/model-context-protocol)