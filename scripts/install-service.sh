#!/usr/bin/env bash
# Install a macOS LaunchAgent so the API (uvicorn) auto-starts on login and restarts if it dies.
# Pairs with `brew services start ollama` (Ollama) and the Docker DB (restart: unless-stopped),
# so the whole stack stays up without manual waking. Uninstall with scripts/uninstall-service.sh.
set -euo pipefail

REPO="$(cd "$(dirname "$0")/.." && pwd)"
LABEL="com.snomed-hybrid-search.api"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"

if [ ! -x "$REPO/.venv/bin/uvicorn" ]; then
  echo "ERROR: $REPO/.venv/bin/uvicorn not found — run 'make install' first."; exit 1
fi

# macOS TCC: LaunchAgents cannot read ~/Documents, ~/Desktop, ~/Downloads without extra permission.
case "$REPO" in
  "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
    echo "⚠ WARNING: this repo lives under a macOS-protected folder:"
    echo "    $REPO"
    echo "  A LaunchAgent usually CANNOT read there and will fail with a PermissionError."
    echo "  Options, best first:"
    echo "    1. Use 'make up' instead (runs in your Terminal, which has access) — recommended."
    echo "    2. Move the repo outside those folders (e.g. ~/snomed-hybrid-search) and reinstall."
    echo "    3. Grant Full Disk Access to $REPO/.venv/bin/python3 in System Settings ▸ Privacy & Security."
    echo ;;
esac

mkdir -p "$HOME/Library/LaunchAgents"
cat > "$PLIST" <<EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>$REPO/.venv/bin/uvicorn</string>
    <string>api.server:app</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--port</string><string>8090</string>
  </array>
  <key>WorkingDirectory</key><string>$REPO</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>/tmp/snomed-api.log</string>
  <key>StandardErrorPath</key><string>/tmp/snomed-api.log</string>
  <key>EnvironmentVariables</key>
  <dict><key>PATH</key><string>/opt/homebrew/bin:/usr/bin:/bin</string></dict>
</dict>
</plist>
EOF

launchctl unload -w "$PLIST" 2>/dev/null || true
launchctl load -w "$PLIST"
echo "✓ Installed & loaded $LABEL"
echo "  The API will auto-start on login and restart if it crashes. Logs: /tmp/snomed-api.log"
echo "  Also make Ollama durable:  brew services start ollama"
