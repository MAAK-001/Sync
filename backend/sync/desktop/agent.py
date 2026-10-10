"""Sync Desktop Agent (Windows): joins calls and meetings automatically, always with an off switch.

    python -m sync.desktop.agent                         (the backend must be running: python -m sync.main)
    python -m sync.desktop.agent --server http://192.168.1.5:8765 --token <key>   (a shared Sync on another laptop)

* Signs in as you: on the laptop where Sync was set up this is automatic; elsewhere use the key from
  Settings → "Desktop agent on another laptop" (it is saved for next time).
* Watches which app is using the microphone (never the audio itself) to spot Phone Link calls, Zoom, Teams, Meet,
  WhatsApp…, asks the backend whether to join (your auto-listen rules), then shows a skip countdown on screen.
* When Sync starts listening, a window in the middle of the screen asks which project the call belongs to
  (or to start a new one). Sync never picks the project on its own.
* While listening, streams both sides (system audio + mic) to the backend over a WebSocket.
* Nudges appear on a see-through strip at the top of the screen, above every app (see overlay.py).
* Shortcuts: Ctrl+Alt+S skip the countdown / stop listening · Ctrl+Alt+N hide or show nudges ·
  Ctrl+Alt+L listen (when Sync asks first).
* Tray icon (red while Sync is listening): open Sync, stop or delete this call, Auto · Ask · Off, pause.
"""
from __future__ import annotations

import argparse
import asyncio
import ctypes
import ctypes.wintypes
import json
import logging
import threading
import webbrowser
from datetime import datetime, timedelta

import httpx
import websockets

from ..auth import load_agent_config, save_agent_config
from ..config import settings
from .detect import ActiveCall, active_calls
from .overlay import Overlay, app_name

log = logging.getLogger("sync.agent")
COLORS = {"idle": (120, 120, 120), "countdown": (240, 170, 0), "ask": (240, 170, 0), "live": (220, 30, 30),
          "off": (40, 40, 40)}
HOTKEYS = {1: 0x53, 2: 0x4E, 3: 0x4C}  # Ctrl+Alt+ S (skip/stop), N (nudges on/off), L (listen)
CALL_GONE_AFTER = 8  # seconds without the call before Sync stops listening
_instance = None  # the single-instance mutex (kept open while the agent runs)


def _only_instance() -> bool:
    """Two agents would both react to every call (two countdowns, two overlays). Allow one per Windows user."""
    global _instance
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = ctypes.wintypes.HANDLE
    _instance = kernel32.CreateMutexW(None, False, "Local\\SyncDesktopAgent")
    return ctypes.get_last_error() != 183  # ERROR_ALREADY_EXISTS


