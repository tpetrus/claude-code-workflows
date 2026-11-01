"""Pure logic for double-diamond plans. dd_plan.py (CLI) and guard_write.py (hook) own the process I/O."""
import copy, hashlib, json, math, os, re, secrets, stat, subprocess, sys, tempfile
from datetime import datetime, timezone
from functools import reduce

SCHEMA_VERSION, KIND, WIN = 2, "double-diamond-plan", sys.platform == "win32"
LANES, RISKS, STATUSES, SIZES = "quick full".split(), "R0 R1 R2 R3 R4".split(), "draft final superseded".split(), "S M L".split()
DECIDED_BY, SEVERITIES, LIKELIHOODS = ["user", "repo-fact", "planner-assumption"], "blocker high medium low".split(), "low medium high".split()
CRITERIA = "fit risk effort ux reversibility".split()
DEFAULT_WEIGHTS = {"fit": 0.3, "risk": 0.25, "effort": 0.2, "ux": 0.15, "reversibility": 0.1}  # scores 1-5, higher is always better
QUICK_LANE_MAX_RISK, FEATURE_SLUG_MAX, BRIEF_MAX_WORDS = "R2", 40, 600
SWEEP_TTL_MS, CANARY_TTL_MS, LAUNCH_TTL_MS = 72 * 3600 * 1000, 10 * 60 * 1000, 24 * 3600 * 1000
WS = "\t\n\v\f\r \xa0\u1680\u2028\u2029\u202f\u205f\u3000\ufeff" + "".join(map(chr, range(0x2000, 0x200B)))  # every Unicode space, so padded or odd whitespace never counts as content
FEATURE_SLUG = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*")
RUN_ID = re.compile(r"[a-z0-9]+(-[a-z0-9]+)*-\d{8}t\d{6}z-[0-9a-f]{8}", re.A)
OPTION_ID = re.compile(r"O\d{1,2}", re.A)
CANARY_FILE, LAUNCH_SETTINGS_FILE = re.compile(r"dd-canary-.+\.txt"), re.compile(r"dd-launch-\d+\.settings\.json", re.A)
RESERVED_DEVICE = re.compile(r"(con|prn|aux|nul|com[1-9]|lpt[1-9])", re.I | re.A)
FORBIDDEN_KEYS = ["__proto__", "constructor", "prototype"]  # prototype-style keys are never legitimate plan content; reject them early
BRIEF_HEADINGS = ["Stack", "Relevant code", "Analogous feature", "Conventions", "Constraints and risks", "Commands", "Unknowns"]
CLI_COMMANDS = "init apply collect validate show render finalize revise export verify discard canary runs-root help".split()
GUARD_MODES = "plan brief card critic bash".split()
LEAD_AGENTS = ("double-diamond-orchestrator", "double-diamond-orchestrator-high")
WORKER_SEATS = {"double-diamond-scout": "brief", "double-diamond-ideator": "card", "double-diamond-critic": "critic"}
VD_ESCALATION = ["contract ambiguity", "needs unowned path", "verification fails twice", "shared interface change", "high-risk issue discovered"]
ID_ARRAYS = "acceptance_criteria decisions options design.contracts risks slices open_questions critic.findings".split()
# What the lead may patch: True = the whole key, a list = only those child keys.
LEAD_FIELDS = {"feature": ["title", "request"], "critic": ["findings"], "run": ["question_rounds", "lane_changes"], **dict.fromkeys(
    "lane risk problem goals non_goals success_metrics constraints acceptance_criteria decisions scoring options chosen_option "
    "decision_rationale design test_strategy risks slices open_questions".split(), True)}
CARD_FIELDS = "lens summary approach components contracts pros cons risks effort covers_all_acs uncovered_acs confidence".split()
TEST_STRATEGY_KEYS = "unit integration e2e manual non_functional".split()
# Placeholders: TBD anywhere; TODO/FIXME only as a leading marker or "TODO:". Case-sensitive.
PLACEHOLDER = re.compile(rf"^[{WS}]*(TODO|FIXME)\b|\b(TODO|FIXME):|\bTBD\b|<fill[^>]*>|lorem ipsum", re.A)
# Credential *shapes* only, so prose such as "refresh token: rotated" passes: keys need a digit, generic assignments a mixed value.
SECRET = re.compile("|".join([
    "-----BEGIN [A-Z ]*PRIVATE KEY", rf"\bbearer[{WS}]+[A-Za-z0-9._~+/=-]{{24,}}", r"\b(sk|pk|rk)-(?=[A-Za-z0-9_-]*\d)[A-Za-z0-9_-]{20,}",
    r"\b(sk|pk|rk)_(live|test)_[A-Za-z0-9]{16,}", r"\bgh[pousr]_[A-Za-z0-9]{30,}", r"\bglpat-[A-Za-z0-9_-]{20,}", r"\bxox[abpr]-[A-Za-z0-9-]{10,}",
    r"\bAKIA[0-9A-Z]{16}\b", r"\btvly-[A-Za-z0-9_-]{20,}", r"\beyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.", rf"[a-z][a-z0-9+.-]*://[^/{WS}:@]+:[^@{WS}/]+@",
    rf"((api[_-]?key|secret|password|access[_-]?token|auth[_-]?token)[\"']?[{WS}]*[:=][{WS}]*[\"']?"
    r"(?=[A-Za-z0-9_/+=]*\d)(?=[A-Za-z0-9_/+=]*[A-Za-z])[A-Za-z0-9_/+=]{24,})"]), re.I | re.A)
SCANNED = ("problem goals non_goals success_metrics constraints acceptance_criteria decisions options decision_rationale design test_strategy "
           "risks slices open_questions critic").split()  # lead-authored sections plus critic output; the verbatim request may quote anything
UNHASHED = "handoff updated status run".split()  # bookkeeping that legitimately changes after finalize
U = object()  # "undefined": a key that is absent, as opposed to null


def filled(v): return isinstance(v, str) and v.strip(WS) != ""
def is_num(v): return isinstance(v, (int, float)) and not isinstance(v, bool)
def is_int(v): return is_num(v) and (isinstance(v, int) or v.is_integer())
def truthy(v): return v is not None and v is not False and v != 0 and v != ""
def rank(r): return RISKS.index(r) if r in RISKS else -1
def fold(p): return p.lower() if WIN else p
def dedupe(items): return list(dict.fromkeys(items))
def lst(v): return v if isinstance(v, list) else []
def obj(v): return v if isinstance(v, dict) else {}
def dflt(v, default): return default if v is None else v
def match(rx, v): return isinstance(v, str) and rx.fullmatch(v) is not None
def own(o, key): return o[key] if isinstance(o, dict) and key in o else U


def get(o, *keys):
    """Optional chaining: o?.a?.b, with None for anything missing or not an object."""
    for k in keys:
        try: o = o.get(k) if isinstance(o, dict) else None
        except TypeError: return None
    return o


def json_type(v):
    if v is None: return "null"
    return "array" if isinstance(v, list) else "object" if isinstance(v, dict) else "boolean" if isinstance(v, bool) else "number" if is_num(v) else "string"


