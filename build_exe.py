#!/usr/bin/env python3
"""Build CloudBench as a standalone Windows executable.

Prerequisites (install once):
    pip install pyinstaller

Usage:
    python build_exe.py

Output:
    dist/CloudBench/CloudBench.exe

The user runs:
    CloudBench.exe web              # launch web UI on http://localhost:8080
    CloudBench.exe web -p 9000      # custom port
    CloudBench.exe run --host ...   # headless CLI mode
"""

import subprocess
import sys
import shutil
from pathlib import Path

ROOT = Path(__file__).parent
SPEC = ROOT / "cloudbench.spec"
DIST = ROOT / "dist" / "CloudBench"


def check_pyinstaller():
    try:
        import PyInstaller
        print(f"  PyInstaller {PyInstaller.__version__} found")
    except ImportError:
        print("  PyInstaller not found — installing...")
        subprocess.check_call([sys.executable, "-m", "pip", "install", "pyinstaller"])


def clean():
    for d in [ROOT / "build", ROOT / "dist"]:
        if d.exists():
            print(f"  Cleaning {d}")
            shutil.rmtree(d)


def build():
    print(f"  Building from {SPEC} ...")
    subprocess.check_call([
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        str(SPEC),
    ])


def verify():
    exe = DIST / "CloudBench.exe"
    if not exe.exists():
        exe = DIST / "CloudBench"
    if exe.exists():
        size_mb = exe.stat().st_size / (1024 * 1024)
        print(f"\n  Build succeeded: {exe}")
        print(f"  Size: {size_mb:.1f} MB")
        print(f"\n  To run:  {exe} web")
    else:
        print("\n  ERROR: Executable not found in dist/CloudBench/")
        sys.exit(1)


if __name__ == "__main__":
    print("\n=== CloudBench Executable Build ===\n")

    print("[1/4] Checking PyInstaller...")
    check_pyinstaller()

    print("[2/4] Cleaning previous builds...")
    clean()

    print("[3/4] Building executable...")
    build()

    print("[4/4] Verifying output...")
    verify()

    print("\nDone.\n")
