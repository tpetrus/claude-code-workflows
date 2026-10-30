# When to use each agent, skill, and lane

Decision guide for the verified-diamond Claude Code configuration. Routing policy lives in `~/.claude/verified-diamond/routing.json`; this document explains intent. When the two disagree, routing.json wins. Quality/cost tradeoffs per model and effort are in `model-economics.md`.

## First decision: do you need the diamond at all?

| Situation | Use |
|---|---|
| A deterministic command covers it (format, `lint --fix`, codegen, codemod) | Run the command directly. No agent. |
| Small, single-file, obvious change (color, copy, config value) | Direct lane: `claude --model haiku`, direct edit. No workflow ceremony. |
| Pre-captured R0/R1 cleanup with a deterministic verifier | Follow-up fast lane: drain `vd.py followup-ready` items. |
| Substantive multi-file feature, decomposable into bounded tasks | Full diamond: verified-diamond skill + orchestrator. |
| Substantive but inherently serial work (one abstraction, one owner) | Orchestrator session without fan-out. Do not manufacture parallel subtasks. |

## Direct lane (trivial work)
Plain sessions default to Opus (`settings.json`), which is the wrong price for trivial work. For trivial R0/R1 edits, launch `claude --model haiku` (or switch with `/model haiku`). Cost discipline comes from three things: a small model, a precise file-targeted prompt (your specificity replaces discovery - say the file or component if you know it), and the cheapest sufficient verification (lint/build/visual check, no review, no gates ceremony). Use Sonnet instead when any judgment is involved (design tokens, theming, which abstraction to touch). The moment the change stops being trivial - multiple components, shared contracts, protected paths - switch to the full workflow.

## Agents

### verified-diamond-orchestrator (Opus, medium effort)
Use for: requirement ambiguity, architecture, risk classification, building the task DAG, freezing shared contracts, routing, integration, repair after failed verification, merge decisions.
Do not use for: already-bounded implementation (hand to a builder), procedural work (mechanical), or anything a command can do. The orchestrator is the most expensive seat; keep it on judgment, not labor.
Escalate effort to high only for R3 planning or after a failed decomposition.

### verified-diamond-builder (Sonnet, medium effort)
Use for: bounded implementation that needs local engineering judgment, with a stable contract, disjoint owned paths, and a local verifier.
Do not use for: ambiguous scope (orchestrator), procedural transformations (mechanical), or cross-boundary contract changes (return to orchestrator).
Escalate to high effort for concurrency, migrations, authorization, or complex integration. Fast-lane follow-ups run builders at low effort.

### verified-diamond-mechanical (Haiku, low effort)
Use for: procedural, reversible transformations with an exact supplied procedure and a strong deterministic verifier: renames, config edits, generated-code refresh, fixture updates, running a codemod and verifying.
Do not use for: anything requiring a design choice, missing a verifier, or an improvised procedure. A mechanical that must guess is a misrouted builder.
Prefer a deterministic program over a mechanical whenever one exists.

### verified-diamond-explorer (Haiku, medium effort)
Use for: read-only repository discovery - locating files, symbols, usages, conventions, command contracts - before planning or delegation.
Do not use for: any write, any judgment call about what to build. Escalate to Sonnet only when discovery requires nuanced synthesis across many files.

### verified-diamond-reviewer (Sonnet, high effort, plan mode)
Use for: independent adversarial falsification of R2+ changes and anything touching protected paths: correctness, auth/security, data integrity, concurrency, contract compatibility, test adequacy.
Do not use for: implementation (review only), or R0/R1 changes fully covered by deterministic gates - skip review there and let the gates carry the evidence.
Escalate blocking, uncertain findings to Opus high. Non-blocking medium/low findings become follow-up packets via `vd.py followup-add`, not inline fixes.

## Skill and scripts

| Asset | Use when |
|---|---|
| `skills/verified-diamond` | Invoked for any substantive feature. Defines the workflow, packet schema, fast lane, and cost rules. |
| `scripts/vd.py init / add-task / ready / result / approve / status` | Orchestrator records and tracks the DAG for the current feature. |
| `scripts/vd.py followup-add / followup-ready / followup-result` | Capture non-blocking findings as packets; list and resolve them during a fast-lane drain. |
| `skills/verified-diamond/templates/task-graph.yaml` | Template for a feature's task graph. |
| `skills/verified-diamond/templates/task-template.json` | Task shape for `--plan` intake from double-diamond; keys match its `draft_dag`. |
| `skills/verified-diamond/templates/followup-packet.json` | Template for a fast-lane follow-up packet. |

## Lanes at a glance

| | Full diamond | Fast lane |
|---|---|---|
| Entry | verified-diamond skill | drain `followup-ready` items |
| Planning | Opus DAG + contract freeze | None - packets were pre-built at review time |
| Workers | Sonnet-medium builders, Haiku-low mechanicals | Haiku-low mechanical first, Sonnet-low builder if judgment needed |
| Review | Sonnet-high adversarial (R2+) | Skipped when gates fully cover, else diff-only Sonnet-medium |
| Verification | Per-task + full gates | Affected-scope per item, full gates once per batch |
| Risk ceiling | Any (with human approval R2+) | R0/R1 only, fail closed (`followup-add` rejects R2+) |

## State and sessions
State is scoped per project root under `~/.claude/verified-diamond/state/<project-slug>/` (worktree-safe: all worktrees of a repo share one slug): two projects never share state. Within a project, `current-task.json` resets each feature (`vd.py init`); `followups.json` and `telemetry.json` accumulate deliberately (backlog and calibration) but are bounded - resolved follow-ups keep only the most recent 20 (pruned by `vd.py`), hook audit telemetry keeps the trailing 1000 entries in its own per-project `hook-telemetry.json`, and orchestration telemetry should be capped at the trailing 200 entries when written. Run one large feature per session, drain follow-ups before ending it, and start the next feature fresh - see Session hygiene in CLAUDE.md.
