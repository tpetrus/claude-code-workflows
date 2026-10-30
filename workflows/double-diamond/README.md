# double-diamond for Claude Code

A feature-**planning** workflow for Claude Code. It turns a feature idea into a validated, decision-recorded plan using the Double Diamond:
- **Discover** and **Define**: diverge, then converge on the problem.
- **Develop** and **Deliver**: diverge, then converge on the solution.

The strongest model works only at decision points, cheap tiers do the rest, and a deterministic gate checks the result. The package is self-contained: Claude Code and Python are all it needs.

## How it works
The lead (`dd-planner`) makes every decision and asks you only what it can't look up. Cheaper workers each write one file into the run folder; the plan script merges their files, keeps the evidence, and runs the gate.

```mermaid
flowchart TD
    U(["dd-claude.ps1<br/>-High for R3+"]) --> L["claude --agent dd-planner<br/>run folder added, auto memory off, guards on"]
    L --> T{"Triage your idea"}
    T -- "skip: one obvious change" --> S(["Implement directly; no files"])
    T -- "quick or full lane" --> I["init: new run folder in temp"]
    I --> CY{"Canary: the guard must<br/>block a test write"}
    CY -- "CANARY FAIL" --> STOP(["Stop: write guard not active"])
    CY -- "CANARY OK" --> SC["dd-scout writes context-brief.md<br/>(full lane, in the background), then collect"]
    CY -- "CANARY OK" --> Q["Round 1 questions"]
    SC --> DF["Define via patch.json + apply:<br/>problem, goals, non-goals, ACs, decisions"]
    Q --> DF
    DF --> DV["Develop: one dd-ideator per lens<br/>writes cards/O*.json (full lane)"]
    DV --> SCO["collect, then score the options via patch"]
    SCO --> DL["Deliver via patch:<br/>design, risks, slices"]
    DL --> K{"Full lane,<br/>or quick lane at R2?"}
    K -- "yes" --> CR["dd-critic writes critic.json<br/>collect, resolve findings via patch"]
    K -- "no" --> G
    CR --> G{"finalize:<br/>deterministic gate"}
    G -- "fail: fix the listed errors via patch<br/>(up to 3 attempts)" --> G
    G -- "pass" --> E{"End-of-run questions"}
    E -- "export (you agree)" --> X["slug.plan.md + slug.plan.json<br/>written into your repository"]
    X -- "hand off? (you agree)" --> H(["Command to run verified-diamond<br/>in a fresh session"])
    E -- "discard" --> Z(["Run folder deleted"])
```

The same flow without Mermaid, with the run folder and what each seat's write guard allows:
```
you  -->  dd-claude.ps1  -->  claude --agent dd-planner  -->  init  -->  run folder in your temp directory
                                                                        (canary: the guard must block a test write)

<temp>/double-diamond/<run-id>/         written by
    plan.json                           dd_plan.py only (apply, collect, finalize)
    patch.json                          dd-planner; apply merges it, then deletes it
    context-brief.md                    dd-scout
    cards/O1.json, O2.json, ...         dd-ideator, one per lens
    critic.json                         dd-critic
    PLAN.md                             dd_plan.py (render, finalize)
    history/                            dd_plan.py (revise)

  Discover            Define              Develop             Deliver             Gate               End of run
+-----------+       +-----------+       +-----------+       +-----------+       +-----------+       +--------------------+
| scout:    |       | ACs and   |       | ideator:  |       | design,   |       | finalize  |       | export? (asks)     |
| brief +   | ----> | decisions | ----> | cards +   | ----> | risks,    | ----> | PASS: done|  ---> | hand off? (asks)   |
| questions |       | (patch)   |       | scores    |       | slices,   |       | FAIL: fix |       | or discard the run |
|           |       |           |       |           |       | critic    |       | by patch  |       |                    |
+-----------+       +-----------+       +-----------+       +-----------+       +-----------+       +--------------------+
```

## Contents
```
workflows/double-diamond/
    README.md                this file
    install.ps1              installer / uninstaller (-WhatIf, -Force, -Uninstall, -ClaudeHome)
    DESIGN.md                design contract for maintainers (not installed)
    MANIFEST.json            every payload file with its SHA-256 (install/uninstall use it)
    lib/Install-Package.ps1  install logic used by install.ps1
    tools/update_manifest.py regenerates MANIFEST.json from payload/ (not installed)
    tools/smoke_test.py      end-to-end check of the guard, CLI and gate (not installed)
    payload/                 installed as-is into ~/.claude
        agents/dd-planner.md       opus, medium effort   lead: triage, questions, convergence, design
        agents/dd-planner-high.md  opus, high effort     same lead for R3+ plans
        agents/dd-scout.md         haiku                 one read-only repo context brief
        agents/dd-ideator.md       sonnet, medium effort one option per lens
        agents/dd-critic.md        sonnet, high effort   pre-mortem (always in the full lane, at R2 in the quick lane)
        skills/double-diamond/     SKILL.md, references, assets, scripts (dd_plan.py, plan_core.py, guard_write.py, dd-claude launchers)
```

## Models and budgets
Model and effort live in each agent's frontmatter; the strongest model works only at decision points, and the critic runs on a different model from the lead so their blind spots differ.

