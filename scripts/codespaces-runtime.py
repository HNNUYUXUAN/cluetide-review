"""Start one detached release application and verify its local readiness."""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
import json
import os
from pathlib import Path
import re
import socket
import subprocess
import sys
import tempfile
import time
from urllib.error import URLError
from urllib.request import ProxyHandler, Request, build_opener


PORT = 8787
APPLICATION = "agent_release_witness"
STARTUP_TIMEOUT = 20.0


class StartupError(RuntimeError):
    """A startup precondition or application readiness check failed."""


def public_origin(environment: dict[str, str]) -> str:
    name = environment.get("CODESPACE_NAME", "")
    domain = environment.get("GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN", "app.github.dev")
    label = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
    if not re.fullmatch(label, name):
        raise StartupError("CODESPACE_NAME must be a valid Codespaces DNS label")
    if len(domain) > 253 or not re.fullmatch(rf"{label}(?:\.{label})+", domain):
        raise StartupError("The Codespaces port forwarding domain must be a valid hostname")
    hostname = f"{name}-{PORT}.{domain}"
    if len(hostname.split(".")[0]) > 63:
        raise StartupError("The forwarded application hostname is too long")
    return f"https://{hostname}"


@dataclass(frozen=True)
class Runtime:
    root: Path
    python: Path
    origin: str
    port: int = PORT

    @property
    def state_dir(self) -> Path:
        return self.root / "local-data" / "codespaces"

    @property
    def pid_path(self) -> Path:
        return self.state_dir / "server.json"

    @property
    def log_path(self) -> Path:
        return self.state_dir / "server.log"

    @property
    def command(self) -> list[str]:
        return [str(self.python), "-X", "utf8", "-m", "uvicorn",
                "cluetide.release_server:app", "--host", "0.0.0.0",
                "--port", str(self.port), "--no-access-log"]

    def environment(self) -> dict[str, str]:
        result = os.environ.copy()
        result.update({
            "CLUETIDE_DATA_DIR": str(self.root / "local-data"),
            "CLUETIDE_PUBLIC_ORIGIN": self.origin,
            "CLUETIDE_CODESPACES_ORIGIN_REWRITE": "1",
            "CLUETIDE_FRONTEND_ORIGINS": "https://hnnuyuxuan.github.io",
            "CLUETIDE_PREVIEW_ONLY": "1",
            "PYDANTIC_AI_NO_BANNER": "1",
            "PYTHONPATH": str(self.root / "src"),
        })
        pause_marker = self.root / "local-data" / "release-witness" / "writer-paused"
        if pause_marker.exists() or pause_marker.is_symlink() or pause_marker.is_junction():
            result["CLUETIDE_RELEASE_WRITES_ENABLED"] = "0"
        return result


def process_identity(pid: int, proc_root: Path = Path("/proc")) -> dict | None:
    """Read Linux process start time and command to distinguish PID reuse."""
    if not isinstance(pid, int) or isinstance(pid, bool) or pid <= 1:
        return None
    try:
        directory = proc_root / str(pid)
        stat = (directory / "stat").read_text(encoding="utf-8")
        fields = stat.rsplit(")", 1)[1].split()
        if fields[0] == "Z":
            return None
        command = (directory / "cmdline").read_bytes().rstrip(b"\0").split(b"\0")
        return {"pid": pid, "start_ticks": fields[19],
                "command": [os.fsdecode(value) for value in command]}
    except (OSError, IndexError, ValueError):
        return None


def read_state(path: Path) -> dict | None:
    try:
        if path.stat().st_size > 16_384:
            raise StartupError("The application PID record exceeds its size limit")
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            raise StartupError("The application PID record must be an object")
        return state
    except FileNotFoundError:
        return None
    except (ValueError, OSError) as error:
        raise StartupError("The application PID record cannot be read") from error


def write_state(path: Path, value: dict) -> None:
    descriptor, name = tempfile.mkstemp(prefix="server-", suffix=".tmp", dir=path.parent)
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


@contextlib.contextmanager
def startup_lock(path: Path):
    import fcntl

    with path.open("a", encoding="utf-8") as stream:
        try:
            fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            raise StartupError("Another application startup is already in progress") from error
        try:
            yield
        finally:
            fcntl.flock(stream.fileno(), fcntl.LOCK_UN)


def healthy(runtime: Runtime) -> bool:
    request = Request(f"http://127.0.0.1:{runtime.port}/api/health")
    try:
        with build_opener(ProxyHandler({})).open(request, timeout=1.0) as response:
            if response.status != 200:
                return False
            content = response.read(2049)
        return len(content) <= 2048 and json.loads(content) == {
            "status": "ok", "application": APPLICATION,
        }
    except (OSError, URLError, ValueError):
        return False


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as connection:
        connection.settimeout(0.5)
        return connection.connect_ex(("127.0.0.1", port)) == 0


