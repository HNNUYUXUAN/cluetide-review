"""Verify current release bytes recorded in source-manifest.json."""
from pathlib import Path
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    manifest = json.loads((ROOT / "source-manifest.json").read_text(encoding="utf-8"))
    failures = []
    for item in manifest["files"]:
        path = ROOT / item["path"]
        if not path.resolve().is_relative_to(ROOT):
            failures.append({"path": item["path"], "category": "path"})
            continue
        if not path.is_file():
            failures.append({"path": item["path"], "category": "missing"})
            continue
        payload = path.read_bytes()
        if len(payload) != item["bytes"] or hashlib.sha256(payload).hexdigest() != item["sha256"]:
            failures.append({"path": item["path"], "category": "digest"})
    print(json.dumps({"result": "failed" if failures else "passed", "files": len(manifest["files"]), "findings": failures}))
    return int(bool(failures))


if __name__ == "__main__":
    raise SystemExit(main())
