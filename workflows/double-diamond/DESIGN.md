# double-diamond 2.0 design contract

Frozen contract for `double-diamond-claude-code`, a standalone Claude Code planning workflow. Code, prompts and `tools/smoke_test.py` implement it exactly.

## Implementation
- Python 3.9+, standard library only; the plan script, the hooks and the launcher self-test run with `python3`.
- Payload scripts: `dd_plan.py` (CLI), `plan_core.py` (pure logic), `guard_write.py` (PreToolUse hook), `dd-claude.ps1` and `dd-claude.sh` (launchers).
- Maintainer tools (not installed): `tools/update_manifest.py` regenerates the manifest; `tools/smoke_test.py` checks the guard, CLI and gate end to end.

## 1. Invariants
1. A run uses only its repository, its own run folder and the current session: no global plan state, memory files, or cross-repo/cross-session lookup.
2. Each run has its own temp folder; only the creating session can resume it.
3. Nothing reaches the repository except through `export`, after the user agrees.
4. The lead never edits `plan.json`: it writes `patch.json` and runs `apply`. Each worker writes only its own artifact.
5. Nothing assumes a language, framework or stack.
6. Every rule that can be deterministic is enforced in code; the guard and the main CLI path are checked by `tools/smoke_test.py`.

## 2. Run workspace
- **Runs root**: `runs_root() = abspath(DD_RUNS_ROOT)` when set, else `<tempfile.gettempdir()>/double-diamond`. `DD_RUNS_ROOT` is for tests; the launcher, the guard and the CLI all read the same function, so they always agree.
- **IDs**: `FEATURE_SLUG = [a-z0-9]+(-[a-z0-9]+)*` (max 40); `RUN_ID = [a-z0-9]+(-[a-z0-9]+)*-\d{8}t\d{6}z-[0-9a-f]{8}`; `OPTION_ID = O\d{1,2}`. `new_run_id(slug)` returns `<slug>-<YYYYMMDD>t<HHMMSS>z-<8 random hex>` (UTC).
- **Layout** of `<runs root>/<run_id>/`:
  ```
  plan.json         written only by dd_plan.py
  patch.json        lead's change file; deleted by apply
  context-brief.md  dd-scout
  cards/O<n>.json   dd-ideator, one per option
  critic.json       dd-critic
  PLAN.md           generated
  history/          plan.r<N>.json, PLAN.r<N>.md, critic.r<N>.json
  ```
