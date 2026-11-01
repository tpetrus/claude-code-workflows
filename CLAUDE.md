# claude-code-workflows

Installable Claude Code workflows. See README.md (including "The workflow format"). Python 3 standard library only.
Every workflow lives in `workflows/<name>/` and must pass `python3 tools/check.py`. After any change to a workflow,
run `python3 tools/update_manifest.py` before the checks.

verified_diamond:
  install: "n/a"
  format: "n/a"
  lint: "python3 -m compileall -q install.py launcher tools workflows"
  typecheck: "n/a"
  unit: "python3 tools/check.py"
  integration: "python3 tools/check.py --install-roundtrip"
  build: "n/a"
  security: "n/a"
  affected: "python3 tools/check.py"
  low_risk_merge_command: "n/a"
  protected_paths:
    - "install.py"
    - "launcher/**"
    - "workflows/*/payload/hooks/**"
    - "workflows/double-diamond/payload/skills/double-diamond/scripts/guard_write.py"
    - "workflows/*/MANIFEST.json"
