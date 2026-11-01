#!/usr/bin/env python3
"""double-diamond plan CLI: run workspace, patch protocol, artifact collection, validation, rendering, export and the
verified-diamond hand-off. Standard library only. Every run lives in its own temp folder and is resumable only by the session that created it."""
import copy, os, re, shutil, stat, sys, time
from types import SimpleNamespace

sys.dont_write_bytecode = True  # keep the installed skill folder free of __pycache__
from plan_core import (CANARY_FILE, CANARY_TTL_MS, FEATURE_SLUG, FEATURE_SLUG_MAX, KIND, LANES, LAUNCH_SETTINGS_FILE, LAUNCH_TTL_MS, OPTION_ID, RISKS, RUN_ID, SWEEP_TTL_MS,
                       apply_patch, canary_path, collect_artifacts, compute_totals, derive_handoff, detect_project, dflt, dumps, get, js, locate_run,
                       lst, match, new_plan, new_run_id, now_iso, parse_json, realpath_inside, render_markdown, resolve_export_dir, runs_root, runs_root_problem,
                       session_key, stable_stringify, validate_plan, verify_disk, verify_handoff)

TEMPLATE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", "plan.template.json")
HARNESS, BOOLEAN_FLAGS, MAX_REJECTIONS, MAX_GATE_ERRORS = "claude-code", ["force", "help"], 12, 8
USAGE = """Usage: python3 dd_plan.py <command> [args]

  init <slug> [--title "..."] [--lane quick|full] [--risk R0-R4]
                                      Create a new run in the runs folder and print its paths as JSON
  apply <run>                         Merge <run>/patch.json into plan.json (patch.json is always deleted)
  collect <run>                       Merge the context brief, ideator cards and critic report
  validate <run>                      Run the completeness gate and the disk check (exit 1 on errors)
  show <run> [section]                Compact summary, or one top-level section as JSON
  render <run>                        Regenerate PLAN.md only
  finalize <run>                      Gate, then write the hand-off and PLAN.md; status=final
  revise <run>                        Archive a final plan and reopen it as the next draft revision
  export <run> --to <dir> [--force]   Write <slug>.plan.md (and .plan.json when final) into the repository
  verify <file.json>                  Consumer check: final, unchanged since finalize, gate passes
  discard <run>                       Remove the run folder
  canary <run>                        Check that the write guard blocked the canary file
  runs-root                           Print the runs folder
  help                                This text

  <run> is the run folder (or its plan.json) printed by init.
  Environment: DD_RUNS_ROOT overrides the runs folder (tests only)."""


class UsageError(Exception):
    def __init__(self, message, usage=False):
        super().__init__(message)
        self.usage = usage


def parse_args(argv):
    positional, flags, i = [], {}, 0
    while i < len(argv):
        a, nxt = argv[i], argv[i + 1] if i + 1 < len(argv) else None
        if not a.startswith("--"): positional.append(a)
        elif a[2:] in BOOLEAN_FLAGS or nxt is None or nxt.startswith("--"): flags[a[2:]] = True
        else: flags[a[2:]], i = nxt, i + 1
        i += 1
    return positional, flags


def read_bytes(file):
    with open(file, "rb") as f: return f.read()


def read_json(file):
    value, error = parse_json(read_bytes(file).decode("utf-8", "replace"))
    if error: raise ValueError(error)
    return value


def rm(path):
    """Removes a file, link or folder tree; a missing path is fine."""
    try:
        if os.path.isdir(path) and not os.path.islink(path): shutil.rmtree(path)
        elif os.path.isdir(path): os.rmdir(path)
        else: os.remove(path)
    except FileNotFoundError: pass


def write_atomic(file, text):
    """Unique temp name per write; one retry covers a transient Windows lock."""
    tmp = f"{file}.{os.getpid()}.{time.time_ns() // 10**6}.tmp"
    with os.fdopen(os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600), "wb") as f: f.write(text.encode("utf-8", "replace"))
    try:
        try: os.replace(tmp, file)
        except PermissionError:
            time.sleep(0.15)
            os.replace(tmp, file)
    except OSError:
        rm(tmp)
        raise