def norm(v):
    """Integral floats become ints (1.0 -> 1), so serialization and hashes do not depend on how a number was parsed."""
    if isinstance(v, dict): return {k: norm(x) for k, x in v.items()}
    if isinstance(v, list): return [norm(x) for x in v]
    return int(v) if isinstance(v, float) and v.is_integer() and abs(v) < 1e21 else v


def js(v, none="undefined"):
    """Stable text for any JSON value (true/false, integral floats as ints, lists comma-joined); `none` is how a missing value prints.
    The exact output feeds rendered text and hashed hand-off strings, so keep it unchanged."""
    if isinstance(v, bool): return "true" if v else "false"
    if isinstance(v, list): return ",".join("" if x is None else js(x) for x in v)
    return none if v is None else "[object Object]" if isinstance(v, dict) else str(norm(v))


def dumps(v, indent=None): return json.dumps(norm(v), ensure_ascii=False, indent=indent, separators=None if indent else (",", ":"))
def stable_stringify(v): return json.dumps(norm(v), ensure_ascii=False, separators=(",", ":"), sort_keys=True)
def hash_value(v): return hashlib.sha256(stable_stringify(v).encode("utf-8", "replace")).hexdigest()[:16]
def file_hash(buf): return hashlib.sha256(buf).hexdigest()
def content_hash(plan): return hash_value({k: v for k, v in plan.items() if k not in UNHASHED})
def now_iso(): return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_json(text):
    """(value, None) or (None, message); NaN and Infinity are not JSON."""
    def bad(name): raise ValueError(name)
    try: return json.loads(text, parse_constant=bad), None
    except Exception as e: return None, str(e)


# ---------------------------------------------------------------------------------------- run workspace
def runs_root():
    env = os.environ.get("DD_RUNS_ROOT")
    return os.path.abspath(env) if env else os.path.join(tempfile.gettempdir(), "double-diamond")


def new_run_id(slug): return f"{slug}-{datetime.now(timezone.utc):%Y%m%dt%H%M%Sz}-{secrets.token_hex(4)}"
def key_of_session_id(i): return hashlib.sha256(str(i).encode("utf-8", "replace")).hexdigest()[:16]
def session_key(): return key_of_session_id(os.environ["CLAUDE_CODE_SESSION_ID"]) if os.environ.get("CLAUDE_CODE_SESSION_ID") else ""
def session_matches(session_id, stored): return not filled(session_id) or not filled(stored) or key_of_session_id(session_id) == stored  # binds only when both are set
def canary_path(run_id): return os.path.join(runs_root(), f"dd-canary-{run_id}.txt")


def runs_root_problem(root):
    """POSIX privacy of the runs root: an error message, or None when it is private (always None on Windows)."""
    problem = f"runs root is not private: {root}"
    if WIN: return None
    try: st = os.lstat(root)
    except OSError: return problem
    return problem if stat.S_ISLNK(st.st_mode) or st.st_uid != os.getuid() or st.st_mode & 0o077 else None


def rel_inside(root, target):
    """target relative to root ('' when equal), or None when it lies outside (or on another drive)."""
    try: rel = os.path.relpath(target, root)
    except ValueError: return None
    rel = "" if rel == "." else rel
    return None if rel.startswith("..") or os.path.isabs(rel) else rel


def realpath_inside(target, root):
    """True when the real location of the nearest existing ancestor of target is inside the real runs root."""
    try:
        os.stat(root)
        probe = os.path.abspath(target)
        while not os.path.exists(probe):
            if os.path.dirname(probe) == probe: return False
            probe = os.path.dirname(probe)
        return rel_inside(fold(os.path.realpath(root)), fold(os.path.realpath(probe))) is not None
    except (OSError, ValueError): return False


def locate_run(target):
    """The run folder for a folder or plan.json path directly inside the runs root, else None."""
    resolved = os.path.abspath(str(target or ""))
    folder = os.path.dirname(resolved) if os.path.basename(resolved) == "plan.json" else resolved
    return folder if fold(os.path.dirname(folder)) == fold(runs_root()) and match(RUN_ID, os.path.basename(folder)) else None


def detect_project(cwd=None):
    """Works in linked worktrees: --show-toplevel and HEAD are those of the worktree itself."""
    def git(*args): return subprocess.run(["git", *args], cwd=cwd, stdin=subprocess.DEVNULL, capture_output=True, encoding="utf-8", check=True).stdout.strip()
    root, is_git, base = cwd or os.getcwd(), False, ""
    try: root, is_git = os.path.abspath(git("rev-parse", "--show-toplevel")), True
    except (OSError, subprocess.CalledProcessError): pass
    try: base = git("rev-parse", "HEAD") if is_git else ""
    except (OSError, subprocess.CalledProcessError): pass  # empty repository
    return {"name": os.path.basename(root), "root": root, "is_git": is_git, "base_revision": base}


# ---------------------------------------------------------------------------------------- write guard decisions
def expand_home(file_path):
    r"""A leading ~/ or ~\ means the home folder; nothing else expands."""
    return os.path.join(os.path.expanduser("~"), file_path[2:]) if re.match(r"~[\\/]", file_path) else file_path


def caller_seat(mode, agent_id, agent_type):
    """The seat whose rules apply to this tool call: (seat, "") or (None, deny reason).
    The lead's hooks come from the frontmatter of the agent started with --agent, so Claude Code applies them session-wide and they
    also fire for every subagent tool call (the payload then carries agent_id and the subagent's agent_type). A worker must be judged
    by its own seat there, not the lead's, or the lead's deny overrides the worker's allow. Without agent fields (older Claude Code,
    the launcher self-test) the hook's own mode applies, which is the old behavior."""
    if mode not in GUARD_MODES: return None, f'unknown mode "{js(mode)}"; expected {"/".join(GUARD_MODES)}'
    kind = js(agent_type) if filled(agent_type) else ""
    if filled(agent_id) and kind not in WORKER_SEATS: return None, f'subagent "{kind}" is not a double-diamond worker'
    if kind in WORKER_SEATS: return (None, f"{kind} may not run shell commands") if mode == "bash" else (WORKER_SEATS[kind], "")
    if kind in LEAD_AGENTS: return ("bash" if mode == "bash" else "plan"), ""
    return mode, ""


def allowed_write(file_path, mode, cwd, run_exists):
    """True when file_path is a file the given seat may write. Fails closed on anything odd."""
    if mode not in ("plan", "brief", "card", "critic") or not filled(file_path): return False
    root = runs_root()
    rel = rel_inside(fold(root), fold(os.path.abspath(os.path.join(cwd, expand_home(file_path)))))
    if not rel: return False
    parts = re.split(r"[\\/]+" if WIN else "/+", rel)  # off Windows a backslash is an ordinary file-name character
    if not match(RUN_ID, parts[0]) or not run_exists(os.path.join(root, parts[0])): return False
    tail = parts[1:]
    if mode == "card": return len(tail) == 2 and tail[0] == "cards" and re.fullmatch(r"O\d{1,2}\.json", tail[1], re.I | re.A) is not None
    return tail == [{"plan": "patch.json", "brief": "context-brief.md", "critic": "critic.json"}[mode]]


