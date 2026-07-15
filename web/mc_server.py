"""Minecraft server process manager: start, stop, console I/O, log ring buffer."""

from __future__ import annotations

import os
import re
import signal
import subprocess
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Deque, List, Optional


ROOT = Path(__file__).resolve().parent.parent
SERVER_DIR = ROOT / "server"
DEFAULT_JAR = "fabric-server-launch.jar"
READY_PATTERNS = (
    re.compile(r"Done \([\d.]+s\)! For help", re.I),
    re.compile(r"Done \(.*\)!", re.I),
)


@dataclass
class ServerState:
    running: bool = False
    ready: bool = False
    pid: Optional[int] = None
    started_at: Optional[float] = None
    last_error: Optional[str] = None
    jar: str = DEFAULT_JAR


class MinecraftServerManager:
    def __init__(
        self,
        server_dir: Path = SERVER_DIR,
        max_log_lines: int = 5000,
        java_bin: str = "java",
        memory: str = "2G",
    ) -> None:
        self.server_dir = Path(server_dir)
        self.java_bin = java_bin
        self.memory = memory
        self._logs: Deque[str] = deque(maxlen=max_log_lines)
        self._lock = threading.RLock()
        self._proc: Optional[subprocess.Popen[str]] = None
        self._reader: Optional[threading.Thread] = None
        self._state = ServerState()
        self._listeners: List[Callable[[str], None]] = []
        self._ready_event = threading.Event()

    # ------------------------------------------------------------------ state
    def status(self) -> dict:
        with self._lock:
            alive = self._proc is not None and self._proc.poll() is None
            if not alive and self._state.running:
                self._state.running = False
                self._state.ready = False
                self._state.pid = None
            return {
                "running": bool(alive),
                "ready": self._state.ready and bool(alive),
                "pid": self._proc.pid if alive and self._proc else None,
                "started_at": self._state.started_at,
                "last_error": self._state.last_error,
                "jar": self._state.jar,
                "server_dir": str(self.server_dir),
                "log_lines": len(self._logs),
            }

    def get_logs(self, lines: int = 100) -> List[str]:
        lines = max(1, min(int(lines), 2000))
        with self._lock:
            buf = list(self._logs)
        return buf[-lines:]

    def add_log_listener(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            self._listeners.append(callback)

    def remove_log_listener(self, callback: Callable[[str], None]) -> None:
        with self._lock:
            if callback in self._listeners:
                self._listeners.remove(callback)

    def _free_world_lock(self) -> None:
        """Stop orphan Java processes for this server dir and drop stale session.lock."""
        lock_path = self.server_dir / "world" / "session.lock"
        server_dir = str(self.server_dir.resolve())
        orphan_pids: List[int] = []
        proc_root = Path("/proc")
        if proc_root.is_dir():
            for entry in proc_root.iterdir():
                if not entry.name.isdigit():
                    continue
                try:
                    cmdline = (entry / "cmdline").read_bytes().replace(b"\x00", b" ").decode(
                        "utf-8", "ignore"
                    )
                    # Only real JVM server processes — never bash/python wrappers
                    if "fabric-server-launch" not in cmdline:
                        continue
                    first = cmdline.strip().split()[0] if cmdline.strip() else ""
                    if not first.endswith("java"):
                        continue
                    cwd = ""
                    try:
                        cwd = os.readlink(entry / "cwd")
                    except OSError:
                        pass
                    if server_dir in cmdline or cwd == server_dir or cwd.startswith(server_dir + "/"):
                        orphan_pids.append(int(entry.name))
                except (OSError, ValueError):
                    continue

        # Don't kill our own managed child twice later — only external orphans
        own = self._proc.pid if self._proc is not None else None
        for pid in orphan_pids:
            if own is not None and pid == own:
                continue
            try:
                self._append_log(f"[bridge] Stopping orphan Minecraft pid={pid}")
                os.kill(pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            except PermissionError as exc:
                self._append_log(f"[bridge] Cannot stop pid={pid}: {exc}")

        if orphan_pids:
            time.sleep(1.5)
            for pid in orphan_pids:
                if own is not None and pid == own:
                    continue
                try:
                    os.kill(pid, 0)
                except ProcessLookupError:
                    continue
                try:
                    os.kill(pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        if lock_path.exists():
            try:
                lock_path.unlink()
                self._append_log("[bridge] Removed world/session.lock")
            except OSError as exc:
                self._append_log(f"[bridge] Could not remove session.lock: {exc}")

    # ----------------------------------------------------------------- control
    def start(self, jar: str = DEFAULT_JAR) -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                return {"ok": True, "message": "Server already running", **self.status()}

            jar_path = self.server_dir / jar
            if not jar_path.exists():
                msg = f"Server jar not found: {jar_path}"
                self._state.last_error = msg
                return {"ok": False, "message": msg}

            # Ensure eula accepted
            eula = self.server_dir / "eula.txt"
            if not eula.exists() or "eula=true" not in eula.read_text(encoding="utf-8", errors="ignore").lower():
                eula.write_text("eula=true\n", encoding="utf-8")

            # Previous Ctrl+C can leave Java alive → session.lock error on next start
            self._free_world_lock()

            self._ready_event.clear()
            self._state = ServerState(running=True, ready=False, jar=jar, started_at=time.time())
            self._append_log(f"[bridge] Starting Minecraft server: {jar}")

            cmd = [
                self.java_bin,
                f"-Xms{self.memory}",
                f"-Xmx{self.memory}",
                "-jar",
                jar,
                "nogui",
            ]
            try:
                self._proc = subprocess.Popen(
                    cmd,
                    cwd=str(self.server_dir),
                    stdin=subprocess.PIPE,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    universal_newlines=True,
                    preexec_fn=os.setsid if os.name != "nt" else None,
                )
            except Exception as exc:  # noqa: BLE001
                self._state.running = False
                self._state.last_error = str(exc)
                self._append_log(f"[bridge] Failed to start: {exc}")
                return {"ok": False, "message": str(exc)}

            self._state.pid = self._proc.pid
            self._reader = threading.Thread(target=self._read_stdout, daemon=True)
            self._reader.start()
            return {"ok": True, "message": "Server starting", **self.status()}

    def stop(self, force: bool = False, timeout: float = 30.0) -> dict:
        with self._lock:
            proc = self._proc
        if proc is None or proc.poll() is not None:
            with self._lock:
                self._state.running = False
                self._state.ready = False
                self._state.pid = None
            return {"ok": True, "message": "Server not running"}

        self._append_log("[bridge] Stopping server (save-all + stop)...")
        try:
            self.send_command("save-all flush")
            time.sleep(0.5)
            self.send_command("stop")
        except Exception:
            pass

        deadline = time.time() + timeout
        while time.time() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.2)

        if proc.poll() is None:
            if force:
                self._append_log("[bridge] Force killing server process group...")
                try:
                    if os.name != "nt" and proc.pid:
                        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                    else:
                        proc.kill()
                except Exception as exc:  # noqa: BLE001
                    return {"ok": False, "message": f"Force kill failed: {exc}"}
            else:
                return {"ok": False, "message": "Server did not stop in time; try force=true"}

        with self._lock:
            self._proc = None
            self._state.running = False
            self._state.ready = False
            self._state.pid = None
        self._ready_event.clear()
        self._append_log("[bridge] Server stopped")
        return {"ok": True, "message": "Server stopped"}

    def send_command(self, command: str) -> dict:
        command = (command or "").strip()
        if command.startswith("/"):
            command = command[1:].strip()
        if not command:
            return {"ok": False, "message": "Empty command"}

        with self._lock:
            proc = self._proc
            ready = self._state.ready

        if proc is None or proc.poll() is not None:
            return {"ok": False, "message": "Server is not running"}
        if proc.stdin is None:
            return {"ok": False, "message": "Server stdin is not available"}

        try:
            self._append_log(f"[bridge] > {command}")
            proc.stdin.write(command + "\n")
            proc.stdin.flush()
            return {
                "ok": True,
                "message": "Command sent",
                "command": command,
                "server_ready": ready,
            }
        except Exception as exc:  # noqa: BLE001
            self._state.last_error = str(exc)
            return {"ok": False, "message": str(exc)}

    def wait_until_ready(self, timeout: float = 300.0) -> bool:
        return self._ready_event.wait(timeout=timeout)

    def wait_for_console(self, pattern: str, timeout: float = 15.0) -> dict:
        try:
            regex = re.compile(pattern, re.I)
        except re.error:
            # treat as literal substring
            regex = re.compile(re.escape(pattern), re.I)

        deadline = time.time() + max(0.5, float(timeout))
        # snapshot current length to prefer new lines, but also scan recent
        with self._lock:
            start_idx = max(0, len(self._logs) - 50)

        while time.time() < deadline:
            with self._lock:
                snapshot = list(self._logs)
            for line in snapshot[start_idx:]:
                if regex.search(line):
                    return {"ok": True, "matched": True, "line": line}
            start_idx = len(snapshot)
            time.sleep(0.15)

        return {
            "ok": True,
            "matched": False,
            "line": None,
            "message": f"No match for {pattern!r} within {timeout}s",
        }

    # ----------------------------------------------------------------- internal
    def _read_stdout(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        try:
            for line in proc.stdout:
                line = line.rstrip("\n")
                self._append_log(line)
                if not self._state.ready:
                    for pat in READY_PATTERNS:
                        if pat.search(line):
                            self._state.ready = True
                            self._ready_event.set()
                            self._append_log("[bridge] Server is READY")
                            break
        except Exception as exc:  # noqa: BLE001
            self._append_log(f"[bridge] Reader error: {exc}")
        finally:
            code = proc.poll()
            self._append_log(f"[bridge] Process exited with code {code}")
            with self._lock:
                self._state.running = False
                self._state.ready = False
                self._state.pid = None
            self._ready_event.clear()

    def _append_log(self, line: str) -> None:
        # Also mirror to real stdout so start.sh users see MC logs
        print(line, flush=True)
        with self._lock:
            self._logs.append(line)
            listeners = list(self._listeners)
        for cb in listeners:
            try:
                cb(line)
            except Exception:
                pass


# singleton used by the web app
manager = MinecraftServerManager(
    memory=os.environ.get("MC_MEMORY", "2G"),
    java_bin=os.environ.get("JAVA_BIN", "java"),
)
