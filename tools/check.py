#!/usr/bin/env python3
"""Check that every workflow follows the shared format, then run each workflow's tests.

    python3 tools/check.py                     self-test + format + manifests + every workflows/<name>/tests/*.py
    python3 tools/check.py --install-roundtrip also install, upgrade and uninstall everything in a temp folder
    python3 tools/check.py wf-plan      only the named workflow(s)

The format is described in the repository README ("The workflow format"). The self-test copies each real
workflow, breaks one rule at a time, and makes sure the checker notices, so a rule cannot silently stop
working. A scan for the retired workflow names runs every time. Exit 0 when everything passes. Standard library only.
"""
import argparse, contextlib, hashlib, io, json, os, re, shutil, subprocess, sys, tempfile

REPO = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO)
import install  # noqa: E402

failures = []
NAME_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
HOOK_PATH_RE = re.compile(r"(?:\$HOME|~)/\.claude/([^\s'\"]+?\.(?:py|sh|ps1))")
MANIFEST_NOISE = "MANIFEST.json out of date"
# Retired-name fixtures used by self_test; stripped from this file before the stale-name scan.
RETIRED_FIXTURES = ("dd" + "-claude.ps1", "dd" + "-scout")


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

    # replaces: names of retired workflows this one supersedes; legacy_settings: their old hooks and env.
    replaces = meta.get("replaces", [])
    if not isinstance(replaces, list):
        fail(name, "replaces must be a list of workflow names")
        replaces = []
    for old in replaces:
        if not (isinstance(old, str) and NAME_RE.match(old)):
            fail(name, f"replaces entry {old!r} must be a workflow name (lowercase words joined by hyphens)")
        elif old == name:
            fail(name, "replaces must not list the workflow's own name")
        elif old in install.available():
            fail(name, f"replaces '{old}', which is still a workflow in this repository")
    if "legacy_settings" in meta:
        ls = meta["legacy_settings"]
        if not isinstance(ls, dict):
            fail(name, "legacy_settings must be an object with 'hooks' and 'env'")
        else:
            if set(ls) - {"hooks", "env"}:
                fail(name, "legacy_settings may only contain 'hooks' and 'env'")
            hooks = ls.get("hooks", [])
            if not (isinstance(hooks, list) and all(isinstance(h, str) for h in hooks)):
                fail(name, "legacy_settings.hooks must be a list of command strings")
                hooks = []
            env = ls.get("env", {})
            if not (isinstance(env, dict) and all(isinstance(k, str) and isinstance(v, str) for k, v in env.items())):
                fail(name, "legacy_settings.env must be an object of string to string")
            frag = texts.get("settings.fragment.json")
            if frag:
                try:
                    registered = {h.get("command") for es in json.loads(frag).get("hooks", {}).values()
                                  for e in es for h in e.get("hooks", [])}
                except (ValueError, AttributeError):
                    registered = set()
                for cmd in hooks:
                    if cmd in registered:
                        fail(name, f"legacy_settings.hooks lists '{cmd}', which settings.fragment.json still registers")

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


# ---------------------------------------------------------------- stale-name scan

# Built by concatenation so this file does not match its own patterns.
STALE_ANY_CASE = re.compile("double" + ".?" + "diamond|" + "verified" + ".?" + "diamond", re.I)
STALE_SHORT = re.compile("\\b" + "dd" + "[-_]|\\b" + "vd" + "[-_]|\\b" + "vd" + "\\." + "py\\b", re.I)
STALE_SKIP_DIRS = {".git", "__pycache__"}
STALE_SKIP_ROOT_DIRS = {"backups", ".claude"}  # skipped only directly under the repo root
STALE_HISTORY = "docs/plans/rename-workflows/"  # the plan that drove the rename, kept as a historical record


