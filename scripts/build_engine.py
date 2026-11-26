"""Build the standalone PyInstaller --onedir Pyxel 3 engine bundle for Tauri packaging.

Uses --onedir (instead of --onefile) so Windows/macOS distribution keeps fast cold-start
without runtime temp-dir unpacking latency.
"""
# SPDX-License-Identifier: MIT
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIST_BASE = os.path.join(ROOT, "src-tauri", "engine_dist")
WORK_DIR = os.path.join(ROOT, ".pyinstaller_work")
ENTRY = os.path.join(ROOT, "engine", "walk_engine.py")


def main():
    os.makedirs(DIST_BASE, exist_ok=True)
    cmd = [
        sys.executable, "-m", "PyInstaller",
        "--noconfirm",
        "--clean",
        "--onedir",
        "--console",
        "--name", "vrm_walk_engine",
        "--distpath", DIST_BASE,
        "--workpath", WORK_DIR,
        "--specpath", WORK_DIR,
        "--paths", os.path.join(ROOT, "engine"),
        "--collect-all", "pyxel",
        ENTRY,
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd)
    if os.path.exists(WORK_DIR):
        shutil.rmtree(WORK_DIR, ignore_errors=True)
    print("Engine bundle ready at:", os.path.join(DIST_BASE, "vrm_walk_engine"))


if __name__ == "__main__":
    main()
