"""Which call or meeting app is using the microphone right now? (Windows)

Windows records microphone use per app in the registry (CapabilityAccessManager ConsentStore):
LastUsedTimeStop == 0 means "in use now". A crashed app can leave a stale 0, so we also require the
app's process to be running. Window titles add context (meeting name, caller).
"""
from __future__ import annotations

import re
from dataclasses import dataclass

BASE = r"SOFTWARE\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone"

# app label -> (registry key fragments, process names)
KNOWN: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    "phone link": (("microsoft.yourphone",), ("phoneexperiencehost.exe", "yourphone.exe")),
    "zoom": (("zoom.exe",), ("zoom.exe",)),
    "teams": (("msteams", "teams.exe", "ms-teams.exe"), ("ms-teams.exe", "teams.exe")),
    "webex": (("atmgr.exe", "webex", "ciscocollabhost.exe"), ("atmgr.exe", "ciscocollabhost.exe", "webexmta.exe")),
    "whatsapp": (("whatsapp",), ("whatsapp.exe", "whatsapp.root.exe")),
    "slack": (("slack.exe",), ("slack.exe",)),
    "discord": (("discord.exe",), ("discord.exe",)),
    "skype": (("skype",), ("skype.exe",)),
}
BROWSERS = ("chrome.exe", "msedge.exe", "brave.exe", "firefox.exe", "opera.exe", "vivaldi.exe")
MEETING_TITLE = re.compile(r"(\bMeet\b|Google Meet|Zoom|Microsoft Teams|\bTeams\b|Jitsi|Webex|Whereby|Huddle)", re.I)


@dataclass
class ActiveCall:
    app: str
    title: str = ""
    caller: str = ""


def mic_users() -> list[str]:
    """Registry keys (lower-case) of apps whose microphone use has not stopped."""
    import winreg

    active: list[str] = []

    def walk(key, prefix=""):
        i = 0
        while True:
            try:
                name = winreg.EnumKey(key, i)
            except OSError:
                return
            i += 1
            try:
                with winreg.OpenKey(key, name) as sub:
                    if name == "NonPackaged":
                        walk(sub, "np:")
                        continue
                    try:
                        start, _ = winreg.QueryValueEx(sub, "LastUsedTimeStart")
                        stop, _ = winreg.QueryValueEx(sub, "LastUsedTimeStop")
                    except OSError:
                        continue
                    if start and stop == 0:
                        active.append(prefix + name.lower())
            except OSError:
                continue

    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, BASE) as root:
        walk(root)
    return active


def hands_free_call_active() -> bool:
    """Is a Bluetooth hands-free call (a phone call taken on this PC) in progress?

    Phone Link doesn't open the microphone itself: Windows' Bluetooth audio service (svchost.exe) streams the
    caller's voice into the default speakers as a session named "Microphone (<phone> Hands-Free HF Audio)".
    That session is only *active* while a call is up, so it's a reliable signal and it needs no extra permissions.
    """
    import comtypes
    from comtypes import CLSCTX_ALL
    from pycaw.pycaw import AudioUtilities, IAudioSessionControl2, IAudioSessionManager2

    comtypes.CoInitialize()  # this runs in a worker thread
    try:
        endpoints = AudioUtilities.GetDeviceEnumerator().EnumAudioEndpoints(0, 1)  # playback, active
        for i in range(endpoints.GetCount()):
            manager = endpoints.Item(i).Activate(IAudioSessionManager2._iid_, CLSCTX_ALL, None)
            sessions = manager.QueryInterface(IAudioSessionManager2).GetSessionEnumerator()
            for j in range(sessions.GetCount()):
                ctl = sessions.GetSession(j).QueryInterface(IAudioSessionControl2)
                if ctl.GetState() == 1 and "hands-free" in ctl.GetDisplayName().lower():
                    return True
        return False
    except Exception:  # noqa: BLE001  (audio devices can change while we look)
        return False
    finally:
        comtypes.CoUninitialize()


def _windows() -> list[tuple[str, str]]:
    """(process name, window title) of visible top-level windows."""
    import psutil
    import win32gui
    import win32process

    out: list[tuple[str, str]] = []

    def cb(hwnd, _):
        if win32gui.IsWindowVisible(hwnd):
            title = win32gui.GetWindowText(hwnd)
            if title:
                try:
                    _, pid = win32process.GetWindowThreadProcessId(hwnd)
                    out.append((psutil.Process(pid).name().lower(), title))
                except Exception:  # noqa: BLE001  (process went away)
                    pass

    win32gui.EnumWindows(cb, None)
    return out


def active_calls() -> list[ActiveCall]:
    import psutil

    users = mic_users()
    running = {p.info["name"].lower() for p in psutil.process_iter(["name"]) if p.info["name"]}
    windows = _windows()
    calls: list[ActiveCall] = []
    for app, (keys, procs) in KNOWN.items():
        if any(k in u for u in users for k in keys) and running & set(procs):
            titles = [t for p, t in windows if p in procs]
            title = next((t for t in titles if t.lower() not in (app, "phone link")), titles[0] if titles else "")
            caller = title if app == "phone link" and title.lower() != "phone link" else ""
            calls.append(ActiveCall(app=app, title=title, caller=caller))
    for browser in BROWSERS:
        if any(u.endswith(browser) for u in users) and browser in running:
            title = next((t for p, t in windows if p == browser and MEETING_TITLE.search(t)), "")
            if title:
                app = "meet" if "meet" in title.lower() else MEETING_TITLE.search(title).group(1).lower()
                calls.append(ActiveCall(app=app, title=title))
    # A phone call taken on this PC through Phone Link never shows up as a microphone user (see above).
    # Only when no other call app explains the hands-free audio (a Bluetooth headset in Zoom also looks like this).
    if not calls and running & set(KNOWN["phone link"][1]) and hands_free_call_active():
        calls.append(ActiveCall(app="phone link", title="Phone call"))
    return calls