def split_command(command):
    """Splits like a shell for plain words and quotes; None for anything that could chain, redirect, substitute or escape."""
    # Printable ASCII plus tab only: smart quotes, NBSP, U+2028 and control characters are read as quotes or separators by some
    # shells. % and ^ (cmd), ! (history), backtick and $ can expand or escape.
    if re.search(r"[^\x20-\x7e\t]|[`$%^!]", command): return None
    tokens, cur, quote = [], None, ""
    for i, c in enumerate(command):
        if c == "\\" and (command[i + 1] if i + 1 < len(command) else " ") in "\"' \t;&|<>()": return None
        if quote:
            if c == quote: quote = ""
            else: cur += c
        elif c in "\"'": quote, cur = c, dflt(cur, "")
        elif c in " \t":
            if cur is not None: tokens.append(cur)
            cur = None
        elif c in ";&|<>()": return None
        else: cur = (cur or "") + c
    if quote: return None
    return tokens + [cur] if cur is not None else tokens


def bash_invocation(command, script_path):
    """Parses `python3 "<script_path>" <cli command> [args]` (quoted or not), nothing chained: (name, args) or None."""
    if not filled(command) or not filled(script_path): return None
    tokens = split_command(command)
    if not tokens or len(tokens) < 3: return None
    def path_key(p): return fold(re.sub(r"^/([a-zA-Z])/", lambda m: m[1].upper() + ":/", p.replace("\\", "/")))
    ok = re.fullmatch(r"python3(\.exe)?", tokens[0], re.I | re.A) and path_key(tokens[1]) == path_key(script_path) and tokens[2] in CLI_COMMANDS
    return (tokens[2], tokens[3:]) if ok else None


# ---------------------------------------------------------------------------------------- plan creation, scoring
def new_plan(template, project, slug, run_id, title, lane, risk, harness, key, now):
    plan = {**copy.deepcopy(template), "run_id": run_id, "lane": lane, "risk": risk, "created": now, "updated": now}
    plan["feature"].update(slug=slug, title=title)
    plan["run"].update(harness=harness, session_key=key)
    plan["project"] = {"name": project["name"], "is_git": project["is_git"] is True, "base_revision": project["base_revision"]}
    return plan


def resolve_weights(plan):
    """Partial weights are completed from the defaults, then normalized. Zero drops a criterion."""
    given = get(plan, "scoring", "weights")
    if not isinstance(given, dict) or not given: return dict(DEFAULT_WEIGHTS), None
    valid = all(k in CRITERIA and is_num(v) and math.isfinite(v) and v >= 0 for k, v in given.items())
    merged = {**DEFAULT_WEIGHTS, **(given if valid else {})}
    total = reduce(lambda a, b: a + b, merged.values(), 0)
    if not valid or total <= 0: return dict(DEFAULT_WEIGHTS), f"scoring.weights ignored: keys must be {'/'.join(CRITERIA)} with non-negative numbers and a positive total"
    return {k: v / total for k, v in merged.items() if v > 0}, None


def compute_totals(plan):
    weights, warning = resolve_weights(plan)
    for option in (o for o in lst(plan.get("options")) if isinstance(o, dict)):
        scores = get(option, "scores")
        complete = isinstance(scores, dict) and all(is_int(scores.get(k)) and 1 <= scores[k] <= 5 for k in weights)
        total = math.floor(reduce(lambda t, kw: t + scores[kw[0]] * kw[1], weights.items(), 0) * 100 + 0.5) / 100 if complete else None  # round half up to 2 decimals
        option["total"] = int(total) if total is not None and total.is_integer() else total
    return weights, [warning] if warning else []


# ---------------------------------------------------------------------------------------- patch protocol
def strip_meta(item): return {k: v for k, v in item.items() if k not in ("id", "_delete")}


def forbidden_key_problems(value, at, problems):
    """Rejects prototype-style keys at any depth of untrusted JSON; `at` is the path of the containing value."""
    if isinstance(value, list):
        for i, v in enumerate(value): forbidden_key_problems(v, f"{at}[{i}]", problems)
    elif isinstance(value, dict):
        for key, child in value.items():
            if key in FORBIDDEN_KEYS: problems.append(f"forbidden key {key} at {at}")
            forbidden_key_problems(child, key if at == "patch" else f"{at}.{key}", problems)
    return problems


def merge_object(target, patch, tmpl, at, ctx):
    """RFC 7386 merge plus id-keyed arrays. `tmpl` is the template object at the same path (or U)."""
    problems = ctx["problems"]
    for key, value in patch.items():
        where = f"{at}.{key}" if at else key
        ref, cur = own(tmpl, key), own(target, key)
        if value is None:
            if where in ("options", "critic.findings"): problems.append(f"{where}: cannot be reset with null; change items by id")
            elif ref is not U: target[key] = copy.deepcopy(ref)
            else: target.pop(key, None)
        elif where in ID_ARRAYS: merge_id_array(target, key, value, where, ctx)
        elif isinstance(value, dict) and (cur is U or isinstance(cur, dict)):
            if ref is not U and not isinstance(ref, dict): problems.append(f"{where}: expected {json_type(ref)}, got object")
            else:
                if cur is U: target[key] = {}
                merge_object(target[key], value, ref, where, ctx)
        else:
            expected = json_type(ref if ref is not U else cur if cur is not U else value)
            if expected != json_type(value): problems.append(f"{where}: expected {expected}, got {json_type(value)}")
            else: target[key] = copy.deepcopy(value)


def merge_id_array(target, key, patch_list, where, ctx):
    if not isinstance(patch_list, list): return ctx["problems"].append(f"{where}: expected an array of items with string ids")
    items = lst(own(target, key))
    handler = {"options": merge_option, "critic.findings": merge_finding}.get(where, merge_item)
    for i, item in enumerate(patch_list):
        if not isinstance(item, dict) or not filled(item.get("id")): ctx["problems"].append(f"{where}[{i}]: every item needs a string id")
        else: handler(items, next((j for j, x in enumerate(items) if get(x, "id") == item["id"]), -1), item, f"{where}[{i}]", ctx)
    target[key] = items


def merge_item(items, index, item, label, ctx):
    if item.get("_delete") is True:
        if index < 0: ctx["problems"].append(f"{label}: cannot delete unknown id {item['id']}")
        else: del items[index]
    elif index >= 0: merge_object(items[index], strip_meta(item), U, label, ctx)
    else:
        items.append({"id": item["id"]})
        merge_object(items[-1], strip_meta(item), U, label, ctx)


def merge_finding(items, index, item, label, ctx):
    problems, extra = ctx["problems"], [k for k in item if k not in ("id", "resolution")]
    if extra: problems.append(f"{label}: only resolution may be patched on a critic finding (not {', '.join(extra)})")
    elif index < 0: problems.append(f"{label}: unknown critic finding {item['id']}; findings come from collect")
    elif not isinstance(item.get("resolution"), str): problems.append(f"{label}.resolution must be a string")
    else: items[index]["resolution"] = item["resolution"]


