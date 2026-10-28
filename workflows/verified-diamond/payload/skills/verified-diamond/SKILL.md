---
name: verified-diamond
description: Execute a production software task through a cost-aware verified diamond: expensive orchestration, parallel bounded builders/mechanicals, deterministic verification, independent review, and risk-based approval.
model: opus
effort: medium
---
# Verified Diamond

## Entry gate
1. Read `~/.claude/verified-diamond/routing.json`.
2. Read repo guidance and discover real verification commands.
3. Run `python3 ~/.claude/scripts/vd.py init --title "$ARGUMENTS"`.
4. Fail closed if applicable commands are not explicitly documented.

## Flow
INTAKE -> DISCOVER -> SPECIFY -> PLAN -> APPROVAL? -> EXECUTE_DAG -> INTEGRATE -> REVIEW -> REPAIR -> FINAL_GATE -> MERGE_DECISION.

## Decomposition score
Delegate only when the task has a stable contract, disjoint write surface, sufficient context, a local verifier, and meaningful critical-path reduction. Keep ambiguous design, shared contracts, and integration with the orchestrator.

## Task packet
Every task must specify id, role, title, dependencies, base revision, inputs, owned paths, forbidden paths, acceptance criteria, verification command, expected output, retry budget, and escalation triggers. Record it with `vd.py add-task` before dispatch.

## Execution
Launch only ready tasks. Use at most 3 builders, 4 mechanicals, and 5 total subagents. Builders use worktree isolation. Mechanical tasks must be procedural. After each worker, run the designated verifier and record results. Do not trust self-reported success.

## Review
For R2+, auth, data, migration, payment, infrastructure, concurrency, or external-input changes, run the independent reviewer after integration. Block critical/high-confidence findings. Non-blocking medium/low findings are not fixed inline and not left as prose: while context is hot, record each as a complete follow-up packet with `vd.py followup-add` (see Follow-up fast lane).

## Follow-up fast lane
Nice-to-have cleanups and fixes must stay cheap without losing dependability. The expensive part of a follow-up is re-establishing context, so pay it once: capture a complete task packet (objective, worker_class, owned/forbidden paths, acceptance, verify, risk, origin) in `~/.claude/verified-diamond/state/followups.json` at the moment the finding surfaces. Later execution is a lookup, not a re-plan.

Qualification (fail closed): fast-lane items must be R0/R1 with a complete packet, a deterministic verifier that fully covers the change, and no shared contract, protected path, dependency, CI, auth, or schema surface. Anything else is ejected to the full workflow.

Execution: no orchestrator re-planning. Deterministic-first: if a formatter, lint --fix, codemod, or generator covers the item, the procedure is that command and the route is mechanical (Haiku low). Items needing local judgment go to a builder at Sonnet low. Group 3-5 items by ownership area into one worker session per batch; drain immediately after the main run while caches are warm. Run affected-scope verification per item and the full configured gates once per batch. Skip adversarial review only where deterministic gates fully cover the change; otherwise use a diff-only Sonnet-medium review.

Escalation and calibration: retry once at tier, then escalate one tier; after a second verification failure, eject to the full workflow. Record outcomes per item in telemetry; if more than 10% of the trailing 20 fast-lane items escalate, raise the default fast-lane route one tier. Routing details live in `routing.json` under `followup_fast_lane`.

Retention: state never grows unbounded. `vd.py` prunes resolved follow-ups to the most recent 20 (pending/blocked items are always kept). Cap orchestration telemetry at the trailing 200 entries when writing it. State is scoped per project root and shared across that project's worktrees.

## Merge
R0/R1 may auto-merge only if configuration is complete, all gates pass, no protected files or shared contracts changed, no dependency/CI/infrastructure/auth/schema change exists, and the change is reversible. R2+ always requires human approval.

## Stop
Stop for missing contract, two repeated failures, scope expansion, unavailable credentials, unresolved high finding, destructive action, or human gate. Produce summary, acceptance status, changed files, commands, reviews, rollout, rollback, and remaining risks.
