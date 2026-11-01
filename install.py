#!/usr/bin/env python3
"""Install, update or remove claude-code-workflows in a Claude Code config directory (default ~/.claude).

    python3 install.py --list                         list workflows in this repo
    python3 install.py <name> [<name> ...] | --all    install or update (add --dry-run to preview)
    python3 install.py <name> --uninstall             remove a workflow
    python3 install.py --shell                        add `claude --workflow <name>` to your shell
    python3 install.py --shell --uninstall            remove it again

Safe by default:
  * The payload must match the workflow's MANIFEST.json (regenerate with tools/update_manifest.py).
  * Nothing is written if any target file differs from what would be installed; --force replaces those
    files and keeps a copy under <target>/backups/claude-code-workflows/<time>/.
  * settings.json and CLAUDE.md are merged, never replaced. The README.md block is regenerated.
  * Files left by older layouts of a workflow are retired (moved to backups) only when unmodified.
  * Uninstall removes only files that are unchanged since install, unless --force; state/ is kept.
Standard library only (Python 3.8+).
"""
import argparse, hashlib, json, os, shutil, subprocess, sys, time

REPO = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = os.path.join(REPO, "workflows")
LAUNCHER_SRC = os.path.join(REPO, "launcher", "claude-workflow.py")
LAUNCHER_REL = "scripts/claude-workflow.py"
MARK = "claude-code-workflows"
MERGED = {"settings.fragment.json", "global-contract.md"}  # payload files merged, not copied
STAMP = time.strftime("%Y%m%d-%H%M%S")


def log(msg=""):
    print(msg)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def tpath(target, rel):
    return os.path.join(target, *rel.split("/"))


# ---------------------------------------------------------------- workflow format

def workflow_dir(name):
    return os.path.join(WORKFLOWS, name)


def available():
    return sorted(n for n in os.listdir(WORKFLOWS) if os.path.isfile(os.path.join(WORKFLOWS, n, "workflow.json")))


def load_meta(name):
    path = os.path.join(workflow_dir(name), "workflow.json")
    if not os.path.isfile(path):
        sys.exit(f"unknown workflow: {name} (try --list)")
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def _walk(root):
    for base, dirs, files in os.walk(root):
        dirs[:] = sorted(d for d in dirs if d != "__pycache__")
        for f in sorted(files):
            if not f.endswith(".pyc"):
                yield os.path.join(base, f)


def install_map(name):
    """[(source path, target-relative path)] for every file the workflow installs (MANIFEST.json excluded)."""
    wf = workflow_dir(name)
    out = []
    payload = os.path.join(wf, "payload")
    for src in _walk(payload):
        rel = os.path.relpath(src, payload).replace(os.sep, "/")
        if rel not in MERGED:
            out.append((src, rel))
    docs = os.path.join(wf, "docs")
    if os.path.isdir(docs):
        for src in _walk(docs):
            out.append((src, f"{name}/docs/" + os.path.relpath(src, docs).replace(os.sep, "/")))
    for f in ("README.md", "workflow.json"):
        out.append((os.path.join(wf, f), f"{name}/{f}"))
    return sorted(out, key=lambda x: x[1])


def build_manifest(name):
    meta = load_meta(name)
    files = [{"path": rel, "sha256": sha(src)} for src, rel in install_map(name)]
    return {"name": name, "version": meta.get("version", "0.0.0"), "files": files}


def manifest_problems(name):
    path = os.path.join(workflow_dir(name), "MANIFEST.json")
    if not os.path.isfile(path):
        return ["MANIFEST.json is missing"]
    with open(path, encoding="utf-8") as f:
        have = json.load(f)
    want = build_manifest(name)
    problems = []
    if have.get("version") != want["version"]:
        problems.append(f"version {have.get('version')} != workflow.json {want['version']}")
    a = {x["path"]: x["sha256"] for x in have.get("files", [])}
    b = {x["path"]: x["sha256"] for x in want["files"]}
    problems += [f"not in payload: {p}" for p in sorted(set(a) - set(b))]
    problems += [f"not in manifest: {p}" for p in sorted(set(b) - set(a))]
    problems += [f"changed: {p}" for p in sorted(set(a) & set(b)) if a[p] != b[p]]
    return problems


