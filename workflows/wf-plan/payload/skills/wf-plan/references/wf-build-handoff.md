# wf-build hand-off

Optional: wf-plan works without wf-build and the reverse. When both are installed, `finalize` embeds a hand-off block, and the user may export the final plan so a wf-build orchestrator starts from settled requirements instead of re-brainstorming.

## Rules
- Offered only in the end-of-run question round, when `init` reported `wf_build_available` and the plan is final, and only after the user agrees.
- Requires an export: it uses the exported `<slug>.plan.json` in the repository, never a file in the temp run folder.
- wf-plan never starts it: after a yes it only prints the command, and the user runs it in a fresh session. wf-build never writes to the plan file.

## What finalize writes
`plan.json` -> `handoff.wf_build`: `generated_by` ("wf-plan wf_plan.py finalize"), `plan_revision`, `plan_hash` (16-hex content hash; verify recomputes it), `base_revision` (git HEAD when planned), `risk`, `freeze_first` (`[{ id, name, kind, definition }]`, one per contract), `draft_dag` (one wf-build task-template object per slice), `notes` (run verify first; `verify[]` intentionally empty; `owned_paths` are drafts; the wf-build risk is authoritative).

## Slice to task-template mapping
| wf-build field | Source |
|---|---|
| `id` | `slice.id` |
| `objective` | `slice.title + ": " + slice.objective` |
| `dependencies` | `slice.depends_on` |
| `base_revision` | `project.base_revision` |
| `worker_class` | `builder` (the orchestrator may re-route to mechanical) |
| `owned_paths` | `slice.draft_paths` (drafts; re-verify) |
| `forbidden_paths` | `[]` (the orchestrator assigns these) |
| `contracts` | self-contained text per contract, `"C1 Name (kind): definition"`, so a worker packet needs no lookup |
| `acceptance` | full Given/When/Then text of each `slice.acceptance_refs` |
| `verify` | `[]` always: it comes from the repository `wf_build` command contract, never from a plan |
| `risk` | `slice.risk` |
| `retry_limit`, `escalation` | wf-build defaults |

These keys must match wf-build's `task-template.json` whenever that skill is installed.

## Integrity
`python3 "<skill-dir>/scripts/wf_plan.py" verify "<exported plan.json>"` works on the exported file alone. It prints `VERIFIED <slug> r<rev> hash <hash>` and exits 0 only when the plan is `final`, its content hash matches the hash recorded at finalize (any later edit breaks it), and it still passes the completeness gate using the evidence recorded in the file. Otherwise it prints the reasons and `NOT VERIFIED`, and exits 1.

## How to start it
In a **fresh session** started with `claude --workflow wf-build`, quoting the path: `/wf-build --plan "<exported plan.json>" <feature title>`. The orchestrator then runs `verify` on the file, compares the base revision with HEAD, adopts the ACs and settled decisions, freezes `freeze_first`, refines `draft_dag` and fills `verify` from the repository contract. It may raise the risk class and stops to ask if the plan conflicts with the repository. To change the plan, revise it in wf-plan and export again; never edit the exported file by hand.