def write_json(file, value): write_atomic(file, dumps(value, 2) + "\n")


def assert_private_root(root):
    if (problem := runs_root_problem(root)): raise UsageError(problem)


def resolve_run(target):
    """The run must sit directly in the runs root, carry a generated run id and hold a plan; only the session that created it may use it."""
    if not target: raise UsageError("missing <run>")
    not_a_run = UsageError(f"not a double-diamond run in this session's runs folder: {target}")
    folder = locate_run(target)
    if not folder: raise not_a_run
    assert_private_root(os.path.dirname(folder))
    file = os.path.join(folder, "plan.json")
    try: plan = read_json(file)
    except Exception: raise not_a_run from None
    if get(plan, "kind") != KIND: raise not_a_run
    stored, current = dflt(get(plan, "run", "session_key"), ""), session_key()
    if stored and current and stored != current: raise UsageError("this run belongs to another session; start a new run with init")
    return SimpleNamespace(dir=folder, file=file, plan=plan, run_id=os.path.basename(folder))


def sweep(root):
    """Removes expired runs and stale canaries. Never follows links; touches nothing else."""
    try: names = os.listdir(root)
    except OSError: return
    for name in names:
        try:
            full = os.path.join(root, name)
            st = os.lstat(full)
            is_dir, mtime = stat.S_ISDIR(st.st_mode), st.st_mtime
            if is_dir:
                try: mtime = os.lstat(os.path.join(full, "plan.json")).st_mtime  # the plan's mtime, not the folder's
                except OSError: pass
            age_ms = (time.time() - mtime) * 1000
            if is_dir and match(RUN_ID, name) and age_ms > SWEEP_TTL_MS or stat.S_ISREG(st.st_mode) and (
                    match(CANARY_FILE, name) and age_ms > CANARY_TTL_MS or match(LAUNCH_SETTINGS_FILE, name) and age_ms > LAUNCH_TTL_MS): rm(full)
        except OSError: pass  # best effort


def read_artifacts(folder):
    def read_file(path):
        try: return read_bytes(path) if stat.S_ISREG(os.lstat(path).st_mode) else None
        except OSError: return None
    try:
        with os.scandir(os.path.join(folder, "cards")) as entries:
            cards = [(e.name, read_bytes(e.path)) for e in entries if e.is_file(follow_symlinks=False) and e.name.lower().endswith(".json")]
    except OSError: cards = []
    return read_file(os.path.join(folder, "context-brief.md")), read_file(os.path.join(folder, "critic.json")), cards


def gate(plan, folder):
    """Plan gate plus the disk check. Works on `plan` in place (totals), so pass a copy to stay read-only. -> (weights, errors, warnings)"""
    weights, weight_warnings = compute_totals(plan)
    errors, warnings = validate_plan(plan)
    brief, critic, cards = read_artifacts(folder)
    stems = {re.sub(r"\.json\Z", "", n, flags=re.I): b for n, b in cards}
    disk_errors, disk_warnings = verify_disk(plan, brief, critic, {i: b for i, b in stems.items() if match(OPTION_ID, i)})
    return weights, errors + disk_errors, weight_warnings + warnings + disk_warnings


def print_gate(errors, warnings):
    for e in errors: print(f"ERROR  {e}")
    for w in warnings: print(f"WARN   {w}")
    print(f"FAIL ({len(errors)} error(s), {len(warnings)} warning(s))" if errors else f"PASS ({len(warnings)} warning(s))")


def refuse(message):
    print(f"REFUSED {message}")
    return 1


def mentions(error, key): return re.search(rf"(^|[^\w.]){re.escape(key)}([^\w]|\Z)", error, re.A) is not None