# ---------------------------------------------------------------- helpers

def is_link(path):
    isjunction = getattr(os.path, "isjunction", None)
    return os.path.islink(path) or bool(isjunction and isjunction(path))


def backup(target, path, args):
    """Move (or copy) path into the backups folder; returns the backup path."""
    try:
        rel = os.path.relpath(path, target)
    except ValueError:  # different drive on Windows
        rel = ".."
    if rel.startswith(".."):  # outside the config dir (e.g. a shell profile): mirror its full path
        rel = os.path.join("outside", *[c for c in os.path.abspath(path).replace(":", "").split(os.sep) if c])
    dst = os.path.join(target, "backups", MARK, STAMP, rel)
    n = 1
    while os.path.exists(dst):  # never overwrite an earlier backup from the same run
        dst = os.path.join(target, "backups", MARK, f"{STAMP}-{n}", rel)
        n += 1
    if not args.dry_run:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(path, dst)
    return dst


def write_text(path, text, target, args, what):
    cur = open(path, encoding="utf-8").read() if os.path.exists(path) else None
    if cur == text:
        return
    log(f"  {what}: {path}")
    if args.dry_run:
        return
    if cur is not None:
        backup(target, path, args)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def set_block(text, name, body):
    """Replace or append <!-- BEGIN name --> ... <!-- END name -->; body None removes it."""
    begin, end = f"<!-- BEGIN {name} -->", f"<!-- END {name} -->"
    if begin in text and end in text:
        head, rest = text.split(begin, 1)
        tail = rest.split(end, 1)[1].lstrip("\n")
        if body is None:
            new = (head.rstrip() + ("\n\n" + tail if tail.strip() else "")).strip()
            return new + "\n" if new else ""
        return head + f"{begin}\n{body.strip()}\n{end}\n" + (("\n" + tail) if tail else "")
    if body is None:
        return text
    return (text.rstrip() + "\n\n" if text.strip() else "") + f"{begin}\n{body.strip()}\n{end}\n"


def check_python():
    exe = shutil.which("python3")
    ok = False
    if exe:
        try:
            p = subprocess.run([exe, "--version"], capture_output=True, text=True, timeout=20)
            ok = p.returncode == 0 and "was not found" not in (p.stdout + p.stderr)
        except Exception:
            ok = False
    if not ok:
        log("  warning: python3 is missing from PATH or is the Microsoft Store alias stub; the workflows' scripts and hooks need a real python3.")


def is_legacy(rel, path):
    """True when path is byte-identical to a file some workflow's older layout installed at rel."""
    digest = sha(path)
    return any((load_meta(n).get("legacy_files") or {}).get(rel) == digest for n in available())


def read_settings(path):
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8-sig") as f:
            return json.load(f)
    except ValueError as e:
        sys.exit(f"{path} is not valid JSON ({e}); fix it, then re-run. Nothing was written.")


def installed_names(target):
    names = []
    if os.path.isdir(target):
        for n in sorted(os.listdir(target)):
            if os.path.isfile(os.path.join(target, n, "workflow.json")):
                names.append(n)
    return names


# ---------------------------------------------------------------- CLAUDE.md, settings.json, README.md

def merge_contract(name, target, args, remove=False):
    src = os.path.join(workflow_dir(name), "payload", "global-contract.md")
    dst = os.path.join(target, "CLAUDE.md")
    cur = open(dst, encoding="utf-8").read() if os.path.exists(dst) else ""
    if not os.path.isfile(src):
        remove = True  # no contract (any more): drop a marked block an older version of this workflow left behind
    if remove:
        if f"<!-- BEGIN {name} -->" not in cur:
            return
        new = set_block(cur, name, None)
    else:
        body = open(src, encoding="utf-8").read()
        first = body.strip().splitlines()[0].lstrip("# ").strip()
        if f"<!-- BEGIN {name} -->" not in cur and first and first in cur:
            log(f"  CLAUDE.md has an unmarked copy of the {name} contract; left untouched. Wrap it in "
                f"<!-- BEGIN {name} --> / <!-- END {name} --> lines so the installer can update it.")
            return
        new = set_block(cur, name, body)
    write_text(dst, new, target, args, ("remove" if remove else "update") + f" {name} block in")


