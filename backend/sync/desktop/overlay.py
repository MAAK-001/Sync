"""On-screen nudges (Windows): a see-through strip across the top-centre of the screen, above every app.

The window itself is invisible and clicks pass straight through it, so whatever you are doing stays usable. Only
the cards are drawn: the call gate ("Sync joins in 5 s"), Sync on/off notices, nudges and the wrap-up card.
Nudges vanish on their own after a few seconds. Ctrl+Alt+N hides or shows nudges (the call gate and the on/off
notices always show, so you always know when Sync is listening).

When a call starts without a project, a small window in the middle of the screen asks which project it belongs
to (or to start a new one). That choice is always the user's: Sync never guesses.

Tk is used because it ships with Python. It must run on the main thread; other threads talk to it through
``Overlay.post``.
"""
from __future__ import annotations

import ctypes
import queue
import re
import time
import tkinter as tk
from dataclasses import dataclass, field

KEY = "#010203"  # colour key: pixels of exactly this colour are fully transparent and let clicks through
# Sync's design theme (the same tokens as the app: light, calm, green accent)
WHITE = "#ffffff"
TEXT = "#19201C"
MUTED = "#6D746E"
LINE = "#E3E7E1"
PANEL = "#F5F6F3"
GREEN = "#28644F"
GREEN_SOFT = "#EAF2ED"
GREEN_LINE = "#BFD6C8"
AMBER = "#94601E"
AMBER_SOFT = "#FBF4E7"
AMBER_LINE = "#EAD7B3"
RED = "#D92D20"
BLACK = "#19201C"

# kind -> (card fill, border, label colour, label)
STYLE = {
    "caution": (AMBER_SOFT, AMBER_LINE, AMBER, "CAUTION"),
    "confirm": (WHITE, LINE, GREEN, "CONFIRM"),
    "ask": (WHITE, LINE, GREEN, "ASK"),
    "fact": (GREEN_SOFT, GREEN_LINE, GREEN, "ALREADY KNOWN"),
    "answer": (GREEN_SOFT, GREEN_LINE, GREEN, "ANSWER"),
    "tip": (WHITE, LINE, GREEN, "TIP"),
    "gate": (WHITE, AMBER_LINE, AMBER, "CALL DETECTED"),
    "wrapup": (PANEL, LINE, MUTED, "BEFORE YOU FINISH · CLOSE THE LOOP"),
    "on": (WHITE, LINE, RED, "●  SYNC IS LISTENING · VISIBLE ONLY TO YOU"),
    "off": (WHITE, LINE, GREEN, "SYNC STOPPED LISTENING"),
    "info": (WHITE, LINE, MUTED, "SYNC"),
}
_PREFIX = re.compile(r"^(?:Ask|Confirm|Caution|Known|Answer):\s*", re.I)
_APP_NAMES = {"phone link": "Phone Link", "zoom": "Zoom", "teams": "Teams", "meet": "Google Meet", "webex": "Webex",
              "whatsapp": "WhatsApp", "slack": "Slack", "discord": "Discord"}

GWL_EXSTYLE = -20
WS_EX_TOPMOST, WS_EX_TRANSPARENT, WS_EX_TOOLWINDOW = 0x8, 0x20, 0x80
WS_EX_LAYERED, WS_EX_NOACTIVATE = 0x80000, 0x8000000
SWP_FLAGS = 0x0001 | 0x0002 | 0x0010  # no size, no move, no activate


def app_name(app: str) -> str:
    return _APP_NAMES.get(app.lower(), app.title())


@dataclass
class Card:
    key: str
    kind: str
    text: str
    note: str = ""
    lines: list[str] = field(default_factory=list)
    app: str = ""  # the gate's app, for its live countdown
    ttl: float | None = 20.0  # seconds on screen (None: until removed)
    born: float = field(default_factory=time.monotonic)
    session_id: int | None = None
    nudge_id: int | None = None

    @property
    def left(self) -> float:
        return 1.0 if self.ttl is None else max(0.0, 1 - (time.monotonic() - self.born) / self.ttl)

    @property
    def is_nudge(self) -> bool:
        return self.kind in ("caution", "confirm", "ask", "fact", "answer", "tip", "wrapup")


