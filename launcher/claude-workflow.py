#!/usr/bin/env python3
"""Start Claude Code in a workflow: claude --workflow <name> [workflow options] [claude args]

Installed to ~/.claude/scripts/claude-workflow.py by claude-code-workflows' install.py.
The shell integration (install.py --shell) makes `claude --workflow ...` call this script; every
other `claude` call goes straight to Claude Code. Without it, run this script directly:

    python3 ~/.claude/scripts/claude-workflow.py <name> [args]
    python3 ~/.claude/scripts/claude-workflow.py            (lists installed workflows)
    CLAUDE_WORKFLOW_DRY_RUN=1 ...                          (print the command instead of running it)

Each installed workflow has ~/.claude/<name>/workflow.json. Its "launch" entry says how to start it:
  {"agent": "<name>-orchestrator"}                 runs: claude --agent <name>-orchestrator [args]
  {"windows": "<ps1>", "posix": "<sh>"}            runs the workflow's own launcher script [args]
Standard library only.
"""
import json, os, shutil, signal, subprocess, sys


def config_dir():
    return os.environ.get("CLAUDE_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".claude")


def installed(home):
    found = {}
    if os.path.isdir(home):
        for name in sorted(os.listdir(home)):
            path = os.path.join(home, name, "workflow.json")
            if os.path.isfile(path):
                try:
                    with open(path, encoding="utf-8") as f:
                        found[name] = json.load(f)
                except (OSError, ValueError):
                    pass
    return found


def show_list(home, out=sys.stdout):
    flows = installed(home)
    if not flows:
        print(f"No workflows are installed in {home}.", file=out)
        return
    print("Installed workflows (start one with: claude --workflow <name>):", file=out)
    for name, meta in flows.items():
        print(f"  {name:<20} {meta.get('summary', '')}", file=out)


def split_args(argv):
    """Return (workflow name or None, remaining args). Accepts '--workflow NAME', '--workflow=NAME' or a bare NAME first."""
    rest, name, i = [], None, 0
    while i < len(argv):
        a = argv[i]
        if name is None and a == "--workflow":
            if i + 1 < len(argv) and not argv[i + 1].startswith("-"):
                name = argv[i + 1]
                i += 2
                continue
            i += 1
            continue
        if name is None and a.startswith("--workflow="):
            name = a.split("=", 1)[1] or None
        else:
            rest.append(a)
        i += 1
    if name is None and rest and not rest[0].startswith("-") and "--workflow" not in argv:
        name, rest = rest[0], rest[1:]
    return name, rest


def find_powershell():
    for exe in ("pwsh", "powershell"):
        path = shutil.which(exe)
        if path:
            return path
    return None


def command_for(home, meta, rest):
    launch = meta.get("launch") or {}
    claude = shutil.which("claude")
    if "agent" in launch:
        if not claude:
            sys.exit("claude-workflow: 'claude' was not found on PATH. Install Claude Code and start a new shell.")
        return [claude, "--agent", launch["agent"], *rest]
    if os.name == "nt" and "windows" in launch:
        ps = find_powershell()
        if not ps:
            sys.exit("claude-workflow: PowerShell was not found on PATH.")
        return [ps, "-NoProfile", "-File", os.path.join(home, *launch["windows"].split("/")), *rest]
    if os.name != "nt" and "posix" in launch:
        bash = shutil.which("bash") or "/bin/bash"
        return [bash, os.path.join(home, *launch["posix"].split("/")), *rest]
    sys.exit(f"claude-workflow: workflow '{meta.get('name')}' has no launcher for this platform.")


def main(argv):
    home = config_dir()
    name, rest = split_args(argv)
    if name in (None, "list"):
        if any(a in ("-h", "--help") for a in rest):
            print(__doc__)
        show_list(home)
        return 0
    flows = installed(home)
    if name not in flows:
        print(f"claude-workflow: no workflow named '{name}' is installed in {home}.", file=sys.stderr)
        show_list(home, sys.stderr)
        return 2
    cmd = command_for(home, flows[name], rest)
    if os.environ.get("CLAUDE_WORKFLOW_DRY_RUN") == "1":
        print(json.dumps(cmd))
        return 0
    if os.name != "nt":
        os.execv(cmd[0], cmd)
    # Windows: let Claude Code handle Ctrl+C itself instead of this wrapper dying underneath it.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    return subprocess.call(cmd)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
