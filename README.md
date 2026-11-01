# claude-code-workflows

Installable, opinionated **workflows for [Claude Code](https://claude.com/claude-code)**.

A *workflow* is a packaged way of working with Claude on a kind of task. Each one bundles:

- a lead **agent**, the *orchestrator*, which makes the decisions;
- a **skill** that tells the orchestrator what to do, step by step;
- helper **agents** that the orchestrator hands bounded work to (often on cheaper models);
- optionally, **scripts**, safety **hooks**, config, and settings.

You install workflows into your Claude Code configuration folder (`~/.claude`) with one command, and start
one with `claude --workflow <name>`.

| Workflow | What it is for | Start it |
|---|---|---|
| [`double-diamond`](workflows/double-diamond/README.md) | **Plan** a feature before building it: discover, define, develop, deliver. Opus decides, cheaper models gather and draft, and a deterministic gate checks the plan. Exports a plan that verified-diamond can build. | `claude --workflow double-diamond` |
| [`verified-diamond`](workflows/verified-diamond/README.md) | **Build** a change: Opus plans, Sonnet/Haiku build in parallel, real commands decide pass/fail, a reviewer tries to break it, and the risk level decides who approves the merge. | `claude --workflow verified-diamond`, then `/verified-diamond <task>` |

## Quick start

You need Python 3.8+ (as `python3` on your PATH) and Claude Code. macOS, Linux, and Windows all work.

```bash
git clone <this-repo-url> claude-code-workflows
cd claude-code-workflows

python3 install.py --list              # what is available, and what is installed
python3 install.py --all --dry-run     # preview every change
python3 install.py --all               # install everything into ~/.claude
python3 install.py --shell             # once: teach your terminal `claude --workflow <name>`
```

Open a new terminal (and restart Claude Code if it was running), then run `claude --workflow` on its own to
list what is installed.

## Using the workflows

Run these from the project folder you want to work on.

**Plan a feature** with double-diamond:

```text
claude --workflow double-diamond          # add --high for risky plans (auth, data, payments, new contracts)
> Add CSV export to the report screen
```

The orchestrator asks you only the questions it can't answer from the code, compares options, runs a
pre-mortem, and checks the plan with a script. At the end it offers to export the plan into your
repository and prints the command to build it.

**Build a change** with verified-diamond:

```text
claude --workflow verified-diamond
> /verified-diamond Add rate limiting to the public API
> /verified-diamond --plan "docs/plans/csv-export.plan.json" CSV export     # build an exported plan
```

verified-diamond only works in a repository that documents its real install, lint, test, and build
commands (see [Preparing a repository](#preparing-a-repository-for-verified-diamond)). If they are
missing, it stops and asks.

**How starting works.** `claude --workflow <name>` starts Claude Code *as that workflow's orchestrator
agent* (`<name>-orchestrator`), and the agent preloads the workflow's skill (`<name>`). Each skill refuses to
run without its agent, so this is the one supported way in. Any extra arguments are passed on to Claude
Code (for example `claude --workflow verified-diamond --model opus`). Every other `claude` command behaves
exactly as before.

Claude Code itself has no `--workflow` option. `install.py --shell` adds a small `claude` function to
your PowerShell profile and/or `~/.bashrc` / `~/.zshrc` that catches `--workflow` and hands it to the
launcher at `~/.claude/scripts/claude-workflow.py`. If you'd rather not change your shell profile, call the
launcher directly:

```bash
python3 ~/.claude/scripts/claude-workflow.py double-diamond --high
```

## What the installer does

- Copies each workflow's files into `~/.claude` (layout below) and records them in
  `~/.claude/<name>/MANIFEST.json`.
- **Never silently overwrites.** If any file it would write already exists and differs, it stops before
  writing anything. Re-run with `--force` to replace those files; a copy goes to
  `~/.claude/backups/claude-code-workflows/<time>/`.
- **Merges** `settings.json` (permissions unioned, hooks added once, env set only if absent) and adds the
  workflow's contract, if it ships one, to `~/.claude/CLAUDE.md` inside `<!-- BEGIN/END <name> -->` markers
  (and removes that block once a workflow stops shipping one). No workflow here ships one today. It keeps an
  overview of installed workflows in `~/.claude/README.md` the same way. Your own text outside the markers
  is never touched.
- Retires files left by older layouts of a workflow (moves them to backups), but only if you haven't
  edited them.
- Leaves your `model` / `effortLevel` alone unless you pass `--set-model`.
- Is safe to re-run: unchanged files are left alone.

| Command | What it does |
|---|---|
| `python3 install.py --list` | List workflows in this repo and whether each is installed |
| `python3 install.py <name> [<name> ...]` or `--all` | Install or update |
| `python3 install.py <name> --uninstall` | Remove a workflow (files you edited and `state/` are kept; `--force` removes edited files too) |
| `python3 install.py --shell` / `--shell --uninstall` | Add or remove the `claude --workflow` shell function |
| `--dry-run` | Show what would change, and write nothing |
| `--target DIR` | Use a config folder other than `~/.claude` (for testing) |

## Preparing a repository for verified-diamond

verified-diamond installs at the *user* level, but it only works in a repository that tells it how to
verify changes. It fails closed otherwise. Add this to the repository's `CLAUDE.md` (or README), filling
in **real** commands and using `n/a` only where a step truly doesn't apply:

```yaml
verified_diamond:
  install: "npm ci"
  format: "npm run format:check"
  lint: "npm run lint"
  typecheck: "npm run typecheck"
  unit: "npm test"
  integration: "npm run test:integration"
  build: "npm run build"
  security: "npm audit --audit-level=high"
  affected: "npx jest --findRelatedTests {files}"   # fast, scoped check used per task
  low_risk_merge_command: "gh pr merge --squash --auto"
  protected_paths:
    - ".github/workflows/**"
    - "infrastructure/**"
    - "migrations/**"
    - "**/auth/**"
```

Or paste this prompt into Claude Code in the repository. It prepares the contract without touching
feature code:

```text
Prepare this repository for the verified-diamond workflow. Do not implement any feature.

1. Read the README, package/build manifests, CI config, and any existing CLAUDE.md to find the
   REAL commands for: install, format, lint, typecheck, unit tests, integration tests, build,
   security scan, and an "affected-scope" test command that runs only tests related to changed files.
2. Add (or update) a `verified_diamond:` YAML block in the project CLAUDE.md with those commands,
   plus `low_risk_merge_command` and `protected_paths` (CI workflows, infrastructure, migrations,
   auth, anything secret-bearing you find).
3. Run each command once to prove it works. Report any that fail or don't exist.
4. NEVER invent a command. For anything missing, tell me what's missing and propose options
   (e.g., which linter to add) and wait for my decision.
5. Finish with a table: command | status (works / fails / missing) | notes.
```

## The workflow format

Every workflow in this repository follows the same layout and naming rules, so the installer, the
launcher, and the checker work for all of them without special cases. `python3 tools/check.py` enforces
the rules.

### In this repository

```text
workflows/<name>/
├── workflow.json        required  the workflow's identity card (see below)
├── README.md            required  human guide: what it does, how to start it, diagrams; installed too
├── MANIFEST.json        required  generated by tools/update_manifest.py; never edit by hand
├── DESIGN.md            optional  design contract for maintainers (not installed)
├── docs/                optional  extra guides, installed to ~/.claude/<name>/docs/
├── tests/               required  at least one test script; tools/check.py runs every tests/*.py
└── payload/             required  mirrors ~/.claude exactly; copied as-is
    ├── agents/
    │   ├── <name>-orchestrator.md    required  the lead agent; must preload the skill
    │   └── <name>-<role>.md          helper agents (scout, builder, reviewer, ...)
    ├── skills/<name>/                required  exactly one skill, named like the workflow
    │   ├── SKILL.md
    │   ├── scripts/                  launchers, CLIs, and agent-scoped guard scripts
    │   ├── references/               longer docs the skill loads on demand
    │   └── assets/                   templates and schemas
    ├── hooks/<name>-<purpose>.py     optional  global hooks registered via settings.fragment.json
    ├── <name>/                       optional  config the workflow reads at run time (e.g. routing.json)
    ├── settings.fragment.json        optional  merged into ~/.claude/settings.json
    └── global-contract.md            optional  merged into ~/.claude/CLAUDE.md as a marked block (loaded in every
                                                session, so keep workflow rules in the skill instead)
```

### Once installed in `~/.claude`

```text
~/.claude/
├── README.md                     overview of installed workflows (marked block, kept up to date)
├── agents/<name>-*.md            agents, named after their workflow
├── skills/<name>/                the skill and its scripts
├── hooks/<name>-*.py             global hooks, if any
├── <name>/                       workflow home: workflow.json, README.md, MANIFEST.json, docs/, config, state/
├── scripts/claude-workflow.py    the shared `claude --workflow` launcher
└── backups/claude-code-workflows/  anything the installer replaced or retired
```

### Naming rules

- The folder name is the workflow name: lowercase words joined by hyphens (`double-diamond`).
- The skill is called exactly `<name>`, and its `SKILL.md` frontmatter says `name: <name>`.
- The lead agent is `<name>-orchestrator`. Every other agent is `<name>-<role>`. File name = frontmatter
  `name`.
- The orchestrator preloads its skill (`skills: [<name>]` in its frontmatter), and the skill's first
  step refuses to run in any other agent. Starting a workflow therefore always needs both the agent and
  the skill.
- Hooks are `hooks/<name>-<purpose>.py`. Anything a workflow writes at run time goes under
  `~/.claude/<name>/state/` (or a temp folder), never elsewhere in `~/.claude`.
- Every hook command (in agent frontmatter or `settings.fragment.json`) must run a file the workflow
  installs, and every `Agent(...)` an agent may launch must be one of the workflow's own agents.
- Launcher scripts must start `<name>-orchestrator` and pass on every extra argument.
- After a rename, list the old file in `legacy_files`, and make sure the old name appears nowhere in
  the payload (the checker looks).

`tools/check.py` also tests itself: it copies each workflow, breaks one rule at a time, and fails if it
doesn't notice. If you add a rule, add a case for it to `self_test()`.

### `workflow.json`

```json
{
  "name": "my-workflow",
  "version": "1.0.0",
  "summary": "One sentence a non-expert understands; shown by --list, the launcher, and ~/.claude/README.md.",
  "orchestrator": "my-workflow-orchestrator",
  "skill": "my-workflow",
  "usage": ["claude --workflow my-workflow", "then: /my-workflow <task>"],
  "launch": { "agent": "my-workflow-orchestrator" },
  "requires": ["claude", "python3"],
  "legacy_files": { "agents/old-name.md": "<sha256 of the old file>" }
}
```

- `launch` is either `{"agent": "<name>-orchestrator"}` (the launcher runs
  `claude --agent <name>-orchestrator <args>`) or, when a workflow needs its own setup before Claude
  starts, `{"windows": "skills/<name>/scripts/launch.ps1", "posix": "skills/<name>/scripts/launch.sh"}`.
  Those scripts must end by starting the orchestrator agent, and receive every extra argument.
- `legacy_files` lists files an older version installed that this version no longer ships, with the
  SHA-256 they had. On install, each one is moved to backups if it is still unmodified. Use this whenever
  you rename or move an installed file, so users are not left with stale agents.

## Creating a new workflow

1. **Copy the shape.** Create `workflows/<name>/` with `workflow.json`, `README.md`, `tests/`, and
   `payload/agents/<name>-orchestrator.md` plus `payload/skills/<name>/SKILL.md`. The two existing
   workflows are working examples: `verified-diamond` uses the simple `agent` launch, and
   `double-diamond` uses its own launcher scripts.
2. **Write the orchestrator agent.** Give it `name: <name>-orchestrator`, a description that ends with
   "Start it with `claude --workflow <name>`.", and preload the skill:
   ```yaml
   ---
   name: my-workflow-orchestrator
   description: Leads my-workflow. Start it with `claude --workflow my-workflow`.
   model: opus
   skills:
     - my-workflow
   ---
   ```
3. **Write the skill.** Begin its steps with a gate like: "This skill runs only in a session started as
   the `<name>-orchestrator` agent (`claude --workflow <name>`). If this session is not that agent, stop
   and tell the user to restart with `claude --workflow <name>`."
4. **Add helpers** as `<name>-<role>.md` agents, scripts under `skills/<name>/scripts/`, and anything
   else from the format above. Use only the folders the format allows.
5. **Write a test** in `tests/` that runs your scripts in a temporary folder (see either workflow's
   `tests/smoke_test.py`). It must exit 0 on success and must not touch the real `~/.claude`.
6. **Generate the manifest and check everything:**
   ```bash
   python3 tools/update_manifest.py <name>
   python3 tools/check.py --install-roundtrip
   ```
7. **Try it for real:** `python3 install.py <name>`, then `claude --workflow <name>`.
8. **List it** in the table at the top of this README.

## Modifying an existing workflow

1. Edit the files under `workflows/<name>/`, never the installed copies in `~/.claude` (the installer
   treats edited installed files as conflicts and won't overwrite them without `--force`).
2. Bump `version` in `workflow.json` (patch for fixes, minor for new behavior, major for anything that
   breaks how people start or use it).
3. If you renamed, moved, or deleted an installed file, add its old path and old SHA-256 to
   `legacy_files` so the installer cleans it up for existing users. Get the hash with
   `git show HEAD:workflows/<name>/payload/<old path> | sha256sum`.
4. Regenerate the manifest and run the checks:
   ```bash
   python3 tools/update_manifest.py <name>
   python3 tools/check.py --install-roundtrip
   ```
5. Update the workflow's `README.md` (and `DESIGN.md` if it has one), then reinstall with
   `python3 install.py <name>`.

## Removing a workflow

- **From your machine:** `python3 install.py <name> --uninstall`. This removes the files listed in the
  installed manifest that you haven't edited, its `CLAUDE.md` block, its `settings.json` hooks and env
  entries, and its row in `~/.claude/README.md`. It keeps files you edited (unless `--force`), the
  workflow's `state/` folder, and permission rules (they may be yours too). Delete
  `~/.claude/<name>/` yourself afterwards if you don't need its state.
- **From this repository:** uninstall it first wherever it's installed, then delete
  `workflows/<name>/`, remove its row from the table at the top of this README, and run
  `python3 tools/check.py`. If another workflow refers to it (double-diamond's hand-off mentions
  verified-diamond, for example), update that workflow too.

## Repository layout

```text
install.py                  installer / uninstaller / shell integration (standard library only)
launcher/claude-workflow.py the `claude --workflow` launcher, installed to ~/.claude/scripts/
tools/update_manifest.py    regenerates workflows/*/MANIFEST.json
tools/check.py              checks the workflow format, manifests, tests, and an install round trip
workflows/<name>/           one folder per workflow, in the format above
```

## License

None yet. Add a `LICENSE` before sharing publicly.
