#!/bin/bash
# Wrapper so hooks.json can pass the event name as $1.
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
exec python3 "$ROOT/.cursor/hooks/record-event.py" "${1:-unknown}"
