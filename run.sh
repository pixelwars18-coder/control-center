#!/usr/bin/env bash
# Launch one app: ./run.sh monitor | mods | jarvis
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
PY="$DIR/.venv/bin/python"
# Install or update dependencies when the environment is missing or requirements.txt has changed.
if [ ! -x "$PY" ] || [ ! -f "$DIR/.venv/.installed" ] || [ "$DIR/requirements.txt" -nt "$DIR/.venv/.installed" ]; then
  echo "Setting up the Python environment (one time)…"
  if command -v uv >/dev/null; then
    [ -x "$PY" ] || uv venv -q "$DIR/.venv"
    uv pip install -q --python "$PY" -r "$DIR/requirements.txt"
  else
    [ -x "$PY" ] || python3 -m venv "$DIR/.venv"
    "$DIR/.venv/bin/pip" install -q -r "$DIR/requirements.txt"
  fi
  touch "$DIR/.venv/.installed"
fi
case "${1:-}" in
  monitor) app=monitor_app.py ;;
  mods) app=mod_manager_app.py ;;
  jarvis) app=jarvis_app.py ;;
  *) echo "Usage: $0 monitor|mods|jarvis"; exit 1 ;;
esac
cd "$DIR"
exec "$PY" "$DIR/$app"