def merge_settings(name, target, args, remove=False):
    src = os.path.join(workflow_dir(name), "payload", "settings.fragment.json")
    if not os.path.isfile(src):
        return
    with open(src, encoding="utf-8") as f:
        frag = json.load(f)
    dst = os.path.join(target, "settings.json")
    cur = read_settings(dst)
    new = json.loads(json.dumps(cur))
    cmds = {h.get("command") for es in frag.get("hooks", {}).values() for e in es for h in e.get("hooks", [])}
    if remove:
        for event in list(new.get("hooks", {})):
            kept = [e for e in new["hooks"][event] if not any(h.get("command") in cmds for h in e.get("hooks", []))]
            if kept:
                new["hooks"][event] = kept
            else:
                del new["hooks"][event]
        for k, v in frag.get("env", {}).items():
            if new.get("env", {}).get(k) == v:
                del new["env"][k]
        if frag.get("permissions"):
            log("  note: permission rules added to settings.json at install were left in place (they may be your own too).")
    else:
        for key, items in frag.get("permissions", {}).items():
            lst = new.setdefault("permissions", {}).setdefault(key, [])
            lst.extend(i for i in items if i not in lst)
        for k, v in frag.get("env", {}).items():
            new.setdefault("env", {}).setdefault(k, v)
        for event, entries in frag.get("hooks", {}).items():
            lst = new.setdefault("hooks", {}).setdefault(event, [])
            have = {h.get("command") for e in lst for h in e.get("hooks", [])}
            for e in entries:
                if not all(h.get("command") in have for h in e.get("hooks", [])):
                    lst.append(e)
        if args.set_model:
            for k in ("model", "effortLevel", "ultracode"):
                if k in frag:
                    new[k] = frag[k]
    if new != cur:
        write_text(dst, json.dumps(new, indent=2) + "\n", target, args, ("unmerge" if remove else "merge") + " settings")


def readme_block(target, names):
    rows = []
    for n in names:
        with open(os.path.join(target, n, "workflow.json"), encoding="utf-8") as f:
            m = json.load(f)
        start = "<br>".join(f"`{u}`" if u.startswith("claude") or u.startswith("/") else u for u in m.get("usage", []))
        rows.append(f"| **{n}** {m.get('version', '')} | {m.get('summary', '')} | {start} |")
    table = "\n".join(rows)
    return f"""# Claude Code workflows

This folder (`~/.claude`) holds your personal Claude Code configuration. The parts described below were
installed by **claude-code-workflows** from `{REPO}`. Everything else here belongs to Claude Code or to you.

A *workflow* is a packaged way of working with Claude: a lead agent (the **orchestrator**), a **skill**
that tells it what to do step by step, helper agents, and sometimes scripts and safety hooks.

## Installed workflows

| Workflow | What it is for | How to start it |
|---|---|---|
{table}

Each workflow has its own guide at `~/.claude/<name>/README.md`.

## Starting a workflow

Open a terminal in your project folder and run:

```text
claude --workflow <name>
```

This starts Claude Code as the workflow's orchestrator agent (`<name>-orchestrator`) with its skill
(`<name>`) loaded. Running the skill without its agent is not supported. Extra arguments are passed on to
Claude Code, and every other `claude` command works as usual. `claude --workflow` on its own lists what
is installed.

If your shell answers `claude --workflow` with "unknown option", the shell integration is not set up:
run `python3 install.py --shell` in the claude-code-workflows repository and open a new terminal, or call
the launcher directly: `python3 ~/.claude/scripts/claude-workflow.py <name>`.

## Where things live

```text
agents/<name>-orchestrator.md   the workflow's lead agent; other agents/<name>-*.md are its helpers
skills/<name>/                  the skill: SKILL.md plus scripts/, references/, assets/
<name>/                         workflow home: workflow.json, README.md, MANIFEST.json, docs/, config, state/
hooks/<name>-*.py               safety hooks registered in settings.json (only some workflows have them)
scripts/claude-workflow.py      the `claude --workflow` launcher, shared by all workflows
backups/{MARK}/  copies of anything the installer replaced or retired
```

Some workflows also add a marked block to `CLAUDE.md` and entries to `settings.json`; the installer adds
and removes those for you.

## Updating or removing

From the claude-code-workflows repository:

```text
python3 install.py <name>              install or update
python3 install.py <name> --uninstall  remove (files you edited and state/ are kept)
```

Do not edit installed files here: change them in the repository and reinstall. The installer reports
files that differ from the repository and will not overwrite them without `--force`.
"""


