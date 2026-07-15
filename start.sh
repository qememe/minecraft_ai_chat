#!/usr/bin/env bash
# Minecraft AI Chat — Fabric 26.2 server + web control panel
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

WEB_PORT="${WEB_PORT:-8080}"
MC_MEMORY="${MC_MEMORY:-2G}"
AUTO_START_MC="${AUTO_START_MC:-1}"

# If default port is busy, pick the next free one (unless WEB_PORT was set explicitly).
if [[ -z "${WEB_PORT_SET_BY_USER:-}" ]]; then
  if command -v ss >/dev/null 2>&1; then
    if ss -tln | awk '{print $4}' | grep -qE "[:.]${WEB_PORT}\$"; then
      for try in 8080 8081 8088 8765 9000 18080; do
        if ! ss -tln | awk '{print $4}' | grep -qE "[:.]${try}\$"; then
          echo "[warn] Port ${WEB_PORT} is busy, using ${try}"
          WEB_PORT="$try"
          break
        fi
      done
    fi
  fi
fi

export WEB_PORT MC_MEMORY AUTO_START_MC

echo "=============================================="
echo " Minecraft AI Chat  |  Fabric 26.2"
echo "=============================================="

# --- Java check
if ! command -v java >/dev/null 2>&1; then
  echo "ERROR: java not found. Install Java 21+ (OpenJDK recommended)."
  exit 1
fi
echo "[ok] Java: $(java -version 2>&1 | head -1)"

# --- Python check
if ! command -v python3 >/dev/null 2>&1; then
  echo "ERROR: python3 not found."
  exit 1
fi
echo "[ok] Python: $(python3 --version)"

# --- Ensure server jar + folders
SERVER_DIR="$ROOT/server"
mkdir -p "$SERVER_DIR/mods" "$SERVER_DIR/plugins" "$ROOT/config"

JAR="$SERVER_DIR/fabric-server-launch.jar"
if [[ ! -f "$JAR" ]]; then
  echo "[setup] Downloading Fabric server launcher for Minecraft 26.2..."
  curl -fsSL -o "$JAR" \
    "https://meta.fabricmc.net/v2/versions/loader/26.2/0.19.3/1.1.1/server/jar"
fi

FABRIC_API="$SERVER_DIR/mods/fabric-api-0.154.2+26.2.jar"
if [[ ! -f "$FABRIC_API" ]]; then
  echo "[setup] Downloading Fabric API for 26.2..."
  curl -fsSL -o "$FABRIC_API" \
    "https://cdn.modrinth.com/data/P7dR8mSH/versions/Kr4WG5mG/fabric-api-0.154.2%2B26.2.jar"
fi

if [[ ! -f "$SERVER_DIR/eula.txt" ]]; then
  printf 'eula=true\n' > "$SERVER_DIR/eula.txt"
fi

if [[ ! -f "$SERVER_DIR/server.properties" ]]; then
  cat > "$SERVER_DIR/server.properties" << 'EOF'
motd=Minecraft AI Chat Server 26.2 (Fabric)
gamemode=survival
difficulty=easy
max-players=20
online-mode=true
enable-command-block=true
enable-rcon=true
rcon.password=change_me
rcon.port=25575
server-port=25565
view-distance=10
simulation-distance=10
spawn-protection=16
EOF
fi