- **resolve_run(target)**: accepts the run folder or its `plan.json`. Valid only if the folder's parent equals the runs root (case-insensitive on Windows), its name matches `RUN_ID`, and `plan.json` exists with `kind: "double-diamond-plan"`. Else usage error `not a double-diamond run in this session's runs folder: <target>`.
- **Session binding**: `session_key() = sha256(CLAUDE_CODE_SESSION_ID)[:16]` when the variable is set, else `""`; `init` stores it in `run.session_key`. Every run-scoped command fails with usage error `this run belongs to another session; start a new run with init` when stored and current keys are both non-empty and differ.
- **Sweep** (by `init`, TTL 72 h): `lstat` each runs-root entry, never follow links. Remove a directory whose name matches `RUN_ID` and whose `plan.json` mtime (directory mtime if missing) exceeds the TTL. Remove a file `dd-canary-.+\.txt` older than 10 minutes, or `dd-launch-\d+\.settings\.json` older than 24 hours. Touch nothing else.
- **Privacy (non-Windows)**: runs root and run folders are created with mode `0700`. The runs root must not be a symlink, must be owned by the current user and must have no group/other permission bits; else every command fails with `runs root is not private: <path>`.
- **Canary path**: `<runs root>/dd-canary-<run_id>.txt`.

## 3. plan.json schema 2
Template `assets/plan.template.json`, key order as shown:
```json
{
  "schema_version": 2, "kind": "double-diamond-plan", "run_id": "", "revision": 1, "status": "draft",
  "lane": "full", "risk": "R1", "created": "", "updated": "",
  "feature": { "slug": "", "title": "", "request": "" },
  "project": { "name": "", "is_git": false, "base_revision": "" },
  "problem": { "statement": "", "users": [], "evidence": [] },
  "goals": [], "non_goals": [], "success_metrics": [], "constraints": [],
  "acceptance_criteria": [], "decisions": [], "scoring": { "weights": {} },
  "options": [], "dropped_options": [], "chosen_option": "", "decision_rationale": "",
  "design": { "overview": "", "components": [], "contracts": [], "data": "", "ux": "", "accessibility": "",
        "security_privacy": "", "observability": "", "rollout": "" },
  "test_strategy": { "unit": "", "integration": "", "e2e": "", "manual": "", "non_functional": "" },
  "risks": [], "slices": [], "open_questions": [],
  "critic": { "passes": 0, "verdict": "", "findings": [] },
  "evidence": { "brief_sha256": "", "cards": {}, "critic_sha256": "",
          "critic_basis": { "chosen_option": "", "risk": "", "contracts_sha256": "", "acs_sha256": "" } },
  "handoff": {},
  "run": { "harness": "", "session_key": "", "question_rounds": 0, "lane_changes": [] }
}
```
- `init` sets `run.harness` to `"claude-code"`.
- `content_hash` excludes `handoff`, `updated`, `status`, `run`.
- `project` never stores an absolute path. `detect_project(cwd)` returns `{ name, root, is_git, base_revision }` via `git rev-parse --show-toplevel` and `git rev-parse HEAD` (works in linked worktrees); outside git `root = cwd`, `is_git = false`. Only `name`, `is_git`, `base_revision` enter the plan.

## 4. Ownership
Lead-owned (patchable): `feature.title`, `feature.request`, `lane` (only `quick` to `full`; downgrade rejected), `risk`, `problem`, `goals`, `non_goals`, `success_metrics`, `constraints`, `acceptance_criteria`, `decisions`, `scoring`, `options` (below), `chosen_option`, `decision_rationale`, `design`, `test_strategy`, `risks`, `slices`, `open_questions`, `critic.findings[].resolution` (only that field, only existing ids), `run.question_rounds`, `run.lane_changes`. Everything else is script-owned; a patch touching it is rejected. Keys `__proto__`, `constructor` and `prototype` are rejected at any depth.

**Options**: id must match `OPTION_ID`.
- Existing `source: "ideator"`: patch item may contain only `id`, `scores`, `_delete`.
- Existing `source: "lead"`: any card field plus `scores`, `_delete`; never `source` or `total`.
- New id: `source` becomes `"lead"` (a patch saying `"ideator"` is rejected). If the id is in `dropped_options`, remove it from that list (deliberate revival).
- `_delete: true`: remove the option, append its id to `dropped_options` (deduplicated), clear `chosen_option` if it pointed there, delete `evidence.cards[id]`.

## 5. Patch protocol (`apply <run>`)
1. No `patch.json`: usage error `no patch.json in <run>`.
2. Read it, then **always delete it** (success or failure); the next patch is a new file.
3. Plan `status: "final"`: `REJECTED` with `plan is final: run revise first`.
4. Invalid JSON or not an object: `REJECTED`.
5. Collect every problem first. Any problem: print `REJECTED` and up to 12 `  - <problem>` lines, apply nothing, exit 1.
6. Merge, compute totals, set `updated`, write `plan.json` only if content changed.
7. Print `APPLIED <comma-separated top-level keys>`, then `GATE PASS (<w> warnings)` or `GATE FAIL (<e> errors, <w> warnings)`, then up to 8 `  - <error>` lines for errors mentioning a touched key (else the first errors). Then `  - ... <n> more (run validate)` if truncated. Exit 0.

**Merge** (RFC 7386 plus id-keyed arrays):
- Objects merge recursively. `null` resets a key present in the template to its template value, and deletes any other key.
- `ID_ARRAYS = acceptance_criteria, decisions, options, design.contracts, risks, slices, open_questions, critic.findings`. Every patch item needs a string `id`. `{ "id": "X", "_delete": true }` removes an existing item (unknown id: problem). Existing id deep-merges (same rules). New id is appended in patch order (for `critic.findings` a new id is a problem). Existing order is kept.
- Any other array replaces the whole value.
- A JSON type (object, array, string, number, boolean) differing from the template's at the same path is a problem, except `null`.

## 6. Collect and evidence (`collect <run>`)
`file_hash(buf)` = full lowercase SHA-256 hex of the bytes.
- **Brief**: if `context-brief.md` exists it must have every heading in `BRIEF_HEADINGS = Stack, Relevant code, Analogous feature, Conventions, Constraints and risks, Commands, Unknowns` as a line matching `^##\s+<heading>` (case-insensitive). Missing: `brief: rejected (missing: ...)`, `evidence.brief_sha256 = ""`. Else `evidence.brief_sha256 = file_hash` and `brief: ok` (or `brief: ok (over 600 words)`). Absent file: `brief: absent`, evidence unchanged.
- **Cards**: `cards/*.json` sorted. File name must be `<id>.json` with `id` matching `OPTION_ID` and `card.id` equal. Ids in `dropped_options` are skipped (`skipped (dropped)`). Rejected: a card whose id belongs to a `source: "lead"` option, or with `check_card` problems. Same `file_hash` as `evidence.cards[id]`: `unchanged`. Else replace the option's card fields (`CARD_FIELDS`), keep `scores`, set `source: "ideator"`, record `evidence.cards[id]`.
- **Critic**: if `critic.json` exists, invalid JSON or `check_critic` problems give `critic: rejected (...)`; same `file_hash` as `evidence.critic_sha256` gives `critic: unchanged`. Else `critic.passes += 1`, set `verdict`, findings `{ id, severity, area, finding, suggestion, resolution }` (resolution kept only when id, finding and severity are unchanged), `evidence.critic_sha256 = file_hash`, `evidence.critic_basis = { chosen_option, risk, contracts_sha256, acs_sha256 }` from the plan at that moment (16-hex stable hash).
- Write `plan.json` only if content changed. Output is a digest, one line per item, each at most 300 characters; exit 0 (rejections appear in the digest):
  ```
  brief: ok
  card O1 merged: <lens> | <summary> | effort <S|M|L> | covers_all_acs <bool> | confidence <c> | pros: a; b | cons: c; d
  card O2 rejected: <problems>
  critic: merged pass 1, verdict <v>
  critic F1 high <area>: <finding> -> <suggestion>
  ```