def update_readme(target, args):
    names = installed_names(target)
    dst = os.path.join(target, "README.md")
    cur = open(dst, encoding="utf-8").read() if os.path.exists(dst) else ""
    if cur and is_legacy("README.md", dst):
        log(f"  retire old-layout file (moved to backups): {dst}")
        backup(target, dst, args)
        cur = ""
    new = set_block(cur, MARK, readme_block(target, names) if names else None)
    if new or cur:
        if not new.strip() and os.path.exists(dst):
            log(f"  remove: {dst}")
            if not args.dry_run:
                backup(target, dst, args)
                os.remove(dst)
            return
        write_text(dst, new, target, args, "update workflows overview in")


# ---------------------------------------------------------------- install / uninstall

def install(name, target, args):
    meta = load_meta(name)
    problems = manifest_problems(name)
    if problems:
        sys.exit(f"'{name}' payload does not match its MANIFEST.json; run python3 tools/update_manifest.py\n  "
                 + "\n  ".join(problems))
    read_settings(os.path.join(target, "settings.json"))  # fail before writing anything if it is unreadable
    log(f"{'[dry run] ' if args.dry_run else ''}Installing {name} {meta.get('version', '')} into {target}")

    files = install_map(name) + [(LAUNCHER_SRC, LAUNCHER_REL)]
    # What the last install wrote: a file still matching it was not edited by the user, so it may be upgraded.
    rec_path = tpath(target, f"{name}/MANIFEST.json")
    recorded = {}
    for other in installed_names(target):
        try:
            with open(tpath(target, f"{other}/MANIFEST.json"), encoding="utf-8") as f:
                for x in json.load(f).get("files", []):
                    if other == name or x.get("shared"):
                        recorded[x["path"]] = x["sha256"]
        except (OSError, ValueError, KeyError, TypeError):
            pass
    adds, overwrites, conflicts, unchanged, retire, links = [], [], [], 0, [], []
    for rel in sorted({r.split("/")[0] + "/" + r.split("/")[1] for _, r in files if r.startswith("skills/")}):
        if is_link(tpath(target, rel)):
            (links if args.force else conflicts).append(tpath(target, rel) + " is a link (junction or symlink); --force replaces the link, never its target")
    for src, rel in files:
        dst = tpath(target, rel)
        if not os.path.exists(dst):
            adds.append((src, dst))
        elif sha(src) == sha(dst):
            unchanged += 1
        elif args.force or recorded.get(rel) == sha(dst):
            overwrites.append((src, dst))
        else:
            conflicts.append(f"{dst} differs from the repository version")
    for rel, digest in (meta.get("legacy_files") or {}).items():
        if rel == "README.md":
            continue  # the overview README is handled by update_readme, whichever workflow installs first
        dst = tpath(target, rel)
        if os.path.isfile(dst):
            if sha(dst) == digest:
                retire.append(dst)
            else:
                log(f"  note: {dst} is left over from an older layout but was modified; left in place, review it by hand")

    if conflicts:
        log("Stopped before writing anything:")
        for c in conflicts:
            log(f"  conflict: {c}")
        log("Review those files, then re-run with --force to replace them (copies are kept in backups/).")
        return False

    if not args.dry_run and links:
        for link in links:
            p = link.split(" is a link")[0]
            log(f"  replace link: {p}")
            os.unlink(p) if os.path.islink(p) else os.rmdir(p)
    for dst in retire:
        log(f"  retire old-layout file (moved to backups): {dst}")
        if not args.dry_run:
            backup(target, dst, args)
            os.remove(dst)
            d = os.path.dirname(dst)
            while os.path.abspath(d) != os.path.abspath(target) and os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
                d = os.path.dirname(d)
    for src, dst in adds + overwrites:
        log(f"  {'add' if (src, dst) in adds else 'overwrite (copy kept in backups)'}: {dst}")
        if not args.dry_run:
            if os.path.exists(dst):
                backup(target, dst, args)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(src, dst)
    with open(os.path.join(workflow_dir(name), "MANIFEST.json"), encoding="utf-8") as f:
        record = json.load(f)
    record["files"].append({"path": LAUNCHER_REL, "sha256": sha(LAUNCHER_SRC), "shared": True})
    write_text(rec_path, json.dumps(record, indent=2) + "\n", target, args, "record")
    merge_contract(name, target, args)
    merge_settings(name, target, args)
    if not args.dry_run:
        update_readme(target, args)
    else:
        log(f"  update workflows overview in: {os.path.join(target, 'README.md')}")
    log(f"files: {len(adds)} added, {len(overwrites)} overwritten, {unchanged} unchanged, {len(retire)} retired")
    log(f"Start it with: claude --workflow {name}   (restart Claude Code first if it is running)")
    return True


