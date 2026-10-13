"""Reference Agent client for exact proposals, wallet handoff and controlled releases."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import zipfile
from urllib.parse import urlsplit

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from cluetide.release_exchange import MAX_PACKAGE_BYTES, strict_json, verify_package
from cluetide.release_feedback import MAX_FEEDBACK_REQUEST_BYTES
from cluetide.releases import ReleaseError


def bounded_response(client, method, url, *, limit=MAX_PACKAGE_BYTES, **kwargs):
    with client.stream(method, url, **kwargs) as response:
        content = bytearray()
        for chunk in response.iter_bytes():
            content.extend(chunk)
            if len(content) > limit:
                raise ValueError("The response exceeds the exchange limit")
        return response.status_code, bytes(content)


def workflow_status(state, version_id, timestamp):
    getters = state.get("getter_state", {})
    task, version, attempt = getters.get("task"), getters.get("version"), getters.get("attempt")
    if not task or not version:
        return "unregistered"
    decision = str(version["decision"])
    if decision == "4":
        if attempt and str(attempt.get("result")) in {"1", "2"}:
            return "receipt_recorded" if str(attempt["result"]) == "1" else "failed"
        return "consumed"
    if int(task["version_count"]) > version_id:
        return "superseded"
    if decision == "3":
        return "correction_required"
    if decision == "2":
        return "revoked"
    if int(version["deadline"]) <= timestamp:
        return "expired"
    return "approved" if decision == "1" else "awaiting_approval"


def wait_for_review(client, base, args):
    deadline = time.monotonic() + args.timeout
    terminal = {"correction_required", "revoked", "expired", "superseded", "failed", "receipt_recorded"}
    if args.until == "decision":
        terminal.update(("approved", "consumed"))
    elif args.until == "execution":
        terminal.add("consumed")
    while True:
        response = client.post(base + "/read", json={"task_id": args.task_id, "version_id": args.version_id})
        response.raise_for_status()
        state = strict_json(response.content)
        status = workflow_status(state, args.version_id, int(time.time()))
        result = {"status": status, "task_id": args.task_id, "version_id": args.version_id,
                  "verification_scope": "current_chain_getter", "chain": state}
        if status == "correction_required":
            feedback = client.get(base + "/tasks/" + args.task_id + "/feedback", params={"version": args.version_id})
            feedback.raise_for_status()
            result["feedback"] = strict_json(feedback.content)["feedback"]
            result["feedback_pending"] = not result["feedback"]
        if status in terminal:
            return result
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return {**result, "status": "timeout", "last_status": status}
        time.sleep(min(args.interval, remaining))


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
    create.add_argument("--public", action="store_true", help="Publish task metadata to the selected public service")
    for name in ("title", "owner", "proposer", "reviewer", "executor"):
        create.add_argument("--" + name, required=True)
    submit = commands.add_parser("submit-version")
    submit.add_argument("--public", action="store_true", help="Publish original release materials to the selected public service")
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
    execute.add_argument("--public", action="store_true", help="Execute the approved publication on the selected public service")
    execute.add_argument("--task-id", required=True)
    execute.add_argument("--version-id", required=True, type=int)
    execute.add_argument("--attempt-id", required=True)
    execute.add_argument("--start-transaction-hash", required=True)
    receipt = commands.add_parser("receipt")
    receipt.add_argument("--task-id", required=True)
    receipt.add_argument("--attempt-id", required=True)
    export = commands.add_parser("export")
    export.add_argument("--task-id", required=True)
    export.add_argument("--version-id", type=int)
    export.add_argument("--output", type=Path, required=True)
    feedback = commands.add_parser("feedback")
    feedback.add_argument("--task-id", required=True)
    feedback.add_argument("--version-id", type=int)
    feedback.add_argument("--file", type=Path, help="Upload an already wallet-signed feedback JSON")
    feedback.add_argument("--public", action="store_true", help="Publish the signed review feedback")
    wait = commands.add_parser("wait")
    wait.add_argument("--task-id", required=True)
    wait.add_argument("--version-id", required=True, type=int)
    wait.add_argument("--interval", type=float, default=5)
    wait.add_argument("--timeout", type=float, default=300)
    wait.add_argument("--until", choices=("decision", "execution", "receipt"), default="decision")
    verify = commands.add_parser("verify-package")
    verify.add_argument("--package", type=Path, required=True)
    args = parser.parse_args()
    try:
        if args.command == "verify-package":
            if args.package.stat().st_size > MAX_PACKAGE_BYTES:
                raise ValueError()
            result = verify_package(args.package.read_bytes())
            if "task" in result:
                task = result.pop("task")
                result["task_id"] = task["task_id"] if task else None
            print(json.dumps(result, ensure_ascii=True, indent=2))
            return 0
        parsed = urlsplit(args.url)
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password
                or parsed.query or parsed.fragment or parsed.path not in {"", "/"}
                or (parsed.scheme == "http" and parsed.hostname not in {"127.0.0.1", "localhost", "::1"})):
            raise ValueError()
        writing = args.command in {"create-task", "submit-version", "execute"} or args.command == "feedback" and args.file
        if writing and parsed.hostname not in {"127.0.0.1", "localhost", "::1"} and not args.public:
            print(json.dumps({"status": "public_confirmation_required", "message": "Add --public to publish these materials to the selected public service."}))
            return 1
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
            payload["artifact_text"] = artifact.read_bytes().decode("utf-8")
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
        elif args.command == "export":
            path = "/tasks/" + args.task_id + "/bundle"
            params = {"version": args.version_id} if args.version_id is not None else None
        elif args.command == "feedback":
            path = "/tasks/" + args.task_id + "/feedback"
            if args.file:
                if args.file.stat().st_size > MAX_FEEDBACK_REQUEST_BYTES:
                    raise ValueError()
                method, payload = "POST", strict_json(args.file.read_bytes())
            else:
                params = {"version": args.version_id} if args.version_id is not None else None
        with httpx.Client(timeout=75, trust_env=False, follow_redirects=False) as client:
            if args.command == "wait":
                if not 1 <= args.interval <= 60 or not 0 <= args.timeout <= 86400:
                    raise ValueError()
                result = wait_for_review(client, args.url.rstrip("/") + "/api/releases", args)
                print(json.dumps(result, ensure_ascii=True, indent=2))
                return 2 if result["status"] == "timeout" else 0
            if args.command == "export":
                status, content = bounded_response(client, "GET", args.url.rstrip("/") + "/api/releases" + path, params=params)
                if status != 200:
                    raise ValueError()
                verified = verify_package(content)
                args.output.parent.mkdir(parents=True, exist_ok=True)
                with args.output.open("xb") as output:
                    output.write(content)
                print(json.dumps({"status": "exported", "path": str(args.output), "bytes": len(content),
                                  "selected_version": verified["selected_version"], "complete_history": verified["complete_history"]}))
                return 0
            response = client.request(method, args.url.rstrip("/") + "/api/releases" + path, json=payload, params=params, headers=headers)
        if not response.is_success:
            print(json.dumps({"status": "unavailable", "http_status": response.status_code,
                              "message": "The release request could not complete. Inspect the service state before retrying."}))
            return 1
        result = strict_json(response.content)
        if args.command == "create-task":
            key = result.pop("write_key")
            if not isinstance(key, str) or not 32 <= len(key) <= 128:
                raise ValueError()
            identity = args.url.rstrip("/").lower() + "|" + result["task_id"].lower()
            save_access(args.access_file, identity, key)
            result["package_write_access"] = "Saved in the private Agent access file. Keep that file with the task creator."
        print(json.dumps(result, ensure_ascii=True, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError, UnicodeError, RecursionError, zipfile.BadZipFile, ReleaseError, httpx.HTTPError):
        print(json.dumps({"status": "unavailable", "message": "Check the release service URL and bounded UTF-8 artifact."}), file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
