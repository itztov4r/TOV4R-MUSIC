"""Offline preflight for deploying TOV4R MUSIC on a shared Python bot host.

Run after installing requirements.txt. Never print or paste your Discord token.
"""
import importlib.metadata
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

MIN_PYTHON = (3, 13)
PACKAGES = ("discord.py", "davey", "python-dotenv", "yt-dlp", "requests")

def binary_version(program: str):
    path = shutil.which(program)
    if not path:
        return None
    try:
        result = subprocess.run([path, "-version" if program == "ffmpeg" else "--version"],
                                capture_output=True, text=True, timeout=8, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return None
    text = (result.stdout or result.stderr).splitlines()
    return text[0] if text else None

def main():
    ok = True
    print("TOV4R MUSIC — host compatibility check (secrets are never printed)")
    python_ok = sys.version_info >= MIN_PYTHON
    print(f"{'OK' if python_ok else 'FAIL'} Python: {sys.version.split()[0]} (recommended 3.13+)")
    ok &= python_ok

    ffmpeg = binary_version("ffmpeg")
    ffmpeg_ok = ffmpeg is not None
    print(f"{'OK' if ffmpeg_ok else 'FAIL'} FFmpeg: {ffmpeg or 'not available on PATH'}")
    ok &= ffmpeg_ok

    node = binary_version("node")
    match = re.search(r"v?(\d+)\.", node or "")
    node_ok = bool(match and int(match.group(1)) >= 22)
    print(f"{'OK' if node_ok else 'FAIL'} Node.js: {node or 'not available'} (22+ for YouTube EJS)")
    ok &= node_ok

    for package in PACKAGES:
        try:
            version = importlib.metadata.version(package)
            print(f"OK dependency: {package} {version}")
        except importlib.metadata.PackageNotFoundError:
            print(f"FAIL dependency: {package} (install requirements.txt)")
            ok = False

    # The token's value is intentionally never displayed.
    configured = bool(os.getenv("DISCORD_TOKEN", "").strip())
    if not configured:
        # python-dotenv, if installed, may load a privately stored .env file.
        try:
            from dotenv import load_dotenv
            load_dotenv(Path(__file__).parent / ".env")
            configured = bool(os.getenv("DISCORD_TOKEN", "").strip())
        except ImportError:
            pass
    print(f"{'OK' if configured else 'FAIL'} DISCORD_TOKEN: {'configured' if configured else 'missing'}")
    ok &= configured

    db_path = os.getenv("DJ_DB_PATH", "dj_access.sqlite3")
    print("INFO DJ database: ensure the hosting provider persists its directory across restarts.")
    print("INFO Audio sources can still fail due to external provider restrictions.")
    print("RESULT:", "ready for a live Discord/voice test" if ok else "fix FAIL lines before launch")
    return 0 if ok else 1

if __name__ == "__main__":
    raise SystemExit(main())
