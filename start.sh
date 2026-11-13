#!/usr/bin/env sh
set -eu
cd "$(dirname "$0")"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
[ -f .env ] || .venv/bin/python configure.py
exec .venv/bin/python bot.py