def _log_to_file() -> None:
    """The agent usually runs without a console: keep a small log next to Sync's data for troubleshooting."""
    from logging.handlers import RotatingFileHandler

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(settings.data_dir / "agent.log", maxBytes=1_000_000, backupCount=1, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    logging.getLogger("httpx").setLevel(logging.WARNING)  # one line per request is noise


def _icon_image(state: str):
    from PIL import Image, ImageDraw

    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((6, 6, 58, 58), fill=COLORS.get(state, COLORS["idle"]) + (255,))
    d.ellipse((22, 22, 42, 42), fill=(255, 255, 255, 230))
    return img


def _dpi_aware() -> None:
    """Crisp text on scaled displays (125 %, 150 %…). Must run before any window is created."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:  # noqa: BLE001
        try:
            ctypes.windll.user32.SetProcessDPIAware()
        except Exception:  # noqa: BLE001
            pass


class Agent:
    def __init__(self, server: str | None = None, token: str | None = None) -> None:
        saved = load_agent_config()
        self.server = (server or saved.get("server") or f"http://127.0.0.1:{settings.port}").rstrip("/")
        self.token = token or saved.get("token") or ""
        self.user_id: int | None = None
        self.state = "idle"
        self.call: ActiveCall | None = None
        self.session_id: int | None = None
        self.ignored: set[str] = set()  # apps to ignore until their current call ends
        self.skip = threading.Event()
        self.accept = threading.Event()
        self.stop = threading.Event()
        self.delete = False
        self.mode = "auto"
        self.icon = None
        self.loop: asyncio.AbstractEventLoop | None = None
        self.http = httpx.AsyncClient(base_url=self.server, timeout=30)
        self.signed_in = asyncio.Event()
        self.overlay = Overlay()

    @property
    def ws_base(self) -> str:
        return self.server.replace("http", "ws", 1)

    # ---------------------------------------------------------- signing in

    async def sign_in(self) -> None:
        """Wait until we can act as the user (on first run, until they create their profile in the app)."""
        told = False
        while True:
            if not self.token:  # first run: the server saves this laptop's key when the profile is created
                self.token = load_agent_config().get("token", "")
            if self.token:
                try:
                    r = await self.http.get("/me", headers={"X-Sync-Token": self.token})
                    if r.status_code == 200:
                        self.user_id = r.json()["id"]
                        self.overlay.owner_id = self.user_id
                        self.http.headers["X-Sync-Token"] = self.token
                        self.signed_in.set()
                        log.info("signed in to %s as %s", self.server, r.json()["name"])
                        return
                    if r.status_code == 401:
                        self.token = ""
                except httpx.HTTPError:
                    pass
            if not told:
                log.warning("waiting for Sync at %s: start it (python -m sync.main) and create your profile in the app",
                            self.server)
                told = True
            await asyncio.sleep(3)

    # ---------------------------------------------------------- tray

    def set_state(self, state: str, tip: str = "") -> None:
        self.state = state
        if self.icon is not None:
            self.icon.icon = _icon_image("off" if self.mode == "off" and state == "idle" else state)
            self.icon.title = f"Sync — {tip or state}"
            self.icon.update_menu()

    def _menu(self):
        import pystray

        def set_mode(mode):
            def _f(icon, item):
                self.mode = mode
                asyncio.run_coroutine_threadsafe(self.http.put("/me/autolisten", json={"mode": mode}), self.loop)
                self.overlay.notice(f"Auto-listen: {mode}")
                self.set_state(self.state)
            return _f

        def pause(icon, item):
            until = (datetime.now() + timedelta(hours=1)).isoformat(timespec="seconds")
            asyncio.run_coroutine_threadsafe(self.http.put("/me/autolisten", json={"paused_until": until}), self.loop)
            self.overlay.notice("Auto-listen paused for 1 hour")

        def stop_and_delete(icon, item):
            self.delete = True
            self.stop.set()

        M = pystray.MenuItem
        return pystray.Menu(
            M("Open Sync", lambda i, it: webbrowser.open(f"{self.server}/app/"), default=True),
            M(lambda item: f"Sync: {self.state}" + (f" · {app_name(self.call.app)}" if self.call else ""), None,
              enabled=False),
            M("Skip this call", lambda i, it: self.skip.set(), visible=lambda it: self.state in ("countdown", "ask")),
            M("Listen to this call", lambda i, it: self.accept.set(), visible=lambda it: self.state == "ask"),
            M("Stop listening (this call)", lambda i, it: self.stop.set(), visible=lambda it: self.state == "live"),
            M("Stop and delete this call", stop_and_delete, visible=lambda it: self.state == "live"),
            pystray.Menu.SEPARATOR,
            M("Show nudges on screen  (Ctrl+Alt+N)", lambda i, it: self.overlay.toggle(),
              checked=lambda it: not self.overlay.hidden),
            M("Auto-listen", pystray.Menu(
                M("Auto", set_mode("auto"), checked=lambda it: self.mode == "auto", radio=True),
                M("Ask every time", set_mode("ask"), checked=lambda it: self.mode == "ask", radio=True),
                M("Off", set_mode("off"), checked=lambda it: self.mode == "off", radio=True))),
            M("Pause for 1 hour", pause),
            M("Quit", lambda icon, it: self.quit()),
        )

    def hotkey(self) -> None:
        """Global shortcuts (they work in any app)."""
        user32 = ctypes.windll.user32
        for hid, vk in HOTKEYS.items():
            if not user32.RegisterHotKey(None, hid, 0x0002 | 0x0001 | 0x4000, vk):  # Ctrl+Alt, no auto-repeat
                log.warning("could not register Ctrl+Alt+%s (another app uses it)", chr(vk))
        msg = ctypes.wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) != 0:
            if msg.message != 0x0312:  # WM_HOTKEY
                continue
            if msg.wParam == 1:
                (self.skip if self.state in ("countdown", "ask") else self.stop).set()
            elif msg.wParam == 2:
                self.overlay.toggle()
                if self.icon is not None:
                    self.icon.update_menu()
            elif msg.wParam == 3 and self.state == "ask":
                self.accept.set()

    def quit(self) -> None:
        self.stop.set()
        self.overlay.quit()
        if self.icon is not None:
            self.icon.stop()

    # ---------------------------------------------------------- what the server says (nudges, project choice)

    async def follow_events(self) -> None:
        """Forward live events (nudges, wrap-up, on/off) to the overlay; ask for the project when a call starts."""
        await self.signed_in.wait()
        while True:
            try:
                async with websockets.connect(f"{self.ws_base}/ws/events?client=agent&token={self.token}") as ws:
                    async for raw in ws:
                        ev = json.loads(raw)
                        mine = ev.get("owner_id") == self.user_id
                        if ev.get("type") == "session.live" and mine and ev.get("project_id") is None:
                            await self._ask_project(ev)
                        elif ev.get("type") == "project.linked" and mine:
                            self.overlay.close_picker(ev["session_id"])
                        elif ev.get("type") == "ended" and ev.get("session_id") == self.session_id:
                            self.stop.set()  # stopped (or deleted) from the app: stop capturing too
                        self.overlay.event(ev)
            except (OSError, websockets.WebSocketException):
                await asyncio.sleep(2)  # backend not up yet (or restarting)

    async def _ask_project(self, ev: dict) -> None:
        try:
            projects = (await self.http.get("/projects")).json()
        except httpx.HTTPError:
            projects = []
        projects = [p for p in projects if p.get("status") == "active"]
        when = datetime.now().strftime("%H:%M")
        what = "Sample call · " + ev.get("title", "") if ev.get("app") == "sample" else (
            ev.get("caller") or app_name(ev.get("app", "")) or "Conversation")
        sid = ev["session_id"]

        def chosen(choice) -> None:  # Tk thread
            asyncio.run_coroutine_threadsafe(self._file(sid, choice), self.loop)

        self.overlay.picker(sid, f"{what} · {when}", projects, chosen)

    async def _file(self, sid: int, choice: dict | None) -> None:
        if choice is None:
            self.overlay.notice("No project yet. You can file this conversation later in the Sync app.")
            return
        body = {"new_project": True} if choice.get("new") else {"project_id": choice["project_id"]}
        try:
            r = await self.http.post(f"/sessions/{sid}/link", json=body)
            r.raise_for_status()
            name = r.json().get("name", "")
            text = f"New project “{name}” (rename it in the app)" if choice.get("new") else f"Filed under “{name}”"
            self.overlay.notice(text)
        except httpx.HTTPError as e:
            log.warning("could not file the conversation: %s", e)
            self.overlay.notice("Couldn't file this conversation. Choose its project in the Sync app.")

    # ---------------------------------------------------------- call handling

    async def watch(self) -> None:
        """Check about four times a second which app is using the microphone (never the audio itself)."""
        await self.sign_in()
        try:
            self.mode = (await self.http.get("/me/autolisten")).json().get("mode", "auto")
        except httpx.HTTPError:
            pass
        self.set_state("idle", "watching for calls")
        while True:
            calls = await asyncio.to_thread(active_calls)
            apps = {c.app for c in calls}
            self.ignored &= apps  # forget ignores once those calls end
            new = next((c for c in calls if c.app not in self.ignored), None)
            if new is not None and self.state == "idle":
                try:
                    await self.handle(new)
                except Exception:  # noqa: BLE001  (one failed call must not stop the watcher)
                    log.exception("call handling failed")
                    self.overlay.clear_gate()
                    self.call, self.session_id = None, None
                    self.set_state("idle")
            await asyncio.sleep(0.25)

    async def handle(self, call: ActiveCall) -> None:
        self.call = call
        self.skip.clear()
        self.accept.clear()
        self.stop.clear()
        self.delete = False
        try:
            r = await self.http.post("/calls/detected", json={"app": call.app, "title": call.title, "caller": call.caller})
            r.raise_for_status()
            r = r.json()
        except httpx.HTTPError as e:
            log.warning("backend unreachable: %s", e)
            self.ignored.add(call.app)
            self.call = None
            return
        if r["decision"] == "never":
            self.overlay.notice(f"{app_name(call.app)} call — Sync stays off ({r.get('reason', 'your rules')})")
            self.ignored.add(call.app)
            self.call = None
            return
        sid, countdown = r["session_id"], int(r.get("countdown", 5))
        if r["decision"] == "ask":
            self.set_state("ask", f"{app_name(call.app)}: listen to this call?")
            self.overlay.gate(call.app, countdown, ask=True)
            for _ in range(240):  # up to a minute
                if self.accept.is_set() or self.skip.is_set() or not await self._still_on(call):
                    break
                await asyncio.sleep(0.25)
            if not self.accept.is_set():
                await self._skip(sid, call)
                return
        else:
            self.set_state("countdown", f"joining {app_name(call.app)} in {countdown}s")
            self.overlay.gate(call.app, countdown)
            for _ in range(countdown * 10):
                if self.skip.is_set():
                    await self._skip(sid, call)
                    return
                await asyncio.sleep(0.1)
        self.overlay.clear_gate()
        await self.listen(sid, call)

    async def _skip(self, sid: int, call: ActiveCall) -> None:
        self.overlay.clear_gate()
        await self.http.post(f"/sessions/{sid}/skip")
        self.ignored.add(call.app)
        self.call = None
        self.overlay.notice("Skipped — Sync is off for this call and nothing was kept", kind="off")
        self.set_state("idle", "watching for calls")

    async def _still_on(self, call: ActiveCall) -> bool:
        return any(c.app == call.app for c in await asyncio.to_thread(active_calls))

    async def listen(self, sid: int, call: ActiveCall) -> None:
        from .capture import CallCapture

        # Open the audio and start the session at the same time; frames queue up until the connection is ready.
        started = asyncio.create_task(self.http.post(f"/sessions/{sid}/start", json={"audio": True}))
        try:
            capture = await asyncio.to_thread(CallCapture)
        except Exception:
            log.exception("could not open the microphone / speakers")
            await started
            await self.http.post(f"/sessions/{sid}/end", json={"delete": True})
            self.overlay.notice("Sync couldn't open the microphone or speakers, so it isn't listening to this call",
                                kind="off", seconds=8)
            self.ignored.add(call.app)
            self.call = None
            self.set_state("idle", "watching for calls")
            return
        (await started).raise_for_status()
        self.session_id = sid
        self.set_state("live", f"listening · {app_name(call.app)}")
        log.info("listening to %s (conversation %s)", call.app, sid)
        reason = "stopped by you"
        try:
            while not self.stop.is_set():
                try:
                    async with websockets.connect(f"{self.ws_base}/ws/sessions/{sid}?token={self.token}",
                                                  max_size=None) as ws:
                        reason = await self._stream(ws, capture, call)
                    if reason:
                        break
                    problem = "the server closed the connection"
                except (OSError, websockets.WebSocketException) as e:
                    problem = str(e) or type(e).__name__
                # The connection dropped. Keep listening unless the conversation or the call is really over.
                if self.stop.is_set():
                    break
                if not await self._session_live(sid):
                    reason = "the conversation was ended in the app"
                    break
                log.warning("connection to Sync dropped (%s); reconnecting", problem)
                await asyncio.sleep(1)  # audio keeps queueing meanwhile; nothing is lost
                if not await self._still_on(call) and await self._call_gone(call):
                    reason = "the call ended"
                    break
        finally:
            log.info("stopped listening (conversation %s): %s", sid, reason)
            await asyncio.to_thread(capture.close)
            try:
                await self.http.post(f"/sessions/{sid}/end", json={"delete": self.delete})
            except httpx.HTTPError as e:
                log.warning("could not end conversation %s: %s", sid, e)
            if self.stop.is_set():
                self.ignored.add(call.app)  # stopped by hand: don't rejoin this same call
            self.session_id = None
            self.call = None
            self.set_state("idle", "watching for calls")

    async def _stream(self, ws, capture, call: ActiveCall) -> str:
        """Send audio until the call ends or you stop. Returns why it stopped ("" = the connection dropped)."""
        drain = asyncio.create_task(self._discard(ws))  # live events reach the overlay via /ws/events
        loop = asyncio.get_running_loop()
        try:
            gone_since = None
            next_check = loop.time() + 1
            while not self.stop.is_set():
                while not capture.out.empty():
                    await ws.send(capture.out.get_nowait())
                if drain.done():  # the server closed this conversation's connection
                    return ""
                await asyncio.sleep(0.05)
                if loop.time() >= next_check:  # once a second: has the call ended?
                    next_check = loop.time() + 1
                    on = await self._still_on(call)
                    gone_since = None if on else (gone_since or loop.time())
                    # Call-state signals can blink (devices switching, Bluetooth hiccups): wait before deciding.
                    if gone_since and loop.time() - gone_since >= CALL_GONE_AFTER:
                        return "the call ended"
            return "stopped by you"
        finally:
            drain.cancel()

    async def _call_gone(self, call: ActiveCall) -> bool:
        for _ in range(CALL_GONE_AFTER):
            await asyncio.sleep(1)
            if await self._still_on(call):
                return False
        return True

    async def _session_live(self, sid: int) -> bool:
        try:
            r = await self.http.get(f"/sessions/{sid}")
            return r.status_code == 200 and r.json().get("status") == "live"
        except httpx.HTTPError:
            return True  # the server is restarting: keep the audio and try again

    @staticmethod
    async def _discard(ws) -> None:
        try:
            async for _ in ws:
                pass
        except websockets.ConnectionClosed:
            pass

    # ---------------------------------------------------------- run

    def run(self) -> None:
        import pystray

        _dpi_aware()
        self.loop = asyncio.new_event_loop()
        threading.Thread(target=self.loop.run_forever, daemon=True).start()
        threading.Thread(target=self.hotkey, daemon=True).start()
        asyncio.run_coroutine_threadsafe(self.watch(), self.loop)
        asyncio.run_coroutine_threadsafe(self.follow_events(), self.loop)
        self.icon = pystray.Icon("sync", _icon_image("idle"), "Sync — watching for calls", self._menu())
        threading.Thread(target=self.icon.run, daemon=True).start()
        self.overlay.run()  # Tk owns the main thread until Quit


def run() -> None:
    ap = argparse.ArgumentParser(description="Sync desktop agent")
    ap.add_argument("--server", help="Sync's address, e.g. http://192.168.1.5:8765 (default: this laptop)")
    ap.add_argument("--token", help="your desktop-agent key from Settings (saved for next time)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    _log_to_file()
    if not _only_instance():
        log.error("the Sync desktop agent is already running (see the tray icon); not starting a second one")
        ctypes.windll.user32.MessageBoxW(None, "Sync's desktop agent is already running. Look for its icon in the tray.",
                                         "Sync", 0x40)
        return
    if args.token:
        save_agent_config((args.server or f"http://127.0.0.1:{settings.port}").rstrip("/"), args.token)
    Agent(args.server, args.token).run()


if __name__ == "__main__":
    run()