def merge_option(items, index, item, label, ctx):
    plan, problems, oid = ctx["plan"], ctx["problems"], item["id"]
    if not match(OPTION_ID, oid): return problems.append(f"{label}: option id must look like O1")
    existing = items[index] if index >= 0 else None
    ideator = get(existing, "source") == "ideator"
    forbidden = [k for k in item if k not in (["id", "scores", "_delete"] if ideator else ["id", "source", *CARD_FIELDS, "scores", "_delete"])]
    if forbidden:
        return problems.append(f"{label}: ideator option {oid} may only change scores (not {', '.join(forbidden)})" if ideator else f"{label}: {', '.join(forbidden)} cannot be patched on option {oid}")
    if "source" in item and item["source"] != "lead": return problems.append(f'{label}.source: only "lead" is allowed')
    if item.get("_delete") is True:
        if existing is None: return problems.append(f"{label}: cannot delete unknown id {oid}")
        del items[index]
        plan["dropped_options"] = dedupe(lst(plan.get("dropped_options")) + [oid])
        if plan.get("chosen_option") == oid: plan["chosen_option"] = ""
        if isinstance(get(plan, "evidence", "cards"), dict): plan["evidence"]["cards"].pop(oid, None)
        return None
    if item.get("scores") is not None and not isinstance(item["scores"], dict): return problems.append(f"{label}.scores: expected object, got {json_type(item['scores'])}")
    target = {"id": oid, "source": "lead"} if existing is None else existing
    merge_object(target, {k: v for k, v in strip_meta(item).items() if k != "source"}, U, label, ctx)
    if not isinstance(target.get("scores"), dict): target["scores"] = {}
    if existing is None:
        items.append(target)
        if isinstance(plan.get("dropped_options"), list): plan["dropped_options"] = [i for i in plan["dropped_options"] if i != oid]
    return None


def check_ownership(plan, patch):
    problems = []
    for key, value in patch.items():
        rule = own(LEAD_FIELDS, key)
        if rule is U: problems.append(f"{key} is script-owned or unknown and cannot be patched")
        elif isinstance(rule, list):
            if value is None: problems.append(f"{key} cannot be reset as a whole; patch {', '.join(rule)}")
            elif not isinstance(value, dict): problems.append(f"{key}: expected an object, got {json_type(value)}")
            else: problems += [f"{key}.{child} is script-owned and cannot be patched" for child in value if child not in rule]
    lane, risk = patch.get("lane"), patch.get("risk")
    if lane == "quick" and plan.get("lane") == "full": problems.append("lane cannot be downgraded from full to quick")
    if lane is not None and lane not in LANES: problems.append(f"lane must be one of {'/'.join(LANES)}")
    if risk is not None and risk not in RISKS: problems.append(f"risk must be one of {'/'.join(RISKS)}")
    return problems


def apply_patch(plan, patch, template):
    """Pure: (plan, patch, template) -> (plan, problems, touched). The plan is a new object, or the input when rejected."""
    if get(plan, "status") == "final": return plan, ["plan is final: run revise first"], []
    if not isinstance(patch, dict): return plan, ["patch.json must be a JSON object"], []
    touched = list(patch)
    try:
        problems = forbidden_key_problems(patch, "patch", []) or check_ownership(plan, patch)
        if problems: return plan, problems, touched
        nxt = copy.deepcopy(plan)
        merge_object(nxt, patch, template, "", {"plan": nxt, "problems": problems})
        if problems: return plan, problems, touched
        compute_totals(nxt)
        return nxt, [], touched
    except Exception as e: return plan, [f"unexpected error while applying the patch: {e}"], touched


# ---------------------------------------------------------------------------------------- gate
def find_cycle(slices):
    deps, state = {s["id"]: lst(s.get("depends_on")) for s in slices}, {}
    def visit(i, trail):
        if not isinstance(i, str) or i not in deps or state.get(i) == 2: return None
        if state.get(i) == 1: return trail[trail.index(i):] + [i]
        state[i] = 1
        for d in deps[i]:
            cycle = visit(d, trail + [i])
            if cycle: return cycle
        state[i] = 2
        return None
    return next(filter(None, (visit(i, []) for i in deps)), None)


def walk_strings(value, at, visit):
    if isinstance(value, str): visit(value, at)
    elif isinstance(value, list):
        for i, v in enumerate(value): walk_strings(v, f"{at}[{i}]", visit)
    elif isinstance(value, dict):
        for k, v in value.items(): walk_strings(v, f"{at}.{k}", visit)


def unique_ids(items, label, errors):
    seen = []
    for i, item in enumerate(items):
        id_ = get(item, "id")
        if not filled(id_): errors.append(f"{label}[{i}].id is required")
        elif id_ in seen: errors.append(f"{label}: duplicate id {id_}")
        else: seen.append(id_)
    return seen


def string_list(box, key, label, errors, required=False):
    """Optional list fields must be arrays of non-empty strings when present."""
    value = box.get(key, U) if isinstance(box, dict) else U
    if value is U and not required: return []
    if not isinstance(value, list): return errors.append(f"{label} must be an array") or []
    errors.extend(f"{label}[{i}] must be a non-empty string" for i, v in enumerate(value) if not filled(v))
    return value


def critic_basis(plan):
    """What a critic pass was based on: recorded by collect, compared by the gate."""
    return {"chosen_option": dflt(plan.get("chosen_option"), ""), "risk": dflt(plan.get("risk"), ""),
            "contracts_sha256": hash_value(dflt(get(plan, "design", "contracts"), [])), "acs_sha256": hash_value(dflt(plan.get("acceptance_criteria"), []))}