# ---------------------------------------------------------------------------------------- commands
def cmd_init(args, flags):
    slug = args[0] if args else None
    if not slug or not match(FEATURE_SLUG, slug) or len(slug) > FEATURE_SLUG_MAX: raise UsageError(f"init needs a lowercase kebab-case <slug> of at most {FEATURE_SLUG_MAX} chars")
    lane, risk = flags.get("lane", "full"), flags.get("risk", "R1")
    if lane not in LANES: raise UsageError(f"--lane must be one of {'/'.join(LANES)}")
    if risk not in RISKS: raise UsageError(f"--risk must be one of {'/'.join(RISKS)}")
    root = runs_root()
    os.makedirs(root, mode=0o700, exist_ok=True)
    assert_private_root(root)
    sweep(root)
    run_id = new_run_id(slug)
    folder, p = os.path.join(root, run_id), lambda name: os.path.join(folder, name)
    os.mkdir(folder, 0o700)
    os.mkdir(p("cards"), 0o700)
    title = flags["title"] if isinstance(flags.get("title"), str) else ""
    write_json(p("plan.json"), new_plan(read_json(TEMPLATE), detect_project(), slug, run_id, title, lane, risk, HARNESS, session_key(), now_iso()))
    warnings = [] if os.environ.get("DD_LAUNCHER") == "1" else ["not started with the launcher (claude --workflow double-diamond): write guards and memory isolation are off"]
    vd = os.path.exists(os.path.join(os.path.expanduser("~"), ".claude", "skills", "verified-diamond", "SKILL.md"))
    print(dumps({"run": folder, "plan": p("plan.json"), "patch": p("patch.json"), "brief": p("context-brief.md"), "cards_dir": p("cards"), "critic": p("critic.json"),
                 "canary": canary_path(run_id), "verified_diamond_available": vd, "warnings": warnings}))


def cmd_apply(run, args, flags):
    patch_file = os.path.join(run.dir, "patch.json")
    try: raw = read_bytes(patch_file).decode("utf-8", "replace") if stat.S_ISREG(os.lstat(patch_file).st_mode) else None
    except OSError: raise UsageError(f"no patch.json in {run.dir}") from None
    rm(patch_file)
    def reject(problems):
        print("REJECTED")
        for p in problems[:MAX_REJECTIONS]: print(f"  - {p}")
        if len(problems) > MAX_REJECTIONS: print(f"  - ... {len(problems) - MAX_REJECTIONS} more")
        return 1
    if raw is None: return reject(["patch.json is not a regular file"])
    if run.plan.get("status") == "final": return reject(["plan is final: run revise first"])
    patch, error = parse_json(raw)
    if error: return reject([f"patch.json is not valid JSON: {error}"])
    try:
        plan, problems, touched = apply_patch(run.plan, patch, read_json(TEMPLATE))
        if not problems and stable_stringify(plan) != stable_stringify(run.plan):
            plan["updated"] = now_iso()
            write_json(run.file, plan)
    except Exception as e: return reject([f"unexpected error: {e}"])
    if problems: return reject(problems)
    count = len(compute_totals(plan)[1])
    errors, warnings = validate_plan(plan)
    count += len(warnings)
    print(f"APPLIED {','.join(touched)}")
    print(f"GATE FAIL ({len(errors)} errors, {count} warnings)" if errors else f"GATE PASS ({count} warnings)")
    shown = [e for e in errors if any(mentions(e, k) for k in touched)] or errors
    for e in shown[:MAX_GATE_ERRORS]: print(f"  - {e}")
    if len(shown) > MAX_GATE_ERRORS: print(f"  - ... {len(shown) - MAX_GATE_ERRORS} more (run validate)")


def cmd_collect(run, args, flags):
    if run.plan.get("status") == "final": return refuse("plan is final: run revise first")
    brief, critic, cards = read_artifacts(run.dir)
    plan, lines = collect_artifacts(run.plan, brief, cards, critic)
    if stable_stringify(plan) != stable_stringify(run.plan):
        plan["updated"] = now_iso()
        write_json(run.file, plan)
    print("\n".join(lines))


def cmd_validate(run, args, flags):
    _, errors, warnings = gate(copy.deepcopy(run.plan), run.dir)
    print_gate(errors, warnings)
    return 1 if errors else 0


