# claude-code-workflows

Installable Claude Code workflows. See README.md. Python 3 standard library only.

verified_diamond:
  install: "n/a"
  format: "n/a"
  lint: "python3 -m compileall -q install.py workflows"
  typecheck: "n/a"
  unit: "python3 workflows/double-diamond/tools/smoke_test.py"
  integration: "python3 install.py verified-diamond --dry-run --target .tmp-vd-check"
  build: "n/a"
  security: "n/a"
  affected: "python3 workflows/double-diamond/tools/smoke_test.py"
  low_risk_merge_command: "n/a"
  protected_paths:
    - "install.py"
    - "workflows/*/payload/hooks/**"
    - "workflows/double-diamond/payload/skills/double-diamond/scripts/guard_write.py"
    - "workflows/double-diamond/MANIFEST.json"
