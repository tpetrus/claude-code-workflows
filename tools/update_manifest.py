#!/usr/bin/env python3
"""Regenerate workflows/<name>/MANIFEST.json for every workflow (or the ones named).

    python3 tools/update_manifest.py                 all workflows
    python3 tools/update_manifest.py double-diamond  one workflow

The manifest lists every file install.py copies (target-relative path and SHA-256) plus the version from
workflow.json. install.py refuses a payload that does not match it, and uninstall uses it to remove only
unchanged files. Run it after every change to a workflow, after its tests pass.
"""
import json, os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import install  # noqa: E402


def main(names):
    for name in names or install.available():
        manifest = install.build_manifest(name)
        path = os.path.join(install.workflow_dir(name), "MANIFEST.json")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(json.dumps(manifest, indent=2) + "\n")
        print(f"{name}: MANIFEST.json lists {len(manifest['files'])} files, version {manifest['version']}")


if __name__ == "__main__":
    main(sys.argv[1:])