| Seat | Model | Effort | Max turns | When | Escalation |
|---|---|---|---|---|---|
| dd-planner | opus | medium | - | triage, questions, framing, convergence, design, slicing, final call | R3+ plans: start with `-High` (dd-planner-high, high effort) |
| dd-scout | haiku | (not set) | 30 | one read-only context brief per plan | if the brief comes back thin on a large or unusual repository, re-run it once on sonnet |
| dd-ideator | sonnet | medium | 12 | full lane only, 2-3 lenses in parallel | re-run a failed ideator once; after a second failure continue while two lenses remain, or the lead writes the option by patch |
| dd-critic | sonnet | high | 20 | always in the full lane, at R2 in the quick lane | a second pass only if a blocker changed the chosen option or a contract |

Budgets (enforced by the skill text and the gate): at most 3 question rounds of 4 questions (2-4 options each), plus one round for critic blockers and the end-of-run round; 2-3 ideators; at most 2 critic passes and 10 findings; up to 3 finalize attempts; brief about 600 words, option card under 350 words.

## Requirements
- Claude Code with subagent `effort`, `maxTurns`, `hooks`, `skills` and `omitClaudeMd` frontmatter support. `omitClaudeMd` needs v2.1.271+.
- Python 3.9+ as `python3` on PATH (standard library only), for the plan script, the write-guard hooks and the launcher guard self-test. On Windows, `python3` may be the Microsoft Store alias stub, which the launcher detects and refuses; install Python or disable the alias in Settings > Apps > Advanced app settings > App execution aliases.
- Access to `opus`, `sonnet` and `haiku`.

## Install
Run these from `workflows/double-diamond/` in this repo. This workflow has its own PowerShell installer; the repo-level `install.py` does not install it.
```powershell
pwsh -NoProfile -File install.ps1 -WhatIf    # preview
pwsh -NoProfile -File install.ps1            # install into ~/.claude
```
The installer:
- checks the payload against MANIFEST.json;
- skips files that are already identical;
- stops **before writing anything** if an existing file differs (re-run with `-Force` to overwrite).

If `~/.claude/skills/double-diamond` is a link (junction or symbolic link), the installer refuses to write through it and replaces the link only with `-Force`. The link's target is never touched.

PowerShell 7 (`pwsh`) is required. Windows PowerShell 5.1 fails `-WhatIf` (`Get-FileHash` honors it and returns nothing).

Nothing needs adding to `settings.json`: the launcher grants access to the run folder itself.

## Use
Start planning with the launcher. It is the only supported entry point, because planning must run in the **main session** (subagents cannot ask you questions) and the launcher turns on the safety measures:
```powershell
pwsh -File ~/.claude/skills/double-diamond/scripts/dd-claude.ps1          # lead on opus (medium effort)
pwsh -File ~/.claude/skills/double-diamond/scripts/dd-claude.ps1 -High    # R3+ plans: dd-planner-high (high effort)
```
```sh
~/.claude/skills/double-diamond/scripts/dd-claude.sh [--high]             # macOS / Linux
```
Extra arguments are passed on to `claude`. The launcher:
- needs `python3` and `claude` on PATH, and the installed guard at `~/.claude/skills/double-diamond/scripts/guard_write.py`;
- runs a guard self-test with `python3` before starting claude, and refuses to start if it fails;
- starts `claude --agent dd-planner` (or `dd-planner-high`) with `--add-dir <runs folder>`, so the run folder is reachable;
- passes a temporary `--settings` file that turns auto memory off and excludes your user `CLAUDE.md`, and sets `CLAUDE_CODE_DISABLE_AUTO_MEMORY=1` and `DD_LAUNCHER=1` for that process only.

A plain `/double-diamond` session has no write guards and is **unsupported**.

Each plan lives in its own run folder in the system temp directory, and only the session that created it can resume it. Nothing reaches your repository until you agree to an export at the end of the run. Old run folders are swept after 72 hours. The old `~/.config/double-diamond` folder from version 1 is no longer used and can be deleted.

## Write guards
Each agent has `PreToolUse` hooks that run `skills/double-diamond/scripts/guard_write.py`:

| Seat | Write / Edit hook | Bash hook |
|---|---|---|
| planner, planner-high | only `patch.json` in a run folder | only `python3 "<dd_plan.py>" <command> ...` |
| scout | only `context-brief.md` | none (no Bash tool) |
| ideator | only `cards/O<n>.json` | none (no Bash tool) |
| critic | only `critic.json` | none (no Bash tool) |

Guards fail closed: an unknown mode or an unreadable payload is denied. A blocked call gets a JSON `deny` decision. That works in both Git Bash and PowerShell; an exit code 2 would be rewritten to 1 by PowerShell, which Claude Code treats as non-blocking. The hooks call `$HOME/.claude/skills/...`, so install into the default `~/.claude`.

## Verify
The runtime checks are the launcher's guard self-test (it runs before claude starts) and the in-run canary (the guard must block a test write, or the lead stops).

For maintainers, `tools/smoke_test.py` (standard library only, not installed) exercises the write guard and the full CLI path (`init`, `apply`, `collect`, `finalize`, `export`, `verify`) in a throwaway runs folder and git repository:
```sh
python3 tools/smoke_test.py
```
Run it, then `python3 tools/update_manifest.py`, after every payload change. The package has not been run inside a live Claude Code session.

## Uninstall
```powershell
pwsh -NoProfile -File install.ps1 -Uninstall
```
This removes only files listed in MANIFEST.json, and keeps any you modified unless you pass `-Force`.