def validate_plan(plan):
    """Deterministic completeness gate. Run compute_totals(plan) first so option totals are current. -> (errors, warnings)"""
    errors, warnings = [], []
    if not isinstance(plan, dict): return ["plan is not a JSON object"], warnings
    def need(cond, msg):
        if not cond: errors.append(msg)
    g = lambda *k: get(plan, *k)
    slist = lambda box, key, label, required=False: string_list(box, key, label, errors, required)
    lane, risk, evidence = plan.get("lane"), plan.get("risk"), obj(plan.get("evidence"))
    need(plan.get("schema_version") == SCHEMA_VERSION, f"schema_version must be {SCHEMA_VERSION}")
    need(plan.get("kind") == KIND, f"kind must be {KIND}")
    need(lane in LANES, f"lane must be one of {'/'.join(LANES)}")
    need(risk in RISKS, f"risk must be one of {'/'.join(RISKS)}")
    need(plan.get("status") in STATUSES, f"status must be one of {'/'.join(STATUSES)}")
    need(is_int(plan.get("revision")) and plan["revision"] >= 1, "revision must be an integer >= 1")
    if lane == "quick" and rank(risk) > rank(QUICK_LANE_MAX_RISK):
        errors.append(f"quick lane is limited to {QUICK_LANE_MAX_RISK}: upgrade to the full lane and record it in run.lane_changes")
    need(match(FEATURE_SLUG, js(dflt(g("feature", "slug"), ""))), "feature.slug must be lowercase kebab-case")
    need(filled(g("feature", "title")), "feature.title is required")
    need(filled(g("problem", "statement")), "problem.statement is required")
    need(len(slist(plan.get("problem"), "users", "problem.users", True)) > 0, "problem.users needs at least one user/persona")
    slist(plan.get("problem"), "evidence", "problem.evidence")
    need(len(slist(plan, "goals", "goals", True)) > 0, "goals needs at least one goal")
    non_goals = slist(plan, "non_goals", "non_goals")
    if lane == "full": need(len(non_goals) > 0, "full lane: non_goals needs at least one entry (scope control)")
    slist(plan, "success_metrics", "success_metrics")
    slist(plan, "constraints", "constraints")
    acs = lst(plan.get("acceptance_criteria"))
    need(len(acs) > 0, "acceptance_criteria needs at least one criterion")
    ac_ids = unique_ids(acs, "acceptance_criteria", errors)
    for i, ac in enumerate(acs):
        for k in ("given", "when", "then"): need(filled(get(ac, k)), f"acceptance_criteria[{i}].{k} is required")
    decisions = lst(plan.get("decisions"))
    unique_ids(decisions, "decisions", errors)
    for i, d in enumerate(decisions):
        need(filled(get(d, "question")) and filled(get(d, "answer")), f"decisions[{i}] needs question and answer")
        need(get(d, "decided_by") in DECIDED_BY, f"decisions[{i}].decided_by must be one of {'/'.join(DECIDED_BY)}")
    options, card_evidence = lst(plan.get("options")), obj(evidence.get("cards"))
    need(len(options) >= 2, "options needs at least two alternatives (forces a real comparison)")
    option_ids = unique_ids(options, "options", errors)
    if lane == "full": need(len({o["lens"] for o in options if filled(get(o, "lens"))}) >= 2, "full lane: options must come from at least two distinct lenses")
    for i, o in enumerate(options):
        need(filled(get(o, "summary")), f"options[{i}].summary is required")
        need(len(slist(o, "approach", f"options[{i}].approach", True)) > 0, f"options[{i}].approach needs at least one step")
        need(isinstance(get(o, "covers_all_acs"), bool), f"options[{i}].covers_all_acs must be true or false")
        uncovered = slist(o, "uncovered_acs", f"options[{i}].uncovered_acs")
        if get(o, "covers_all_acs") is True and uncovered: errors.append(f"options[{i}] claims covers_all_acs but lists uncovered_acs")
        need(is_num(get(o, "total")), f"options[{i}].scores must rate {'/'.join(CRITERIA)} as integers 1-5")
        if get(o, "source") == "ideator":
            need(filled(get(card_evidence, o.get("id"))), f"options[{i}] ({js(o.get('id'))}) is an ideator option without card evidence: run collect")
    chosen = next((o for o in options if get(o, "id") == plan.get("chosen_option")), None)
    need(filled(plan.get("chosen_option")) and plan["chosen_option"] in option_ids, "chosen_option must reference an option id")
    need(filled(plan.get("decision_rationale")), "decision_rationale is required")
    if chosen is not None:
        need(chosen.get("covers_all_acs") is True, "chosen option must cover every acceptance criterion")
        best = max(o["total"] if is_num(get(o, "total")) else -math.inf for o in options)
        if is_num(chosen.get("total")) and chosen["total"] < best:
            warnings.append(f"chosen option {js(chosen.get('id'))} ({js(chosen['total'])}) is not the top score ({js(best)}); decision_rationale must justify the override")
    need(filled(g("design", "overview")), "design.overview is required")
    contracts = lst(g("design", "contracts"))
    contract_ids = unique_ids(contracts, "design.contracts", errors)
    for i, c in enumerate(contracts): need(filled(get(c, "name")) and filled(get(c, "definition")), f"design.contracts[{i}] needs name and definition")
    need(any(filled(g("test_strategy", k)) for k in TEST_STRATEGY_KEYS), f"test_strategy needs at least one of {'/'.join(TEST_STRATEGY_KEYS)}")
    risks = lst(plan.get("risks"))
    unique_ids(risks, "risks", errors)
    for i, r in enumerate(risks):
        need(filled(get(r, "description")) and filled(get(r, "mitigation")), f"risks[{i}] needs description and mitigation")
        need(get(r, "likelihood") in LIKELIHOODS and get(r, "impact") in LIKELIHOODS, f"risks[{i}] likelihood/impact must be low/medium/high")
    slices = lst(plan.get("slices"))
    need(len(slices) > 0, "slices needs at least one delivery slice")
    slice_ids, covered, used_contracts = unique_ids(slices, "slices", errors), [], []
    for i, s in enumerate(slices):
        need(filled(get(s, "title")) and filled(get(s, "objective")), f"slices[{i}] needs title and objective")
        need(get(s, "risk") in RISKS, f"slices[{i}].risk must be R0-R4")
        need(get(s, "size") in SIZES, f"slices[{i}].size must be S/M/L")
        if get(s, "risk") in RISKS and risk in RISKS and rank(s["risk"]) > rank(risk): errors.append(f"slices[{i}].risk {s['risk']} exceeds plan risk {risk}; raise the plan risk")
        for d in slist(s, "depends_on", f"slices[{i}].depends_on"): need(d in slice_ids and d != get(s, "id"), f"slices[{i}].depends_on references unknown or self slice {js(d, 'null')}")
        refs = slist(s, "acceptance_refs", f"slices[{i}].acceptance_refs", True)
        need(len(refs) > 0, f"slices[{i}].acceptance_refs must trace to at least one acceptance criterion")
        for r in refs: need(r in ac_ids, f"slices[{i}] references unknown acceptance criterion {js(r, 'null')}")
        for c in slist(s, "contracts", f"slices[{i}].contracts"): need(c in contract_ids, f"slices[{i}] references unknown contract {js(c, 'null')}")
        covered += refs
        used_contracts += lst(get(s, "contracts"))
        for p in slist(s, "draft_paths", f"slices[{i}].draft_paths"):
            if isinstance(p, str) and (p.startswith("/") or (WIN and p[:1] == "\\") or re.match(r"[a-z]:", p, re.I) or ".." in re.split(r"[\\/]", p)):
                warnings.append(f'slices[{i}].draft_paths "{p}" should be repository-relative without ".."')
    cycle = find_cycle([s for s in slices if filled(get(s, "id"))])
    if cycle: errors.append(f"slices dependency cycle: {' -> '.join(cycle)}")
    errors += [f"acceptance criterion {i} is not delivered by any slice" for i in ac_ids if i not in covered]
    warnings += [f"contract {i} is not used by any slice" for i in contract_ids if i not in used_contracts]
    questions = lst(plan.get("open_questions"))
    unique_ids(questions, "open_questions", errors)
    for i, q in enumerate(questions):
        need(filled(get(q, "question")), f"open_questions[{i}].question is required")
        if get(q, "blocking") is True: errors.append(f"open question {js(dflt(q.get('id'), i))} is blocking: resolve it or mark it non-blocking with an owner")
    if rank(risk) >= 2:
        need(len(risks) > 0, "R2+: risks needs at least one entry")
        need(filled(g("design", "rollout")), "R2+: design.rollout (rollout and rollback) is required")
    critic_ran = filled(evidence.get("critic_sha256"))
    if lane == "full" or rank(risk) >= 2: need(critic_ran, "critic pre-mortem must run before finalize (run double-diamond-critic, then collect)")
    if critic_ran:
        basis, current = obj(evidence.get("critic_basis")), critic_basis(plan)
        if basis.get("chosen_option") != current["chosen_option"] or basis.get("risk") != current["risk"]:
            errors.append("critic is stale: the chosen option or risk changed since it ran; re-run double-diamond-critic")
        if basis.get("contracts_sha256") != current["contracts_sha256"] or basis.get("acs_sha256") != current["acs_sha256"]:
            warnings.append("contracts or acceptance criteria changed after the critic ran; re-run double-diamond-critic only if a blocker fix changed them")
    for i, f in enumerate(lst(g("critic", "findings"))):
        name = f"{js(dflt(get(f, 'id'), i))} ({js(get(f, 'severity'))})"
        if get(f, "severity") in ("blocker", "high") and not filled(f.get("resolution")): errors.append(f"critic finding {name} needs a resolution")
        elif not filled(get(f, "resolution")): warnings.append(f"critic finding {name} has no resolution")
    if g("critic", "verdict") == "rethink": warnings.append("critic verdict was 'rethink': confirm the resolutions address the approach, not only individual findings")
    if lane == "full" and g("project", "is_git") is True and not filled(evidence.get("brief_sha256")): errors.append("full lane in a repository: run double-diamond-scout, then collect")
    assumptions = [d for d in decisions if get(d, "decided_by") == "planner-assumption"]
    if assumptions: warnings.append(f"{len(assumptions)} decision(s) are planner assumptions: confirm them with the user")
    for key in SCANNED:
        def scan(s, at):
            if key != "critic" and PLACEHOLDER.search(s): errors.append(f'{at} contains a placeholder (TBD, a leading TODO/FIXME, "TODO:", <fill>)')
            if SECRET.search(s): errors.append(f"{at} looks like it contains a credential: remove it")
        walk_strings(plan.get(key), key, scan)
    if SECRET.search(dumps(dflt(plan.get("feature"), {}))): errors.append("feature contains something that looks like a credential: remove it")
    return errors, warnings