# --- Stop leftover Minecraft from previous runs (session.lock / second instance)
# Uses /proc (not pkill -f) so we never kill this start.sh by accident.
cleanup_stale_minecraft() {
  python3 - "$SERVER_DIR" <<'PY'
import os, signal, sys, time
from pathlib import Path
server = Path(sys.argv[1]).resolve()
killed = []
for entry in Path("/proc").iterdir():
    if not entry.name.isdigit():
        continue
    try:
        cmd = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode("utf-8", "ignore")
    except OSError:
        continue
    # Real JVM only (avoid killing shells that merely mention the jar name)
    if "fabric-server-launch" not in cmd:
        continue
    first = cmd.strip().split()[0] if cmd.strip() else ""
    if not first.endswith("java"):
        continue
    try:
        cwd = os.readlink(entry / "cwd")
    except OSError:
        cwd = ""
    if str(server) in cmd or cwd == str(server):
        pid = int(entry.name)
        try:
            os.kill(pid, signal.SIGTERM)
            killed.append(pid)
        except ProcessLookupError:
            pass
if killed:
    print(f"[setup] Stopping leftover Minecraft: {killed}")
    time.sleep(1.5)
    for pid in killed:
        try:
            os.kill(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
lock = server / "world" / "session.lock"
if lock.exists():
    lock.unlink(missing_ok=True)
    print("[ok] Cleared world/session.lock")
PY
}
cleanup_stale_minecraft

# --- Python venv + deps
VENV="$ROOT/.venv"
if [[ ! -d "$VENV" ]]; then
  echo "[setup] Creating Python virtualenv..."
  python3 -m venv "$VENV"
fi
# shellcheck disable=SC1091
source "$VENV/bin/activate"

deps_ok() {
  python3 - <<'PY' 2>/dev/null
import fastapi, uvicorn, openai, httpx, pydantic
print("ok")
PY
}

# pip breaks on ALL_PROXY=socks://... (needs socks5://). Install without proxy.
# Also skip reinstall when packages are already present.
if deps_ok >/dev/null; then
  echo "[ok] Python dependencies already installed"
else
  echo "[setup] Installing Python dependencies..."
  # shellcheck disable=SC2034
  (
    # Local env only for this subshell — do not leak to Minecraft/uvicorn
    unset ALL_PROXY all_proxy HTTP_PROXY HTTPS_PROXY http_proxy https_proxy \
          FTP_PROXY ftp_proxy 2>/dev/null || true
    # If user wants proxy, prefer socks5 form that requests understands
    if [[ -n "${PIP_PROXY:-}" ]]; then
      export ALL_PROXY="$PIP_PROXY" HTTPS_PROXY="$PIP_PROXY" HTTP_PROXY="$PIP_PROXY"
    fi
    pip install -q --disable-pip-version-check -r "$ROOT/web/requirements.txt"
  )
  if ! deps_ok >/dev/null; then
    echo "ERROR: Failed to install Python packages (fastapi/uvicorn/openai)."
    echo "  Tip: your shell has SOCKS proxy (ALL_PROXY=socks://...). Either:"
    echo "    unset ALL_PROXY all_proxy"
    echo "    # then: source .venv/bin/activate && pip install -r web/requirements.txt"
    echo "  Or install with socks5:// and PySocks, or without proxy."
    exit 1
  fi
  echo "[ok] Python dependencies installed"
fi

# --- Detect LAN IP
detect_ip() {
  local ip=""
  if command -v hostname >/dev/null 2>&1; then
    ip="$(hostname -I 2>/dev/null | awk '{print $1}')" || true
  fi
  if [[ -z "${ip:-}" ]]; then
    ip="$(python3 - <<'PY'
import socket
try:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.connect(("8.8.8.8", 80))
    print(s.getsockname()[0])
    s.close()
except Exception:
    print("127.0.0.1")
PY
)"
  fi
  echo "$ip"
}

LAN_IP="$(detect_ip)"

echo ""
echo "----------------------------------------------"
echo " Starting stack..."
echo "  Minecraft logs will stream below."
echo "  Mods folder:    $SERVER_DIR/mods"
echo "  Plugins folder: $SERVER_DIR/plugins"
echo ""
echo "  Web UI — open THIS first (always works on this PC):"
echo "    >>>  http://127.0.0.1:${WEB_PORT}  <<<"
echo ""
echo "  From other devices on LAN (if firewall allows):"
echo "    http://${LAN_IP}:${WEB_PORT}"
if [[ -n "${ALL_PROXY:-${all_proxy:-}}" ]]; then
  echo ""
  echo "  [warn] SOCKS/proxy is set in your environment (${ALL_PROXY:-$all_proxy})."
  echo "         Browsers/extensions may break LAN URLs (ERR_EMPTY_RESPONSE)."
  echo "         Use http://127.0.0.1:${WEB_PORT} or disable proxy for local net."
fi
echo ""
MC_PORT="$(awk -F= '/^server-port=/{print $2; exit}' "$SERVER_DIR/server.properties" 2>/dev/null || true)"
MC_PORT="${MC_PORT:-25565}"
echo "  Minecraft (игра) — Multiplayer → Direct Connection:"
echo "    >>>  127.0.0.1:${MC_PORT}  <<<"
echo "    LAN: ${LAN_IP}:${MC_PORT}"
echo "  Stop everything: Ctrl+C"
echo "----------------------------------------------"
echo ""

cd "$ROOT/web"
# Do not inherit broken socks:// proxy into the web process children needlessly
# (AI API calls can still use system proxy if socks5; local UI must stay direct).
exec python3 -m uvicorn app:app --host 0.0.0.0 --port "$WEB_PORT" --log-level info