class Overlay:
    MAX_NUDGES = 2  # newest first; older ones make room
    MAX_CARDS = 3  # keep the strip small: it sits over your work

    def __init__(self) -> None:
        self.inbox: queue.Queue = queue.Queue()
        self.cards: list[Card] = []
        self.hidden = False
        self.owner_id: int | None = None  # the signed-in user: the overlay ignores teammates' conversations
        self.root: tk.Tk | None = None
        self._picker: tk.Toplevel | None = None
        self._picker_sid: int | None = None

    # ------------------------------------------------------------ thread-safe API

    def post(self, kind: str, **data) -> None:
        self.inbox.put((kind, data))

    def gate(self, app: str, seconds: int, ask: bool = False) -> None:
        self.post("gate", app=app, seconds=seconds, ask=ask)

    def clear_gate(self) -> None:
        self.post("clear_gate")

    def notice(self, text: str, kind: str = "info", seconds: float = 4.0) -> None:
        self.post("notice", text=text, style=kind, seconds=seconds)

    def event(self, ev: dict) -> None:
        self.post("event", ev=ev)

    def toggle(self) -> None:
        self.post("toggle")

    def picker(self, session_id: int, title: str, projects: list[dict], on_choice) -> None:
        """Ask which project a conversation belongs to. ``on_choice`` gets {"project_id", "name"}, {"new": True}
        or None ("decide later"); it is called on the Tk thread."""
        self.post("picker", session_id=session_id, title=title, projects=projects, on_choice=on_choice)

    def close_picker(self, session_id: int) -> None:
        self.post("close_picker", session_id=session_id)

    def quit(self) -> None:
        self.post("quit")

    # ------------------------------------------------------------ handling (Tk thread)

    def _handle(self, kind: str, data: dict) -> None:
        if kind == "gate":
            self._drop(lambda c: c.kind == "gate")
            app = app_name(data["app"])
            if data["ask"]:
                card = Card("gate", "gate", f"{app} — listen to this call?",
                            note="Ctrl+Alt+L  listen     Ctrl+Alt+S  skip", ttl=60)
            else:
                card = Card("gate", "gate", "", note="Ctrl+Alt+S  skip (nothing is kept)", app=app,
                            ttl=float(data["seconds"]))
            self.cards.append(card)
        elif kind == "clear_gate":
            self._drop(lambda c: c.kind == "gate")
        elif kind == "notice":
            self._drop(lambda c: c.kind in ("on", "off", "info"))
            self.cards.append(Card("notice", data["style"], data["text"], ttl=data["seconds"]))
        elif kind == "toggle":
            self.hidden = not self.hidden
            self._handle("notice", {"text": "Nudges hidden — Ctrl+Alt+N to show them again" if self.hidden
                                    else "Nudges are back on screen", "style": "info", "seconds": 2.5})
        elif kind == "event":
            self._on_event(data["ev"])
        elif kind == "picker":
            self._open_picker(**data)
        elif kind == "close_picker":
            if self._picker_sid == data["session_id"]:
                self._close_picker()
        elif kind == "quit" and self.root is not None:
            self.root.destroy()
            self.root = None

    def _on_event(self, ev: dict) -> None:
        t, sid = ev.get("type"), ev.get("session_id")
        if self.owner_id is not None and ev.get("owner_id") not in (None, self.owner_id):
            return  # a teammate's conversation: their overlay, not this one
        if t == "nudge":
            k = ev.get("kind", "ask")
            text = _PREFIX.sub("", ev.get("text", "")).strip()
            text = text[:1].upper() + text[1:]
            ttl = max(float(ev.get("ttl", 20)), 30.0 if k == "caution" else 20.0)
            self.cards.append(Card(f"n{ev.get('id')}", k if k in STYLE else "ask", text, note=ev.get("why", ""),
                                   ttl=ttl, session_id=sid, nudge_id=ev.get("id")))
            nudges = [c for c in self.cards if c.kind in ("caution", "confirm", "ask", "fact", "answer", "tip")]
            for old in nudges[:-self.MAX_NUDGES]:
                self.cards.remove(old)
        elif t == "nudge.outcome" and ev.get("outcome") in ("used", "dismissed"):
            self._drop(lambda c: c.nudge_id == ev.get("id"))  # already acted on
        elif t == "wrapup":
            lines = _wrapup_lines(ev)
            self._drop(lambda c: c.kind == "wrapup" and c.session_id == sid)
            if lines:
                self.cards.append(Card(f"w{sid}", "wrapup", "Still open as the call closes:", lines=lines,
                                       ttl=45, session_id=sid))
        elif t == "session.live":
            app = ev.get("app", "")
            where = f"Sample call — {ev.get('title', '')}" if app == "sample" else (app_name(app) if app else "")
            self._handle("notice", {"text": f"Listening{' · ' + where if where else ''}", "style": "on",
                                    "seconds": 4.0})
        elif t == "ended":
            self._drop(lambda c: c.session_id == sid)
            if self._picker_sid == sid:
                self._close_picker()
            text = "This conversation was deleted" if ev.get("deleted") else "Your recap is being prepared"
            self._handle("notice", {"text": text, "style": "off", "seconds": 4.0})

    def _drop(self, pred) -> None:
        self.cards = [c for c in self.cards if not pred(c)]

    # ------------------------------------------------------------ "which project?" window

    def _open_picker(self, session_id: int, title: str, projects: list[dict], on_choice) -> None:
        if self.root is None:
            return
        self._close_picker()
        s = self.s
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=WHITE, highlightthickness=max(1, int(s)), highlightbackground=LINE, highlightcolor=LINE)
        w, h = int(580 * s), int(520 * s)
        sw, sh = self.root.winfo_screenwidth(), self.root.winfo_screenheight()
        win.geometry(f"{w}x{h}+{(sw - w) // 2}+{(sh - h) // 2}")
        pad = int(28 * s)
        box = tk.Frame(win, bg=WHITE)
        box.pack(fill="both", expand=True, padx=pad, pady=pad)
        head = tk.Frame(box, bg=WHITE)
        head.pack(fill="x")
        size = int(24 * s)
        logo = tk.Canvas(head, width=size, height=size, bg=WHITE, highlightthickness=0)
        _rounded(logo, 0, 0, size - 1, size - 1, int(6 * s), fill=BLACK)
        for x, y0, y1 in ((6, 10, 14), (9, 7, 17), (12, 5, 19), (15, 8, 16), (18, 10, 14)):
            logo.create_line(x * s, y0 * s, x * s, y1 * s, fill=WHITE, width=max(1, int(1.8 * s)), capstyle="round")
        logo.pack(side="left")
        tk.Label(head, text="  WHICH PROJECT IS THIS?  ·  VISIBLE ONLY TO YOU", fg=MUTED, bg=WHITE,
                 font=self.fonts["label"]).pack(side="left")
        tk.Label(box, text=title, fg=TEXT, bg=WHITE, font=("Segoe UI Semibold", 18), justify="left",
                 wraplength=w - 2 * pad).pack(anchor="w", pady=(int(14 * s), int(4 * s)))
        tk.Label(box, text="Sync is listening. Choose the project so it can use everything you already know. "
                           "Sync never guesses.", fg=MUTED, bg=WHITE, font=self.fonts["note"], justify="left",
                 wraplength=w - 2 * pad).pack(anchor="w")
        query = tk.StringVar()
        border = tk.Frame(box, bg=LINE)  # 1px borders: a frame around the field
        border.pack(fill="x", pady=(int(18 * s), int(8 * s)))
        row = tk.Frame(border, bg=WHITE)
        row.pack(fill="x", padx=1, pady=1)
        tk.Label(row, text="Search", fg=MUTED, bg=WHITE, font=self.fonts["note"]).pack(side="left", padx=(int(12 * s), 0))
        entry = tk.Entry(row, textvariable=query, bg=WHITE, fg=TEXT, insertbackground=TEXT, relief="flat",
                         font=("Segoe UI", 12), highlightthickness=0)
        entry.pack(side="left", fill="x", expand=True, padx=int(8 * s), ipady=int(7 * s))
        lborder = tk.Frame(box, bg=LINE)
        lborder.pack(fill="both", expand=True)
        listbox = tk.Listbox(lborder, bg=WHITE, fg=TEXT, selectbackground=GREEN_SOFT, selectforeground=GREEN,
                             relief="flat", highlightthickness=0, font=("Segoe UI", 12), activestyle="none",
                             exportselection=False, bd=0)
        listbox.pack(fill="both", expand=True, padx=1, pady=1)
        shown: list[dict] = []

        def refill(*_):
            q = query.get().strip().lower()
            shown[:] = [x for x in projects if q in x["name"].lower()]
            listbox.delete(0, "end")
            for x in shown:
                calls = x.get("sessions", 0)
                listbox.insert("end", f"   {x['name']}" + (f"   ·   {calls} conversation{'s' if calls != 1 else ''}" if calls else ""))
            if shown:
                listbox.selection_set(0)
            elif not projects:
                listbox.insert("end", "   No projects yet. Start a new one.")

        def choose(choice):
            self._close_picker()
            on_choice(choice)

        def use(*_):
            sel = listbox.curselection()
            if shown and sel:
                x = shown[sel[0]]
                choose({"project_id": x["id"], "name": x["name"]})

        def move(step):
            sel = listbox.curselection()
            if shown:
                i = max(0, min(len(shown) - 1, (sel[0] if sel else -1) + step))
                listbox.selection_clear(0, "end")
                listbox.selection_set(i)
                listbox.see(i)
            return "break"

        buttons = tk.Frame(box, bg=WHITE)
        buttons.pack(fill="x", pady=(int(18 * s), 0))

        def button(text, cmd, primary=False, side="left", padx=0):
            frame = tk.Frame(buttons, bg=BLACK if primary else LINE)  # 1px border
            tk.Button(frame, text=text, command=cmd, relief="flat", cursor="hand2", font=("Segoe UI Semibold", 11),
                      bg=BLACK if primary else WHITE, fg=WHITE if primary else TEXT,
                      activebackground="#2b332e" if primary else PANEL, activeforeground=WHITE if primary else TEXT,
                      padx=int(16 * s), pady=int(6 * s), bd=0).pack(padx=1, pady=1)
            frame.pack(side=side, padx=padx)

        if projects:
            button("Use this project", use, primary=True)
            button("+ New project", lambda: choose({"new": True}), padx=int(8 * s))
        else:  # nothing to pick from yet
            button("+ New project", lambda: choose({"new": True}), primary=True)
        button("Decide later", lambda: choose(None), side="right")
        query.trace_add("write", refill)
        listbox.bind("<Double-Button-1>", use)
        entry.bind("<Return>", use)
        entry.bind("<Down>", lambda e: move(1))
        entry.bind("<Up>", lambda e: move(-1))
        win.bind("<Escape>", lambda e: choose(None))
        refill()
        self._picker, self._picker_sid = win, session_id
        win.lift()
        win.after(50, lambda: (win.focus_force(), entry.focus_set()))

    def _close_picker(self) -> None:
        if self._picker is not None:
            try:
                self._picker.destroy()
            except tk.TclError:
                pass
        self._picker, self._picker_sid = None, None

    # ------------------------------------------------------------ window

    def run(self) -> None:
        """Create the window and run the Tk loop on this (main) thread until ``quit``."""
        root = self.root = tk.Tk()
        root.withdraw()
        root.overrideredirect(True)
        root.configure(bg=KEY)
        root.attributes("-topmost", True)
        root.attributes("-transparentcolor", KEY)
        self.s = root.winfo_fpixels("1i") / 96.0  # display scale (1.0 at 100 %, 1.5 at 150 %)
        sw, sh = root.winfo_screenwidth(), root.winfo_screenheight()
        self.w = int(min(max(sw * 0.55, 560 * self.s), 1100 * self.s))
        self.h = int(sh * 0.6)
        root.geometry(f"{self.w}x{self.h}+{(sw - self.w) // 2}+{int(sh * 0.025)}")
        self.canvas = tk.Canvas(root, width=self.w, height=self.h, bg=KEY, highlightthickness=0, bd=0)
        self.canvas.pack(fill="both", expand=True)
        self.fonts = {"label": ("Segoe UI Semibold", 8), "text": ("Segoe UI Semibold", 14),
                      "note": ("Segoe UI", 10), "line": ("Segoe UI", 11)}
        root.deiconify()
        root.update_idletasks()
        self._click_through()
        self._last = None
        self._topmost_at = 0.0
        root.after(50, self._tick)
        root.mainloop()

    def _click_through(self) -> None:
        user32 = ctypes.windll.user32
        hwnd = int(self.root.wm_frame(), 16)
        style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
        user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_LAYERED | WS_EX_TRANSPARENT | WS_EX_TOOLWINDOW
                              | WS_EX_NOACTIVATE | WS_EX_TOPMOST)
        self.hwnd = hwnd
        self._raise()

    def _raise(self) -> None:
        ctypes.windll.user32.SetWindowPos(self.hwnd, -1, 0, 0, 0, 0, SWP_FLAGS)  # HWND_TOPMOST

    def _tick(self) -> None:
        try:
            while True:
                self._handle(*self.inbox.get_nowait())
        except queue.Empty:
            pass
        if self.root is None:
            return
        self.cards = [c for c in self.cards if c.left > 0]
        visible = self._visible()
        state = [(c.key, c.text, round(c.left, 2)) for c in visible]
        if state != self._last:
            self._last = state
            self._draw(visible)
        if visible and time.monotonic() - self._topmost_at > 3:
            self._topmost_at = time.monotonic()
            self._raise()  # stay above apps that also ask to be on top
        self.root.after(100, self._tick)

    def _visible(self) -> list[Card]:
        order = {"gate": 0, "on": 1, "off": 1, "info": 1, "wrapup": 2}
        cards = [c for c in self.cards if not (self.hidden and c.is_nudge)]
        return sorted(cards, key=lambda c: (order.get(c.kind, 3), -c.born))[: self.MAX_CARDS]

    def _draw(self, cards: list[Card]) -> None:
        cv, s = self.canvas, self.s
        cv.delete("all")
        pad, gap = int(18 * s), int(10 * s)
        x_text = pad
        width = self.w - 2 * pad
        y = 0
        for c in cards:
            fill, border, accent, label = STYLE.get(c.kind, STYLE["info"])
            top = y
            first = cv.create_text(x_text, y + pad, anchor="nw", text=label, fill=accent, font=self.fonts["label"])
            if c.is_nudge and c.kind != "wrapup":
                cv.create_text(self.w - pad, y + pad, anchor="ne", text="ONLY YOU SEE THIS", fill=MUTED,
                               font=self.fonts["label"])
            y = cv.bbox(first)[3] + int(6 * s)
            text = c.text
            if c.kind == "gate" and c.app:
                text = f"{c.app} — Sync starts listening in {max(1, round(c.left * c.ttl + 0.49))} s"
            y = cv.bbox(cv.create_text(x_text, y, anchor="nw", text=text, fill=TEXT, width=width,
                                       font=self.fonts["text"]))[3]
            for line in c.lines:
                y = cv.bbox(cv.create_text(x_text, y + int(5 * s), anchor="nw", text=f"•  {line}", fill=TEXT,
                                           width=width, font=self.fonts["line"]))[3]
            if c.note:
                y = cv.bbox(cv.create_text(x_text, y + int(5 * s), anchor="nw", text=c.note, fill=MUTED,
                                           width=width, font=self.fonts["note"]))[3]
            bottom = y + pad + (int(6 * s) if c.ttl is not None else 0)
            inner = _rounded(cv, 1, top + 1, self.w - 2, bottom - 1, int(10 * s), fill=fill)
            outer = _rounded(cv, 0, top, self.w - 1, bottom, int(10 * s), fill=border)  # shows as a 1px border
            cv.tag_lower(inner)
            cv.tag_lower(outer)
            if c.ttl is not None:  # time left before this card goes away
                track_y = bottom - int(10 * s)
                bar = max(2, int(2 * s))
                cv.create_rectangle(x_text, track_y, self.w - pad, track_y + bar, fill=border, width=0)
                cv.create_rectangle(x_text, track_y, x_text + (self.w - pad - x_text) * c.left, track_y + bar,
                                    fill=accent, width=0)
            y = bottom + gap


