#!/usr/bin/env bash
# Starts Claude Code as the double-diamond planner. The only supported Claude Code entry point.
# Usage: launch.sh [--high] [extra claude args]
#  Checks that python3, claude and the installed write guard work (the guard must block a test write),
#  creates the runs folder, writes a per-launch settings file (auto memory off, user CLAUDE.md and
#  rules folder hidden), then runs
#  `claude --agent double-diamond-orchestrator|double-diamond-orchestrator-high --add-dir <runs root> --settings <file> <extra args>`.
#  DD_LAUNCHER and CLAUDE_CODE_DISABLE_AUTO_MEMORY are set for the claude process only.
set -eu

fail() {
  printf 'double-diamond launcher: %s\n' "$*" >&2
  exit 1
}

json_escape() {
  printf '%s' "$1" | sed -e 's/\/\\/g' -e 's/"/\\"/g'
}

high=0
extra=()
for arg in "$@"; do
  if [ "$arg" = "--high" ]; then
    high=1
  else
    extra+=("$arg")
  fi
done

# 1. Tools
for tool in python3 claude; do
  command -v "$tool" >/dev/null 2>&1 || fail "'$tool' was not found on PATH. Install it and start a new shell."
done

# 2. Guard reachability: the hooks run python3 "$HOME/.claude/skills/double-diamond/scripts/guard_write.py".
home_dir="${HOME:-}"
[ -n "$home_dir" ] || fail 'HOME is not set, so the write guard cannot be located.'
guard="$home_dir/.claude/skills/double-diamond/scripts/guard_write.py"
[ -f "$guard" ] || fail "the write guard is missing: $guard. The hooks run it from \$HOME/.claude/skills/double-diamond/scripts. Install the Claude Code package and try again."
# The Bash guard accepts only printable ASCII without $ % ^ | ` or ", so the plan script path must qualify too.
plan_script="$(dirname "$guard")/dd_plan.py"
if printf '%s' "$plan_script" | LC_ALL=C grep -q '[^ -~]' || printf '%s' "$plan_script" | grep -q '[`$%^|"]'; then
  fail "the plan script path contains characters the Bash guard refuses (non-ASCII or one of \$ % ^ | \` \"): $plan_script. Use a plain-ASCII home folder."
fi

# 3. Guard self-test: the guard must deny a write outside the runs folder, or the Claude Code hooks would let writes through.
probe='{"session_id":"dd-selftest","tool_input":{"file_path":"/dd-selftest/outside.txt"}}'
verdict="$(printf '%s' "$probe" | python3 "$guard" plan 2>/dev/null)" || verdict=""
case "$verdict" in
  *'"permissionDecision":"deny"'*|*'"permissionDecision": "deny"'*) ;;
  *) fail 'the write guard did not run with python3, the Claude Code hooks would let writes through; on Windows `python3` may be the Microsoft Store stub (Settings > Apps > Advanced app settings > App execution aliases), so install a real python3 and try again' ;;
esac

# 4. Runs root
script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
runs_root="$(python3 "$script_dir/dd_plan.py" runs-root)" || fail "could not read the runs folder from $script_dir/dd_plan.py."
[ -n "$runs_root" ] || fail "could not read the runs folder from $script_dir/dd_plan.py."
mkdir -p "$runs_root"
chmod 700 "$runs_root" 2>/dev/null || true  # private on macOS/Linux; dd_plan.py refuses a non-private root there

# 5. Per-launch settings: no auto memory, user CLAUDE.md and user rules folder hidden (both slash styles, as Claude Code matches literally).
config_dir="${CLAUDE_CONFIG_DIR:-$home_dir/.claude}"
user_md="$config_dir/CLAUDE.md"
md_forward="${user_md//\\//}"
md_backward="${user_md//\//\\}"
user_rules="$config_dir/rules/**"
rules_forward="${user_rules//\\//}"
rules_backward="${user_rules//\//\\}"
settings="$runs_root/dd-launch-$$.settings.json"
printf '{"autoMemoryEnabled":false,"claudeMdExcludes":["%s","%s","%s","%s"]}\n' \
  "$(json_escape "$md_forward")" "$(json_escape "$md_backward")" "$(json_escape "$rules_forward")" "$(json_escape "$rules_backward")" > "$settings"

cleanup() {
  rm -f "$settings"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# 6. Run claude with the launcher variables set for the child only.
agent="double-diamond-orchestrator"
[ "$high" -eq 1 ] && agent="double-diamond-orchestrator-high"
status=0
DD_LAUNCHER=1 CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 \
  claude --agent "$agent" --add-dir "$runs_root" --settings "$settings" ${extra[@]+"${extra[@]}"} || status=$?
exit "$status"
