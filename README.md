# claude-code-workflows

Installable, opinionated **workflows for [Claude Code](https://claude.com/claude-code)**: bundles of subagents, skills, hooks, scripts, and routing policy that you drop into `~/.claude/` with one command.

Each workflow lives in `workflows/<name>/` and is self-contained. More will be added over time.

| Workflow | What it does |
|---|---|
| [`double-diamond`](workflows/double-diamond/README.md) | Feature **planning**: Discover → Define → Develop → Deliver. Opus decides, Haiku/Sonnet gather and draft, a deterministic gate validates. Exports a plan that `verified-diamond` can consume. |
| [`verified-diamond`](workflows/verified-diamond/README.md) | Cost-aware plan → parallel build → deterministic verify → adversarial review → risk-based merge. Opus plans, Sonnet/Haiku build, commands decide pass/fail. |

```text
workflows/<name>/
├── README.md        what it is, diagrams, how it behaves
├── payload/         what gets installed (agents/ skills/ hooks/ scripts/ settings fragment, …)
└── docs/            extra docs (installed alongside)
install.py           generic, idempotent installer
```

## Install

Requires Python 3.8+ and Claude Code. Works on macOS, Linux, and Windows (the hooks call `python3`, so make sure it's on your PATH).

```bash
git clone <this-repo-url> claude-code-workflows
cd claude-code-workflows

python3 install.py --list                         # available workflows
python3 install.py verified-diamond --dry-run     # preview every change
python3 install.py verified-diamond               # install into ~/.claude
```

Then **restart Claude Code** and check it:

```bash
python3 ~/.claude/scripts/vd.py status
```

What the installer does, and doesn't do:

- Copies agents, skills, hooks, scripts, and routing config into `~/.claude/`.
- **Never silently overwrites.** A file that already exists and differs is skipped and reported; re-run with `--force` to replace it (a timestamped `.bak` is kept).
- **Merges** `settings.json` (permissions unioned, hooks appended once, env set only if absent) and **appends** the workflow's contract to `~/.claude/CLAUDE.md` inside `<!-- BEGIN/END -->` markers, so re-running updates it in place.
- Leaves your `model` / `effortLevel` alone unless you pass `--set-model`.
- `--target DIR` installs somewhere other than `~/.claude` (handy for testing).
- Safe to re-run; unchanged files are left alone. To uninstall, delete the files listed in the workflow README and the marked block in `CLAUDE.md`.

### Installing double-diamond

`double-diamond` has its own PowerShell installer (it needs `pwsh` 7, Python 3.9+ and Claude Code). `install.py` points you to it instead of installing it.

```powershell
pwsh -NoProfile -File workflows/double-diamond/install.ps1 -WhatIf   # preview
pwsh -NoProfile -File workflows/double-diamond/install.ps1           # install into ~/.claude
```

Start planning with its launcher, then hand the exported plan to verified-diamond in a fresh session. Details: [`workflows/double-diamond/README.md`](workflows/double-diamond/README.md).

## Using it on a clean (or any) repository

The workflow installs at the *user* level, but it only works in a repo that tells it how to verify changes. It fails closed otherwise. Add this to the repo's `CLAUDE.md` (or README), filling in **real** commands and using `n/a` only where a step truly doesn't apply:

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

Then, in Claude Code from the repo root:

```text
/verified-diamond Add rate limiting to the public API
```

### Prompt to set up a clean repo

Paste this into Claude Code in a new or unprepared repository. It prepares the contract without touching feature code:

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

Once that table is green, start real work with `/verified-diamond <task>`.

### Prompt to install it for you

```text
Install the verified-diamond workflow from <path-or-url-to-this-repo>:
1. Run `python3 install.py verified-diamond --dry-run` and show me the output.
2. If nothing looks destructive, run it for real, then `python3 ~/.claude/scripts/vd.py status`.
3. Tell me to restart Claude Code, and list any files that were skipped because they differed.
```

## Adding a new workflow

1. Create `workflows/<name>/payload/` (or ship your own `install.ps1`, as `double-diamond` does) using the same sub-directories (`agents`, `skills`, `hooks`, `scripts`, plus an optional `<name>/` config dir), and optionally `global-contract.md` and `settings.fragment.json`.
2. Add `workflows/<name>/README.md` explaining the behavior, with diagrams.
3. `python3 install.py <name> --dry-run` against a scratch `--target` to verify.
4. Add a row to the table above.

> The installer currently ships a fixed set of payload directories and merges `settings.fragment.json` / `global-contract.md` by workflow name. Extend `install.py` if a new workflow needs something else.

## License

None yet. Add a `LICENSE` before sharing publicly.
