#!/usr/bin/env python3
"""Check that every workflow follows the shared format, then run each workflow's tests.

    python3 tools/check.py                     self-test + format + manifests + every workflows/<name>/tests/*.py
    python3 tools/check.py --install-roundtrip also install, upgrade and uninstall everything in a temp folder
    python3 tools/check.py double-diamond      only the named workflow(s)

The format is described in the repository README ("The workflow format"). The self-test copies each real
workflow, breaks one rule at a time, and makes sure the checker notices, so a rule cannot silently stop
working. Exit 0 when everything passes. Standard library only.
"""
import argparse, contextlib, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import install  # noqa: E402

failures = []
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
HOOK_PATH_RE = re.compile(r"(?:\$HOME|~)/\.claude/([^\s'\"]+?\.(?:py|sh|ps1))")
MANIFEST_NOISE = "MANIFEST.json out of date"


def fail(name, msg):
    failures.append(f"{name}: {msg}")


def frontmatter(path):
    """Return name, description, tools, skills and raw text from a Markdown file's YAML frontmatter."""
    text = open(path, encoding="utf-8").read()
    out = {"name": None, "description": "", "tools": "", "skills": [], "text": ""}
    m = re.match(r"^---\r?\n(.*?)\r?\n---\r?\n", text, re.S)
    if not m:
        return out
    fm = out["text"] = m.group(1)
    for key in ("name", "description", "tools"):
        v = re.search(rf"^{key}:\s*(.+?)\s*$", fm, re.M)
        if v:
            out[key] = v.group(1).strip().strip("'\"")
    block = re.search(r"^skills:\s*\r?\n((?:[ \t]+-\s*.+\r?\n?)+)", fm, re.M)
    flow = re.search(r"^skills:\s*\[(.*?)\]\s*$", fm, re.M)
    if block:
        out["skills"] = [s.strip().strip("'\"") for s in re.findall(r"-\s*(.+)", block.group(1))]
    elif flow:
        out["skills"] = [s.strip().strip("'\"") for s in flow.group(1).split(",") if s.strip()]
    return out


def payload_texts(payload):
    """{relative path: text} for every text file in the payload."""
    out = {}
    for path in install._walk(payload):
        try:
            with open(path, encoding="utf-8") as f:
                out[os.path.relpath(path, payload).replace(os.sep, "/")] = f.read()
        except (UnicodeDecodeError, OSError):
            pass
    return out


def check_tree():
    """Every folder under workflows/ must be a workflow (have a workflow.json)."""
    for entry in sorted(os.listdir(install.WORKFLOWS)):
        path = os.path.join(install.WORKFLOWS, entry)
        if os.path.isdir(path) and entry != "__pycache__" and not os.path.isfile(os.path.join(path, "workflow.json")):
            fail(entry, "folder under workflows/ has no workflow.json (finish it or remove it)")


