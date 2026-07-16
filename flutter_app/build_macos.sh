#!/usr/bin/env bash
set -euo pipefail

APP_NAME="Assistant"
APP_BUNDLE_ID="com.executiveassistant.flutterApp"
PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FLUTTER_DIR="$PROJECT_ROOT/flutter_app"
BUILD_DIR="$FLUTTER_DIR/build/macos/Build/Products/Release"
DMG_NAME="$APP_NAME.dmg"
DMG_PATH="$FLUTTER_DIR/build/$DMG_NAME"
DATA_DIR="$HOME/Assistant"

echo "==> Building Python backend with PyInstaller..."
cd "$PROJECT_ROOT"
uv pip install pyinstaller
uv run pyinstaller --clean "$FLUTTER_DIR/backend.spec"

echo "==> Building Flutter macOS app..."
cd "$FLUTTER_DIR"

# Pass Sparkle feed URL and Sentry DSN to the build.
# These are referenced in Info.plist (SPARKLE_FEED_URL) and Dart (sentry_dsn).
export SPARKLE_FEED_URL="${SPARKLE_FEED_URL:-}"
export SPARKLE_ED_KEY="${SPARKLE_ED_KEY:-}"

SENTRY_ARGS=""
if [ -n "${SENTRY_DSN:-}" ]; then
  SENTRY_ARGS="$SENTRY_ARGS --dart-define=sentry_dsn=$SENTRY_DSN"
  echo "  Sentry DSN configured"
fi

flutter build macos --release $SENTRY_ARGS

echo "==> Embedding backend into .app bundle..."
# Flutter build produces 'flutter_app.app' (from PRODUCT_NAME in AppInfo.xcconfig)
FLUTTER_APP="$BUILD_DIR/flutter_app.app"
RESOURCES="$FLUTTER_APP/Contents/Resources"
mkdir -p "$RESOURCES"
cp "$PROJECT_ROOT/dist/ea" "$RESOURCES/ea"
chmod +x "$RESOURCES/ea"

# Rename to display name for DMG
APP_BUNDLE="$BUILD_DIR/$APP_NAME.app"
if [ "$FLUTTER_APP" != "$APP_BUNDLE" ]; then
  rm -rf "$APP_BUNDLE"
  mv "$FLUTTER_APP" "$APP_BUNDLE"
fi

echo "==> Ensuring data directory exists..."
mkdir -p "$DATA_DIR"

echo "==> Copying .env template if missing..."
if [ ! -f "$DATA_DIR/.env" ]; then
  if [ -f "$PROJECT_ROOT/.env.example" ]; then
    cp "$PROJECT_ROOT/.env.example" "$DATA_DIR/.env"
    # Embed OAuth defaults from build environment (set in CI or local .env)
    if [ -n "${DEFAULT_GWS_CLIENT_ID:-}" ]; then
      if grep -q "^# DEFAULT_GWS_CLIENT_ID=" "$DATA_DIR/.env"; then
        sed -i '' "s/^# DEFAULT_GWS_CLIENT_ID=/DEFAULT_GWS_CLIENT_ID=$DEFAULT_GWS_CLIENT_ID/" "$DATA_DIR/.env"
        echo "  Embedded DEFAULT_GWS_CLIENT_ID from build env"
      fi
    fi
    if [ -n "${DEFAULT_GWS_CLIENT_SECRET:-}" ]; then
      if grep -q "^# DEFAULT_GWS_CLIENT_SECRET=" "$DATA_DIR/.env"; then
        sed -i '' "s/^# DEFAULT_GWS_CLIENT_SECRET=/DEFAULT_GWS_CLIENT_SECRET=$DEFAULT_GWS_CLIENT_SECRET/" "$DATA_DIR/.env"
        echo "  Embedded DEFAULT_GWS_CLIENT_SECRET from build env"
      fi
    fi
    echo "  Created $DATA_DIR/.env — edit to add your API keys"
  elif [ -f "$PROJECT_ROOT/docker/.env.example" ]; then
    cp "$PROJECT_ROOT/docker/.env.example" "$DATA_DIR/.env"
    echo "  Created $DATA_DIR/.env from template — edit to add your API keys"
  else
    echo "  WARNING: No .env template found. Create $DATA_DIR/.env manually."
  fi
fi

echo "==> Creating DMG..."
mkdir -p "$FLUTTER_DIR/build"
rm -f "$DMG_PATH"

if command -v create-dmg &>/dev/null; then
  create-dmg \
    --volname "$APP_NAME" \
    --volicon "$FLUTTER_DIR/macos/Runner/Assets.xcassets/AppIcon.appiconset/app_icon_1024.png" \
    --window-pos 200 120 \
    --window-size 600 400 \
    --icon-size 100 \
    --icon "$APP_NAME.app" 150 200 \
    --hide-extension "$APP_NAME.app" \
    --app-drop-link 450 200 \
    "$DMG_PATH" \
    "$APP_BUNDLE"
else
  echo "  create-dmg not found, using hdiutil (no custom icons)..."
  STAGING="$FLUTTER_DIR/build/dmg-staging"
  rm -rf "$STAGING"
  mkdir -p "$STAGING"
  cp -R "$APP_BUNDLE" "$STAGING/"
  ln -s /Applications "$STAGING/Applications"
  hdiutil create -volname "$APP_NAME" -srcfolder "$STAGING" -ov -format UDZO "$DMG_PATH"
  rm -rf "$STAGING"
fi

echo "==> Done! DMG at: $DMG_PATH"
echo "    Data directory: $DATA_DIR"
echo ""
echo "To install: open $DMG_PATH and drag $APP_NAME.app to Applications"
echo "Before first launch, edit $DATA_DIR/.env to add your API keys"
