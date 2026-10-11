"""Reference Agent client for exact proposals, wallet handoff and controlled releases."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
from urllib.parse import urlsplit

import httpx


def read_access(path: Path) -> dict:
    if not path.exists():
        return {}
    if path.stat().st_size > 262144:
        raise ValueError()
    value = json.loads(path.read_text("utf-8"))
    if not isinstance(value, dict) or not all(isinstance(k, str) and isinstance(v, str) for k, v in value.items()):
        raise ValueError()
    return value


def save_access(path: Path, identity: str, key: str) -> None:
    access = read_access(path)
    access[identity] = key
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(prefix="agent-access-", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(access, stream, indent=2)
            stream.write("\n")
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8787", help="ClueTide release service URL")
    parser.add_argument("--access-file", type=Path, default=Path(__file__).resolve().parents[1] / "local-data" / "agent-write-access.json",
                        help="Private file retaining task package-write capabilities")
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("config")
    commands.add_parser("tasks")
    create = commands.add_parser("create-task")
    for name in ("title", "owner", "proposer", "reviewer", "executor"):
        create.add_argument("--" + name, required=True)
    submit = commands.add_parser("submit-version")
    for name in ("task-id", "artifact", "summary", "source-reference"):
        submit.add_argument("--" + name, required=True)
    submit.add_argument("--expires-at", required=True, type=int, help="Exact manifest deadline in Unix seconds")
    submit.add_argument("--test-evidence", action="append", required=True)
    show = commands.add_parser("task")
    show.add_argument("--task-id", required=True)
    for name in ("prepare", "verify"):
        action = commands.add_parser(name)
        action.add_argument("--action", required=True, choices=["deploy", "create_task", "submit_version", "approve", "request_correction", "revoke", "start_execution", "record_receipt"])
        action.add_argument("--account", required=True)
        action.add_argument("--task-id")
        action.add_argument("--version-id", type=int)
        action.add_argument("--attempt-id")
        if name == "verify":
            action.add_argument("--transaction-hash", required=True)
    read = commands.add_parser("read")
    read.add_argument("--task-id", required=True)
    read.add_argument("--version-id", type=int)
    read.add_argument("--attempt-id")
    execute = commands.add_parser("execute")
    execute.add_argument("--task-id", required=True)
    execute.add_argument("--version-id", required=True, type=int)
    execute.add_argument("--attempt-id", required=True)
    execute.add_argument("--start-transaction-hash", required=True)
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--task-id", required=True)
    receipt.add_argument("--attempt-id", required=True)
    args = parser.parse_args()
    try:
        parsed = urlsplit(args.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError()
        method, path, payload, params, headers = "GET", "", None, None, {}
        values = vars(args)
        if args.command in {"config", "tasks"}:
            path = "/" + args.command
        elif args.command == "create-task":
            method, path = "POST", "/tasks"
            payload = {key: values[key] for key in ("title", "owner", "proposer", "reviewer", "executor")}
        elif args.command == "submit-version":
            identity = args.url.rstrip("/").lower() + "|" + args.task_id.lower()
            key = os.environ.get("CLUETIDE_TASK_WRITE_KEY") or read_access(args.access_file).get(identity)
            if not key:
                print(json.dumps({"status": "unavailable", "message": "Use the creator's private access file or set CLUETIDE_TASK_WRITE_KEY to add a package."}), file=sys.stderr)
                return 1
            headers["X-ClueTide-Task-Key"] = key
            artifact = Path(args.artifact)
            if artifact.stat().st_size > 32768:
                raise ValueError()
            payload = {key: values[key] for key in ("summary", "source_reference", "expires_at", "test_evidence")}
            payload["artifact_text"] = artifact.read_text("utf-8")
            method, path = "POST", "/tasks/" + args.task_id + "/versions"
        elif args.command == "task":
            path = "/tasks/" + args.task_id
        elif args.command in {"prepare", "verify", "read"}:
            fields = ("action", "account", "task_id", "version_id", "attempt_id", "transaction_hash")
            payload = {key: values[key] for key in fields if values.get(key) is not None}
            method, path = "POST", "/" + args.command
        elif args.command == "execute":
            payload = {key: values[key] for key in ("version_id", "attempt_id", "start_transaction_hash")}
            method, path = "POST", "/tasks/" + args.task_id + "/execute"
        elif args.command == "receipt":
            path, params = "/attempts/" + args.attempt_id + "/receipt", {"task_id": args.task_id}
        with httpx.Client(timeout=75, trust_env=False, follow_redirects=False) as client:
            response = client.request(method, args.url.rstrip("/") + "/api/releases" + path, json=payload, params=params, headers=headers)
        if not response.is_success:
            print(json.dumps({"status": "unavailable", "http_status": response.status_code,
                              "message": "The release request could not complete. Inspect the service state before retrying."}))
            return 1
        result = response.json()
        if args.command == "create-task":
            key = result.pop("write_key")
            if not isinstance(key, str) or not 32 <= len(key) <= 128:
                raise ValueError()
            identity = args.url.rstrip("/").lower() + "|" + result["task_id"].lower()
            save_access(args.access_file, identity, key)
            result["package_write_access"] = "Saved in the private Agent access file. Keep that file with the task creator."
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, httpx.HTTPError):
        print(json.dumps({"status": "unavailable", "message": "Check the release service URL and bounded UTF-8 artifact."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