def uninstall(name, target, args):
    man_path = tpath(target, f"{name}/MANIFEST.json")
    src = man_path if os.path.isfile(man_path) else os.path.join(workflow_dir(name), "MANIFEST.json")
    if not os.path.isfile(src):
        sys.exit(f"no record of {name} in {target}")
    with open(src, encoding="utf-8") as f:
        manifest = json.load(f)
    log(f"{'[dry run] ' if args.dry_run else ''}Removing {name} from {target}")
    removed, kept, dirs = 0, [], set()
    for item in manifest["files"] + [{"path": f"{name}/MANIFEST.json", "sha256": None}]:
        if item.get("shared"):
            continue  # the shared launcher goes only when the last workflow does (below)
        dst = tpath(target, item["path"])
        if not os.path.isfile(dst):
            continue
        if item["sha256"] and sha(dst) != item["sha256"] and not args.force:
            kept.append(dst)
            continue
        if item["sha256"] and sha(dst) != item["sha256"]:
            backup(target, dst, args)
        log(f"  remove: {dst}")
        removed += 1
        if not args.dry_run:
            os.remove(dst)
        d = os.path.dirname(dst)
        while os.path.abspath(d) != os.path.abspath(target):
            dirs.add(d)
            d = os.path.dirname(d)
    if not args.dry_run:
        for d in sorted(dirs, key=len, reverse=True):
            if os.path.isdir(d) and not os.listdir(d):
                os.rmdir(d)
    merge_contract(name, target, args, remove=True)
    merge_settings(name, target, args, remove=True)
    if not args.dry_run:
        if not installed_names(target):
            launcher = tpath(target, LAUNCHER_REL)
            if os.path.isfile(launcher) and sha(launcher) == sha(LAUNCHER_SRC):
                log(f"  remove (no workflows left): {launcher}")
                os.remove(launcher)
        update_readme(target, args)
    for k in kept:
        log(f"  kept (modified since install; --force removes it): {k}")
    if os.path.isdir(tpath(target, name)):
        log(f"  kept: {tpath(target, name)} (state and files you added)")
    log(f"files: {removed} removed, {len(kept)} kept")
    return True


# ---------------------------------------------------------------- shell integration

def shell_profiles(args):
    if args.profile:
        return [os.path.abspath(os.path.expanduser(p)) for p in args.profile]
    found = []
    for exe in ("powershell", "pwsh"):
        path = shutil.which(exe)
        if path:
            try:
                p = subprocess.run([path, "-NoProfile", "-Command", "$PROFILE"], capture_output=True, text=True, timeout=60)
                prof = p.stdout.strip()
                if p.returncode == 0 and prof and prof not in found:
                    found.append(prof)
            except Exception:
                pass
    home = os.path.expanduser("~")
    for rc in (".bashrc", ".zshrc"):
        path = os.path.join(home, rc)
        if os.path.isfile(path):
            found.append(path)
    if os.name != "nt" and not any(p.endswith(("bashrc", "zshrc")) for p in found):
        found.append(os.path.join(home, ".bashrc"))
    return found


