#!/usr/bin/env python3
"""Install a workflow from this repo into a Claude Code config directory (default ~/.claude).

    python3 install.py verified-diamond            # install
    python3 install.py verified-diamond --dry-run  # show what would change
    python3 install.py --list

Safe by default: existing files that differ are skipped (use --force to overwrite, with a
.bak backup), settings.json and CLAUDE.md are merged rather than replaced.
"""
import argparse, json, os, shutil, sys, time

REPO = os.path.dirname(os.path.abspath(__file__))
WORKFLOWS = os.path.join(REPO, "workflows")
# payload subdirectory -> destination under the target config dir
COPY_DIRS = ["agents", "skills", "hooks", "scripts", "verified-diamond"]
DOCS_DEST = os.path.join("verified-diamond", "docs")


def log(msg):
    print(msg)


def same(a, b):
    with open(a, "rb") as fa, open(b, "rb") as fb:
        return fa.read() == fb.read()


def copy_file(src, dst, args, stats):
    if os.path.exists(dst):
        if same(src, dst):
            stats["unchanged"] += 1
            return
        if not args.force:
            log(f"  skip (differs, use --force): {dst}")
            stats["skipped"] += 1
            return
        if not args.dry_run:
            shutil.copy2(dst, dst + f".bak-{int(time.time())}")
        log(f"  overwrite (backup kept): {dst}")
    else:
        log(f"  add: {dst}")
    stats["written"] += 1
    if not args.dry_run:
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        shutil.copy2(src, dst)


def copy_tree(src_root, dst_root, args, stats):
    for base, _, files in os.walk(src_root):
        for f in files:
            src = os.path.join(base, f)
            copy_file(src, os.path.join(dst_root, os.path.relpath(src, src_root)), args, stats)


def merge_claude_md(wf, target, args):
    src = os.path.join(wf, "payload", "global-contract.md")
    dst = os.path.join(target, "CLAUDE.md")
    name = os.path.basename(wf)
    begin, end = f"<!-- BEGIN {name} -->", f"<!-- END {name} -->"
    with open(src, encoding="utf-8") as f:
        block = f"{begin}\n{f.read().strip()}\n{end}\n"
    cur = open(dst, encoding="utf-8").read() if os.path.exists(dst) else ""
    if begin in cur and end in cur:
        head, rest = cur.split(begin, 1)
        new = head + block + rest.split(end, 1)[1].lstrip("\n")
        action = "update contract block in"
    elif "Verified Diamond global operating contract" in cur:
        log(f"  CLAUDE.md already contains an unmarked copy of the contract; left untouched")
        return
    else:
        new = (cur.rstrip() + "\n\n" if cur.strip() else "") + block
        action = "append contract block to"
    if new == cur:
        return
    log(f"  {action}: {dst}")
    if not args.dry_run:
        os.makedirs(target, exist_ok=True)
        if cur:
            shutil.copy2(dst, dst + f".bak-{int(time.time())}")
        with open(dst, "w", encoding="utf-8") as f:
            f.write(new)


def merge_settings(wf, target, args):
    with open(os.path.join(wf, "payload", "settings.fragment.json"), encoding="utf-8") as f:
        frag = json.load(f)
    dst = os.path.join(target, "settings.json")
    cur = json.load(open(dst, encoding="utf-8")) if os.path.exists(dst) else {}
    new = json.loads(json.dumps(cur))
    # permissions: union per list
    for key, items in frag.get("permissions", {}).items():
        lst = new.setdefault("permissions", {}).setdefault(key, [])
        lst.extend(i for i in items if i not in lst)
    # env: only set if absent
    for k, v in frag.get("env", {}).items():
        new.setdefault("env", {}).setdefault(k, v)
    # hooks: append entries whose command is not already registered
    for event, entries in frag.get("hooks", {}).items():
        lst = new.setdefault("hooks", {}).setdefault(event, [])
        have = {h.get("command") for e in lst for h in e.get("hooks", [])}
        for e in entries:
            if not all(h.get("command") in have for h in e.get("hooks", [])):
                lst.append(e)
    # model / effortLevel / ultracode are personal preferences: set only when absent and asked for
    if args.set_model:
        for k in ("model", "effortLevel", "ultracode"):
            if k in frag:
                new[k] = frag[k]
    if new == cur:
        return
    log(f"  merge settings: {dst}")
    if not args.dry_run:
        os.makedirs(target, exist_ok=True)
        if cur:
            shutil.copy2(dst, dst + f".bak-{int(time.time())}")
        with open(dst, "w", encoding="utf-8") as f:
            json.dump(new, f, indent=2)
            f.write("\n")


def install(name, args):
    wf = os.path.join(WORKFLOWS, name)
    if not os.path.isdir(os.path.join(wf, "payload")):
        sys.exit(f"unknown workflow: {name} (try --list)")
    own = os.path.join(wf, "install.ps1")
    if os.path.exists(own):
        sys.exit(f"'{name}' ships its own installer; see {os.path.join(wf, 'README.md')} (run {own} with pwsh)")
    target = os.path.expanduser(args.target)
    log(f"{'[dry run] ' if args.dry_run else ''}Installing '{name}' into {target}")
    stats = dict(written=0, unchanged=0, skipped=0)
    for d in COPY_DIRS:
        src = os.path.join(wf, "payload", d)
        if os.path.isdir(src):
            copy_tree(src, os.path.join(target, d), args, stats)
    docs = os.path.join(wf, "docs")
    if os.path.isdir(docs):
        copy_tree(docs, os.path.join(target, DOCS_DEST), args, stats)
    merge_claude_md(wf, target, args)
    merge_settings(wf, target, args)
    log(f"files: {stats['written']} written, {stats['unchanged']} unchanged, {stats['skipped']} skipped")
    if stats["skipped"]:
        log("Some files differ from the repo version; review them, then re-run with --force to replace.")
    log("Next: restart Claude Code, then run: python3 ~/.claude/scripts/vd.py status")


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("workflow", nargs="?")
    p.add_argument("--target", default="~/.claude", help="Claude Code config dir (default ~/.claude)")
    p.add_argument("--dry-run", action="store_true")
    p.add_argument("--force", action="store_true", help="overwrite differing files (backups kept)")
    p.add_argument("--set-model", action="store_true", help="also apply the workflow's model/effort defaults")
    p.add_argument("--list", action="store_true")
    args = p.parse_args()
    if args.list or not args.workflow:
        for n in sorted(os.listdir(WORKFLOWS)):
            if os.path.isdir(os.path.join(WORKFLOWS, n, "payload")):
                print(n)
        return
    install(args.workflow, args)


if __name__ == "__main__":
    main()