**verify_disk(plan, brief, critic, cards)** (pure; buffers or `None`; `cards` maps id to buffer) returns `(errors, warnings)`. Errors: recorded evidence whose file is missing or whose hash differs (`<artifact> changed since collect: run collect`). Warnings: artifact on disk never collected. Used by `validate` and `finalize`, not `verify`.

## 7. Gate (`validate_plan`, schema 2)
- `schema_version` must be 2.
- `test_strategy`: at least one of `unit`, `integration`, `e2e`, `manual`, `non_functional` non-empty.
- Critic required when `lane == "full"` or risk is R2+: `evidence.critic_sha256` non-empty (`critic pre-mortem must run before finalize (run dd-critic, then collect)`).
- Critic stale (error) when `critic_basis.chosen_option` or `critic_basis.risk` differ from the plan: `critic is stale: the chosen option or risk changed since it ran; re-run dd-critic`. Contracts or AC hash changed: warning `contracts or acceptance criteria changed after the critic ran; re-run dd-critic only if a blocker fix changed them`.
- Full lane with `project.is_git`: `evidence.brief_sha256` non-empty (`full lane in a repository: run dd-scout, then collect`).
- Every `source: "ideator"` option needs `evidence.cards[id]`.
- Credentials: `(sk|pk|rk)-` keys must contain a digit. Generic `api key|secret|password|access token|auth token` assignments must have a value of 24+ characters mixing letters and digits. So `pk-customer-account-lookup-service` and `secret: PAYMENTS_GATEWAY_KEY_VAULT_REF` pass; real key shapes fail.
- Also: placeholders, quick lane limited to R2, slices and cycles, AC coverage, R2+ risks and rollout, blocking open questions, resolutions for blocker/high findings, warnings (see `references/plan-schema.md`).

## 8. CLI (`scripts/dd_plan.py`)
Exit codes: 0 success; 1 gate fail, rejected, refused, not verified or canary fail; 2 usage error. Writes are atomic (temp file, rename, one retry on a Windows lock). Every run-scoped command applies `resolve_run` and the session check.