def recover_listener(runtime: Runtime, proc_root: Path = Path("/proc")) -> dict | None:
    """Adopt a resumed listener only after proving its process and service identity."""
    try:
        inodes = set()
        for table in ("tcp", "tcp6"):
            for line in (proc_root / "net" / table).read_text("ascii").splitlines()[1:]:
                fields = line.split()
                if fields[3] == "0A" and int(fields[1].split(":")[1], 16) == runtime.port:
                    inodes.add(fields[9])
        if not inodes:
            return None
        holders = []
        matched_inodes = set()
        for directory in proc_root.iterdir():
            if not directory.name.isdecimal():
                continue
            try:
                sockets = {os.readlink(fd) for fd in (directory / "fd").iterdir()}
                owned_inodes = {inode for inode in inodes if f"socket:[{inode}]" in sockets}
                if not owned_inodes:
                    continue
                identity = process_identity(int(directory.name), proc_root)
                if (directory.stat().st_uid != os.getuid()
                        or (directory / "cwd").resolve() != runtime.root.resolve()
                        or (directory / "exe").resolve() != runtime.python.resolve()
                        or not identity or identity["command"] != runtime.command):
                    return None
                environment = dict(item.split(b"=", 1) for item in
                                   (directory / "environ").read_bytes().split(b"\0") if b"=" in item)
                expected = runtime.environment()
                for name in ("CLUETIDE_PUBLIC_ORIGIN", "CLUETIDE_DATA_DIR", "PYTHONPATH",
                             "CLUETIDE_FRONTEND_ORIGINS"):
                    if environment.get(name.encode()) != expected[name].encode():
                        return None
                holders.append(identity)
                matched_inodes.update(owned_inodes)
            except (OSError, ValueError):
                continue
        if len(holders) == 1 and matched_inodes == inodes and healthy(runtime):
            # Re-read after the HTTP check to detect PID reuse during inspection.
            if process_identity(holders[0]["pid"], proc_root) == holders[0]:
                return holders[0]
    except (OSError, ValueError, IndexError):
        pass
    return None


def ready_message(runtime: Runtime, pid: int, reused: bool) -> None:
    print(f"Application {'already running' if reused else 'ready'} (PID {pid}).")
    print(f"Forwarded origin: {runtime.origin}")
    print(f"Startup log: {runtime.log_path}")
    print("Port visibility is managed separately in GitHub Codespaces.")


def start(runtime: Runtime, timeout: float = STARTUP_TIMEOUT) -> None:
    if not runtime.python.is_file():
        raise StartupError("Install the Codespaces Python environment before startup")
    if not (runtime.root / "frontend" / "dist" / "index.html").is_file():
        raise StartupError("Build the frontend before application startup")
    state = read_state(runtime.pid_path)
    if state:
        identity = process_identity(state.get("pid"))
        if identity and identity == state.get("identity"):
            if (state.get("root") != str(runtime.root) or state.get("origin") != runtime.origin
                    or identity["command"] != runtime.command):
                raise StartupError("The existing application uses different startup settings")
            if healthy(runtime):
                ready_message(runtime, identity["pid"], reused=True)
                return
            raise StartupError(f"The existing application is not healthy; inspect {runtime.log_path}")
    if port_in_use(runtime.port):
        identity = recover_listener(runtime)
        if identity:
            write_state(runtime.pid_path, {"pid": identity["pid"], "identity": identity,
                        "root": str(runtime.root), "origin": runtime.origin, "status": "ready"})
            ready_message(runtime, identity["pid"], reused=True)
            return
        raise StartupError(f"Port {runtime.port} is occupied by a process outside this PID record")

    with runtime.log_path.open("ab", buffering=0) as log:
        log.write(b"\n--- Application startup ---\n")
        process = subprocess.Popen(runtime.command, cwd=runtime.root, env=runtime.environment(),
                                   stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                   close_fds=True, start_new_session=True)
    identity = process_identity(process.pid)
    if identity is None:
        raise StartupError(f"The application exited during startup; inspect {runtime.log_path}")
    state = {"pid": process.pid, "identity": identity, "root": str(runtime.root),
             "origin": runtime.origin, "status": "starting"}
    write_state(runtime.pid_path, state)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        exit_code = process.poll()
        if exit_code is not None:
            state.update(status="failed", exit_code=exit_code)
            write_state(runtime.pid_path, state)
            raise StartupError(f"The application exited with status {exit_code}; inspect {runtime.log_path}")
        if healthy(runtime):
            state["status"] = "ready"
            write_state(runtime.pid_path, state)
            ready_message(runtime, process.pid, reused=False)
            return
        time.sleep(0.2)
    raise StartupError(f"The application did not become healthy within {timeout:g}s; inspect {runtime.log_path}")


def main() -> int:
    try:
        if not sys.platform.startswith("linux"):
            raise StartupError("This startup entry point runs inside a Linux Codespaces container")
        if sys.version_info[:2] != (3, 12):
            raise StartupError("Python 3.12 is required")
        os.umask(0o077)
        root = Path(__file__).resolve().parents[1]
        runtime = Runtime(root, root / ".venv" / "bin" / "python", public_origin(dict(os.environ)))
        os.environ["CLUETIDE_PUBLIC_ORIGIN"] = runtime.origin
        runtime.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        runtime.state_dir.chmod(0o700)
        with startup_lock(runtime.state_dir / "startup.lock"):
            start(runtime)
        return 0
    except (StartupError, OSError) as error:
        print(f"Codespaces application startup failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