def shell_snippet(profile, launcher):
    if profile.lower().endswith(".ps1"):
        launcher = launcher.replace("'", "''")
        return f"""# Adds `claude --workflow <name>` (see ~/.claude/README.md). Every other `claude` call goes straight to Claude Code.
function claude {{
  if (@($args | Where-Object {{ "$_" -eq '--workflow' -or "$_" -like '--workflow=*' }}).Count -gt 0) {{
    & python3 '{launcher}' @args
    return
  }}
  $real = Get-Command claude -CommandType Application, ExternalScript -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $real) {{ Write-Error 'claude: Claude Code was not found on PATH.'; return }}
  if ($MyInvocation.ExpectingInput) {{ $input | & $real.Source @args }} else {{ & $real.Source @args }}
}}"""
    launcher = launcher.replace("\\", "/").replace("'", "'\\''")
    return f"""# Adds `claude --workflow <name>` (see ~/.claude/README.md). Every other `claude` call goes straight to Claude Code.
claude() {{
  for _a in "$@"; do
    case "$_a" in --workflow|--workflow=*) python3 '{launcher}' "$@"; return;; esac
  done
  command claude "$@"
}}"""


def shell(target, args):
    launcher = os.path.abspath(tpath(target, LAUNCHER_REL))
    if not args.uninstall and not os.path.isfile(launcher):
        log(f"note: {launcher} is not installed yet; install a workflow first or `claude --workflow` will fail.")
    for prof in shell_profiles(args):
        raw = open(prof, "rb").read() if os.path.exists(prof) else b""
        bom = raw.startswith(b"\xef\xbb\xbf")
        cur = raw.decode("utf-8-sig")
        begin, end = f"# BEGIN {MARK}", f"# END {MARK}"
        if begin in cur and end in cur:
            head, rest = cur.split(begin, 1)
            tail = rest.split(end, 1)[1].lstrip("\n")
        else:
            head, tail = cur, ""
        if args.uninstall:
            new = (head.rstrip() + ("\n" + tail if tail else "")).strip()
            new = new + "\n" if new else ""
        else:
            block = f"{begin}\n{shell_snippet(prof, launcher)}\n{end}\n"
            new = (head.rstrip() + "\n\n" if head.strip() else "") + block + (("\n" + tail) if tail else "")
        if new == cur:
            log(f"  unchanged: {prof}")
            continue
        log(f"  {'[dry run] ' if args.dry_run else ''}{'remove from' if args.uninstall else 'update'}: {prof}")
        if not args.dry_run:
            if os.path.exists(prof):
                backup(target, prof, args)
            os.makedirs(os.path.dirname(prof), exist_ok=True)
            with open(prof, "w", encoding="utf-8-sig" if bom else "utf-8", newline="\n") as f:
                f.write(new)
    if not args.uninstall:
        log("Open a new terminal, then try: claude --workflow")


# ---------------------------------------------------------------- main

def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("workflows", nargs="*", metavar="name")
    p.add_argument("--all", action="store_true", help="every workflow in this repo")
    p.add_argument("--list", action="store_true", help="list workflows in this repo")
    p.add_argument("--uninstall", action="store_true", help="remove instead of install")
    p.add_argument("--shell", action="store_true", help="add (or with --uninstall remove) `claude --workflow` in your shell profiles")
    p.add_argument("--profile", action="append", help="with --shell: profile file to edit instead of the detected ones (repeatable)")
    p.add_argument("--target", default=os.environ.get("CLAUDE_CONFIG_DIR") or "~/.claude", help="Claude Code config dir (default ~/.claude)")
    p.add_argument("--dry-run", action="store_true", help="show what would change, write nothing")
    p.add_argument("--force", action="store_true", help="replace differing files / remove modified files (copies kept in backups/)")
    p.add_argument("--set-model", action="store_true", help="also apply a workflow's model/effort defaults to settings.json")
    args = p.parse_args()
    target = os.path.abspath(os.path.expanduser(args.target))

    if args.list or not (args.workflows or args.all or args.shell):
        have = installed_names(target)
        for n in available():
            m = load_meta(n)
            log(f"{n:<20} {m.get('version', ''):<8} {'installed' if n in have else '':<10} {m.get('summary', '')}")
        return 0
    names = available() if args.all else args.workflows
    for n in names:
        load_meta(n)
    ok = True
    if names and not args.uninstall:
        check_python()
        if target != os.path.abspath(os.path.expanduser("~/.claude")):
            log(f"  warning: hooks and launchers refer to $HOME/.claude; installing into {target} is for testing.")
    for n in names:
        ok = (uninstall if args.uninstall else install)(n, target, args) and ok
    if args.shell:
        shell(target, args)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
