#!/bin/bash
# Build the menu bar app bundle.
# Usage: build.sh [output-app-path]   (default: ~/Applications/mem0 toggle.app)
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
APP="${1:-$HOME/Applications/mem0 toggle.app}"
ARCH="$(uname -m)"   # arm64 or x86_64

mkdir -p "$(dirname "$APP")"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS"

echo "compiling MemoToggle ($ARCH)..."
swiftc -O -target "${ARCH}-apple-macos12.0" -framework Cocoa \
    -o "$APP/Contents/MacOS/MemoToggle" "$DIR/MemoToggle.swift"

cp "$DIR/Info.plist" "$APP/Contents/Info.plist"
mkdir -p "$APP/Contents/Resources"
cp "$DIR/AppIcon.icns" "$APP/Contents/Resources/AppIcon.icns"
echo "built: $APP"
