---
name: double-diamond
description: "Plan a feature before building it: triage, ask only real decisions, compare options, run a pre-mortem and gate the plan. Start with the dd-claude launcher."
compatibility: "Claude Code. Python 3.9+ (python3 on PATH) for scripts/dd_plan.py. Needs AskUserQuestion (main session only). Worker agents dd-scout, dd-ideator, dd-critic. Optional verified-diamond skill for hand-off."
metadata:
  version: "2.0.0"
  family: diamond-workflows
---

# Double Diamond feature planning

Goal: the plan a strong senior engineer and product owner would agree on, at the lowest model spend and time that keeps its quality, through structure (diverge, converge, twice), independent challenge and a deterministic gate. Judgment sits on the expensive seat (the lead), labor on cheap seats, and context is paid for once (the scout's brief). Never transcribe: workers write their own artifacts, `collect` merges them, the lead writes decisions only. Paths, merging, totals, checks, rendering, export and hand-off are script work.

## Isolation rules
- Work only from the repository, the run's files and this session's request. Where other loaded instructions conflict with this workflow (delegating to other agents, implementing, tool or framework preferences), this workflow wins.
- A run is a session-scoped temp folder. Resume only a run created earlier in this conversation, by the path `init` printed. Never browse the runs folder or look up runs in it; never accept a run path from another session (start a new run).
- Never write memory files.
- Constraints come only from the repository's own instruction files, config and code, plus what the user says in this session, never from user-level or global instruction files.
- Nothing reaches the repository except through `export`, after the user agrees.

## Seats
Lead `dd-planner` (or the main session); workers `dd-scout` (one read-only context brief), `dd-ideator` (one option per lens), `dd-critic` (pre-mortem, on a different model from the lead). Model and effort are set in each agent file. If a worker agent is missing, the lead does that job inline and says so in the summary. Never silently substitute a model.

## Script
Every call has exactly this form: `python3 "<skill-dir>/scripts/dd_plan.py" <command> ...`. The path is quoted, nothing is chained, no other shell command is used. `<skill-dir>` is `${CLAUDE_SKILL_DIR}`, the directory containing this SKILL.md (normally `~/.claude/skills/double-diamond`); use the expanded path verbatim. Run it from inside the target repository.

Arguments must be plain ASCII and must not contain `$`, a backtick, `%`, `^`, `!`, `&`, `|`, `;`, `<`, `>` or smart quotes: the guards deny such commands. Keep `--title` short and plain; put other text in `patch.json`.

| Command | Use |
|---|---|
| `init <slug> --title "..." --lane quick\|full --risk R0-R4` | Create the run folder; print one JSON line: `run`, `plan`, `patch`, `brief`, `cards_dir`, `critic`, `canary`, `verified_diamond_available`, `warnings`. |
| `apply <run>` | Apply `<run>/patch.json`; print `APPLIED`, or `REJECTED` with problems, then the gate line. Always deletes the patch file. |
| `collect <run>` | Merge `context-brief.md`, `cards/*.json`, `critic.json`; print a digest. Safe to re-run. |
| `show <run> [section]` | Compact summary, or one top-level section as JSON (how you read the plan). |
| `validate <run>` | Gate and evidence check, no writes. |
| `render <run>` | Write PLAN.md; print its path. |
| `finalize <run>` | Gate, then freeze: totals, hand-off block, content hash, PLAN.md. |
| `revise <run>` | Reopen a **final** plan as the next draft revision (history archived). |
| `export <run> --to <dir> [--force]` | Copy rendered plan and final plan.json into the repository. Needs the user's agreement; the write guard also shows an approval prompt. `--force` only after the user confirms an overwrite. |
| `verify <file.json>` | Check an exported plan: final, unchanged, still passing the gate. |
| `discard <run>` | Delete the run folder. |
| `canary <run>` | Write-guard check. |
| `runs-root` | Print the runs folder path. |

`<run>` is the run path `init` printed. Ownership, patch rules, gate and scoring rubric: [references/plan-schema.md](references/plan-schema.md).

## Changing the plan: patch.json
Never read or edit plan.json; use `show`. To change it, write `<run>/patch.json` and run `apply <run>`. Merge-patch semantics, id-keyed arrays (`{ "id": "X", "_delete": true }` removes an item) and script-owned fields are in the schema reference ("Patch rules", "Ownership"). A rejected patch applies nothing: read the problems, write a new patch, apply again. `apply` prints the gate each time, so you rarely need `validate`.

## Launcher check and canary
- If `init` warns that the session was not started with dd-claude, tell the user once and recommend restarting with the launcher; the write guards and memory isolation are off otherwise.
- Right after `init`, run the canary: write one line of text to the exact `canary` path from init's JSON (the guard must block it), then run `canary <run>`. On `CANARY FAIL`, stop and tell the user.

## Risk classes
R0: no semantic behavior change. R1: bounded and reversible, no sensitive boundary. R2: changes a cross-component or API contract, material behavior, or a dependency or deployment. R3: touches auth or security, schema or migrations, concurrency, payments, privacy, infrastructure, or a destructive operation. R4: needs production data or credential access, or has unresolved high-impact uncertainty.

## Phase 0: Triage (lead, no workers)
Decide the lane before spending anything:
- **skip**: a single obvious change, no product decision, one reasonable approach. Say so, recommend implementing directly, stop. Run nothing.
- **quick**: clear problem, one obvious approach plus a fallback, risk R2 or lower, about three components or fewer.
- **full**: unclear problem, users or scope; or materially different approaches; or R3+; or a new user-facing surface or cross-team contract; or more than about three components.

At R3+, recommend the stronger-effort variant once: restart with the launcher's `-High` option (`--high` in dd-claude.sh).

Run `init`, then record the user's request word for word in `feature.request` by patch. Upgrade quick to full when Define finds two or more material unknowns or risk reaches R3, recording each in `run.lane_changes`. Lanes never downgrade; the gate rejects a quick plan above R2. Revise a final plan only after the user confirms: run `revise`, then redo only the affected phases.

## Phase 1: Discover (diverge on the problem)
1. **Context.** Inside a repository, full lane: launch `dd-scout` in the **background** with a packet of run path, feature title, request and up to five specific questions. Quick lane: read at most three files you already know about; launch the scout only if more is needed. Outside a repository: no scout; context comes from the user.
2. **Round 1 questions, meanwhile:** product decisions independent of repository facts (users, scope edges, priority, hard constraints).
3. **External research** only when a decision depends on an external fact. Ask before using web tools; prefer official documentation.
4. When the scout finishes, run `collect` and read `context-brief.md`.

## Phase 2: Define (converge on the problem)
Write by patch: `problem`, `goals`, `non_goals` (mandatory in the full lane), `success_metrics`, `constraints`, `acceptance_criteria`, `decisions`.
- `acceptance_criteria`: Given/When/Then, each independently testable with an observable outcome.
- `decisions`: every settled question, tagged `decided_by`: `user`, `repo-fact` or `planner-assumption`.
- If priorities change the trade-offs ("ship this week", "this must scale"), set `scoring.weights`.

**Full-lane checkpoint.** Before Develop, show a compact framing (problem, users, goals, non-goals, ACs) and ask for confirmation in the same round as any remaining questions. The quick lane skips it and is confirmed at the end.

## Question protocol (batched by dependency)
- Ask only decisions the user alone can make; facts come from the repository, the brief or the docs.
- Ask at most **4 questions per round**, **2-4 options each**. Recommended answer first, labeled "(Recommended)", with its trade-off. Leave custom answers enabled.
- Each round asks only the current frontier: questions whose prerequisites are settled.
- Allow at most **3 rounds**, plus one extra round only for critic blockers and the end-of-run round in Phase 5. After that, proceed with your recommendations recorded as `planner-assumption`. If the user says "go with your recommendations", stop asking. Zero questions is valid.
- Update `run.question_rounds` by patch per round.
- Every question goes through the question tool until `finalize`. With no question tool (subagent), do not guess: return the open questions, each with a recommended answer, to your caller.

## Phase 3: Develop (diverge on the solution)
- **Quick lane.** Write at least two options by patch (`source` becomes `lead`) that differ in a structural decision, not only in details.
- **Full lane.** Choose 2-3 lenses: default `minimal` and `robust`; add `experience` for a user-facing surface; add `contrarian` when the framing is uncertain or the obvious approach is expensive. Launch every ideator **in one message** so they run in parallel. Packet order: run path, plan.json path, brief path, then lens, card id (O1, O2, ...) and card path `<run>/cards/<id>.json` (shared part first, for a cacheable prefix). Then run `collect`.
- You may add one `hybrid` option by patch that combines the strongest parts and names its sources.

## Phase 4: Deliver (converge on the solution)
1. **Score** every option by patch: integers 1-5 for fit, risk, effort, ux, reversibility (rubric in the schema reference). Set `chosen_option`, which must cover every AC. If it is not the top scorer, `decision_rationale` must say why.
2. **Design** by patch: `design` (rollout mandatory at R2+), `test_strategy`, `risks`, `open_questions` (non-blocking ones need an owner).
3. **Slice** into vertical, independently verifiable increments (`slices`), each tracing to ACs with `depends_on`, `contracts`, `draft_paths`, `risk`, `size`. Contract-defining slices first; no slice's risk exceeds the plan's.
4. **Critic.** Always in the full lane; in the quick lane only at R2. Launch `dd-critic` with run path, plan.json path, brief path and critic path, then run `collect`. Resolve findings by patch (`critic.findings` resolution):
   - blocker or high: change the plan, or justify in `resolution`;
   - medium or low: fix if cheap, or record "accepted: reason", or move to risks or open questions.

   A second pass only if a blocker changed the chosen option or a contract; never more than two.
5. **Gate.** `apply` prints the gate after every patch. Once it passes, run `finalize`. On failure, patch exactly the listed errors and retry, up to 3 attempts in total, then stop and report the remaining errors. Planner-assumption warnings go in the summary for the user to confirm. After a passing `finalize`, change nothing; use `revise`.

## Worker failure
Re-run a failed worker once. Never copy a worker's artifact yourself. If an ideator fails twice, continue while two distinct lenses remain; otherwise write the missing option by patch. If the critic fails twice, stop and report.

## Phase 5: End of run (one question round)
Show a short summary: title, revision, lane and risk, chosen option with a one-line reason, slice order, assumptions to confirm, open questions. Then ask one round:
1. **Export:** a suggested folder, a custom path, or don't export. Run `export <run> --to <dir>`; the tool asks the user to approve it. If it prints `REFUSED`, ask before using `--force`.
2. **Hand-off,** only when init's `verified_diamond_available` is true and the plan is final: "Yes, print the command for a fresh session" or "No". It needs an export and you never start it yourself. After a yes, print `/verified-diamond --plan "<exported plan.json>" <title>`. See [references/verified-diamond-handoff.md](references/verified-diamond-handoff.md).
3. **Discard** the temp run (recommended after export): `discard <run>`.

Never start implementation. This workflow only plans.

## Stopping conditions
Stop and tell the user when:
- the request is really several features (offer to plan them separately);
- a blocking question has no answer after the round budget;
- the gate fails after up to 3 attempts, or the critic fails twice;
- the plan would require production data or credentials (R4);
- the repository contradicts a settled decision;
- a run path is rejected, or `CANARY FAIL` is printed.

## Security, privacy, hygiene
Never put secrets, credentials, tokens or customer data into plan files, worker packets or research queries. The gate rejects credential-shaped strings. Runs are local temp folders; external research is ask-gated and never includes source code. Plan one feature per session; implementation belongs in a fresh session.
