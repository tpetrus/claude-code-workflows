"""End-to-end smoke test for the double-diamond payload: python3 tools/smoke_test.py (from anywhere).
Standard library only; not installed. Runs the source payload with the current interpreter in a throwaway runs root
(DD_RUNS_ROOT) and a throwaway git repository, so nothing touches the real runs folder, ~/.claude or this repository.
Covers the write guard (allow, deny, ask, session binding), the patch protocol, collect, the gate, finalize, export,
verify, revise, canary and discard. Exit 0 when every check passes, 1 otherwise."""
import json, os, shutil, subprocess, sys, tempfile
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent / "payload" / "skills" / "double-diamond" / "scripts"
DD_PLAN, GUARD = SCRIPTS / "dd_plan.py", SCRIPTS / "guard_write.py"
SESSION, OTHER_SESSION = "smoke-session", "another-session"
failures, passed = [], 0


def check(name, condition, detail=""):
    global passed
    if condition: passed += 1
    else: failures.append(f"{name}{': ' + detail if detail else ''}")


def run_cli(env, cwd, *args):
    p = subprocess.run([sys.executable, "-B", str(DD_PLAN), *args], cwd=cwd, env=env, capture_output=True, encoding="utf-8")
    return p.returncode, p.stdout, p.stderr


def guard(env, mode, payload):
    raw = payload if isinstance(payload, str) else json.dumps(payload)
    p = subprocess.run([sys.executable, "-B", str(GUARD), mode], input=raw, env=env, capture_output=True, encoding="utf-8")
    try: return json.loads(p.stdout)["hookSpecificOutput"]["permissionDecision"]
    except (ValueError, KeyError, TypeError): return f"unparseable output: {p.stdout!r}"