def scan_stale_names(root):
    """Return 'file:line: text' for every retired workflow name under root (allowlist: see the README)."""
    hits = []
    def stale(s):
        return STALE_ANY_CASE.search(s) or STALE_SHORT.search(s)

    for base, dirs, files in os.walk(root):
        at_root = os.path.abspath(base) == os.path.abspath(root)
        dirs[:] = sorted(d for d in dirs if d not in STALE_SKIP_DIRS and not (at_root and d in STALE_SKIP_ROOT_DIRS))
        for fn in sorted(dirs + files):
            rel = os.path.relpath(os.path.join(base, fn), root).replace(os.sep, "/")
            if rel.startswith(STALE_HISTORY) or (rel + "/").startswith(STALE_HISTORY):
                continue
            if stale(fn):
                hits.append(f"{rel}: old name in the path")
        for fn in sorted(files):
            path = os.path.join(base, fn)
            rel = os.path.relpath(path, root).replace(os.sep, "/")
            if rel.startswith(STALE_HISTORY):
                continue
            try:
                with open(path, "rb") as f:
                    raw = f.read()
            except OSError:
                continue
            if b"\0" in raw[:8192]:
                continue  # a real binary
            text = raw.decode("utf-8", errors="replace")
            if rel == "tools/check.py":
                def strip_fixtures(ln):
                    for fixture in RETIRED_FIXTURES:
                        ln = ln.replace(fixture, "")
                    return ln

                text = "\n".join(strip_fixtures(ln) if "_sub(" in ln else ln for ln in text.split("\n"))
            elif re.fullmatch(r"workflows/[^/]+/workflow\.json", rel):
                try:
                    data = json.loads(text)
                    for key in ("legacy_files", "replaces", "legacy_settings"):
                        data.pop(key, None)
                    text = json.dumps(data, indent=2)
                except (ValueError, AttributeError):
                    pass
            for n, line in enumerate(text.splitlines(), 1):
                if stale(line):
                    hits.append(f"{rel}:{n}: {line.strip()[:100]}")
    return hits


