#!/usr/bin/env bash
# Remove the API LaunchAgent installed by scripts/install-service.sh.
set -euo pipefail
LABEL="com.snomed-hybrid-search.api"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
launchctl unload -w "$PLIST" 2>/dev/null || true
rm -f "$PLIST"
echo "✓ Removed $LABEL (the API no longer auto-starts)."