# ---------------------------------------------------------------------------------------- hand-off
def derive_handoff(plan):
    """Maps slices onto verified-diamond's task-template.json fields. Contract entries carry their full definition."""
    ac_text = {get(a, "id"): f"{js(get(a, 'id'))}: Given {js(get(a, 'given'))}, when {js(get(a, 'when'))}, then {js(get(a, 'then'))}" for a in dflt(plan.get("acceptance_criteria"), [])}
    contracts = {get(c, "id"): c for c in dflt(get(plan, "design", "contracts"), [])}
    slices, base = dflt(plan.get("slices"), []), dflt(get(plan, "project", "base_revision"), "")
    def contract_text(i):
        c = contracts.get(i)
        return f"{i} {js(get(c, 'name'))}{' (' + js(get(c, 'kind')) + ')' if truthy(get(c, 'kind')) else ''}: {js(get(c, 'definition'))}" if c is not None else i
    def task(s):
        g = lambda k: dflt(get(s, k), [])
        return {"id": get(s, "id"), "objective": f"{js(get(s, 'title'))}: {js(get(s, 'objective'))}", "dependencies": list(g("depends_on")), "base_revision": base,
                "worker_class": "builder", "owned_paths": list(g("draft_paths")), "forbidden_paths": [], "contracts": [contract_text(c) for c in g("contracts")],
                "acceptance": [ac_text.get(r, r) for r in g("acceptance_refs")], "verify": [], "risk": get(s, "risk"), "retry_limit": 1, "escalation": list(VD_ESCALATION)}
    freeze = dedupe(i for s in slices for i in dflt(get(s, "contracts"), []))
    return {"verified_diamond": {
        # These strings are part of the hand-off contract: verify compares them byte for byte, so changing one invalidates exported plans.
        "generated_by": "double-diamond dd_plan.py finalize", "plan_revision": plan.get("revision"), "plan_hash": content_hash(plan), "base_revision": base, "risk": plan.get("risk"),
        "freeze_first": [{"id": i, "name": get(contracts.get(i), "name") or i, "kind": get(contracts.get(i), "kind") or "", "definition": get(contracts.get(i), "definition") or ""} for i in freeze],
        "draft_dag": [task(s) for s in slices],
        "notes": ["Run `python3 dd_plan.py verify <plan.json>` first: it proves the plan is final and unchanged since finalize.",
                  "verify[] is intentionally empty: fill it from the repository verified_diamond command contract, never from this plan.",
                  "owned_paths are planning drafts: re-verify against the current tree before delegating.",
                  "verified-diamond risk classification is authoritative; this plan's risk is an input."]}}


def verify_handoff(plan):
    """Consumer-side check: final, unchanged since finalize. Pair with validate_plan for the gate."""
    problems, stored = [], get(plan, "handoff", "verified_diamond", "plan_hash")
    if get(plan, "status") != "final": problems.append(f'status is "{js(get(plan, "status"))}", not "final": finalize the plan first')
    if not filled(stored): problems.append("no handoff.verified_diamond.plan_hash: finalize the plan first")
    elif stored != content_hash(plan): problems.append("plan.json changed after finalize: run finalize again (or revise) before handing off")
    else:
        try: same = stable_stringify(plan["handoff"]["verified_diamond"]) == stable_stringify(derive_handoff(plan)["verified_diamond"])
        except Exception: same = False
        if not same: problems.append("handoff block changed after finalize: run finalize again (or revise) before handing off")
    return problems


# ---------------------------------------------------------------------------------------- worker artifacts
def check_card(card):
    if not isinstance(card, dict): return ["card is not a JSON object"]
    checks = [(filled(card.get("id")), "id is required"), (filled(card.get("lens")), "lens is required"), (filled(card.get("summary")), "summary is required"),
              (lst(card.get("approach")) != [], "approach needs at least one step"), (isinstance(card.get("covers_all_acs"), bool), "covers_all_acs must be true or false"),
              ("effort" not in card or card["effort"] in SIZES, "effort must be S/M/L")]
    return [msg for ok, msg in checks if not ok]


def check_critic(report):
    if isinstance(report, dict) and filled(report.get("invalid")): return [f"invalid JSON: {report['invalid']}"]
    if not isinstance(report, dict) or not isinstance(report.get("findings"), list): return ["critic report needs a findings array"]
    problems = []
    for i, f in enumerate(report["findings"]):
        if not filled(get(f, "id")): problems.append(f"findings[{i}].id is required")
        if get(f, "severity") not in SEVERITIES: problems.append(f"findings[{i}].severity must be {'/'.join(SEVERITIES)}")
        if not filled(get(f, "finding")): problems.append(f"findings[{i}].finding is required")
    return problems


def clip(s):
    one = re.sub(f"[{WS}]+", " ", str(s)).strip(WS)
    return one[:297] + "..." if len(one) > 300 else one


def jl(v, sep="; "): return sep.join("" if x is None else js(x) for x in lst(v))


