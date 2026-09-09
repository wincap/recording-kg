#!/bin/bash
# Mark the current Agent turn as finished so record_lesson.py can send the next prompt.
input=$(cat || true)
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
STATE="$ROOT/.cursor/record-state"
mkdir -p "$STATE"
date +%s > "$STATE/idle"
printf '%s\n' '{}'
