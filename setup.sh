# --- HARD RESET OF WORKSPACE ---
APP_DIR="${APP_DIR:-/app}"

# Always leave /app before nuking it
cd /

# If /app exists, remove it completely
if [ -d "$APP_DIR" ]; then
  sudo rm -rf "$APP_DIR"
fi

# Recreate /app and give it to UID 1001
sudo mkdir -p "$APP_DIR"
sudo chown 1001:1001 "$APP_DIR"
sudo chmod -R u+rwX,go-rwx "$APP_DIR"

echo "=== Workspace reset: $APP_DIR is clean ==="
