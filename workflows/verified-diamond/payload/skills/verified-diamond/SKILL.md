---
name: verified-diamond
description: Execute a production software task through a cost-aware verified diamond: expensive orchestration, parallel bounded builders/mechanicals, deterministic verification, independent review, and risk-based approval. Start it with `claude --workflow verified-diamond`.
model: opus
effort: medium
---
# Verified Diamond

## Entry gate
0. This skill runs only in a session started as the `verified-diamond-orchestrator` agent (`claude --workflow verified-diamond`, which runs `claude --agent verified-diamond-orchestrator`). If this session is not that agent, stop before doing anything else and tell the user to restart with `claude --workflow verified-diamond` and run `/verified-diamond <task>` there.
1. Read `~/.claude/verified-diamond/routing.json`.
2. Read repo guidance and discover real verification commands.
3. If `$ARGUMENTS` starts with `--plan "<path>"`, follow Plan intake below; the rest of the arguments is the feature title. Run `python3 ~/.claude/skills/verified-diamond/scripts/vd.py init --title "<title>"` (without the `--plan` part).
4. Fail closed if applicable commands are not explicitly documented.

## Flow
INTAKE -> DISCOVER -> SPECIFY -> PLAN -> APPROVAL? -> EXECUTE_DAG -> INTEGRATE -> REVIEW -> REPAIR -> FINAL_GATE -> MERGE_DECISION.

## Plan intake (`--plan`)
Optional input from the double-diamond workflow: an exported `<slug>.plan.json` of settled requirements. Without `--plan`, nothing here applies.
1. Run `python3 ~/.claude/skills/double-diamond/scripts/dd_plan.py verify "<path>"`. If double-diamond is not installed, the command fails, or it does not print `VERIFIED`, stop and report; never edit the plan file.
2. Compare `handoff.verified_diamond.base_revision` with `git rev-parse HEAD`. If they differ, tell the user which files changed since planning and ask whether to proceed.
3. Adopt the plan's acceptance criteria and settled decisions as the spec; do not re-brainstorm them. If the plan conflicts with the repository, stop and ask.
4. Freeze every contract in `freeze_first` before dispatching any worker.
5. Refine `draft_dag` using `assets/task-template.json` (same keys). `owned_paths` are drafts: re-verify against the current tree. Fill `verify` from the repository command contract, never from the plan. The risk class from this workflow is authoritative and may only be raised.
6. Record each task with `vd.py add-task`, mapping `worker_class` to `role` and `objective` to `title`; carry the remaining fields as extras. `vd.py` itself is unchanged.

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