| Command | Behavior |
|---|---|
| `init <slug> [--title "..."] [--lane quick\|full] [--risk R0-R4]` | Slug must match `FEATURE_SLUG`. Sweep, create `<run>/cards/`, write the plan (template plus `run_id`, feature, lane, risk, project, timestamps, `run.harness = "claude-code"`, `run.session_key`). Print one JSON line: `{"run","plan","patch","brief","cards_dir","critic","canary","verified_diamond_available","warnings"}`. Warning when `DD_LAUNCHER` is not `1`: `not started with dd-claude: write guards and memory isolation are off`. `verified_diamond_available` is true when `~/.claude/skills/verified-diamond/SKILL.md` exists. |
| `apply <run>` | Section 5. |
| `collect <run>` | Section 6. |
| `validate <run>` | Gate plus `verify_disk`. Prints `ERROR  ...`, `WARN   ...`, then `PASS (...)` or `FAIL (...)`. Exit 1 on errors. Never writes. |
| `show <run> [section]` | No section: compact text summary (title, slug, revision, status, lane, risk; AC ids; options with lens, total and chosen marker; dropped options; slice ids with dependencies; critic passes, verdict and unresolved finding ids; evidence presence; gate line). With a section: minified JSON of that top-level key, or usage error. |
| `render <run>` | Write `PLAN.md`, print its path. |
| `finalize <run>` | Already final: print `ALREADY FINAL r<N>: use revise to change it`, exit 0, no write. Else gate plus `verify_disk`; on errors print them, exit 1, no write. On pass: `handoff = derive_handoff(plan)`, `status = "final"`, write `plan.json` and `PLAN.md`, print the gate line and `{"plan","markdown","revision"}`. |
| `revise <run>` | Requires `final` (else usage error). Archive `history/plan.r<N>.json`, `history/PLAN.r<N>.md` (if present); copy `critic.json` to `history/critic.r<N>.json` (if present). `revision += 1`, `status = "draft"`, `handoff = {}`, `project.base_revision` from `detect_project()`. If the base revision changed and a brief was collected, print `WARN   the context brief predates the current HEAD; re-run dd-scout if the change matters`. Print `{"plan","revision","archived"}`. |
| `export <run> --to <dir> [--force]` | Section 9. |
| `verify <file.json>` | Section 9. Not run-scoped. |
| `discard <run>` | Remove the run folder and its canary; print `DISCARDED <run_id>`. |
| `canary <run>` | Canary file exists: delete it, print `CANARY FAIL: the write guard is not active; stop and tell the user`, exit 1. Else print `CANARY OK`. |
| `runs-root` | Print the runs root. |
| `help` | Usage. |

## 9. Export and verify
- `export`: repository root is `detect_project().root`. `--to` is required and relative, with no `..` segment, drive letter, `:`, `.git` segment or Windows reserved device name (`con`, `prn`, `aux`, `nul`, `com1-9`, `lpt1-9`, with or without an extension), and must resolve inside the root, including after `realpath` of its nearest existing ancestor. Every path refusal prints `REFUSED <reason>`, exit 1; a missing or empty `--to` is a usage error (exit 2). The Bash guard answers `ask` for `export`, so the user approves it. Writes `<slug>.plan.md` (rendered; when not final, prefixed by `> **DRAFT**: not finalized. Do not hand off.` and a blank line) and, only when final, `<slug>.plan.json` (the plan with `run.session_key = ""`). An existing target without `--force` prints `REFUSED <relative path> exists (re-run with --force only after the user confirms)`, exit 1, nothing written. Creates the folder, writes atomically, prints `EXPORTED <relative path>` per file, never runs git.
- `verify <file.json>`: any path, including an exported file; uses only the evidence recorded in it (no sibling files). Checks `kind`, `schema_version == 2`, `verify_handoff` (final status, content hash, stored hand-off block equal to `derive_handoff(plan)`) and `validate_plan`. Prints `VERIFIED <slug> r<N> hash <hash>`, or `ERROR  ...` lines plus `NOT VERIFIED` (exit 1).
- The hand-off strings (`generated_by: "double-diamond dd_plan.py finalize"` and the notes) are compared byte for byte by `verify`; changing them invalidates previously exported plans.