def write(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(value if isinstance(value, str) else json.dumps(value, indent=2), encoding="utf-8", newline="\n")


def apply(env, repo, run, patch):
    write(Path(run) / "patch.json", patch)
    return run_cli(env, repo, "apply", run)


BRIEF = "\n".join(f"## {h}\nInference: smoke-test content for {h.lower()}.\n" for h in
                  ["Stack", "Relevant code", "Analogous feature", "Conventions", "Constraints and risks", "Commands", "Unknowns"])
CARD = {"summary": "Add the feature with a small module", "approach": ["Add the module", "Wire it in"], "components": [],
        "contracts": [], "pros": ["small"], "cons": ["less flexible"], "risks": [], "effort": "S", "covers_all_acs": True,
        "uncovered_acs": [], "confidence": "high"}
DEFINE = {
    "feature": {"request": "Add an export button to the report screen."},
    "problem": {"statement": "Users cannot export reports.", "users": ["report readers"]},
    "goals": ["Readers can export a report"], "non_goals": ["Scheduling exports"], "constraints": ["Reuse the existing file writer"],
    "acceptance_criteria": [{"id": "AC1", "given": "a report is open", "when": "the reader chooses export", "then": "a file is saved"}],
    "decisions": [{"id": "D1", "question": "Which format?", "answer": "CSV", "decided_by": "user", "rationale": "requested"}],
}
DELIVER = {
    "options": [{"id": "O1", "scores": {"fit": 5, "risk": 4, "effort": 5, "ux": 3, "reversibility": 5}},
                {"id": "O2", "scores": {"fit": 3, "risk": 4, "effort": 2, "ux": 4, "reversibility": 3}}],
    "chosen_option": "O1", "decision_rationale": "Top score and smallest change.",
    "design": {"overview": "A small export module behind the existing report view.", "rollout": "Ship behind a flag; roll back by turning it off.",
               "contracts": [{"id": "C1", "name": "ExportResult", "kind": "type", "definition": "path and byte count of the saved file"}]},
    "test_strategy": {"unit": "Module tests for the writer call."},
    "risks": [{"id": "K1", "description": "Large reports are slow", "likelihood": "low", "impact": "medium", "mitigation": "Stream rows"}],
    "slices": [{"id": "S1", "title": "Export module", "objective": "Save a report as CSV", "depends_on": [], "acceptance_refs": ["AC1"],
                "contracts": ["C1"], "risk": "R2", "size": "S", "draft_paths": ["src/export"]}],
}


def guard_checks(env, run, repo):
    script = str(DD_PLAN).replace("\\", "/")
    base = {"session_id": SESSION, "cwd": repo}
    def write_call(path): return {**base, "tool_input": {"file_path": str(path)}}
    def bash_call(command, session=SESSION): return {**base, "session_id": session, "tool_input": {"command": command}}
    r = Path(run)
    check("guard: planner may write patch.json", guard(env, "plan", write_call(r / "patch.json")) == "allow")
    check("guard: planner may not write plan.json", guard(env, "plan", write_call(r / "plan.json")) == "deny")
    check("guard: planner may not write the repository", guard(env, "plan", write_call(Path(repo) / "x.txt")) == "deny")
    check("guard: planner may not write a canary", guard(env, "plan", write_call(Path(env["DD_RUNS_ROOT"]) / "dd-canary-x.txt")) == "deny")
    check("guard: planner of another session is denied", guard(env, "plan", {**write_call(r / "patch.json"), "session_id": OTHER_SESSION}) == "deny")
    check("guard: scout may write the brief", guard(env, "brief", write_call(r / "context-brief.md")) == "allow")
    check("guard: scout may not write a card", guard(env, "brief", write_call(r / "cards" / "O1.json")) == "deny")
    check("guard: ideator may write its card", guard(env, "card", write_call(r / "cards" / "O1.json")) == "allow")
    check("guard: ideator may not write a non-option card", guard(env, "card", write_call(r / "cards" / "X1.json")) == "deny")
    check("guard: critic may write critic.json", guard(env, "critic", write_call(r / "critic.json")) == "allow")
    check("guard: path traversal is denied", guard(env, "critic", write_call(f"{run}/../{r.name}/critic.json/../../x")) == "deny")
    check("guard: unknown mode is denied", guard(env, "nope", write_call(r / "patch.json")) == "deny")
    check("guard: unreadable payload is denied", guard(env, "plan", "{not json") == "deny")
    check("guard: missing file_path is denied", guard(env, "plan", base) == "deny")
    check("guard: plain script call is allowed", guard(env, "bash", bash_call(f'python3 "{script}" show "{run}"')) == "allow")
    check("guard: unquoted script call is allowed", guard(env, "bash", bash_call(f"python3 {script} runs-root")) == "allow")
    check("guard: export asks", guard(env, "bash", bash_call(f'python3 "{script}" export "{run}" --to docs')) == "ask")
    for label, command in [("chained command", f'python3 "{script}" show "{run}"; whoami'), ("pipe", f'python3 "{script}" show "{run}" | more'),
                           ("dollar expansion", f'python3 "{script}" show "$HOME"'), ("non-ASCII", f'python3 "{script}" show “{run}”'),
                           ("other interpreter", f'node "{script}" show "{run}"'), ("other script", 'python3 "/tmp/evil.py" show x'),
                           ("unknown command", f'python3 "{script}" rm "{run}"'), ("newline", f'python3 "{script}" show "{run}"\nwhoami')]:
        check(f"guard: {label} is denied", guard(env, "bash", bash_call(command)) == "deny")
    check("guard: script call on another session's run is denied", guard(env, "bash", bash_call(f'python3 "{script}" show "{run}"', OTHER_SESSION)) == "deny")


def main():
    if not shutil.which("git"): print("smoke_test: git is required on PATH"); return 1
    tmp = Path(tempfile.mkdtemp(prefix="dd-smoke-"))
    try:
        runs, repo, home = tmp / "runs", tmp / "repo", tmp / "home"
        repo.mkdir(); home.mkdir()
        env = {**os.environ, "DD_RUNS_ROOT": str(runs), "CLAUDE_CODE_SESSION_ID": SESSION, "DD_LAUNCHER": "1", "HOME": str(home), "USERPROFILE": str(home),
               "PYTHONIOENCODING": "utf-8", "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1"}
        git = lambda *a: subprocess.run(["git", *a], cwd=repo, env=env, capture_output=True, check=True)
        git("init", "-q"); write(repo / "README.md", "smoke\n"); git("add", "."); git("-c", "user.name=smoke", "-c", "user.email=smoke@example.invalid", "commit", "-qm", "init")

        # init
        code, out, err = run_cli({k: v for k, v in env.items() if k != "DD_LAUNCHER"}, repo, "init", "no-launcher", "--title", "x")
        check("init: warns without the launcher", code == 0 and "not started with dd-claude" in out, out + err)
        code, out, err = run_cli(env, repo, "init", "Bad_Slug")
        check("init: rejects a bad slug", code == 2, out + err)
        code, out, err = run_cli(env, repo, "init", "export-button", "--title", "Export button", "--lane", "full", "--risk", "R2")
        info = json.loads(out) if code == 0 else {}
        check("init: prints run paths", code == 0 and {"run", "plan", "patch", "canary", "warnings"} <= set(info), out + err)
        if not info: raise SystemExit
        run = info["run"]
        check("init: no warning under the launcher", info["warnings"] == [], str(info["warnings"]))
        plan = json.loads(Path(info["plan"]).read_text(encoding="utf-8"))
        check("init: harness is claude-code", plan["run"]["harness"] == "claude-code")
        check("init: session key recorded", len(plan["run"]["session_key"]) == 16)
        check("init: project detected as git", plan["project"]["is_git"] is True and len(plan["project"]["base_revision"]) == 40)

        guard_checks(env, run, str(repo))

        # session binding
        code, out, err = run_cli({**env, "CLAUDE_CODE_SESSION_ID": OTHER_SESSION}, repo, "show", run)
        check("session: another session is refused", code == 2 and "another session" in err, out + err)
        code, out, err = run_cli(env, repo, "show", str(tmp))
        check("session: a non-run folder is refused", code == 2 and "not a double-diamond run" in err, out + err)

        # canary
        code, out, _ = run_cli(env, repo, "canary", run)
        check("canary: OK when the guard blocked the write", code == 0 and "CANARY OK" in out, out)
        write(info["canary"], "x\n")
        code, out, _ = run_cli(env, repo, "canary", run)
        check("canary: FAIL when the file exists", code == 1 and "CANARY FAIL" in out and not Path(info["canary"]).exists(), out)

        # patch protocol
        code, out, _ = apply(env, repo, run, {"status": "final", "project": {"name": "x"}})
        check("apply: script-owned keys are rejected", code == 1 and "REJECTED" in out and not Path(info["patch"]).exists(), out)
        code, out, _ = apply(env, repo, run, "{broken")
        check("apply: invalid JSON is rejected", code == 1 and "REJECTED" in out, out)
        code, out, _ = apply(env, repo, run, {"lane": "quick"})
        check("apply: lane downgrade is rejected", code == 1 and "downgraded" in out, out)
        code, out, _ = apply(env, repo, run, {"goals": "not a list"})
        check("apply: type mismatch is rejected", code == 1 and "expected array" in out, out)
        code, out, _ = apply(env, repo, run, DEFINE)
        check("apply: define patch applies", code == 0 and out.startswith("APPLIED") and "GATE FAIL" in out, out)

        # collect: brief, cards, bad card
        write(Path(run) / "context-brief.md", BRIEF)
        write(Path(run) / "cards" / "O1.json", {"id": "O1", "lens": "minimal", **CARD})
        write(Path(run) / "cards" / "O2.json", {"id": "O2", "lens": "robust", **CARD, "effort": "M"})
        write(Path(run) / "cards" / "O3.json", {"id": "O9", "lens": "robust"})
        code, out, _ = run_cli(env, repo, "collect", run)
        check("collect: brief ok", "brief: ok" in out, out)
        check("collect: cards merged", "card O1 merged" in out and "card O2 merged" in out, out)
        check("collect: bad card rejected", "card O3 rejected" in out, out)
        os.remove(Path(run) / "cards" / "O3.json")
        code, out, _ = apply(env, repo, run, {"options": [{"id": "O1", "summary": "rewritten"}]})
        check("apply: ideator option fields are protected", code == 1 and "may only change scores" in out, out)
        code, out, _ = apply(env, repo, run, DELIVER)
        check("apply: deliver patch applies", code == 0 and out.startswith("APPLIED"), out)

        # gate requires the critic, then a resolution for high findings
        code, out, _ = run_cli(env, repo, "finalize", run)
        check("finalize: blocked without the critic", code == 1 and "critic pre-mortem must run" in out, out)
        write(Path(run) / "critic.json", {"verdict": "sound with fixes", "findings": [
            {"id": "F1", "severity": "high", "area": "design", "finding": "No size limit on exports", "suggestion": "Cap rows"}]})
        code, out, _ = run_cli(env, repo, "collect", run)
        check("collect: critic merged", "critic: merged pass 1" in out, out)
        code, out, _ = run_cli(env, repo, "finalize", run)
        check("finalize: blocked by an unresolved high finding", code == 1 and "needs a resolution" in out, out)
        code, out, _ = apply(env, repo, run, {"critic": {"findings": [{"id": "F1", "resolution": "Rows are streamed; cap added to S1 objective."}]}})
        check("apply: finding resolution applies", code == 0 and "GATE PASS" in out, out)
        code, out, _ = apply(env, repo, run, {"design": {"data": "api_key = sk-abcdef1234567890ABCDEF1234"}})
        check("apply: credential-shaped value fails the gate", code == 0 and "credential" in out, out)
        code, out, _ = apply(env, repo, run, {"design": {"data": "TBD"}})
        check("apply: placeholder fails the gate", code == 0 and "placeholder" in out, out)
        apply(env, repo, run, {"design": {"data": None}})

        # evidence on disk must match what was collected
        write(Path(run) / "context-brief.md", BRIEF + "\nchanged\n")
        code, out, _ = run_cli(env, repo, "validate", run)
        check("validate: changed brief is an error", code == 1 and "changed since collect" in out, out)
        write(Path(run) / "context-brief.md", BRIEF)

        code, out, _ = run_cli(env, repo, "finalize", run)
        check("finalize: passes", code == 0 and "PASS" in out, out)
        plan = json.loads(Path(info["plan"]).read_text(encoding="utf-8"))
        handoff = plan.get("handoff", {}).get("verified_diamond", {})
        check("finalize: hand-off names dd_plan.py", handoff.get("generated_by") == "double-diamond dd_plan.py finalize", str(handoff.get("generated_by")))
        check("finalize: hand-off has one task", len(handoff.get("draft_dag", [])) == 1)
        code, out, _ = apply(env, repo, run, {"goals": ["more"]})
        check("apply: final plan rejects patches", code == 1 and "plan is final" in out, out)

        # export
        for label, target in [("absolute path", str(repo)), ("parent segment", "../out"), (".git segment", ".git/x"), ("device name", "docs/con")]:
            code, out, _ = run_cli(env, repo, "export", run, "--to", target)
            check(f"export: refuses {label}", code == 1 and "REFUSED" in out, out)
        code, out, err = run_cli(env, repo, "export", run)
        check("export: --to is required", code == 2, out + err)
        code, out, _ = run_cli(env, repo, "export", run, "--to", "docs/plans")
        exported = repo / "docs" / "plans" / "export-button.plan.json"
        check("export: writes md and json", code == 0 and exported.exists() and (repo / "docs" / "plans" / "export-button.plan.md").exists(), out)
        check("export: clears the session key", json.loads(exported.read_text(encoding="utf-8"))["run"]["session_key"] == "")
        code, out, _ = run_cli(env, repo, "export", run, "--to", "docs/plans")
        check("export: refuses to overwrite without --force", code == 1 and "exists" in out, out)
        code, out, _ = run_cli(env, repo, "export", run, "--to", "docs/plans", "--force")
        check("export: overwrites with --force", code == 0 and "EXPORTED" in out, out)

        # verify
        code, out, _ = run_cli(env, repo, "verify", str(exported))
        check("verify: exported plan verifies", code == 0 and out.startswith("VERIFIED export-button r1"), out)
        tampered = json.loads(exported.read_text(encoding="utf-8"))
        tampered["goals"].append("sneaked in")
        write(tmp / "tampered.json", tampered)
        code, out, _ = run_cli(env, repo, "verify", str(tmp / "tampered.json"))
        check("verify: edited plan is not verified", code == 1 and "NOT VERIFIED" in out, out)

        # revise, discard
        code, out, _ = run_cli(env, repo, "revise", run)
        check("revise: reopens as revision 2", code == 0 and json.loads(out.splitlines()[-1])["revision"] == 2 and (Path(run) / "history" / "plan.r1.json").exists(), out)
        code, out, _ = run_cli(env, repo, "show", run)
        check("show: summary prints", code == 0 and "r2 draft" in out, out)
        code, out, _ = run_cli(env, repo, "discard", run)
        check("discard: removes the run", code == 0 and not Path(run).exists(), out)
    except SystemExit: pass
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    for f in failures: print(f"FAIL  {f}")
    print(f"{passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