def check_stale_names():
    for hit in scan_stale_names(REPO):
        fail("stale-name", hit)


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
    wf_build, wf_plan = "wf-build", "wf-plan"

    def P(wf, *parts):
        return os.path.join(wf, "payload", *parts)

    wf_build_orch = ("agents", "wf-build-orchestrator.md")
    cases = [
        # (label, source workflow, name of the copy, mutation, expected message fragment or None for "no failures")
        ("unchanged wf-build copy passes", wf_build, wf_build, lambda wf: None, None),
        ("unchanged wf-plan copy passes", wf_plan, wf_plan, lambda wf: None, None),
        ("flow-style skills: [x] is accepted", wf_build, wf_build,
         lambda wf: _sub(P(wf, *wf_build_orch), "skills:\n  - wf-build\n", "skills: [wf-build]\n"), None),
        ("folder name", wf_build, "Bad_Name",
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d.update(name="Bad_Name")), "folder name"),
        ("entry gate", wf_build, wf_build, lambda wf: _sub(P(wf, "skills", wf_build, "SKILL.md"), "## Entry gate", "## Start"), "Entry gate"),
        ("orchestrator description", wf_build, wf_build,
         lambda wf: _sub(P(wf, *wf_build_orch), " Start it with `claude --workflow wf-build`.", ""), "description must end"),
        ("skill preload", wf_build, wf_build, lambda wf: _sub(P(wf, *wf_build_orch), "skills:\n  - wf-build\n", ""), "preload"),
        ("Agent(...) target exists", wf_plan, wf_plan,
         lambda wf: _sub(P(wf, "agents", "wf-plan-orchestrator.md"), "Agent(wf-plan-scout,",
                         "Agent(wf-plan-ghost, wf-plan-scout,"), "Agent(wf-plan-ghost)"),
        ("hook command file exists (settings fragment)", wf_build, wf_build,
         lambda wf: _sub(P(wf, "settings.fragment.json"), "wf-build-policy.py pre", "wf-build-missing.py pre"),
         "does not install"),
        ("hook command file exists (agent frontmatter)", wf_plan, wf_plan,
         lambda wf: _sub(P(wf, "agents", "wf-plan-scout.md"), "scripts/guard_write.py", "scripts/guard_gone.py"),
         "does not install"),
        ("legacy_files digest shape", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_files"].update({"agents/builder.md": "abc"})),
         "SHA-256"),
        ("legacy_files path shape", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_files"].update({"../outside.md": "0" * 64})),
         "plain path"),
        ("legacy_files must not name a shipped file", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"),
                               lambda d: d["legacy_files"].update({"agents/wf-build-builder.md": "0" * 64})),
         "still ships"),
        ("stale reference to a retired file", wf_plan, wf_plan,
         lambda wf: _sub(P(wf, "skills", wf_plan, "SKILL.md"), "## Entry gate", f"Run {RETIRED_FIXTURES[0]} first.\n\n## Entry gate"),
         "stale reference"),
        ("stale reference to a retired agent", wf_plan, wf_plan,
         lambda wf: _sub(P(wf, "skills", wf_plan, "SKILL.md"), "## Entry gate", f"Ask {RETIRED_FIXTURES[1]}.\n\n## Entry gate"), "stale reference"),
        ("launch script forwards arguments", wf_plan, wf_plan,
         lambda wf: _sub(P(wf, "skills", wf_plan, "scripts", "launch.ps1"), "$args", "$noargs", None), "forward extra arguments"),
        ("launch script starts the orchestrator", wf_plan, wf_plan,
         lambda wf: _sub(P(wf, "skills", wf_plan, "scripts", "launch.sh"), "wf-plan-orchestrator", "someone-else", None),
         "never starts"),
        ("payload folders are limited", wf_build, wf_build, lambda wf: os.makedirs(P(wf, "scripts")), "not part of the format"),
        ("agent naming", wf_build, wf_build,
         lambda wf: shutil.copy(P(wf, "agents", "wf-build-builder.md"), P(wf, "agents", "builder.md")), "must be named"),
        ("replaces must be a list", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d.update(replaces="x")), "replaces must be a list"),
        ("replaces entry shape", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d.update(replaces=["Bad_Name"])), "must be a workflow name"),
        ("replaces not its own name", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d.update(replaces=["wf-build"])), "own name"),
        ("replaces not a workflow in this repo", wf_build, wf_build,
         lambda wf: (shutil.copytree(os.path.join(install.WORKFLOWS, wf_plan), os.path.join(os.path.dirname(wf), wf_plan)),
                      _json_edit(os.path.join(wf, "workflow.json"), lambda d: d.update(replaces=[wf_plan]))), "still a workflow in this repository"),
        ("legacy_settings is an object", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d.update(legacy_settings=[])), "legacy_settings must be an object"),
        ("legacy_settings keys", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_settings"].update(model="x")), "may only contain"),
        ("legacy_settings.hooks are strings", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_settings"].update(hooks=[1])), "hooks must be a list"),
        ("legacy_settings.env is string to string", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_settings"].update(env={"K": 1})), "env must be an object"),
        ("legacy hook not still registered", wf_build, wf_build,
         lambda wf: _json_edit(os.path.join(wf, "workflow.json"), lambda d: d["legacy_settings"]["hooks"].append("python3 ~/.claude/hooks/wf-build-policy.py pre")), "still registers"),
        ("manifest freshness", wf_build, wf_build, lambda wf: open(os.path.join(wf, "README.md"), "a").write("x\n"), MANIFEST_NOISE),
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
    # The stale-name scan catches a planted old name, and honors its allowlist.
    with tempfile.TemporaryDirectory(prefix="ccw-stale-") as tmp:
        os.makedirs(os.path.join(tmp, "workflows", "x"))
        for rel, text in (("a.md", "fine\nsee " + "Double" + " Diamond here\n"), ("b.py", "run " + "vd" + ".py\n"),
                          ("docs/plans/rename-workflows/p.md", "double" + "-diamond\n")):
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "w", encoding="utf-8") as f:
                f.write(text)
        with open(os.path.join(tmp, "workflows", "x", "workflow.json"), "w", encoding="utf-8") as f:
            json.dump({"name": "x", "replaces": ["double" + "-diamond"], "summary": "ok"}, f, indent=2)
        hits = scan_stale_names(tmp)
        if not (len(hits) == 2 and hits[0].startswith("a.md:2:") and hits[1].startswith("b.py:1:")):
            bad.append(f"stale-name scan: expected hits at a.md:2 and b.py:1 only, got {hits}")
        # Hardening: undecodable text, old names in paths, uppercase short prefixes, nested .claude; root .claude/backups stay skipped.
        extra = (("latin1.txt", b"caf\xe9 \xff " + "verified".encode() + b"-" + "diamond".encode() + b"\n"),
                 ("nul.bin", b"\0" + "double".encode() + b"-" + "diamond".encode() + b"\n"),
                 ("notes-" + "double" + "-" + "diamond" + ".txt", b"clean\n"),
                 ("upper.md", ("DD" + "-scout\n").encode()),
                 ("sub/.claude/n.md", ("double" + "-" + "diamond\n").encode()),
                 ("backups/ok.md", ("double" + "-" + "diamond\n").encode()),
                 (".claude/ok.md", ("double" + "-" + "diamond\n").encode()))
        for rel, data in extra:
            os.makedirs(os.path.dirname(os.path.join(tmp, rel)), exist_ok=True)
            with open(os.path.join(tmp, rel), "wb") as f:
                f.write(data)
        hits = scan_stale_names(tmp)
        for want in ("latin1.txt:1:", "notes-", "upper.md:1:", "sub/.claude/n.md:1:"):
            if not any(h.startswith(want) for h in hits):
                bad.append(f"stale-name scan: expected a hit starting '{want}', got {hits}")
        if len(hits) != 6 or any(h.startswith(("nul.bin", "backups/", ".claude/")) for h in hits):
            bad.append(f"stale-name scan: expected exactly 6 hits (a real binary and root .claude/backups skipped), got {hits}")
    for b in bad:
        fail("self-test", b)
    print(f"checker self-test: {'ok' if not bad else 'FAILED'} ({len(cases) + 2} cases)")


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
        name = "wf-build"
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


def migration_check(names):
    """A fake old install of each replaced workflow is retired when the new workflows are installed."""
    new = {n: install.load_meta(n) for n in names if install.load_meta(n).get("replaces")}
    if not new:
        return
    sha = lambda b: hashlib.sha256(b).hexdigest()  # noqa: E731
    with tempfile.TemporaryDirectory(prefix="ccw-migrate-") as tmp:
        target = os.path.join(tmp, ".claude")
        os.makedirs(target)

        def put(rel, text):
            path = os.path.join(target, *rel.split("/"))
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                f.write(text)

        launcher = open(install.LAUNCHER_SRC, encoding="utf-8").read()
        claude_md = "# Mine\nkeep me\n"
        user_hook = {"matcher": "", "hooks": [{"type": "command", "command": "echo user-hook"}]}
        settings = {"model": "sonnet", "env": {"USER_KEY": "1"}, "hooks": {"PreToolUse": [user_hook]}}
        old_names, legacy_cmds, legacy_env = [], [], {}
        for meta in new.values():
            ls = meta.get("legacy_settings") or {}
            for i, cmd in enumerate(ls.get("hooks", [])):
                event = "Stop" if i == 0 else "PreToolUse"  # Stop holds only a legacy hook: must disappear entirely
                settings["hooks"].setdefault(event, []).append({"matcher": "", "hooks": [{"type": "command", "command": cmd}]})
                legacy_cmds.append(cmd)
            legacy_env.update(ls.get("env", {}))
            old_names += meta["replaces"]
        settings["env"].update(legacy_env)
        edited, retired = {}, {}
        for old in old_names:
            retired[old] = {f"agents/{old}-x.md": "agent\n", f"skills/{old}/SKILL.md": "skill\n", f"hooks/{old}-policy.py": "hook\n"}
            edited[old] = f"agents/{old}-edited.md"
            files = [{"path": rel, "sha256": sha(text.encode())} for rel, text in retired[old].items()]
            for rel, text in retired[old].items():
                put(rel, text)
            put(edited[old], "user edited this\n")
            files.append({"path": edited[old], "sha256": sha(b"original\n")})
            files.append({"path": install.LAUNCHER_REL, "sha256": sha(launcher.encode()), "shared": True})
            # Hostile record entries pointing outside the config dir (with the victim's real digest) must be ignored.
            files += [{"path": p, "sha256": sha(b"victim\n")} for p in
                      ("../outside/victim.txt", "x/..\\..\\outside\\victim.txt", "\\outside\\victim.txt", "x/../../outside/victim.txt",
                       os.path.join(tmp, "outside", "victim.txt").replace("\\", "/"))]
            put(install.LAUNCHER_REL, launcher)
            put(f"{old}/workflow.json", json.dumps({"name": old, "version": "1.0.0", "summary": "old", "usage": []}))
            put(f"{old}/MANIFEST.json", json.dumps({"name": old, "version": "1.0.0", "files": files}))
            put(f"{old}/state/keep.json", "{}\n")
            claude_md += f"\n<!-- BEGIN {old} -->\nold contract\n<!-- END {old} -->\n"
        put("CLAUDE.md", claude_md)
        put("settings.json", json.dumps(settings, indent=2) + "\n")
        victim = os.path.join(tmp, "outside", "victim.txt")
        os.makedirs(os.path.dirname(victim))
        with open(victim, "w", encoding="utf-8", newline="\n") as f:
            f.write("victim\n")

        def snapshot():
            return {os.path.relpath(os.path.join(b, f), target): sha(open(os.path.join(b, f), "rb").read())
                    for b, _, fs in os.walk(target) for f in fs}

        def run(*args):
            return subprocess.run([sys.executable, "-B", os.path.join(REPO, "install.py"), *names, "--target", target, *args],
                                  capture_output=True, text=True, encoding="utf-8")

        before = snapshot()
        conflict_target = os.path.join(tmp, "conflict", ".claude")
        shutil.copytree(target, conflict_target)
        p = run("--dry-run")
        if p.returncode != 0 or snapshot() != before:
            return fail("roundtrip", f"migration: --dry-run failed or changed files:\n{p.stdout}{p.stderr}")
        p = run()
        if p.returncode != 0:
            return fail("roundtrip", f"migration: install failed:\n{p.stdout}{p.stderr}")
        backups = [os.path.join(b, f) for b, _, fs in os.walk(os.path.join(target, "backups")) for f in fs]
        for old in old_names:
            for rel, text in retired[old].items():
                if os.path.exists(os.path.join(target, *rel.split("/"))):
                    fail("roundtrip", f"migration: old file {rel} was not retired")
                if not any(b.endswith(os.path.basename(rel)) and open(b, encoding="utf-8").read() == text for b in backups):
                    fail("roundtrip", f"migration: old file {rel} was not backed up")
            if open(os.path.join(target, *edited[old].split("/")), encoding="utf-8").read() != "user edited this\n":
                fail("roundtrip", f"migration: modified old file {edited[old]} was not kept")
            for rec in ("workflow.json", "MANIFEST.json"):
                if os.path.exists(os.path.join(target, old, rec)):
                    fail("roundtrip", f"migration: old record {old}/{rec} was not removed")
            if not os.path.isfile(os.path.join(target, old, "state", "keep.json")):
                fail("roundtrip", f"migration: {old}/state/keep.json was not kept")
            if f"kept: {os.path.join(target, old)}" not in p.stdout:
                fail("roundtrip", f"migration: install did not say it kept {old}/")
        md = open(os.path.join(target, "CLAUDE.md"), encoding="utf-8").read()
        if "keep me" not in md or any(f"BEGIN {old}" in md or "old contract" in md for old in old_names):
            fail("roundtrip", f"migration: CLAUDE.md not cleaned correctly:\n{md}")
        s = json.load(open(os.path.join(target, "settings.json"), encoding="utf-8-sig"))
        left = [h.get("command") for es in s.get("hooks", {}).values() for e in es for h in e.get("hooks", [])]
        if any(c in left for c in legacy_cmds) or any(k in s.get("env", {}) for k in legacy_env):
            fail("roundtrip", f"migration: legacy hooks or env remain in settings.json: {s}")
        if "echo user-hook" not in left or s.get("env", {}).get("USER_KEY") != "1" or s.get("model") != "sonnet":
            fail("roundtrip", f"migration: the user's settings were changed: {s}")
        if legacy_cmds and "Stop" in s.get("hooks", {}) and not any("echo" in c for c in left if c):
            fail("roundtrip", "migration: an empty hook event was not dropped")
        readme = open(os.path.join(target, "README.md"), encoding="utf-8").read()
        for n in new:
            if f"**{n}**" not in readme:
                fail("roundtrip", f"migration: README.md does not list {n}")
        for old in old_names:
            if old in readme:
                fail("roundtrip", f"migration: README.md still mentions {old}")
        if not os.path.isfile(os.path.join(target, *install.LAUNCHER_REL.split("/"))):
            fail("roundtrip", "migration: the shared launcher was removed")
        for n in names:
            for _, rel in install.install_map(n):
                if not os.path.isfile(os.path.join(target, *rel.split("/"))):
                    fail("roundtrip", f"migration: new file {rel} is missing")
        if not os.path.isfile(victim):
            fail("roundtrip", "migration: a record entry outside the config dir deleted a file")

        # (a) A second install right after the migration changes nothing.
        p = run()
        if p.returncode != 0 or p.stdout.count(" 0 added, 0 overwritten") != len(names):
            fail("roundtrip", f"migration: a second install was not a no-op:\n{p.stdout}{p.stderr}")

        # (b) Uninstalling the new workflows leaves no legacy hooks or env and keeps the user's settings.
        p = run("--uninstall")
        if p.returncode != 0:
            fail("roundtrip", f"migration: uninstall failed:\n{p.stdout}{p.stderr}")
        s = json.load(open(os.path.join(target, "settings.json"), encoding="utf-8-sig"))
        left = [h.get("command") for es in s.get("hooks", {}).values() for e in es for h in e.get("hooks", [])]
        if any(c in left for c in legacy_cmds) or any(k in s.get("env", {}) for k in legacy_env):
            fail("roundtrip", f"migration: legacy hooks or env came back after uninstall: {s}")
        if "echo user-hook" not in left or s.get("env", {}).get("USER_KEY") != "1" or s.get("model") != "sonnet":
            fail("roundtrip", f"migration: uninstall changed the user's settings: {s}")

        # (c) An old install plus a user-edited new-workflow file: install stops before writing anything.
        for n in new:
            rel = next(r for _, r in install.install_map(n))
            conflict_file = os.path.join(conflict_target, *rel.split("/"))
            os.makedirs(os.path.dirname(conflict_file), exist_ok=True)
            with open(conflict_file, "w", encoding="utf-8", newline="\n") as f:
                f.write("user edited a new-workflow file\n")

        def tree(root):
            return {os.path.relpath(os.path.join(b, f), root): sha(open(os.path.join(b, f), "rb").read())
                    for b, _, fs in os.walk(root) for f in fs}

        files_before = tree(conflict_target)
        p = subprocess.run([sys.executable, "-B", os.path.join(REPO, "install.py"), *new, "--target", conflict_target],
                           capture_output=True, text=True, encoding="utf-8")
        if "conflict:" not in p.stdout or tree(conflict_target) != files_before:
            fail("roundtrip", f"migration: a conflicting install wrote files or did not stop:\n{p.stdout}{p.stderr}")
        for old in old_names:
            for rec in ("workflow.json", "MANIFEST.json"):
                if not os.path.isfile(os.path.join(conflict_target, old, rec)):
                    fail("roundtrip", f"migration: a conflicting install removed the old record {old}/{rec}")

        # An unreadable old record never crashes the install and is kept, so a later install can retry.
        target2 = os.path.join(tmp, "unreadable", ".claude")
        old = old_names[0]
        for rec, text in (("workflow.json", "{}"), ("MANIFEST.json", '{"files": null}')):
            os.makedirs(os.path.join(target2, old), exist_ok=True)
            with open(os.path.join(target2, old, rec), "w", encoding="utf-8") as f:
                f.write(text)
        p = subprocess.run([sys.executable, "-B", os.path.join(REPO, "install.py"), *names, "--target", target2],
                           capture_output=True, text=True, encoding="utf-8")
        if p.returncode != 0 or not os.path.isfile(os.path.join(target2, old, "MANIFEST.json")):
            fail("roundtrip", f"migration: an unreadable old record crashed the install or was removed:\n{p.stdout}{p.stderr}")


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
    migration_check(names)
    print(f"  install/uninstall round trip: {'ok' if len(failures) == before else 'FAILED'}")


def main(argv):
    names = [a for a in argv if not a.startswith("--")] or install.available()
    self_test()
    check_tree()
    check_stale_names()
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