## 10. Write guard (`scripts/guard_write.py`, `PreToolUse` hook)
`python3 guard_write.py <plan|brief|card|critic|bash>`; hook JSON on stdin. Fails closed: unknown mode, unreadable payload or missing field is denied.
- **Write modes** use `tool_input.file_path`. Leading `~/` or `~\` expands to the home folder; relative paths resolve against `payload.cwd`. `allowed_write(file_path, mode, cwd, run_exists)` is pure (`run_exists(dir)` is injected). The path relative to the runs root (case-folded on Windows) must not be empty, start with `..` or be absolute; `parts[0]` must match `RUN_ID` and `run_exists` must be true. Then: `plan` allows `[id, "patch.json"]`; `brief` `[id, "context-brief.md"]`; `card` `[id, "cards", O\d{1,2}.json]`; `critic` `[id, "critic.json"]`.
- **bash mode** uses `tool_input.command`. `bash_invocation(command, script_path)` (pure) allows only `python3 "<script_path>" <command> [args]` or the same unquoted:
  - only printable ASCII and tab (no newline, carriage return, smart quotes or other non-ASCII);
  - no backtick, `$`, `%`, `^` or `!` anywhere;
  - no unquoted `; & | < > ( )`, and no backslash escaping a quote, space or one of those characters;
  - tokens split on unquoted whitespace; single or double quotes group;
  - token 1 is `python3` or `python3.exe`, case-insensitive;
  - token 2, after MSYS conversion (`/c/Users/...` to `C:/Users/...`) and `\` to `/` (case-folded on Windows), equals the `dd_plan.py` next to the guard;
  - token 3 is one of the CLI command names.
- **Hardening**:
  - Non-Windows: the relative path is split on `/` only.
  - Also deny when the `realpath` of the target's nearest existing ancestor is outside `realpath(runs root)`.
  - Modes `plan` and `bash` are session-bound: when payload `session_id` and the run's `run.session_key` are both non-empty they must match (`sha256(session_id)` first 16 hex). Worker modes are not session-bound until subagent session ids are verified live.
  - Every unexpected exception is a deny.
- **`export` asks**: an allowed bash command whose command name is `export` gets `permissionDecision: "ask"`.
- **Decision output** (stdout, exit 0): allowed is `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"allow","permissionDecisionReason":"double-diamond guard: allowed"}}`. Denied is the same shape with `"deny"` and a reason (also written to stderr). A JSON decision is used instead of exit code 2 because a PowerShell wrapper rewrites a native exit 2 to 1, which Claude Code treats as non-blocking.

## 11. Launcher (`scripts/dd-claude.ps1`, `scripts/dd-claude.sh`)
`pwsh -File dd-claude.ps1 [-High] [extra claude args]` and `dd-claude.sh [--high] [extra args]`; the only supported entry point.
1. `python3` and `claude` must be on PATH.
2. Guard reachability: hooks run `python3 "$HOME/.claude/skills/double-diamond/scripts/guard_write.py"`. Compute `HOME` (PowerShell: `$env:HOME`, else `$env:USERPROFILE`) and require that file to exist; else stop with an explanation. The plan script path must be printable ASCII without `` ` $ % ^ ! | `` or `"`, because the Bash guard refuses those.
3. Guard self-test: pipe a write outside the runs folder through the guard with `python3`; refuse to start unless it answers `deny` (this also catches the Microsoft Store `python3` alias stub on Windows).
4. Runs root from `python3 "<scripts>/dd_plan.py" runs-root`; create it (mode 700 on macOS and Linux).
5. Write `<runs root>/dd-launch-<pid>.settings.json`: `{"autoMemoryEnabled":false,"claudeMdExcludes":[<user CLAUDE.md>, <user rules folder>/**]}`, each in forward-slash and backslash forms. The user CLAUDE.md is `$CLAUDE_CONFIG_DIR/CLAUDE.md`, else `<home>/.claude/CLAUDE.md`.
6. Set `DD_LAUNCHER=1` and `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` for the child only (restore afterwards), run `claude --agent dd-planner|dd-planner-high --add-dir <runs root> --settings <file> <extra>`, delete the settings file, exit with claude's code.

## 12. Agents
**Every agent prompt** contains this precedence line verbatim:
> Work only from the repository, the run's files and this session's request. Where other loaded instructions conflict with this workflow (delegating to other agents, implementing, tool or framework preferences), this workflow wins.

**Worker prompts** also say: write your file within your first few steps, then refine it; if you cannot write it, say so in one line and do not return the artifact as text.
- **Scout**: covers architecture and boundaries, error handling, configuration, testing and naming; UI, state and styling only if the repository has them.
- **Experience lens**: any user-facing surface (UI, CLI, API, docs, operators); accessibility (WCAG 2.2 AA) only when there is a UI.