def merge_brief(plan, brief):
    if brief is None: return "brief: absent"
    text = brief.decode("utf-8", "replace")
    missing = [h for h in BRIEF_HEADINGS if not re.search(f"^##[{WS}]+{re.escape(h)}", text, re.I | re.M | re.A)]
    plan["evidence"]["brief_sha256"] = "" if missing else file_hash(brief)
    if missing: return f"brief: rejected (missing: {', '.join(missing)})"
    return f"brief: ok (over {BRIEF_MAX_WORDS} words)" if len([w for w in re.split(f"[{WS}]+", text) if w]) > BRIEF_MAX_WORDS else "brief: ok"


def merge_cards(plan, files):
    """files is [(file, bytes)]; one digest line per file."""
    lines, dropped = [], lst(plan.get("dropped_options"))
    for file, buf in sorted(files, key=lambda f: (f[0].lower(), f[0].swapcase())):
        stem = re.sub(r"\.json\Z", "", file, flags=re.I)
        def reject(problems): lines.append(clip(f"card {stem} rejected: {'; '.join(problems)}"))
        if not file.endswith(".json") or not match(OPTION_ID, stem): reject(["file name must be O<n>.json"]); continue
        if stem in dropped: lines.append(f"card {stem} skipped (dropped)"); continue
        card, error = parse_json(buf.decode("utf-8", "replace"))
        if error: reject([f"invalid JSON: {error}"]); continue
        problems = check_card(card)
        if isinstance(card, dict) and filled(card.get("id")) and card["id"] != stem: problems.append(f"id {card['id']} must equal the file name {stem}")
        existing = next((o for o in plan["options"] if get(o, "id") == stem), None)
        if existing is not None and existing.get("source") != "ideator": problems.append(f"id {stem} belongs to a lead-written option; give the card a new id")
        if problems: reject(problems); continue
        digest = file_hash(buf)
        if plan["evidence"]["cards"].get(stem) == digest: lines.append(f"card {stem} unchanged"); continue
        merged = {"id": stem, "source": "ideator", **{k: card[k] for k in CARD_FIELDS if k in card}, "scores": dflt(get(existing, "scores"), {})}
        if existing is not None: plan["options"][plan["options"].index(existing)] = merged
        else: plan["options"].append(merged)
        plan["evidence"]["cards"][stem] = digest
        pros, cons = (jl(card.get(k)) if lst(card.get(k)) else "-" for k in ("pros", "cons"))
        lines.append(clip(f"card {stem} merged: {js(card['lens'])} | {js(card['summary'])} | effort {js(dflt(card.get('effort'), '-'))} | covers_all_acs "
                          f"{js(card['covers_all_acs'])} | confidence {js(dflt(card.get('confidence'), '-'))} | pros: {pros} | cons: {cons}"))
    return lines


def merge_critic(plan, buf):
    if buf is None: return []
    report, error = parse_json(buf.decode("utf-8", "replace"))
    problems = check_critic({"invalid": error} if error else report)
    if problems: return [clip(f"critic: rejected ({'; '.join(problems)})")]
    digest = file_hash(buf)
    if plan["evidence"].get("critic_sha256") == digest: return ["critic: unchanged"]
    previous = {get(f, "id"): f for f in lst(get(plan, "critic", "findings"))}
    def finding(f):
        prior = previous.get(f["id"])
        same = prior is not None and prior.get("finding") == f.get("finding") and prior.get("severity") == f["severity"]
        return {"id": f["id"], "severity": f["severity"], "area": dflt(f.get("area"), ""), "finding": f["finding"],
                "suggestion": dflt(f.get("suggestion"), ""), "resolution": dflt(prior.get("resolution"), "") if same else ""}
    plan["critic"] = c = {"passes": dflt(get(plan, "critic", "passes"), 0) + 1, "verdict": report["verdict"] if filled(report.get("verdict")) else "",
                          "findings": [finding(f) for f in report["findings"]]}
    plan["evidence"]["critic_sha256"], plan["evidence"]["critic_basis"] = digest, critic_basis(plan)
    return [clip(f"critic: merged pass {c['passes']}, verdict {c['verdict'] or '-'}"),
            *[clip(f"critic {f['id']} {f['severity']} {js(f['area']) if truthy(f['area']) else '-'}: {f['finding']}{' -> ' + js(f['suggestion']) if truthy(f['suggestion']) else ''}") for f in c["findings"]]]


def collect_artifacts(plan, brief=None, cards=(), critic=None):
    """Merges worker artifacts into a copy of the plan. Idempotent; dropped options are never resurrected, lead options never overwritten."""
    nxt = copy.deepcopy(plan)
    nxt["options"], nxt["dropped_options"], nxt["evidence"] = lst(nxt.get("options")), lst(nxt.get("dropped_options")), obj(nxt.get("evidence"))
    nxt["evidence"]["cards"] = obj(nxt["evidence"].get("cards"))
    lines = [merge_brief(nxt, brief), *merge_cards(nxt, cards), *merge_critic(nxt, critic)]
    compute_totals(nxt)
    return nxt, lines


def verify_disk(plan, brief=None, critic=None, cards=None):
    """Recorded evidence must still match the files (buffers or None; cards maps id -> buffer). -> (errors, warnings)"""
    errors, warnings, cards = [], [], cards or {}
    evidence = obj(get(plan, "evidence"))
    recorded = obj(evidence.get("cards"))
    def check(name, expected, buf):
        if filled(expected):
            if buf is None or file_hash(buf) != expected: errors.append(f"{name} changed since collect: run collect")
        elif buf is not None: warnings.append(f"{name} exists but was never collected: run collect")
    check("context-brief.md", evidence.get("brief_sha256"), brief)
    check("critic.json", evidence.get("critic_sha256"), critic)
    for i in dedupe([*recorded, *cards]):
        if i not in recorded and i in lst(get(plan, "dropped_options")): continue
        check(f"cards/{i}.json", recorded.get(i), cards.get(i))
    return errors, warnings


# ---------------------------------------------------------------------------------------- export path rules
def resolve_export_dir(to, root):
    """`to` must be a relative folder without .., :, .git or reserved device names that stays inside root: (dir, rel, error)."""
    def fail(msg): return None, None, msg
    if not filled(to): return fail("--to <dir> is required")
    if os.path.isabs(to) or re.match(r"[a-zA-Z]:|[\\/]", to): return fail("--to must be a relative path inside the repository")
    if ":" in to: return fail("--to must not contain ':' (drive letters and alternate data streams)")
    segments = re.split(r"[\\/]+", to)
    if ".." in segments: return fail("--to must not contain .. segments")
    if ".git" in [re.sub(r"[. ]+\Z", "", s).lower() for s in segments]: return fail("--to must not contain a .git segment")  # Windows ignores trailing dots and spaces
    reserved = next((s for s in segments if match(RESERVED_DEVICE, s.split(".")[0].rstrip(WS))), None)
    if reserved is not None: return fail(f"--to must not contain the Windows reserved device name {reserved}")
    folder = os.path.abspath(os.path.join(root, to))
    rel = rel_inside(root, folder)
    return fail("--to must stay inside the repository") if rel is None else (folder, rel.replace(os.sep, "/"), None)