def check_format(name):
    wf = install.workflow_dir(name)
    payload = os.path.join(wf, "payload")
    try:
        meta = install.load_meta(name)
    except (ValueError, SystemExit) as e:
        return fail(name, f"workflow.json unreadable: {e}")
    for key in ("name", "version", "summary", "orchestrator", "skill", "usage", "launch"):
        if key not in meta:
            fail(name, f"workflow.json is missing '{key}'")
    if not NAME_RE.match(name):
        fail(name, "folder name must be lowercase words joined by hyphens (e.g. my-workflow)")
    if meta.get("name") != name:
        fail(name, "workflow.json 'name' must match the folder name")
    orch = f"{name}-orchestrator"
    if meta.get("orchestrator") != orch:
        fail(name, f"workflow.json 'orchestrator' must be '{orch}'")
    if meta.get("skill") != name:
        fail(name, f"workflow.json 'skill' must be '{name}'")
    if not os.path.isfile(os.path.join(wf, "README.md")):
        fail(name, "README.md is missing")

    allowed = {"agents", "skills", "hooks", name} | install.MERGED
    for entry in os.listdir(payload) if os.path.isdir(payload) else []:
        if entry not in allowed and entry != "__pycache__":
            fail(name, f"payload/{entry} is not part of the format (allowed: {', '.join(sorted(allowed))})")

    # The skill: named like the workflow, alone in skills/, and gated on the orchestrator agent.
    skill_md = os.path.join(payload, "skills", name, "SKILL.md")
    if not os.path.isfile(skill_md):
        fail(name, f"payload/skills/{name}/SKILL.md is missing")
    else:
        if frontmatter(skill_md)["name"] != name:
            fail(name, f"SKILL.md frontmatter name must be '{name}'")
        body = open(skill_md, encoding="utf-8").read()
        gate = re.search(r"^## Entry gate[ \t]*\r?\n(.*?)(?=^## |\Z)", body, re.M | re.S)
        if not gate or orch not in gate.group(1):
            fail(name, f"SKILL.md needs an '## Entry gate' section that requires the {orch} agent")
    skills_dir = os.path.join(payload, "skills")
    if os.path.isdir(skills_dir):
        extra = [d for d in os.listdir(skills_dir) if d != name]
        if extra:
            fail(name, f"payload/skills may only contain '{name}/' (found {', '.join(extra)})")

    # Agents: <name>-<role>.md, file name = frontmatter name, orchestrators preload the skill and say how to start.
    agents_dir = os.path.join(payload, "agents")
    agents = sorted(os.listdir(agents_dir)) if os.path.isdir(agents_dir) else []
    if f"{orch}.md" not in agents:
        fail(name, f"payload/agents/{orch}.md is missing")
    for a in agents:
        if not (a.startswith(f"{name}-") and a.endswith(".md")):
            fail(name, f"payload/agents/{a} must be named {name}-<role>.md")
            continue
        fm = frontmatter(os.path.join(agents_dir, a))
        if fm["name"] != a[:-3]:
            fail(name, f"payload/agents/{a} frontmatter name is '{fm['name']}', expected '{a[:-3]}'")
        if a.startswith(orch):
            if name not in fm["skills"]:
                fail(name, f"payload/agents/{a} must preload its skill (frontmatter 'skills: [- {name}]')")
            if f"Start it with `claude --workflow {name}" not in fm["description"]:
                fail(name, f"payload/agents/{a} description must end with: Start it with `claude --workflow {name}`.")
        for group in re.findall(r"Agent\(([^)]*)\)", fm["tools"]):
            for helper in (x.strip() for x in group.split(",") if x.strip()):
                if f"{helper}.md" not in agents:
                    fail(name, f"payload/agents/{a} tools list Agent({helper}), which is not in payload/agents")

    hooks_dir = os.path.join(payload, "hooks")
    for h in os.listdir(hooks_dir) if os.path.isdir(hooks_dir) else []:
        if h != "__pycache__" and not h.startswith(f"{name}-"):
            fail(name, f"payload/hooks/{h} must be named {name}-<purpose>")

    # Hook commands (agent frontmatter and settings fragment) must run files this workflow installs.
    shipped = {rel for _, rel in install.install_map(name)} if os.path.isdir(payload) else set()
    texts = payload_texts(payload) if os.path.isdir(payload) else {}
    for rel, text in texts.items():
        if rel.startswith("agents/") or rel == "settings.fragment.json":
            for ref in HOOK_PATH_RE.findall(text):
                if ref not in shipped and ref != install.LAUNCHER_REL:
                    fail(name, f"payload/{rel} runs ~/.claude/{ref}, which this workflow does not install")

    # legacy_files: {path: sha256}, never a path still shipped, and nothing in the payload still points at it.
    legacy = meta.get("legacy_files", {})
    if not isinstance(legacy, dict):
        fail(name, "legacy_files must be an object of {path: sha256}")
        legacy = {}
    shipped_names = {r.rsplit("/", 1)[-1] for r in shipped}
    for rel, digest in legacy.items():
        if not (isinstance(digest, str) and re.fullmatch(r"[0-9a-f]{64}", digest)):
            fail(name, f"legacy_files['{rel}'] must be a lowercase SHA-256 hex digest")
        if rel.startswith(("/", "..")) or "/../" in rel or "\\" in rel or ":" in rel:
            fail(name, f"legacy_files['{rel}'] must be a plain path relative to ~/.claude")
        if rel in shipped:
            fail(name, f"legacy_files names '{rel}', which this version still ships")
        # Old agent names (hyphenated; plain words like "builder" are too common to scan for) and old file
        # names that are no longer shipped anywhere must not appear in the payload.
        base = rel.rsplit("/", 1)[-1]
        needles = [f".claude/{rel}"]
        if rel.startswith("agents/"):
            if "-" in base[:-3]:
                needles.append(base[:-3])
        elif base not in shipped_names:
            needles.append(base)
        for needle in needles:
            pattern = re.compile(rf"(?<![\w.-]){re.escape(needle)}(?![\w-])")
            for prel, text in texts.items():
                if pattern.search(text):
                    fail(name, f"stale reference: payload/{prel} still mentions '{needle}' (retired in legacy_files)")

    # Launch: the orchestrator agent directly, or the workflow's own scripts, which start it and forward arguments.
    launch = meta.get("launch") or {}
    if "agent" in launch:
        if launch["agent"] != orch:
            fail(name, f"launch.agent must be '{orch}'")
    elif "windows" in launch or "posix" in launch:
        for key, forward in (("windows", "$args"), ("posix", '"$@"')):
            rel = launch.get(key)
            if not rel:
                fail(name, f"launch.{key} is missing")
                continue
            path = os.path.join(payload, *rel.split("/"))
            if not os.path.isfile(path):
                fail(name, f"launch.{key} {rel} does not exist in payload/")
                continue
            script = open(path, encoding="utf-8").read()
            if orch not in script:
                fail(name, f"launch.{key} {rel} never starts {orch}")
            if forward not in script:
                fail(name, f"launch.{key} {rel} must forward extra arguments (no {forward} found)")
    else:
        fail(name, "launch needs 'agent', or 'windows' and 'posix' scripts")

    for p in install.manifest_problems(name):
        fail(name, f"{MANIFEST_NOISE} ({p}); run python3 tools/update_manifest.py")