**Planner prompt** also says: follow the preloaded double-diamond skill; plan only; write only `patch.json`; run only the plan script, exactly as the skill shows; resume only a run created earlier in this conversation and never browse the runs folder; never write memory files; every question to the user goes through AskUserQuestion until `finalize`.

**Frontmatter**:
- `dd-planner`:
  - `tools: Read, Grep, Glob, Bash, Write, Edit, Agent(dd-scout, dd-ideator, dd-critic), AskUserQuestion, WebFetch, WebSearch` (no `Skill`; the skill is preloaded);
  - `model: opus`, `effort: medium`, `skills: [double-diamond]`;
  - hooks: matcher `Write|Edit` runs `python3 "$HOME/.claude/skills/double-diamond/scripts/guard_write.py" plan`; matcher `Bash` runs the same with `bash`.
- `dd-planner-high`: identical except `effort: high` and its description.
- Workers: `tools: Read, Grep, Glob, Write`, `omitClaudeMd: true`, a `Write|Edit` guard hook in their own mode.
  - `dd-scout`: `model: haiku` (effort not set), `maxTurns: 30`, mode `brief`.
  - `dd-ideator`: `model: sonnet`, `effort: medium`, `maxTurns: 12`, mode `card`.
  - `dd-critic`: `model: sonnet`, `effort: high`, `maxTurns: 20`, mode `critic` (a different model from the lead, so the blind spots differ).

## 13. Skill and docs
- **SKILL.md frontmatter**: `name: double-diamond`; description of at most 200 characters; `compatibility`; `metadata` with `version: "2.0.0"` and `family: diamond-workflows`. Never `disable-model-invocation` (it would block the planner's `skills:` preload).
- **SKILL.md body**:
  - fewer than 500 lines (target about 170);
  - contains `at most **4 questions per round**`, `at most **3 rounds**` and `up to 3 attempts`;
  - every script call is `python3 "<skill-dir>/scripts/dd_plan.py" <command> ...`, path quoted, nothing chained; `<skill-dir>` is `${CLAUDE_SKILL_DIR}`.
- **Stack-neutral**: no framework or stack names in the skill, references or agent prompts (for example Angular, React, Vue, Rails, Spring Boot, ASP.NET).
- **End-of-run question round** (one round):
  1. export (suggested folder, custom path, or don't export);
  2. only when `verified_diamond_available` and final: hand off ("Yes, print the command for a fresh session" or "No"). Requires an export; never automatic. The printed command is `/verified-diamond --plan "<exported plan.json>" <title>`;
  3. discard the temp run (recommended after export).

## 14. Package
- `tools/update_manifest.py` regenerates `MANIFEST.json` from `payload/`: keeps `kind`, `name`, `harness`, sets `version: "2.0.0"` and `generated`, lists files sorted by path with SHA-256.
- `lib/Install-Package.ps1` (dot-sourced by `install.ps1`): `Get-PackageManifest`, `Test-PackagePayload` (refuses a payload that does not match the manifest), `Install-PackageFiles` (plans every copy, stops before writing if a target differs unless `-Force`), `Uninstall-PackageFiles` (removes only unchanged files unless `-Force`, then prunes empty folders).
- `install.ps1` refuses to write through a linked skill folder (junction or symbolic link) and replaces the link only with `-Force`, never touching its target. It warns when `python3` is missing or is the Microsoft Store stub, and when the target is not `~/.claude` (the hooks call `$HOME/.claude/...`).
- `tools/smoke_test.py` runs against the source payload with the current interpreter, in a temporary runs root (`DD_RUNS_ROOT`) and a temporary git repository. It covers the guard decisions (allow, deny, ask, session binding), the patch protocol, collect, the gate, finalize, export, verify, canary and discard. Run it after every payload change, before regenerating the manifest.

## 15. Open items (verify live before relying on them)
- Whether the guard's `allow` avoids prompts in background workers; whether `--add-dir` applies to subagents; whether `claudeMdExcludes` via `--settings` excludes the user CLAUDE.md and rules; whether subagent hook payloads carry the parent `session_id` (if they do, bind worker modes too).
- Deferred hardening: canaries for the Bash and worker hooks (only the planner's Write hook is canary-checked); Read/Glob are not session-scoped; no lock around concurrent plan.json writes (single-lead use only).
