"""Regenerates MANIFEST.json from payload/: python3 tools/update_manifest.py (from anywhere).
Keeps kind, name and harness, sets version and generated, and lists every payload file sorted by path with its SHA-256."""
import hashlib, json
from datetime import datetime, timezone
from pathlib import Path

VERSION = "2.0.0"
root = Path(__file__).resolve().parent.parent
payload, manifest_path = root / "payload", root / "MANIFEST.json"
previous = json.loads(manifest_path.read_text(encoding="utf-8"))
files = sorted(({"path": p.relative_to(payload).as_posix(), "sha256": hashlib.sha256(p.read_bytes()).hexdigest()}
                for p in payload.rglob("*") if p.is_file() and "__pycache__" not in p.parts), key=lambda f: f["path"])
manifest = {"kind": previous["kind"], "name": previous["name"], "harness": previous["harness"], "version": VERSION,
            "generated": datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z"), "files": files}
with open(manifest_path, "w", encoding="utf-8", newline="\n") as f: f.write(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
print(f"MANIFEST.json: {len(files)} files, version {VERSION}")