# ---------------------------------------------------------------- self-test of the rules

def _sub(path, old, new, count=1):
    with open(path, encoding="utf-8") as f:
        text = f.read()
    if old not in text:
        raise AssertionError(f"self-test fixture is stale: '{old[:50]}' not found in {path}")
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text.replace(old, new) if count is None else text.replace(old, new, count))


def _json_edit(path, fn):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    fn(data)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(data, f, indent=2)


def self_test():
    """Break each rule in a copy of a real workflow and make sure check_format reports it."""
    vd, dd = "verified-diamond", "double-diamond"

    def P(wf, *parts):
        return os.path.join(wf, "payload", *parts)

    vd_orch = ("agents", "verified-diamond-orchestrator.md")
    cases = [
        # (label, source workflow, name of the copy, mutation, expected message fragment or None for "no failures")
        ("unchanged verified-diamond copy passes", vd, vd, lambda wf: None, None),
        ("unchanged double-diamond copy passes", dd, dd, lambda wf: None, None),
        ("flow-style skills: [x] is accepted", vd, vd,
         lambda wf: _sub(P(wf, *vd_orch), "skills:\n  - verified-diamond\n", "skills: [verified-diamond]\n"), None),
        ("folder name", vd, "Bad_Name",
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d.update(name="Bad_Name")), "folder name"),
        ("entry gate", vd, vd, lambda wf: _sub(P(wf, "skills", vd, "SKILL.md"), "## Entry gate", "## Start"), "Entry gate"),
        ("orchestrator description", vd, vd,
         lambda wf: _sub(P(wf, *vd_orch), " Start it with `claude --workflow verified-diamond`.", ""), "description must end"),
        ("skill preload", vd, vd, lambda wf: _sub(P(wf, *vd_orch), "skills:\n  - verified-diamond\n", ""), "preload"),
        ("Agent(...) target exists", dd, dd,
         lambda wf: _sub(P(wf, "agents", "double-diamond-orchestrator.md"), "Agent(double-diamond-scout,",
                         "Agent(double-diamond-ghost, double-diamond-scout,"), "Agent(double-diamond-ghost)"),
        ("hook command file exists (settings fragment)", vd, vd,
         lambda wf: _sub(P(wf, "settings.fragment.json"), "verified-diamond-policy.py pre", "verified-diamond-missing.py pre"),
         "does not install"),
        ("hook command file exists (agent frontmatter)", dd, dd,
         lambda wf: _sub(P(wf, "agents", "double-diamond-scout.md"), "scripts/guard_write.py", "scripts/guard_gone.py"),
         "does not install"),
        ("legacy_files digest shape", vd, vd,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_files"].update({"agents/builder.md": "abc"})),
         "SHA-256"),
        ("legacy_files path shape", vd, vd,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_files"].update({"../outside.md": "0" * 64})),
         "plain path"),
        ("legacy_files must not name a shipped file", vd, vd,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"),
                               lambda d: d["legacy_files"].update({"agents/verified-diamond-builder.md": "0" * 64})),
         "still ships"),
        ("stale reference to a retired file", dd, dd,
         lambda wf: _sub(P(wf, "skills", dd, "SKILL.md"), "## Entry gate", "Run dd-claude.ps1 first.\n\n## Entry gate"),
         "stale reference"),
        ("stale reference to a retired agent", dd, dd,
         lambda wf: _sub(P(wf, "skills", dd, "SKILL.md"), "## Entry gate", "Ask dd-scout.\n\n## Entry gate"), "stale reference"),
        ("launch script forwards arguments", dd, dd,
         lambda wf: _sub(P(wf, "skills", dd, "scripts", "launch.ps1"), "$args", "$noargs", None), "forward extra arguments"),
        ("launch script starts the orchestrator", dd, dd,
         lambda wf: _sub(P(wf, "skills", dd, "scripts", "launch.sh"), "double-diamond-orchestrator", "someone-else", None),
         "never starts"),
        ("payload folders are limited", vd, vd, lambda wf: os.makedirs(P(wf, "scripts")), "not part of the format"),
        ("agent naming", vd, vd,
         lambda wf: shutil.copy(P(wf, "agents", "verified-diamond-builder.md"), P(wf, "agents", "builder.md")), "must be named"),
        ("manifest freshness", vd, vd, lambda wf: open(os.path.join(wf, "README.md"), "a").write("x\n"), MANIFEST_NOISE),
    ]
    real_root, saved = install.WORKFLOWS, failures[:]
    bad = []
    try:
        with tempfile.TemporaryDirectory(prefix="ccw-selftest-") as tmp:
            for i, (label, src, copy_name, mutate, expect) in enumerate(cases):
                root = os.path.join(tmp, str(i))
                wf = os.path.join(root, copy_name)
                shutil.copytree(os.path.join(real_root, src), wf, ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
                try:
                    mutate(wf)
                except AssertionError as e:
                    bad.append(f"{label}: {e}")
                    continue
                install.WORKFLOWS = root
                failures[:] = []
                try:
                    check_format(copy_name)
                finally:
                    install.WORKFLOWS = real_root
                got = failures[:]
                if expect is None:
                    got = [g for g in got if MANIFEST_NOISE not in g]
                    if got:
                        bad.append(f"{label}: expected no failures, got {got}")
                elif not any(expect in g for g in got):
                    bad.append(f"{label}: expected a failure mentioning '{expect}', got {got}")
            # A folder without workflow.json is reported.
            orphan_root = os.path.join(tmp, "orphan")
            os.makedirs(os.path.join(orphan_root, "half-done"))
            install.WORKFLOWS = orphan_root
            failures[:] = []
            try:
                check_tree()
            finally:
                install.WORKFLOWS = real_root
            if not any("no workflow.json" in g for g in failures):
                bad.append("folder without workflow.json: not reported")
    finally:
        install.WORKFLOWS = real_root
        failures[:] = saved
    for b in bad:
        fail("self-test", b)
    print(f"checker self-test: {'ok' if not bad else 'FAILED'} ({len(cases) + 1} cases)")


# ---------------------------------------------------------------- tests and install round trip

def run_tests(name):
    tests = os.path.join(install.workflow_dir(name), "tests")
    found = sorted(f for f in os.listdir(tests) if f.endswith(".py")) if os.path.isdir(tests) else []
    if not found:
        fail(name, "tests/ has no test script")
    for t in found:
        p = subprocess.run([sys.executable, "-B", os.path.join(tests, t)], capture_output=True, text=True, encoding="utf-8")
        status = "ok" if p.returncode == 0 else "FAILED"
        print(f"  {name}/tests/{t}: {status}")
        if p.returncode != 0:
            fail(name, f"tests/{t} failed:\n{p.stdout[-3000:]}{p.stderr[-3000:]}")


def legacy_retire_check():
    """In-process: an unmodified old-layout file is retired to backups; an edited one is left in place."""
    real_root = install.WORKFLOWS
    with tempfile.TemporaryDirectory(prefix="ccw-legacy-") as tmp:
        root, target = os.path.join(tmp, "workflows"), os.path.join(tmp, ".claude")
        name = "verified-diamond"
        shutil.copytree(os.path.join(real_root, name), os.path.join(root, name), ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        os.makedirs(os.path.join(target, "agents", "olddir"))
        for rel, text in (("agents/old-agent.md", "old\n"), ("agents/olddir/old-edited.md", "edited\n")):
            with open(os.path.join(target, *rel.split("/")), "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
        legacy = {"agents/old-agent.md": hashlib.sha256(b"old\n").hexdigest(),
                  "agents/olddir/old-edited.md": hashlib.sha256(b"orig\n").hexdigest()}
        _json_edit(os.path.join(root, name, "workflow.json"), lambda d: d["legacy_files"].update(legacy))
        install.WORKFLOWS = root
        try:
            with open(os.path.join(root, name, "MANIFEST.json"), "w", encoding="utf-8", newline="\n") as f:
                json.dump(install.build_manifest(name), f, indent=2)
            args = argparse.Namespace(dry_run=False, force=False, set_model=False)
            with contextlib.redirect_stdout(io.StringIO()):
                ok = install.install(name, target, args)
        finally:
            install.WORKFLOWS = real_root
        backups = [os.path.join(b, f) for b, _, fs in os.walk(os.path.join(target, "backups")) for f in fs]
        if not ok:
            fail("roundtrip", "legacy check: install failed")
        if os.path.exists(os.path.join(target, "agents", "old-agent.md")) or not any(p.endswith("old-agent.md") for p in backups):
            fail("roundtrip", "an unmodified legacy file was not retired to backups")
        if not os.path.exists(os.path.join(target, "agents", "olddir", "old-edited.md")):
            fail("roundtrip", "an edited legacy file was removed")


def roundtrip(names):
    before = len(failures)
    with tempfile.TemporaryDirectory(prefix="ccw-check-") as tmp:
        target = os.path.join(tmp, ".claude")
        os.makedirs(target)
        open(os.path.join(target, "CLAUDE.md"), "w").write("# Mine\nkeep me\n")
        with open(os.path.join(target, "settings.json"), "w", encoding="utf-8-sig") as f:  # BOM, as PowerShell writes it
            f.write('{"model": "sonnet"}\n')
        env = {**os.environ, "CLAUDE_CONFIG_DIR": target}
        installer = os.path.join(REPO, "install.py")

        def run(*args):
            return subprocess.run([sys.executable, "-B", *args], capture_output=True, text=True, encoding="utf-8", env=env)

        p = run(installer, *names, "--target", target)
        if p.returncode != 0:
            return fail("roundtrip", f"install failed:\n{p.stdout}{p.stderr}")
        again = run(installer, *names, "--target", target)
        if again.returncode != 0 or " 0 added, 0 overwritten" not in again.stdout:
            fail("roundtrip", f"second install is not a no-op:\n{again.stdout}{again.stderr}")

        # A user edit is a conflict (nothing written); a file still matching the install record is upgraded.
        n0 = names[0]
        rec_path = os.path.join(target, n0, "MANIFEST.json")
        readme_rel = f"{n0}/README.md"
        readme_path = os.path.join(target, n0, "README.md")
        repo_readme = os.path.join(install.workflow_dir(n0), "README.md")
        open(readme_path, "a", encoding="utf-8").write("\nuser edit\n")
        p = run(installer, n0, "--target", target)
        if p.returncode == 0 or "conflict" not in p.stdout:
            fail("roundtrip", f"an edited installed file was not reported as a conflict:\n{p.stdout}")
        rec = json.load(open(rec_path, encoding="utf-8"))
        for item in rec["files"]:
            if item["path"] == readme_rel:
                item["sha256"] = install.sha(readme_path)  # pretend an older version installed this content
        json.dump(rec, open(rec_path, "w", encoding="utf-8"))
        p = run(installer, n0, "--target", target)
        if p.returncode != 0 or install.sha(readme_path) != install.sha(repo_readme):
            fail("roundtrip", f"a recorded older file was not upgraded:\n{p.stdout}{p.stderr}")

        # --force replaces a user edit and keeps a copy of it in backups.
        open(readme_path, "a", encoding="utf-8").write("\nforced edit\n")
        p = run(installer, n0, "--force", "--target", target)
        kept = [os.path.join(b, f) for b, _, fs in os.walk(os.path.join(target, "backups")) for f in fs]
        if p.returncode != 0 or install.sha(readme_path) != install.sha(repo_readme):
            fail("roundtrip", f"--force did not replace an edited file:\n{p.stdout}{p.stderr}")
        elif not any("forced edit" in open(k, encoding="utf-8", errors="replace").read() for k in kept):
            fail("roundtrip", "--force did not keep a backup of the edited file")

        settings = json.load(open(os.path.join(target, "settings.json"), encoding="utf-8-sig"))
        if settings.get("model") != "sonnet":
            fail("roundtrip", "install changed the user's model setting")

        listing = run(os.path.join(target, "scripts", "claude-workflow.py"))
        for n in names:
            if n not in listing.stdout:
                fail("roundtrip", f"launcher does not list {n}:\n{listing.stdout}{listing.stderr}")
        readme = open(os.path.join(target, "README.md"), encoding="utf-8").read()
        for n in names:
            if f"**{n}**" not in readme:
                fail("roundtrip", f"README.md does not list {n}")

        prof = os.path.join(tmp, "profile.ps1")
        rc = os.path.join(tmp, "bashrc")
        open(rc, "w").write("export A=1\n")
        for flags in ([], ["--uninstall"]):
            p = run(installer, "--shell", *flags, "--target", target, "--profile", prof, "--profile", rc)
            if p.returncode != 0:
                fail("roundtrip", f"--shell {flags} failed:\n{p.stdout}{p.stderr}")
        if open(rc).read() != "export A=1\n" or open(prof).read() != "":
            fail("roundtrip", "--shell --uninstall did not restore the profiles")

        # Removing one workflow leaves the others (and the shared launcher) working.
        if len(names) > 1:
            p = run(installer, n0, "--uninstall", "--target", target)
            listing = run(os.path.join(target, "scripts", "claude-workflow.py"))
            readme = open(os.path.join(target, "README.md"), encoding="utf-8").read()
            if p.returncode != 0 or names[1] not in listing.stdout or f"  {n0} " in listing.stdout:
                fail("roundtrip", f"uninstalling {n0} broke the remaining workflows:\n{p.stdout}{listing.stdout}{listing.stderr}")
            if f"**{n0}**" in readme or f"**{names[1]}**" not in readme:
                fail("roundtrip", f"README.md was not updated after uninstalling {n0}")

        p = run(installer, *names, "--uninstall", "--target", target)
        if p.returncode != 0:
            return fail("roundtrip", f"uninstall failed:\n{p.stdout}{p.stderr}")
        left = []
        for base, dirs, files in os.walk(target):
            dirs[:] = [d for d in dirs if d != "backups"]
            left += [os.path.relpath(os.path.join(base, f), target).replace(os.sep, "/") for f in files]
        if sorted(left) != ["CLAUDE.md", "settings.json"]:
            fail("roundtrip", f"uninstall left files behind: {sorted(left)}")
        if open(os.path.join(target, "CLAUDE.md")).read() != "# Mine\nkeep me\n":
            fail("roundtrip", "uninstall did not restore CLAUDE.md")
        settings = json.load(open(os.path.join(target, "settings.json"), encoding="utf-8-sig"))
        if settings.get("hooks"):
            fail("roundtrip", f"uninstall left hooks in settings.json: {settings['hooks']}")
    legacy_retire_check()
    print(f"  install/uninstall round trip: {'ok' if len(failures) == before else 'FAILED'}")


def main(argv):
    names = [a for a in argv if not a.startswith("--")] or install.available()
    self_test()
    check_tree()
    for n in names:
        check_format(n)
    print(f"format and manifests: {'ok' if not failures else 'FAILED'} ({', '.join(names)})")
    for n in names:
        run_tests(n)
    if "--install-roundtrip" in argv:
        roundtrip(names)
    for f in failures:
        print(f"FAIL {f}")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