def _rounded(cv: tk.Canvas, x0: int, y0: int, x1: int, y1: int, r: int, **kw) -> int:
    pts = [x0 + r, y0, x1 - r, y0, x1, y0, x1, y0 + r, x1, y1 - r, x1, y1, x1 - r, y1, x0 + r, y1, x0, y1,
           x0, y1 - r, x0, y0 + r, x0, y0]
    return cv.create_polygon(pts, smooth=True, width=0, **kw)


def _wrapup_lines(card: dict) -> list[str]:
    def short(text: str, n: int = 90) -> str:
        return text if len(text) <= n else text[: n - 1].rstrip() + "…"

    lines = [f"Asked for, not confirmed: {short(x)}" for x in card.get("asked_for", [])[:2]]
    lines += [f"Nobody owns: {short(x)}" for x in card.get("unowned", [])[:1]]
    lines += [f"No date yet: {short(x)}" for x in card.get("missing_dates", [])[:2]]
    lines += [f"No time set: {short(x)}" for x in card.get("no_time", [])[:1]]
    lines += [f"Not answered: {short(x)}" for x in card.get("unanswered", [])[:1]]
    lines += [f"Parked: {short(x)}" for x in card.get("parked", [])[:1]]
    lines += [f"Still to cover: {short(_PREFIX.sub('', g['question']))}" for g in card.get("open_criteria", [])[:1]]
    return lines[:4]