# ---------------------------------------------------------------------------------------- rendering
def cell(v): return re.sub(r"\r?\n", " ", js("" if v is None else v).replace("|", "\\|")).strip(WS)
def bullets(items): return "\n".join(f"- {js(x)}" for x in items) if lst(items) else "_None recorded._"
def section(title, body): return f"## {title}\n\n{body}\n" if filled(body) else ""
def join(*parts): return "\n\n".join(js(x) for x in parts if truthy(x))


def table(head, rows):
    if not rows: return "_None recorded._"
    return "\n".join([f"| {' | '.join(head)} |", f"| {' | '.join('---' for _ in head)} |", *[f"| {' | '.join(map(cell, r))} |" for r in rows]])


def render_markdown(plan, weights=None):
    """Deterministic human view of plan.json: the lead never writes prose twice."""
    f, p, d, t, critic = (obj(plan.get(k)) for k in ("feature", "problem", "design", "test_strategy", "critic"))
    options, base, handoff = lst(plan.get("options")), get(plan, "project", "base_revision"), get(plan, "handoff", "verified_diamond")
    chosen = next((o for o in options if get(o, "id") == plan.get("chosen_option")), None)
    rows = lambda key, *cols: [[get(x, c) for c in cols] for x in lst(plan.get(key))]
    named = lambda pairs, fmt: [fmt(k, v) for k, v in pairs if filled(v)]
    out = [f"# {js(f.get('title') if truthy(f.get('title')) else f.get('slug'))}", "",
           f"> Double-diamond plan `{js(f.get('slug'))}` revision {js(plan.get('revision'))} | status **{js(plan.get('status'))}** | lane {js(plan.get('lane'))} | "
           f"risk {js(plan.get('risk'))} | base {js(base)[:12] if truthy(base) else 'n/a'} | updated {js(plan.get('updated'))}", "",
           "_Generated from plan.json by dd_plan.py. Edit plan.json, not this file._", "",
           section("Request", "\n".join(f"> {l}" for l in re.split(r"\r?\n", js(f["request"]))) if truthy(f.get("request")) else ""),
           section("Problem", join(p.get("statement"), f"**Users:** {jl(p['users'])}" if lst(p.get("users")) else "", f"**Evidence:**\n{bullets(p['evidence'])}" if lst(p.get("evidence")) else "")),
           section("Goals", bullets(plan.get("goals"))), section("Non-goals", bullets(plan.get("non_goals"))),
           section("Success metrics", bullets(plan.get("success_metrics"))), section("Constraints", bullets(plan.get("constraints"))),
           section("Acceptance criteria", table(["ID", "Given", "When", "Then"], rows("acceptance_criteria", "id", "given", "when", "then"))),
           section("Decisions", table(["ID", "Question", "Answer", "Decided by", "Rationale"],
                                      [[a, b, c, "planner assumption (confirm)" if e == "planner-assumption" else e, r] for a, b, c, e, r in rows("decisions", "id", "question", "answer", "decided_by", "rationale")])),
           section("Options considered", join(
               f"Weights: {', '.join(f'{k} {math.floor(w * 100 + 0.5)}%' for k, w in (weights or DEFAULT_WEIGHTS).items())}. Scores 1-5, higher is better.",
               table(["ID", "Lens", "Summary", *CRITERIA, "Covers all ACs", "Total"],
                     [[f"**{js(get(o, 'id'))}** (chosen)" if get(o, "id") == plan.get("chosen_option") else get(o, "id"), get(o, "lens"), get(o, "summary"),
                       *[get(o, "scores", k) for k in CRITERIA], "yes" if truthy(get(o, "covers_all_acs")) else "no", get(o, "total")] for o in options]),
               f"Dropped options: {jl(plan['dropped_options'], ', ')}." if lst(plan.get("dropped_options")) else "")),
           section("Decision", join(f"**{js(chosen['id'])}**: {js(chosen.get('summary'))}" if chosen is not None else "", plan.get("decision_rationale"),
                                    f"**Approach:**\n{bullets(chosen['approach'])}" if lst(get(chosen, "approach")) else "")),
           section("Design", join(
               d.get("overview"),
               f"### Components\n{table(['Name', 'Responsibility', 'Location'], [[get(c, 'name'), get(c, 'responsibility'), get(c, 'location')] for c in d['components']])}" if lst(d.get("components")) else "",
               "### Contracts (freeze before dependent work)\n\n" + "\n".join(f"- **{js(get(c, 'id'))} {js(get(c, 'name'))}** ({js(get(c, 'kind')) if truthy(get(c, 'kind')) else 'contract'}): {js(get(c, 'definition'))}" for c in d["contracts"]) if lst(d.get("contracts")) else "",
               *named([("Data", d.get("data")), ("UX", d.get("ux")), ("Accessibility", d.get("accessibility")), ("Security and privacy", d.get("security_privacy")),
                       ("Observability", d.get("observability")), ("Rollout and rollback", d.get("rollout"))], lambda k, v: f"### {k}\n\n{v}"))),
           section("Test strategy", "\n".join(named([("Unit", t.get("unit")), ("Integration", t.get("integration")), ("End-to-end", t.get("e2e")), ("Manual", t.get("manual")),
                                                     ("Non-functional", t.get("non_functional"))], lambda k, v: f"- **{k}:** {v}"))),
           section("Risks", table(["ID", "Risk", "Likelihood", "Impact", "Mitigation"], rows("risks", "id", "description", "likelihood", "impact", "mitigation"))),
           section("Delivery slices", table(["ID", "Slice", "Depends on", "ACs", "Contracts", "Risk", "Size", "Draft paths"],
                                            [[get(s, "id"), f"{js(get(s, 'title'))}: {js(get(s, 'objective'))}", jl(get(s, "depends_on"), ", "), jl(get(s, "acceptance_refs"), ", "),
                                              jl(get(s, "contracts"), ", "), get(s, "risk"), get(s, "size"), jl(get(s, "draft_paths"), ", ")] for s in lst(plan.get("slices"))])),
           section("Open questions", table(["ID", "Question", "Owner", "Blocking"], [[a, b, c, "yes" if truthy(e) else "no"] for a, b, c, e in rows("open_questions", "id", "question", "owner", "blocking")])),
           section("Critic pre-mortem", join(
               f"Passes: {js(critic.get('passes'))}. {'Verdict: ' + js(critic['verdict']) if truthy(critic.get('verdict')) else ''}".strip(WS),
               table(["ID", "Severity", "Finding", "Resolution"], [[get(x, "id"), get(x, "severity"), get(x, "finding"), get(x, "resolution")] for x in lst(critic.get("findings"))])
               if dflt(critic.get("passes"), 0) > 0 or filled(get(plan, "evidence", "critic_sha256")) else "Not run (quick lane below R2 and deterministic validation covers completeness).")),
           section("Hand-off", f"A verified-diamond draft DAG with {len(lst(handoff['draft_dag']))} task(s) and {len(lst(handoff['freeze_first']))} contract(s) to freeze first "
                               f"is embedded in plan.json under `handoff.verified_diamond` (plan hash `{js(handoff.get('plan_hash'))}`)." if isinstance(handoff, dict) else "")]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(x for x in out if x != "")).strip(WS) + "\n"