def cmd_show(run, args, flags):
    if len(args) > 1 and args[1]:
        if args[1] not in run.plan: raise UsageError(f"unknown section: {args[1]}")
        return print(dumps(run.plan[args[1]]))
    plan = copy.deepcopy(run.plan)
    _, errors, warnings = gate(plan, run.dir)
    ev, f, critic = dflt(plan.get("evidence"), {}), plan["feature"], dflt(plan.get("critic"), {})
    unresolved = [x["id"] for x in lst(critic.get("findings")) if not str(dflt(x.get("resolution"), "")).strip()]
    options = "; ".join(f"{o['id']} {js(dflt(o.get('lens'), '?'))} {js(dflt(o.get('total'), 'unscored'))}{' (chosen)' if o['id'] == plan.get('chosen_option') else ''}" for o in lst(plan.get("options")))
    slices = "; ".join(f"{s['id']}{' after ' + '+'.join(s['depends_on']) if s.get('depends_on') else ''}" for s in lst(plan.get("slices")))
    print("\n".join([f"{f.get('title') or '(untitled)'} [{f['slug']}] r{plan['revision']} {plan['status']} | lane {plan['lane']} | risk {plan['risk']}",
                     f"ACs: {', '.join(a['id'] for a in lst(plan.get('acceptance_criteria'))) or 'none'}", f"options: {options or 'none'}",
                     f"dropped: {', '.join(lst(plan.get('dropped_options'))) or 'none'}", f"slices: {slices or 'none'}",
                     f"critic: {dflt(critic.get('passes'), 0)} pass(es), verdict {critic.get('verdict') or 'none'}, unresolved {', '.join(unresolved) or 'none'}",
                     f"evidence: brief {'yes' if ev.get('brief_sha256') else 'no'}, cards {', '.join(dflt(ev.get('cards'), {})) or 'none'}, critic {'yes' if ev.get('critic_sha256') else 'no'}",
                     f"gate: {'FAIL' if errors else 'PASS'} ({len(errors)} error(s), {len(warnings)} warning(s))"]))


def cmd_render(run, args, flags):
    plan = copy.deepcopy(run.plan)
    markdown = os.path.join(run.dir, "PLAN.md")
    write_atomic(markdown, render_markdown(plan, compute_totals(plan)[0]))
    print(markdown)


def cmd_finalize(run, args, flags):
    plan = run.plan
    if plan.get("status") == "final": return print(f"ALREADY FINAL r{plan['revision']}: use revise to change it")
    weights, errors, warnings = gate(plan, run.dir)
    if errors:
        print_gate(errors, warnings)
        return 1
    plan["handoff"], plan["status"], plan["updated"] = derive_handoff(plan), "final", now_iso()
    markdown = os.path.join(run.dir, "PLAN.md")
    write_json(run.file, plan)
    write_atomic(markdown, render_markdown(plan, weights))
    print_gate(errors, warnings)
    print(dumps({"plan": run.file, "markdown": markdown, "revision": plan["revision"]}))


def cmd_revise(run, args, flags):
    plan, history = run.plan, os.path.join(run.dir, "history")
    if plan.get("status") != "final": raise UsageError(f"plan is {js(plan.get('status'))}, not final: keep editing it")
    rev, previous = plan["revision"], plan["project"]["base_revision"]
    os.makedirs(history, exist_ok=True)
    for src, dst in (("plan.json", f"plan.r{rev}.json"), ("PLAN.md", f"PLAN.r{rev}.md"), ("critic.json", f"critic.r{rev}.json")):
        if os.path.exists(os.path.join(run.dir, src)): shutil.copyfile(os.path.join(run.dir, src), os.path.join(history, dst))
    plan["revision"], plan["status"], plan["handoff"], plan["updated"] = rev + 1, "draft", {}, now_iso()
    plan["project"]["base_revision"] = detect_project()["base_revision"]
    write_json(run.file, plan)
    if plan["project"]["base_revision"] != previous and get(plan, "evidence", "brief_sha256"):
        print("WARN   the context brief predates the current HEAD; re-run double-diamond-scout if the change matters")
    print(dumps({"plan": run.file, "revision": plan["revision"], "archived": history}))


