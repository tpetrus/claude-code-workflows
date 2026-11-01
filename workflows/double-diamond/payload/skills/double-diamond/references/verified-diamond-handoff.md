# verified-diamond hand-off

Optional: double-diamond works without verified-diamond and the reverse. When both are installed, `finalize` embeds a hand-off block, and the user may export the final plan so a verified-diamond orchestrator starts from settled requirements instead of re-brainstorming.

## Rules
- Offered only in the end-of-run question round, when `init` reported `verified_diamond_available` and the plan is final, and only after the user agrees.
- Requires an export: it uses the exported `<slug>.plan.json` in the repository, never a file in the temp run folder.
- double-diamond never starts it: after a yes it only prints the command, and the user runs it in a fresh session. verified-diamond never writes to the plan file.

## What finalize writes
`plan.json` -> `handoff.verified_diamond`: `generated_by` ("double-diamond dd_plan.py finalize"), `plan_revision`, `plan_hash` (16-hex content hash; verify recomputes it), `base_revision` (git HEAD when planned), `risk`, `freeze_first` (`[{ id, name, kind, definition }]`, one per contract), `draft_dag` (one verified-diamond task-template object per slice), `notes` (run verify first; `verify[]` intentionally empty; `owned_paths` are drafts; the verified-diamond risk is authoritative).

## Slice to task-template mapping
| verified-diamond field | Source |
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
| `verify` | `[]` always: it comes from the repository `verified_diamond` command contract, never from a plan |
| `risk` | `slice.risk` |
| `retry_limit`, `escalation` | verified-diamond defaults |

These keys must match verified-diamond's `task-template.json` whenever that skill is installed.

## Integrity
`python3 "<skill-dir>/scripts/dd_plan.py" verify "<exported plan.json>"` works on the exported file alone. It prints `VERIFIED <slug> r<rev> hash <hash>` and exits 0 only when the plan is `final`, its content hash matches the hash recorded at finalize (any later edit breaks it), and it still passes the completeness gate using the evidence recorded in the file. Otherwise it prints the reasons and `NOT VERIFIED`, and exits 1.

## How to start it
In a **fresh session** started with `claude --workflow verified-diamond`, quoting the path: `/verified-diamond --plan "<exported plan.json>" <feature title>`. The orchestrator then runs `verify` on the file, compares the base revision with HEAD, adopts the ACs and settled decisions, freezes `freeze_first`, refines `draft_dag` and fills `verify` from the repository contract. It may raise the risk class and stops to ask if the plan conflicts with the repository. To change the plan, revise it in double-diamond and export again; never edit the exported file by hand.
