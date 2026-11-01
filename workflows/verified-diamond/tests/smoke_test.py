"""Smoke test for the verified-diamond payload: python3 tests/smoke_test.py (from anywhere).
Standard library only; not installed. Runs the source state CLI (vd.py) and policy hook with the current
interpreter, with HOME pointed at a throwaway folder and inside a throwaway git repository, so nothing touches
the real ~/.claude or this repository. Exit 0 when every check passes, 1 otherwise."""
import json, os, subprocess, sys, tempfile
from pathlib import Path

PAYLOAD = Path(__file__).resolve().parent.parent / "payload"
VD = PAYLOAD / "skills" / "verified-diamond" / "scripts" / "vd.py"
HOOK = PAYLOAD / "hooks" / "verified-diamond-policy.py"
ROUTING = PAYLOAD / "verified-diamond" / "routing.json"
failures, passed = [], 0


def check(name, condition, detail=""):
    global passed
    if condition: passed += 1
    else: failures.append(f"{name}{': ' + detail if detail else ''}")


def main():
    with tempfile.TemporaryDirectory(prefix="vd-smoke-") as tmp:
        home, repo = Path(tmp) / "home", Path(tmp) / "repo"
        home.mkdir(); repo.mkdir()
        env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home)}
        subprocess.run(["git", "init", "-q"], cwd=repo, env=env, check=True)

        def vd(*args):
            p = subprocess.run([sys.executable, "-B", str(VD), *args], cwd=repo, env=env, capture_output=True, encoding="utf-8")
            return p.returncode, p.stdout, p.stderr

        def hook(phase, event):
            p = subprocess.run([sys.executable, "-B", str(HOOK), phase], input=json.dumps(event), cwd=repo, env=env,
                               capture_output=True, encoding="utf-8")
            return p.returncode, p.stdout

        check("routing.json parses", isinstance(json.loads(ROUTING.read_text(encoding="utf-8")), dict))
        code, out, err = vd("init", "--title", "Smoke feature")
        check("init", code == 0, err)
        task = {"id": "T1", "role": "builder", "title": "a", "dependencies": [], "owned_paths": ["src/a.py"],
                "forbidden_paths": [], "acceptance": ["works"], "verify": ["true"]}
        check("add-task", vd("add-task", "--json", json.dumps(task))[0] == 0)
        dep = {**task, "id": "T2", "owned_paths": ["src/b.py"], "dependencies": ["T1"]}
        check("add dependent task", vd("add-task", "--json", json.dumps(dep))[0] == 0)
        clash = {**task, "id": "T3"}
        check("overlapping owned paths are refused", vd("add-task", "--json", json.dumps(clash))[0] != 0)
        code, out, _ = vd("ready")
        check("ready lists only dependency-free tasks", code == 0 and [t["id"] for t in json.loads(out)] == ["T1"], out)
        check("result", vd("result", "--id", "T1", "--status", "pass")[0] == 0)
        code, out, _ = vd("ready")
        check("dependent task becomes ready", code == 0 and [t["id"] for t in json.loads(out)] == ["T2"], out)
        code, out, _ = vd("status")
        check("status", code == 0 and json.loads(out)["title"] == "Smoke feature", out)
        packet = {"id": "F1", "objective": "x", "worker_class": "mechanical", "owned_paths": ["a"], "forbidden_paths": [],
                  "acceptance": ["y"], "verify": ["true"], "risk": "R0"}
        check("followup-add", vd("followup-add", "--json", json.dumps(packet))[0] == 0)
        check("R2 follow-up is refused", vd("followup-add", "--json", json.dumps({**packet, "id": "F2", "risk": "R2"}))[0] != 0)
        code, out, _ = vd("followup-ready")
        check("followup-ready", code == 0 and [t["id"] for t in json.loads(out)] == ["F1"], out)
        code, _, err = vd("followup-result", "--id", "F1", "--status", "pass", "--escalated", "--evidence", "ok")
        check("followup-result with --escalated", code == 0, err)
        code, out, _ = vd("followup-ready")
        check("resolved follow-up leaves the ready list", code == 0 and json.loads(out) == [], out)
        check("unknown follow-up is refused", vd("followup-result", "--id", "nope", "--status", "pass")[0] != 0)
        state = home / ".claude" / "verified-diamond" / "state"
        check("state is written under ~/.claude/verified-diamond/state", state.is_dir() and any(state.iterdir()))

        code, _ = hook("pre", {"tool_name": "Bash", "tool_input": {"command": "git status"}, "cwd": str(repo)})
        check("hook allows a harmless command", code == 0)
        code, out = hook("pre", {"tool_name": "Bash", "tool_input": {"command": "git reset " + "--hard"}, "cwd": str(repo)})
        check("hook blocks a destructive command", code == 2 and '"block"' in out, out)
        code, out = hook("pre", {"tool_name": "Read", "tool_input": {"file_path": "/home/u/.s" + "sh/id_rsa"}, "cwd": str(repo)})
        check("hook blocks a secret path", code == 2, out)
        check("hook post phase", hook("post", {"tool_name": "Bash", "cwd": str(repo)})[0] == 0)

    for f in failures:
        print(f"FAIL {f}")
    print(f"verified-diamond smoke test: {passed} passed, {len(failures)} failed")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
