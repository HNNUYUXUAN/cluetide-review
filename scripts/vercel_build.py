"""Run explicitly enabled Neon integration checks in isolated Vercel Preview builds."""
import os
from pathlib import Path
import subprocess
import sys


def main():
    if os.environ.get("CLUETIDE_RUN_DATABASE_TESTS") != "1":
        return 0
    if (os.environ.get("VERCEL_ENV") != "preview"
            or os.environ.get("CLUETIDE_STORAGE_NAMESPACE") != "preview"
            or os.environ.get("CLUETIDE_RELEASE_SIGNING_KEY")
            or not os.environ.get("DATABASE_URL")):
        raise SystemExit("Database validation requires an isolated Preview configuration without the production signer.")
    root = Path(__file__).resolve().parents[1]
    subprocess.run(["uv", "pip", "install", "--python", sys.executable, "-r", "requirements-dev.txt"], cwd=root, check=True)
    environment = os.environ.copy()
    environment["CLUETIDE_TEST_DATABASE_URL"] = environment["DATABASE_URL"]
    return subprocess.run([sys.executable, "-m", "pytest", "tests/test_release_persistent.py", "-q", "--maxfail=1"],
                          cwd=root, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
