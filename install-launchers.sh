#!/usr/bin/env bash
# Add System Monitor, Mod Manager and Jarvis to your desktop's app menu.
# Re-run this if you move the folder. Remove with: ./install-launchers.sh --remove
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
mkdir -p "$APPS"
entry() { # id name comment arg icon
  if [ "${REMOVE:-}" = 1 ]; then rm -f "$APPS/$1.desktop"; return; fi
  cat > "$APPS/$1.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=$2
Comment=$3
Exec="$DIR/run.sh" $4
Icon=$5
Terminal=false
Categories=Utility;
StartupWMClass=$1
DESKTOP
}
[ "${1:-}" = "--remove" ] && REMOVE=1
entry system-monitor "System Monitor" "Live CPU, memory, GPU, disks and processes" monitor utilities-system-monitor
entry mod-manager "Mod Manager" "Turn game mods on and off and set load order" mods applications-games
entry jarvis "Jarvis" "Local-LLM assistant that can control this computer" jarvis audio-input-microphone
command -v update-desktop-database >/dev/null && update-desktop-database "$APPS" 2>/dev/null || true
if [ "${REMOVE:-}" = 1 ]; then echo "Removed launchers from $APPS"; else echo "Installed launchers in $APPS"; fi
