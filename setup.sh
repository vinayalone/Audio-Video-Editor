#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# setup.sh — Install & configure the Telegram Media Trimmer Bot on a VPS
# Tested on Ubuntu 20.04 / 22.04 / 24.04
#
# Usage:
#   chmod +x setup.sh
#   ./setup.sh
# ─────────────────────────────────────────────────────────────────────────────
set -euo pipefail

INSTALL_DIR="$HOME/trimmer-bot"
SERVICE_NAME="trimmer-bot"

echo "════════════════════════════════════════════"
echo "  Telegram Media Trimmer Bot — Setup Script"
echo "════════════════════════════════════════════"
echo ""

# ── 1. System packages ────────────────────────────────────────────────────────
echo "[1/6] Installing system packages (ffmpeg, python3, pip)…"
sudo apt-get update -qq
sudo apt-get install -y -qq ffmpeg python3 python3-pip python3-venv

echo "      ffmpeg: $(ffmpeg -version 2>&1 | head -1)"
echo "      python: $(python3 --version)"

# ── 2. Bot directory ──────────────────────────────────────────────────────────
echo ""
echo "[2/6] Creating bot directory at $INSTALL_DIR…"
mkdir -p "$INSTALL_DIR"

# Copy files to install dir (only if we're not already in it)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ "$SCRIPT_DIR" != "$INSTALL_DIR" ]; then
  cp "$SCRIPT_DIR/bot.py"           "$INSTALL_DIR/"
  cp "$SCRIPT_DIR/requirements.txt" "$INSTALL_DIR/"
  if [ -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env" "$INSTALL_DIR/"
  fi
fi

# ── 3. Python virtual environment ─────────────────────────────────────────────
echo ""
echo "[3/6] Creating Python virtual environment…"
python3 -m venv "$INSTALL_DIR/venv"
"$INSTALL_DIR/venv/bin/pip" install --upgrade -q pip
"$INSTALL_DIR/venv/bin/pip" install -q -r "$INSTALL_DIR/requirements.txt"
echo "      Dependencies installed."

# ── 4. .env / token ───────────────────────────────────────────────────────────
echo ""
echo "[4/6] Configuring BOT_TOKEN…"
ENV_FILE="$INSTALL_DIR/.env"

if [ ! -f "$ENV_FILE" ]; then
  read -rp "      Paste your Telegram Bot Token: " TOKEN
  echo "BOT_TOKEN=$TOKEN" > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  echo "      Token saved to $ENV_FILE"
else
  echo "      .env already exists — skipping."
fi

# ── 5. Systemd service ────────────────────────────────────────────────────────
echo ""
echo "[5/6] Creating systemd service ($SERVICE_NAME)…"

SERVICE_FILE="/etc/systemd/system/$SERVICE_NAME.service"
sudo tee "$SERVICE_FILE" > /dev/null <<EOF
[Unit]
Description=Telegram Media Trimmer Bot
After=network.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$INSTALL_DIR
EnvironmentFile=$INSTALL_DIR/.env
ExecStart=$INSTALL_DIR/venv/bin/python $INSTALL_DIR/bot.py
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"

echo "      Service started."

# ── 6. Status ────────────────────────────────────────────────────────────────
echo ""
echo "[6/6] Checking service status…"
sleep 2
sudo systemctl status "$SERVICE_NAME" --no-pager -l

echo ""
echo "════════════════════════════════════════════"
echo "  ✅ Setup complete!"
echo ""
echo "  Useful commands:"
echo "    View logs :  journalctl -u $SERVICE_NAME -f"
echo "    Stop bot  :  sudo systemctl stop $SERVICE_NAME"
echo "    Start bot :  sudo systemctl start $SERVICE_NAME"
echo "    Restart   :  sudo systemctl restart $SERVICE_NAME"
echo "════════════════════════════════════════════"
