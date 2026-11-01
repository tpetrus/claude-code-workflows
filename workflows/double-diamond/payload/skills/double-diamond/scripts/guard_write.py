#!/usr/bin/env python3
"""Claude Code PreToolUse hook for double-diamond agents: python3 "<skill-dir>/scripts/guard_write.py" <plan|brief|card|critic|bash>
Reads the hook payload on stdin and always exits 0 with a JSON permissionDecision on stdout: allow, deny or (for `export`) ask. A JSON decision is
used instead of exit code 2 because a PowerShell -Command wrapper rewrites a native exit 2 to 1, a non-blocking error. Fails closed.
  plan|brief|card|critic Write/Edit: only that seat's own file inside an existing run folder, also after links are resolved.
  bash                   Only `python3 "<dd_plan.py next to this guard>" <command> ...`, nothing chained; `export` answers ask.
Modes plan and bash are bound to the session that created the run (payload session_id vs run.session_key); worker modes are not.
The lead's hooks are session-wide (they come from the --agent frontmatter) and so also fire for subagent calls: the seat applied is the
caller's (payload agent_type), never the hook's own when they differ. Unknown subagents are denied; workers may not run Bash."""
import os, re, sys

sys.dont_write_bytecode = True  # keep the installed skill folder free of __pycache__
from plan_core import (GUARD_MODES, RUN_ID, allowed_write, bash_invocation, caller_seat, dumps, expand_home, get, js, match, parse_json, realpath_inside, rel_inside,
                       runs_root, session_matches)

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "dd_plan.py")
EXPORT_ASK, SESSION_DENY = "double-diamond guard: export writes into the repository; confirm with the user", "this run belongs to another session"
ALLOW = ("allow", "double-diamond guard: allowed")


def stored_key(folder):
    try:
        with open(os.path.join(folder, "plan.json"), "rb") as f: plan, _ = parse_json(f.read().decode("utf-8-sig", "replace"))  # tolerate a BOM from PowerShell pipes
    except OSError: return ""
    key = get(plan, "run", "session_key")
    return "" if key is None else js(key)


def run_argument(arg, root, cwd):
    """The run folder named by a bash command's first argument, when it sits inside the runs root."""
    if not isinstance(arg, str) or not arg or arg.startswith("--"): return None
    rel = rel_inside(root, os.path.abspath(os.path.join(cwd, arg)))
    first = re.split(r"[\\/]+", rel)[0] if rel else ""
    return os.path.join(root, first) if match(RUN_ID, first) else None


def decide(mode, raw):
    """(decision, reason) with decision allow, deny or ask."""
    if mode not in GUARD_MODES: return "deny", f'unknown mode "{js(mode)}"; expected {"/".join(GUARD_MODES)}'
    payload, error = parse_json(raw)
    if error: return "deny", "unreadable hook payload"
    # The hook's mode names the seat it was registered for; the caller's agent decides which seat's rules apply (see caller_seat).
    mode, why = caller_seat(mode, get(payload, "agent_id"), get(payload, "agent_type"))
    if not mode: return "deny", why
    session_id, cwd, root = get(payload, "session_id"), get(payload, "cwd"), runs_root()
    cwd = cwd if isinstance(cwd, str) and cwd else os.getcwd()
    if mode == "bash":
        command = get(payload, "tool_input", "command")
        if not isinstance(command, str) or not command: return "deny", "no tool_input.command in payload"
        call = bash_invocation(command, SCRIPT)
        if not call: return "deny", "the planner may only run the double-diamond plan script, as a single plain command"
        folder = run_argument(call[1][0] if call[1] else None, root, cwd)
        if folder and not session_matches(session_id, stored_key(folder)): return "deny", SESSION_DENY
        return ("ask", EXPORT_ASK) if call[0] == "export" else ALLOW
    file_path = get(payload, "tool_input", "file_path")
    if not isinstance(file_path, str) or not file_path: return "deny", "no tool_input.file_path in payload"
    # Only the planner seat is session-bound: whether a subagent's hook payload carries the parent session_id is unverified,
    # and binding the worker seats on a guess would block legitimate workers.
    other_session = False
    def exists(folder):
        nonlocal other_session
        if not os.path.exists(os.path.join(folder, "plan.json")): return False
        other_session = mode == "plan" and not session_matches(session_id, stored_key(folder))
        return not other_session
    if not allowed_write(file_path, mode, cwd, exists):
        return "deny", SESSION_DENY if other_session else f"{mode} seat may not write {file_path}; only its own artifact inside a run folder is allowed"
    if not realpath_inside(os.path.abspath(os.path.join(cwd, expand_home(file_path))), root): return "deny", f"{file_path} resolves outside the runs folder"
    return ALLOW


def main(argv):
    try: decision, reason = decide(argv[1] if len(argv) > 1 else None, sys.stdin.buffer.read().decode("utf-8-sig", "replace"))
    except Exception as e: decision, reason = "deny", f"guard error: {e}"
    if decision == "deny":
        reason = f"double-diamond guard: {reason}"
        sys.stderr.write(f"{reason}\n")
    sys.stdout.write(dumps({"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": decision, "permissionDecisionReason": reason}}) + "\n")


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr): stream.reconfigure(encoding="utf-8", newline="\n", errors="replace")
    main(sys.argv)
