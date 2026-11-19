#!/bin/sh
# Entwicklungsserver auf Port 8796 mit einer erfundenen Fahrt (--demo); fragt Transitous nie.
cd "$(dirname "$0")" || exit 1
exec python3 server/server.py --port 8796 --data "${TMPDIR:-/tmp}/oeffi-dev" --static web --demo
