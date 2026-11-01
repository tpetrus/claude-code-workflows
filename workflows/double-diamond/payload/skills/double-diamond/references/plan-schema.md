# plan.json reference (schema_version 2)

`scripts/dd_plan.py` and `scripts/plan_core.py` define this schema. Template: [../assets/plan.template.json](../assets/plan.template.json). The lead never edits plan.json: it writes `patch.json`, runs `apply` and reads with `show`. PLAN.md is generated.

## Run folder
A session-scoped temp folder, `<runs root>/<run_id>/`; `init` prints the paths. Nothing reaches the repository except through `export`, after the user agrees. Contents: `plan.json` (source of truth, written only by dd_plan.py), `patch.json` (the lead's change file, deleted by apply), `context-brief.md` (double-diamond-scout), `cards/<id>.json` (double-diamond-ideator, one per option), `critic.json` (double-diamond-critic), `PLAN.md` (generated), `history/` (plan.r<N>.json, PLAN.r<N>.md, critic.r<N>.json, archived by revise).

## Fields the lead fills (all lead-owned and patchable)
| Field | Shape | Gate |
|---|---|---|
| `feature.title`, `feature.request` | strings | title required; request is the user's words, verbatim |
| `lane`, `risk` | `quick`/`full`, `R0`-`R4` | required; lane only goes quick to full |
| `problem` | `{ statement, users[], evidence[] }` | statement and at least one user |
| `goals[]`, `non_goals[]` | strings | at least one goal; at least one non-goal in the full lane |
| `success_metrics[]`, `constraints[]` | strings | none |
| `acceptance_criteria[]` | `{ id: "AC1", given, when, then }` | at least one; unique ids; every AC delivered by a slice |
| `decisions[]` | `{ id: "D1", question, answer, decided_by: user\|repo-fact\|planner-assumption, rationale }` | assumptions warn |
| `scoring.weights` | optional subset of `{ fit, risk, effort, ux, reversibility }`, non-negative numbers | missing keys take defaults (30/25/20/15/10), then the set is normalized; 0 drops a criterion |
| `options[]` | see Options | at least 2; full lane needs at least 2 distinct lenses |
| `chosen_option`, `decision_rationale` | option id, string | chosen must cover all ACs |
| `design` | `overview`, `components[{name,responsibility,location}]`, `contracts[{id:"C1",name,kind,definition}]`, `data`, `ux`, `accessibility`, `security_privacy`, `observability`, `rollout` | overview required; rollout at R2+ |
| `test_strategy` | `{ unit, integration, e2e, manual, non_functional }` | at least one non-empty string |
| `risks[]` | `{ id: "K1", description, likelihood, impact, mitigation }`, likelihood and impact low/medium/high | at least one at R2+ |
| `slices[]` | `{ id: "S1", title, objective, depends_on[], acceptance_refs[], contracts[], risk, size: S\|M\|L, draft_paths[] }` | at least one; no cycles; refs exist; slice risk cannot exceed plan risk |
| `open_questions[]` | `{ id: "Q1", question, owner, blocking }` | a blocking question fails the gate |
| `critic.findings[].resolution` | string (only that field, only existing ids) | required for blocker/high findings |
| `run.question_rounds`, `run.lane_changes` | number, array | none |

## Ownership
Script-owned; a patch touching any of these is rejected: `schema_version`, `kind`, `run_id`, `revision`, `status`, `created`, `updated`, `project.*`, `dropped_options`, `options[].total`, `options[].source` (set by the script), `critic.passes`, `critic.verdict`, `evidence.*`, `handoff`, `run.harness` (always `claude-code`), `run.session_key`.

`plan_hash` covers everything except `handoff`, `updated`, `status` and `run`. Any other change after `finalize` makes `verify` fail until the plan is revised and finalized again.

## Patch rules (`patch.json`, then `apply <run>`)
- JSON merge patch: object keys merge recursively. `null` resets a key that exists in the template to its template value, and deletes any other key. Any other array replaces the whole value.
- Arrays keyed by `id`: `acceptance_criteria`, `decisions`, `options`, `design.contracts`, `risks`, `slices`, `open_questions`, `critic.findings`. Every patch item needs a string `id`. `{ "id": "X", "_delete": true }` removes an existing item (unknown id is a problem). An existing id deep-merges; a new id is appended in patch order (for `critic.findings` a new id is a problem). Existing order is kept.
- A value whose JSON type differs from the template's at the same path is a problem, except `null`.
- `apply` collects every problem first. On any problem it prints `REJECTED` with up to 12 problems and applies nothing. `patch.json` is deleted in every case, so the next patch is a new file.
- A final plan rejects patches: run `revise` first.
- On success `apply` prints `APPLIED <keys>` and the gate line (`GATE PASS`, or `GATE FAIL` with the errors for the touched keys).

## Options
Id `O1`-`O99`. `collect` merges ideator cards into `options`; a lead-written option has the same shape with `source: "lead"`. Fields: `id`, `source` (`ideator|lead`), `lens` (`minimal|robust|experience|contrarian|hybrid`), `summary`, `approach[]`, `components[{name,responsibility,location}]`, `contracts[]` (new or changed interfaces), `pros[]`, `cons[]`, `risks[]`, `effort` (`S|M|L`), `covers_all_acs`, `uncovered_acs[]`, `confidence` (`low|medium|high`), `scores` (`{fit,risk,effort,ux,reversibility}`, integers).
- Existing `source: "ideator"` option: a patch item may contain only `id`, `scores` and `_delete`.
- Existing `source: "lead"` option: any card field plus `scores` and `_delete`, never `source` or `total`.
- A new id gets `source: "lead"` (a patch saying `ideator` is rejected). If the id is in `dropped_options`, it is removed from there (a deliberate revival).
- `_delete: true` removes the option, adds its id to `dropped_options`, clears `chosen_option` if it pointed at it, and drops its card evidence.
- `collect` replaces an ideator option's card fields when its card changed, keeps the lead's `scores`, and rejects a card whose id belongs to a lead option or a dropped option.

## Evidence (`collect`)
`collect` records each artifact's SHA-256 in `evidence` and prints one digest line per item:
- **Brief:** `context-brief.md` needs the headings Stack, Relevant code, Analogous feature, Conventions, Constraints and risks, Commands and Unknowns; missing headings reject it.
- **Cards:** each `cards/O<n>.json` is named after its own `id`. An unchanged card is skipped.
- **Critic:** `critic.json` increments `critic.passes`, sets `verdict` and findings, and records the basis it ran against (chosen option, risk, hashes of the contracts and ACs). An existing `resolution` is kept only when the finding's id, text and severity are unchanged. Shape: `{ verdict: "sound | sound with fixes | rethink", findings: [{ id, severity: blocker|high|medium|low, area, finding, suggestion }] }`.
- `validate` and `finalize` also check the disk: recorded evidence whose file is missing or changed is an error (run `collect`); an artifact never collected is a warning. `verify` uses only the evidence recorded in the file.

## Gate (`validate`, `apply`, `finalize`)
- `schema_version` must be 2. A quick-lane plan may not exceed R2.
- **Arrays** (goals, non_goals, users, evidence, success_metrics, constraints, approach, depends_on, acceptance_refs, contracts, draft_paths, uncovered_acs) must hold non-empty strings. An option with `covers_all_acs: true` may not have `uncovered_acs`.
- **Critic** required when the lane is full or risk is R2+: `evidence.critic_sha256` must be set (run double-diamond-critic, then `collect`). It is stale (error) when the chosen option or risk changed since it ran; changed contracts or ACs only warn (re-run only if a blocker fix changed them).
- A full-lane plan in a git repository needs a collected brief (run double-diamond-scout, then `collect`). Every `source: "ideator"` option needs recorded card evidence.
- **Placeholders** fail: `TBD` anywhere, `TODO`/`FIXME` as a leading marker or `TODO:`, `<fill...>`, lorem ipsum.
- **Credential-shaped strings** fail in lead-authored sections and critic output: private keys, bearer tokens, provider keys, JWTs, URLs with an embedded password. Names such as `pk-customer-account-lookup-service` or `secret: PAYMENTS_GATEWAY_KEY_VAULT_REF` pass, because real keys mix letters and digits. The verbatim `feature.request` is checked only for credentials.
- **Warnings** (do not fail): planner assumptions, unresolved medium/low findings, a `rethink` verdict, unused contracts, a non-top-scored choice, draft paths that are absolute or contain `..`.

## Scoring rubric (integers 1-5, higher is better)
| Criterion | 5 | 3 | 1 |
|---|---|---|---|
| fit | uses existing architecture, patterns and constraints as they are | needs a few new patterns | fights the architecture or a settled decision |
| risk | well-understood change, easy test surface | some unknowns, mitigable | high chance of defects, security or data issues |
| effort | about S, mostly reuse | about M | about L or more, or new infrastructure |
| ux | best experience for whoever uses the change, all states covered | adequate | confusing, or misses key states |
| reversibility | flag or config, easy rollback | rollback needs a deploy | hard to undo (data migration, public contract) |

Score against the ACs and constraints, not taste. If an option scores 1 on any criterion, explain why in its `cons`.
