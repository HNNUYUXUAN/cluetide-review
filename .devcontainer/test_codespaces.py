"""Local startup checks; all application state is temporary."""

from contextlib import ExitStack, redirect_stdout
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("codespaces_runtime", ROOT / "scripts" / "codespaces-runtime.py")
runtime = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = runtime
SPEC.loader.exec_module(runtime)


class CodespacesChecks(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory(prefix="cluetide-codespaces-")
        self.root = Path(self.directory.name)
        self.config = runtime.Runtime(self.root, Path(sys.executable), "https://review-8787.app.github.dev")
        self.config.state_dir.mkdir(parents=True)
        (self.root / "frontend" / "dist").mkdir(parents=True)
        (self.root / "frontend" / "dist" / "index.html").write_text("<html lang='en'></html>", encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def save_process(self, *, command=None, origin=None):
        identity = {"pid": 123, "start_ticks": "88", "command": command or self.config.command}
        runtime.write_state(self.config.pid_path, {
            "pid": 123, "identity": identity, "root": str(self.root),
            "origin": origin or self.config.origin, "status": "ready",
        })
        return identity

    def test_persistent_writer_pause_survives_environment_recreation(self):
        marker = self.root / "local-data" / "release-witness" / "writer-paused"
        marker.parent.mkdir(parents=True)
        marker.write_bytes(b"Vercel production cutover\n")
        with patch.dict(os.environ, {"CLUETIDE_RELEASE_WRITES_ENABLED": "1"}):
            self.assertEqual(self.config.environment()["CLUETIDE_RELEASE_WRITES_ENABLED"], "0")

    def test_forwarded_origin_validation(self):
        self.assertEqual(runtime.public_origin({"CODESPACE_NAME": "review-demo"}),
                         "https://review-demo-8787.app.github.dev")
        for environment in ({}, {"CODESPACE_NAME": "bad/name"},
                            {"CODESPACE_NAME": "review", "GITHUB_CODESPACES_PORT_FORWARDING_DOMAIN": "bad/path"}):
            with self.subTest(environment=environment), self.assertRaises(runtime.StartupError):
                runtime.public_origin(environment)

    def test_linux_pid_identity_uses_start_time(self):
        proc = self.root / "proc" / "123"
        proc.mkdir(parents=True)
        fields = ["S"] + ["0"] * 18 + ["9988"] + ["0"] * 4
        (proc / "stat").write_text("123 (server worker) " + " ".join(fields), encoding="utf-8")
        (proc / "cmdline").write_bytes(b"python\0-m\0uvicorn\0")
        self.assertEqual(runtime.process_identity(123, proc.parent),
                         {"pid": 123, "start_ticks": "9988", "command": ["python", "-m", "uvicorn"]})
        self.assertIsNone(runtime.process_identity(True, proc.parent))

    def test_matching_healthy_process_is_reused(self):
        identity = self.save_process()
        with patch.object(runtime, "process_identity", return_value=identity), \
                patch.object(runtime, "healthy", return_value=True), \
                patch.object(runtime.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            runtime.start(self.config)
        launch.assert_not_called()

    def test_unhealthy_process_requires_inspection(self):
        identity = self.save_process()
        with patch.object(runtime, "process_identity", return_value=identity), \
                patch.object(runtime, "healthy", return_value=False), \
                patch.object(runtime.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(runtime.StartupError, "not healthy"):
                runtime.start(self.config)
        launch.assert_not_called()

    def test_pid_reuse_and_occupied_port_do_not_launch(self):
        identity = self.save_process()
        identity["start_ticks"] = "999"
        with patch.object(runtime, "process_identity", return_value=identity), \
                patch.object(runtime, "port_in_use", return_value=True), \
                patch.object(runtime.subprocess, "Popen") as launch:
            with self.assertRaisesRegex(runtime.StartupError, "occupied"):
                runtime.start(self.config)
        launch.assert_not_called()

    def listener_fixture(self, *, pid=456):
        proc = self.root / "proc"
        (proc / "net").mkdir(parents=True, exist_ok=True)
        (proc / "net" / "tcp").write_text(
            "sl local_address rem_address st tx_queue tr retrnsmt uid timeout inode\n"
            f"0: 00000000:{self.config.port:04X} 00000000:0000 0A 0:0 0:0 0 0 0 98765\n", encoding="ascii")
        (proc / "net" / "tcp6").write_text("header\n", encoding="ascii")
        directory = proc / str(pid)
        (directory / "fd").mkdir(parents=True, exist_ok=True)
        (directory / "fd" / "7").write_bytes(b"")
        fields = ["S"] + ["0"] * 18 + ["9988"] + ["0"] * 4
        (directory / "stat").write_text(f"{pid} (python) " + " ".join(fields), encoding="utf-8")
        (directory / "cmdline").write_bytes(b"\0".join(os.fsencode(v) for v in self.config.command) + b"\0")
        (directory / "environ").write_bytes(b"\0".join(
            key.encode() + b"=" + value.encode() for key, value in self.config.environment().items()) + b"\0")
        return proc, directory

    def listener_context(self, directory, *, owner=True, cwd=None, python=None, healthy=True):
        stack = ExitStack()
        resolve = Path.resolve

        def resolve_process(path, *args, **kwargs):
            if path == directory / "cwd":
                return cwd or resolve(self.root)
            if path == directory / "exe":
                return python or resolve(self.config.python)
            return resolve(path, *args, **kwargs)

        stack.enter_context(patch.object(runtime.os, "getuid", return_value=directory.stat().st_uid + (0 if owner else 1), create=True))
        stack.enter_context(patch.object(runtime.os, "readlink", return_value="socket:[98765]"))
        stack.enter_context(patch.object(runtime.Path, "resolve", resolve_process))
        stack.enter_context(patch.object(runtime, "healthy", return_value=healthy))
        return stack

    def test_resumed_listener_is_adopted_with_matching_process_and_service(self):
        proc, directory = self.listener_fixture()
        with self.listener_context(directory):
            identity = runtime.recover_listener(self.config, proc)
        self.assertEqual(identity["pid"], 456)
        with patch.object(runtime, "port_in_use", return_value=True), \
                patch.object(runtime, "recover_listener", return_value=identity), \
                patch.object(runtime.subprocess, "Popen") as launch, redirect_stdout(io.StringIO()):
            runtime.start(self.config)
        launch.assert_not_called()
        self.assertEqual(runtime.read_state(self.config.pid_path)["identity"], identity)

    def test_resumed_listener_requires_exact_owner_cwd_interpreter_and_health(self):
        proc, directory = self.listener_fixture()
        for change in ({"owner": False}, {"cwd": self.root / "other"},
                       {"python": self.root / "other-python"}, {"healthy": False}):
            with self.subTest(change=change), self.listener_context(directory, **change):
                self.assertIsNone(runtime.recover_listener(self.config, proc))

    def test_resumed_listener_requires_exact_command_and_environment(self):
        proc, directory = self.listener_fixture()
        command = (directory / "cmdline").read_bytes()
        environment = (directory / "environ").read_bytes()
        mutations = (("cmdline", command + b"--reload\0"),
                     ("environ", environment.replace(self.config.origin.encode(), b"https://other.example")),
                     ("environ", environment.replace(b"CLUETIDE_DATA_DIR=", b"OTHER_DATA_DIR=")),
                     ("environ", environment.replace(b"https://hnnuyuxuan.github.io", b"https://other.example")))
        for name, payload in mutations:
            with self.subTest(name=name):
                (directory / name).write_bytes(payload)
                with self.listener_context(directory):
                    self.assertIsNone(runtime.recover_listener(self.config, proc))
                (directory / "cmdline").write_bytes(command)
                (directory / "environ").write_bytes(environment)

    def test_resumed_listener_rechecks_pid_after_health_request(self):
        proc, directory = self.listener_fixture()
        identity = runtime.process_identity(456, proc)
        with self.listener_context(directory), patch.object(runtime, "process_identity", side_effect=[identity, None]):
            self.assertIsNone(runtime.recover_listener(self.config, proc))

    def test_resumed_listener_requires_an_unambiguous_socket_owner(self):
        proc, directory = self.listener_fixture()
        other = proc / "789"
        shutil.copytree(directory, other)
        with self.listener_context(directory):
            self.assertIsNone(runtime.recover_listener(self.config, proc))

    def test_resumed_listener_requires_ownership_of_every_listening_socket(self):
        proc, directory = self.listener_fixture()
        table = proc / "net" / "tcp"
        with table.open("a", encoding="ascii") as stream:
            stream.write(f"1: 00000000:{self.config.port:04X} 00000000:0000 0A 0:0 0:0 0 0 0 88888\n")
        with self.listener_context(directory):
            self.assertIsNone(runtime.recover_listener(self.config, proc))

    def test_changed_origin_does_not_reuse_process(self):
        identity = self.save_process(origin="https://previous-8787.app.github.dev")
        with patch.object(runtime, "process_identity", return_value=identity):
            with self.assertRaisesRegex(runtime.StartupError, "different startup settings"):
                runtime.start(self.config)

    def test_missing_build_fails_before_launch(self):
        (self.root / "frontend" / "dist" / "index.html").unlink()
        with self.assertRaisesRegex(runtime.StartupError, "Build the frontend"):
            runtime.start(self.config)

    def test_child_failure_is_recorded(self):
        identity = {"pid": 123, "start_ticks": "88", "command": self.config.command}
        with patch.object(runtime, "process_identity", return_value=identity), \
                patch.object(runtime, "port_in_use", return_value=False), \
                patch.object(runtime.subprocess, "Popen") as launch:
            launch.return_value.pid = 123
            launch.return_value.poll.return_value = 3
            with self.assertRaisesRegex(runtime.StartupError, "status 3"):
                runtime.start(self.config)
        self.assertEqual(runtime.read_state(self.config.pid_path)["exit_code"], 3)

    def test_readiness_timeout_reports_incomplete_startup(self):
        identity = {"pid": 123, "start_ticks": "88", "command": self.config.command}
        with patch.object(runtime, "process_identity", return_value=identity), \
                patch.object(runtime, "port_in_use", return_value=False), \
                patch.object(runtime.subprocess, "Popen") as launch:
            launch.return_value.pid = 123
            with self.assertRaisesRegex(runtime.StartupError, "did not become healthy"):
                runtime.start(self.config, timeout=0)
        self.assertEqual(runtime.read_state(self.config.pid_path)["status"], "starting")

    def test_actual_release_server_readiness_and_duplicate_start(self):
        if not (ROOT / "frontend" / "dist" / "index.html").is_file():
            self.skipTest("Build the frontend to exercise its same-origin UI")
        shutil.copytree(ROOT / "frontend" / "dist", self.root / "frontend" / "dist", dirs_exist_ok=True)
        for module in ("__init__", "release_chain", "releases", "release_imports", "release_exchange", "release_feedback", "releases_api", "release_server",
                       "bot", "bundles", "registry", "request_limits", "settings"):
            target = self.root / "src" / "cluetide" / f"{module}.py"
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(ROOT / "src" / "cluetide" / target.name, target)
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        configuration = runtime.Runtime(self.root, Path(sys.executable), self.config.origin, port)
        processes = []
        original_launch = subprocess.Popen

        def launch(*args, **kwargs):
            process = original_launch(*args, **kwargs)
            processes.append(process)
            return process

        def identity(pid):
            return {"pid": pid, "start_ticks": "local-test", "command": configuration.command}

        try:
            with patch.object(runtime.subprocess, "Popen", side_effect=launch), \
                    patch.object(runtime, "process_identity", side_effect=identity), \
                    patch.dict(os.environ, {}, clear=False), redirect_stdout(io.StringIO()):
                os.environ.pop("CLUETIDE_RELEASE_CONTRACT", None)
                runtime.start(configuration)
                runtime.start(configuration)
            self.assertEqual(len(processes), 1)
            opener = build_opener(ProxyHandler({}))
            for path in ("/", "/api/health", "/api/releases/config"):
                with opener.open(f"http://127.0.0.1:{port}{path}", timeout=3) as response:
                    self.assertEqual(response.status, 200)
                    payload = response.read()
                if path == "/api/releases/config":
                    self.assertEqual(json.loads(payload)["chain"]["status"], "unconfigured")
                if path == "/":
                    self.assertIn(b'<html lang="en">', payload)
                    self.assertIn(b"Release Witness", payload)
                    asset = re.search(rb'<script[^>]+src="([^"]+)"', payload).group(1).decode()
                    with opener.open(f"http://127.0.0.1:{port}/{asset.lstrip('./')}", timeout=3) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn("javascript", response.headers["Content-Type"])
            request = Request(f"http://127.0.0.1:{port}/api/releases/config",
                              headers={"Host": "review-8787.app.github.dev", "Origin": configuration.origin})
            with opener.open(request, timeout=3) as response:
                self.assertEqual(response.status, 200)
            request.add_header("Origin", "https://other.example")
            with self.assertRaises(HTTPError) as failure:
                opener.open(request, timeout=3)
            self.assertEqual(failure.exception.code, 403)
        finally:
            for process in processes:
                process.terminate()
                process.wait(timeout=10)


if __name__ == "__main__":
    unittest.main()