def cmd_export(run, args, flags):
    root, to = detect_project()["root"], flags.get("to")
    if not isinstance(to, str) or not to.strip(): raise UsageError("--to <dir> is required")
    folder, rel, error = resolve_export_dir(to, root)
    # Symlinks must not let an export escape the repository: the nearest existing ancestor is checked.
    error = error or (None if realpath_inside(folder, root) else "--to must stay inside the repository")
    if error: return refuse(error)
    plan = copy.deepcopy(run.plan)
    weights, final, slug = compute_totals(plan)[0], run.plan.get("status") == "final", plan["feature"]["slug"]
    markdown = render_markdown(plan, weights)
    files = [(f"{slug}.plan.md", markdown if final else f"> **DRAFT**: not finalized. Do not hand off.\n\n{markdown}")]
    if final: files.append((f"{slug}.plan.json", dumps({**plan, "run": {**plan.get("run", {}), "session_key": ""}}, 2) + "\n"))
    relative = lambda name: f"{rel}/{name}" if rel else name
    existing = [n for n, _ in files if os.path.exists(os.path.join(folder, n))]
    if existing and not flags.get("force"):
        for n in existing: print(f"REFUSED {relative(n)} exists (re-run with --force only after the user confirms)")
        return 1
    os.makedirs(folder, exist_ok=True)
    for name, text in files:
        write_atomic(os.path.join(folder, name), text)
        print(f"EXPORTED {relative(name)}")


def cmd_verify(args, flags):
    if not args: raise UsageError("missing <file.json>")
    path = os.path.abspath(args[0])
    if not os.path.exists(path): raise UsageError(f"file not found: {args[0]}")
    try: plan = read_json(path)
    except (OSError, ValueError, RecursionError) as e: return print(f"ERROR  not valid JSON: {e}\nNOT VERIFIED") or 1
    problems = list(dict.fromkeys(verify_handoff(plan) + validate_plan(plan)[0]))
    if problems: return print("\n".join(f"ERROR  {p}" for p in problems) + "\nNOT VERIFIED") or 1
    print(f"VERIFIED {plan['feature']['slug']} r{plan['revision']} hash {plan['handoff']['verified_diamond']['plan_hash']}")


def cmd_discard(run, args, flags):
    rm(run.dir)
    rm(canary_path(run.run_id))
    print(f"DISCARDED {run.run_id}")


def cmd_canary(run, args, flags):
    file = canary_path(run.run_id)
    if not os.path.exists(file): return print("CANARY OK")
    rm(file)
    print("CANARY FAIL: the write guard is not active; stop and tell the user")
    return 1


RUN_COMMANDS = {n: globals()[f"cmd_{n}"] for n in "apply collect validate show render finalize revise export discard canary".split()}
PLAIN_COMMANDS = {"init": cmd_init, "verify": cmd_verify, "runs-root": lambda *_: print(runs_root())}


def main(argv):
    positional, flags = parse_args(argv)
    command, rest = (positional[0], positional[1:]) if positional else (None, [])
    try:
        if not command or command == "help" or flags.get("help"): print(USAGE)
        elif command in PLAIN_COMMANDS: return PLAIN_COMMANDS[command](rest, flags) or 0
        elif command in RUN_COMMANDS: return RUN_COMMANDS[command](resolve_run(rest[0] if rest else None), rest, flags) or 0
        else: raise UsageError(f"unknown command: {command}", usage=True)
    except Exception as e:
        print(f"{e}\n\n{USAGE}" if isinstance(e, UsageError) and e.usage else str(e) if isinstance(e, UsageError) else f"dd_plan: {e}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr): stream.reconfigure(encoding="utf-8", newline="\n")
    sys.exit(main(sys.argv[1:]))
