"""
Auto-greet League of Legends champ select via the LCU (League Client Update) API.
Now with a Tkinter GUI.

Requires:
    pip install websockets requests urllib3

GUI mode (default — just run the script, no terminal needed):
    python lol_auto_greet_gui.py

CLI mode (original terminal behavior, no window):
    python lol_auto_greet_gui.py --cli
    python lol_auto_greet_gui.py --cli --message "hi team"

What it does:
  1. Reads the client's lockfile to get the local API port + auth token.
  2. Opens a WebSocket to the client's event stream.
  3. Watches for the gameflow phase becoming "ChampSelect" or "EndOfGame".
  4. Waits a few seconds (so everyone's actually loaded into the lobby),
     then sends the configured message — and only that message, nothing
     appended — into the active chat, once per champ select and once
     per post-game screen.
  5. If the client closes/restarts/updates, it reconnects automatically,
     picking up the new port/password League generates on relaunch.

This uses Riot's own local client API (same one tools like Blitz/Porofessor
use) — it does not touch the game process, memory, or render/input pipeline.
"""

import argparse
import asyncio
import base64
import json
import os
import queue
import ssl
import sys
import threading
import time
import webbrowser

import requests
import websockets

try:
    import tkinter as tk
    from tkinter import ttk
    TK_AVAILABLE = True
except ImportError:
    TK_AVAILABLE = False

KOFI_URL = "https://ko-fi.com/dan7hem4n"
TWITCH_URL = "https://twitch.tv/Dan7heM4n"

# ---------------------------------------------------------------------------
# Theme — dark, Twitch-purple accented
# ---------------------------------------------------------------------------
BG_APP = "#0e0e10"
BG_PANEL = "#18181b"
BG_INPUT = "#1f1f23"
TEXT_PRIMARY = "#efeff1"
TEXT_MUTED = "#adadb8"
ACCENT = "#9146ff"
ACCENT_HOVER = "#772ce8"
START_COLOR = "#3ba55d"
START_HOVER = "#2f8b4d"
STOP_COLOR = "#e0245e"
STOP_HOVER = "#b81e4d"
KOFI_COLOR = "#ff5e5b"
KOFI_HOVER = "#e14a47"
FONT_FAMILY = "Segoe UI"

urllib3_warn = False
try:
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    urllib3_warn = True
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Config handling
# ---------------------------------------------------------------------------

def get_config_path():
    """Store config.json next to the running exe/script, not the cwd,
    so it works the same whether launched by double-click or terminal."""
    if getattr(sys, "frozen", False):
        # Running as a PyInstaller-built exe
        base_dir = os.path.dirname(sys.executable)
    else:
        base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, "config.json")


def load_saved_message():
    """Returns the saved message, or None if there isn't one yet."""
    config_path = get_config_path()
    if os.path.exists(config_path):
        try:
            with open(config_path, "r", encoding="utf-8") as f:
                data = json.load(f)
            saved = data.get("message")
            if saved:
                return saved
        except Exception:
            pass
    return None


def save_config(data):
    try:
        with open(get_config_path(), "w", encoding="utf-8") as f:
            json.dump(data, f)
        return True
    except Exception as e:
        print(f"(Couldn't save config: {e})")
        return False


def load_or_prompt_message_cli(cli_message):
    """CLI-mode only: priority is --message flag > saved config.json >
    interactive prompt."""
    if cli_message:
        save_config({"message": cli_message})
        return cli_message

    saved = load_saved_message()
    if saved:
        return saved

    print("What message should be sent when you enter champ select?")
    message = input("Message (e.g. 'hi team'): ").strip()
    if not message:
        message = "hi team"
    save_config({"message": message})
    return message


# ---------------------------------------------------------------------------
# LCU plumbing
# ---------------------------------------------------------------------------

def find_lockfile_nonblocking():
    """Locate the League client's lockfile without ever blocking on
    input() — used in the all-day loop so the script waits quietly if
    League isn't open yet rather than hanging on a prompt."""
    candidates = [
        r"C:\Riot Games\League of Legends\lockfile",
        os.path.expanduser(r"~\AppData\Local\Riot Games\League of Legends\lockfile"),
        "/Applications/League of Legends.app/Contents/LoL/lockfile",  # macOS
    ]
    for path in candidates:
        if os.path.exists(path):
            return path
    return None


def read_lockfile(path):
    with open(path, "r", encoding="utf-8") as f:
        contents = f.read().strip()
    # Format: name:pid:port:password:protocol
    name, pid, port, password, protocol = contents.split(":")
    return {
        "port": port,
        "password": password,
        "protocol": protocol,
    }


def get_auth_header(password):
    token = base64.b64encode(f"riot:{password}".encode()).decode()
    return f"Basic {token}"


def get_current_phase(port, password):
    """Query the client's current gameflow phase directly (REST, not WS)."""
    auth_header = get_auth_header(password)
    try:
        resp = requests.get(
            f"https://127.0.0.1:{port}/lol-gameflow/v1/gameflow-phase",
            headers={"Authorization": auth_header},
            verify=False,
            timeout=5,
        )
        resp.raise_for_status()
        # This endpoint returns a plain JSON string, e.g. "ChampSelect"
        return resp.json()
    except Exception:
        return None


def send_chat_message(port, password, text, retries=6, delay_seconds=2, timeout_seconds=8):
    auth_header = get_auth_header(password)
    base = f"https://127.0.0.1:{port}"

    for attempt in range(retries):
        try:
            resp = requests.get(
                f"{base}/lol-chat/v1/conversations",
                headers={"Authorization": auth_header},
                verify=False,
                timeout=timeout_seconds,
            )
            resp.raise_for_status()
            conversations = resp.json()
        except requests.exceptions.RequestException as e:
            print(f"Request failed on attempt {attempt + 1}/{retries}: {e}")
            if attempt < retries - 1:
                time.sleep(delay_seconds)
                continue
            else:
                print("Giving up after repeated request failures.")
                return

        champ_select_convo = next(
            (
                c for c in conversations
                if c.get("type") in (
                    "championSelect", "multiUserChat", "premadeSummonerVoice",
                    "customGame", "postGame", "post-game",
                )
                or "champ-select" in (c.get("id") or "")
                or "champ_select" in (c.get("id") or "")
                or "postgame" in (c.get("id") or "").lower()
                or "post-game" in (c.get("id") or "").lower()
                or "@conference" in (c.get("id") or "")
            ),
            None,
        )

        if champ_select_convo:
            convo_id = champ_select_convo["id"]
            try:
                post_resp = requests.post(
                    f"{base}/lol-chat/v1/conversations/{convo_id}/messages",
                    headers={"Authorization": auth_header},
                    json={"body": text, "type": "chat"},  # body is exactly `text`, nothing appended
                    verify=False,
                    timeout=timeout_seconds,
                )
                if post_resp.ok:
                    print(f"Sent: {text}")
                else:
                    print(f"Failed to send message: {post_resp.status_code} {post_resp.text}")
            except requests.exceptions.RequestException as e:
                print(f"Failed to send message (request error): {e}")
            return

        print(f"No champ select conversation found yet (attempt {attempt + 1}/{retries}).")
        if attempt == retries - 1:
            print("DEBUG - conversations seen:")
            for c in conversations:
                print(f"    type={c.get('type')!r} id={c.get('id')!r}")
        else:
            time.sleep(delay_seconds)


# ---------------------------------------------------------------------------
# Core watcher — same behavior as before, but cooperatively stoppable via
# a threading.Event so the GUI's Stop button (and clean window close) can
# interrupt it without killing the whole process.
# ---------------------------------------------------------------------------

def status(text):
    """Prefix used so the GUI's log poller can tell a status update
    apart from an ordinary log line and route it to the status bar."""
    print(f"STATUS::{text}")


async def watch_for_champ_select(conn, message, stop_event):
    port = conn["port"]
    password = conn["password"]
    auth_header = get_auth_header(password)

    ws_url = f"wss://127.0.0.1:{port}"
    ssl_ctx = ssl.create_default_context()
    ssl_ctx.check_hostname = False
    ssl_ctx.verify_mode = ssl.CERT_NONE

    # Phases we react to, and how long to wait before sending (gives
    # everyone time to actually load into the lobby/post-game screen
    # first, instead of sending the instant the phase flips).
    TRACKED_PHASES = {
        "ChampSelect": 4,
        "EndOfGame": 3,
    }
    greeted = {phase: False for phase in TRACKED_PHASES}

    async def maybe_greet(phase):
        if phase in TRACKED_PHASES and not greeted[phase]:
            delay = TRACKED_PHASES[phase]
            print(f"Entered {phase} — waiting {delay}s before sending...")
            status(f"{phase} — sending greeting shortly...")
            await asyncio.sleep(delay)
            if stop_event.is_set():
                return
            send_chat_message(port, password, message)
            greeted[phase] = True
            status(f"Greeted in {phase} — watching for next game...")
        elif phase not in TRACKED_PHASES:
            # Left every tracked phase — reset so the next champ select
            # or post-game screen triggers again.
            for p in greeted:
                greeted[p] = False

    async with websockets.connect(
        ws_url,
        additional_headers={"Authorization": auth_header},
        ssl=ssl_ctx,
        max_size=16 * 1024 * 1024,  # allow larger frames, just in case
        ping_interval=20,
        ping_timeout=20,
    ) as ws:
        # Subscribe ONLY to gameflow-phase changes (not all events —
        # subscribing to everything is extremely noisy and can crash
        # the connection with oversized frames).
        await ws.send(json.dumps([5, "OnJsonApiEvent_lol-gameflow_v1_gameflow-phase"]))
        print("Connected. Waiting for champ select or post-game...")
        status("Connected — watching for champ select")

        # Check the CURRENT phase immediately in case we're already in
        # one of the tracked phases (e.g. script started mid-lobby, or
        # the phase was entered before the WebSocket subscription was live).
        current_phase = get_current_phase(port, password)
        if current_phase in TRACKED_PHASES:
            await maybe_greet(current_phase)

        async def consume():
            async for raw_msg in ws:
                if stop_event.is_set():
                    return
                if not raw_msg:
                    continue
                try:
                    msg = json.loads(raw_msg)
                except json.JSONDecodeError:
                    continue

                # Event frames look like: [8, "OnJsonApiEvent", {"uri": ..., "data": ...}]
                if len(msg) < 3:
                    continue
                event = msg[2]
                uri = event.get("uri", "")

                if uri == "/lol-gameflow/v1/gameflow-phase":
                    phase = event.get("data")
                    await maybe_greet(phase)

        async def watch_for_stop():
            while not stop_event.is_set():
                await asyncio.sleep(0.3)

        consume_task = asyncio.create_task(consume())
        stop_task = asyncio.create_task(watch_for_stop())
        done, pending = await asyncio.wait(
            {consume_task, stop_task}, return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
        # Surface any real error from consume() instead of swallowing it
        if consume_task in done and consume_task.exception():
            raise consume_task.exception()


def run_bot(message, stop_event):
    """The all-day watch loop: locate lockfile -> connect -> watch ->
    reconnect on disconnect, all while stop_event is unset. Meant to be
    run on a background thread (in CLI mode it's just called directly)."""
    print(f'Using message: "{message}"')
    status("Waiting for League client to open...")

    lockfile_path = None
    wait_count = 0

    while not stop_event.is_set():
        if lockfile_path is None or not os.path.exists(lockfile_path):
            lockfile_path = find_lockfile_nonblocking()
            if lockfile_path is None:
                wait_count += 1
                if wait_count % 12 == 1:  # roughly every ~60s
                    print("Still waiting for League client to open...")
                _interruptible_sleep(5, stop_event)
                continue
            wait_count = 0

        try:
            conn = read_lockfile(lockfile_path)
        except Exception:
            # Lockfile exists but is mid-write or stale; retry shortly.
            _interruptible_sleep(2, stop_event)
            continue

        print(f"Found client on port {conn['port']}")
        status(f"Found client on port {conn['port']} — connecting...")
        try:
            asyncio.run(watch_for_champ_select(conn, message, stop_event))
        except Exception as e:
            if not stop_event.is_set():
                print(f"Connection lost: {e}")
                status("Connection lost — reconnecting...")

        if stop_event.is_set():
            break

        print("Reconnecting in 3 seconds...")
        lockfile_path = None  # force re-discovery in case client restarted
        _interruptible_sleep(3, stop_event)

    status("Stopped")
    print("Stopped.")


def _interruptible_sleep(seconds, stop_event, step=0.25):
    """time.sleep that bails out early if stop_event gets set, so Stop
    feels responsive even while idly waiting for the client to open."""
    elapsed = 0.0
    while elapsed < seconds and not stop_event.is_set():
        time.sleep(step)
        elapsed += step


# ---------------------------------------------------------------------------
# GUI
# ---------------------------------------------------------------------------

class QueueWriter:
    """A stdout-like object that pushes writes onto a thread-safe queue
    instead of a real terminal, so print() calls from the background
    thread can be picked up and displayed by the Tkinter main thread."""

    def __init__(self, q):
        self.q = q

    def write(self, text):
        if text:
            self.q.put(text)

    def flush(self):
        pass


class HoverButton(tk.Button):
    """A flat tk.Button that swaps to a lighter shade on hover, so
    buttons feel a bit more alive without needing ttk theming."""

    def __init__(self, master, base_bg, hover_bg, **kwargs):
        kwargs.setdefault("relief", "flat")
        kwargs.setdefault("bd", 0)
        kwargs.setdefault("cursor", "hand2")
        kwargs.setdefault("activebackground", hover_bg)
        kwargs.setdefault("fg", TEXT_PRIMARY)
        kwargs.setdefault("activeforeground", TEXT_PRIMARY)
        kwargs.setdefault("font", (FONT_FAMILY, 9, "bold"))
        super().__init__(master, bg=base_bg, **kwargs)
        self._base_bg = base_bg
        self._hover_bg = hover_bg
        self.bind("<Enter>", lambda e: self.configure(bg=self._hover_bg))
        self.bind("<Leave>", lambda e: self.configure(bg=self._base_bg))


class HoverLabel(tk.Label):
    """A clickable label styled like a link (used for the Twitch handle)."""

    def __init__(self, master, url, **kwargs):
        kwargs.setdefault("fg", ACCENT)
        kwargs.setdefault("cursor", "hand2")
        super().__init__(master, **kwargs)
        self._url = url
        self.bind("<Button-1>", lambda e: webbrowser.open(url))
        self.bind("<Enter>", lambda e: self.configure(fg=ACCENT_HOVER))
        self.bind("<Leave>", lambda e: self.configure(fg=ACCENT))


class AutoGreetGUI:
    def __init__(self, root):
        self.root = root
        self.root.title("League Champ-Select Auto-Plugger")
        self.root.geometry("620x480")
        self.root.minsize(520, 380)
        self.root.configure(bg=BG_APP)

        self.log_queue = queue.Queue()
        self.stop_event = threading.Event()
        self.worker_thread = None
        self._tutorial_win = None

        self._build_widgets()
        self._style_scrollbar()
        self._redirect_stdout()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._poll_log_queue)

    def _build_widgets(self):
        # ---- Header: title, Twitch credit, Ko-fi button -------------------
        header = tk.Frame(self.root, bg=BG_APP)
        header.pack(fill="x", padx=16, pady=(14, 6))

        title_block = tk.Frame(header, bg=BG_APP)
        title_block.pack(side="left", fill="x", expand=True)

        tk.Label(
            title_block, text="Champ-Select Auto-Plugger",
            bg=BG_APP, fg=TEXT_PRIMARY, font=(FONT_FAMILY, 16, "bold"),
        ).pack(anchor="w")

        credit_row = tk.Frame(title_block, bg=BG_APP)
        credit_row.pack(anchor="w")
        tk.Label(
            credit_row, text="by ", bg=BG_APP, fg=TEXT_MUTED, font=(FONT_FAMILY, 9),
        ).pack(side="left")
        HoverLabel(
            credit_row, url=TWITCH_URL, text="twitch.tv/Dan7heM4n",
            bg=BG_APP, font=(FONT_FAMILY, 9, "bold"),
        ).pack(side="left")

        self.kofi_btn = HoverButton(
            header, base_bg=KOFI_COLOR, hover_bg=KOFI_HOVER,
            text="☕  Support on Ko-fi", padx=12, pady=6,
            command=lambda: webbrowser.open(KOFI_URL),
        )
        self.kofi_btn.pack(side="right", anchor="n")

        self.help_btn = HoverButton(
            header, base_bg=BG_INPUT, hover_bg="#2a2a2f",
            text="❓  How to Use", padx=12, pady=6,
            command=self._open_tutorial,
        )
        self.help_btn.pack(side="right", anchor="n", padx=(0, 8))

        self._divider()

        # ---- Message row ---------------------------------------------------
        msg_frame = tk.Frame(self.root, bg=BG_APP)
        msg_frame.pack(fill="x", padx=16, pady=(10, 4))

        tk.Label(
            msg_frame, text="Message to send:", bg=BG_APP, fg=TEXT_PRIMARY,
            font=(FONT_FAMILY, 10),
        ).pack(anchor="w", pady=(0, 4))

        entry_row = tk.Frame(msg_frame, bg=BG_APP)
        entry_row.pack(fill="x")

        self.message_var = tk.StringVar(value=load_saved_message() or "hi team")
        self.message_entry = tk.Entry(
            entry_row, textvariable=self.message_var,
            bg=BG_INPUT, fg=TEXT_PRIMARY, insertbackground=TEXT_PRIMARY,
            relief="flat", font=(FONT_FAMILY, 10),
        )
        self.message_entry.pack(side="left", fill="x", expand=True, ipady=6, padx=(0, 8))

        self.save_btn = HoverButton(
            entry_row, base_bg=BG_INPUT, hover_bg="#2a2a2f",
            text="Save", padx=14, pady=6, command=self._on_save,
        )
        self.save_btn.pack(side="left")

        # ---- Start / Stop / status -----------------------------------------
        controls = tk.Frame(self.root, bg=BG_APP)
        controls.pack(fill="x", padx=16, pady=(14, 6))

        self.start_btn = HoverButton(
            controls, base_bg=START_COLOR, hover_bg=START_HOVER,
            text="▶  Start", width=11, padx=6, pady=8, command=self._on_start,
        )
        self.start_btn.pack(side="left")

        self.stop_btn = HoverButton(
            controls, base_bg=STOP_COLOR, hover_bg=STOP_HOVER,
            text="■  Stop", width=11, padx=6, pady=8, command=self._on_stop,
            state="disabled",
        )
        self.stop_btn.pack(side="left", padx=(8, 0))

        self.status_var = tk.StringVar(value="Idle")
        tk.Label(
            controls, textvariable=self.status_var, anchor="e",
            bg=BG_APP, fg=TEXT_MUTED, font=(FONT_FAMILY, 9),
        ).pack(side="right", fill="x", expand=True)

        # ---- Log -------------------------------------------------------------
        log_frame = tk.Frame(self.root, bg=BG_APP)
        log_frame.pack(fill="both", expand=True, padx=16, pady=(8, 14))

        tk.Label(
            log_frame, text="Activity log", bg=BG_APP, fg=TEXT_MUTED,
            font=(FONT_FAMILY, 9),
        ).pack(anchor="w", pady=(0, 4))

        self.log_widget = tk.Text(
            log_frame, height=15, state="disabled", wrap="word",
            font=("Consolas", 9), bg=BG_PANEL, fg=TEXT_PRIMARY,
            insertbackground=TEXT_PRIMARY, relief="flat", bd=0,
            padx=10, pady=8, highlightthickness=0,
        )
        log_scrollbar = ttk.Scrollbar(
            log_frame, style="Dark.Vertical.TScrollbar",
            orient="vertical", command=self.log_widget.yview,
        )
        self.log_widget.configure(yscrollcommand=log_scrollbar.set)

        self.log_widget.pack(side="left", fill="both", expand=True)
        log_scrollbar.pack(side="right", fill="y")

    def _style_scrollbar(self):
        """The default tk.Scrollbar (used by scrolledtext.ScrolledText)
        ignores color overrides on Windows, so instead we build our own
        Text + ttk.Scrollbar and theme it here. 'clam' is the one
        built-in ttk theme that actually honors custom colors."""
        style = ttk.Style(self.root)
        style.theme_use("clam")
        style.configure(
            "Dark.Vertical.TScrollbar",
            background=ACCENT,       # thumb
            troughcolor=BG_PANEL,    # track
            bordercolor=BG_PANEL,
            arrowcolor=TEXT_PRIMARY,
            relief="flat",
            arrowsize=12,
            width=10,
        )
        style.map(
            "Dark.Vertical.TScrollbar",
            background=[("active", ACCENT_HOVER), ("pressed", ACCENT_HOVER)],
            arrowcolor=[("disabled", TEXT_MUTED)],
        )

    def _divider(self):
        tk.Frame(self.root, bg="#2a2a2e", height=1).pack(fill="x", padx=16, pady=(2, 0))

    def _open_tutorial(self):
        """Opens the walkthrough in its own window instead of a modal
        dialog, so it can stay open for reference while the main
        window is still usable (e.g. while you Start it for the
        first time)."""
        if getattr(self, "_tutorial_win", None) is not None:
            try:
                self._tutorial_win.lift()
                self._tutorial_win.focus_force()
                return
            except tk.TclError:
                self._tutorial_win = None  # window was already closed

        win = tk.Toplevel(self.root)
        self._tutorial_win = win
        win.title("How to Use — Champ-Select Auto-Plugger")
        win.geometry("540x560")
        win.minsize(420, 360)
        win.configure(bg=BG_APP)
        win.transient(self.root)  # stays above the main window, but not modal
        win.protocol("WM_DELETE_WINDOW", lambda: (win.destroy(), setattr(self, "_tutorial_win", None)))

        tk.Label(
            win, text="How to Use", bg=BG_APP, fg=TEXT_PRIMARY,
            font=(FONT_FAMILY, 15, "bold"),
        ).pack(anchor="w", padx=18, pady=(16, 2))
        tk.Frame(win, bg="#2a2a2e", height=1).pack(fill="x", padx=18, pady=(0, 10))

        body_frame = tk.Frame(win, bg=BG_APP)
        body_frame.pack(fill="both", expand=True, padx=18, pady=(0, 10))

        text_widget = tk.Text(
            body_frame, wrap="word", bg=BG_PANEL, fg=TEXT_PRIMARY,
            relief="flat", bd=0, padx=14, pady=12, font=(FONT_FAMILY, 10),
            highlightthickness=0, cursor="arrow",
        )
        scrollbar = ttk.Scrollbar(
            body_frame, style="Dark.Vertical.TScrollbar",
            orient="vertical", command=text_widget.yview,
        )
        text_widget.configure(yscrollcommand=scrollbar.set, state="disabled")
        text_widget.pack(side="left", fill="both", expand=True)
        scrollbar.pack(side="right", fill="y")

        text_widget.tag_configure(
            "heading", font=(FONT_FAMILY, 11, "bold"), foreground=ACCENT,
            spacing1=10, spacing3=4,
        )
        text_widget.tag_configure(
            "body", font=(FONT_FAMILY, 10), foreground=TEXT_PRIMARY, spacing3=2,
        )
        text_widget.tag_configure(
            "muted", font=(FONT_FAMILY, 9), foreground=TEXT_MUTED, spacing3=8,
        )

        sections = [
            ("1. First-time setup", [
                "Open the League client and log in — the app just watches "
                "for it, it doesn't launch it for you.",
                "Type the message you want sent into the \"Message to "
                "send\" box at the top, then click Save.",
                "Click Start. You can minimize the window — just leave it "
                "running in the background.",
            ]),
            ("2. What happens automatically", [
                "It waits quietly until League is open, then connects.",
                "When champ select starts, it waits a few seconds (so "
                "everyone's actually loaded in) and sends your message "
                "once.",
                "It does the same thing again on the post-game screen.",
                "After that game ends, it resets itself automatically — "
                "no need to click Start again for the next game.",
            ]),
            ("3. Stopping it", [
                "Click Stop to pause without closing the app.",
                "Closing the window stops everything safely too.",
            ]),
            ("4. If a message doesn't send", [
                "Check the Activity Log at the bottom of the main window "
                "— it prints exactly what's happening (waiting, "
                "connected, sent, or an error).",
                "Riot chat restrictions (from reports, etc.) block "
                "bot-sent messages the same way they'd block you typing "
                "manually — that's a Riot-side account state, not "
                "something this app can work around.",
                "If League updates or restarts mid-day, the app "
                "reconnects automatically — you don't need to restart it.",
            ]),
        ]

        for heading, lines in sections:
            text_widget.configure(state="normal")
            text_widget.insert("end", heading + "\n", "heading")
            for line in lines:
                text_widget.insert("end", "•  " + line + "\n", "body")
            text_widget.configure(state="disabled")

        text_widget.configure(state="normal")
        text_widget.insert(
            "end",
            "\nIf this saves you time, a tip on Ko-fi is always "
            "appreciated \u2014 there's a button for that on the main "
            "window.\n",
            "muted",
        )
        text_widget.configure(state="disabled")

        HoverButton(
            win, base_bg=ACCENT, hover_bg=ACCENT_HOVER,
            text="Got it", padx=16, pady=6,
            command=lambda: (win.destroy(), setattr(self, "_tutorial_win", None)),
        ).pack(pady=(0, 16))

    def _redirect_stdout(self):
        sys.stdout = QueueWriter(self.log_queue)
        sys.stderr = QueueWriter(self.log_queue)

    def _append_log(self, text):
        self.log_widget.configure(state="normal")
        self.log_widget.insert("end", text)
        self.log_widget.see("end")
        self.log_widget.configure(state="disabled")

    def _poll_log_queue(self):
        try:
            while True:
                line = self.log_queue.get_nowait()
                for sub_line in line.splitlines(keepends=True):
                    if sub_line.startswith("STATUS::"):
                        self.status_var.set(sub_line[len("STATUS::"):].strip())
                    else:
                        self._append_log(sub_line)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_log_queue)

    def _on_save(self):
        message = self.message_var.get().strip()
        if not message:
            self.status_var.set("Message can't be empty — not saved.")
            return
        if save_config({"message": message}):
            self.status_var.set("Saved.")

    def _on_start(self):
        message = self.message_var.get().strip()
        if not message:
            self.status_var.set("Enter a message first.")
            return
        save_config({"message": message})

        self.stop_event = threading.Event()
        self.worker_thread = threading.Thread(
            target=run_bot, args=(message, self.stop_event), daemon=True
        )
        self.worker_thread.start()

        self.start_btn.configure(state="disabled")
        self.stop_btn.configure(state="normal")
        self.message_entry.configure(state="disabled")

    def _on_stop(self):
        self.stop_event.set()
        self.status_var.set("Stopping...")
        self.stop_btn.configure(state="disabled")
        self.start_btn.configure(state="normal")
        self.message_entry.configure(state="normal")

    def _on_close(self):
        self.stop_event.set()
        self.root.after(200, self.root.destroy)


def run_gui():
    root = tk.Tk()
    AutoGreetGUI(root)
    root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Auto-greet League champ select.")
    parser.add_argument(
        "--message", "-m",
        help="Message to send in champ select. Overrides saved config.json.",
    )
    parser.add_argument(
        "--cli", action="store_true",
        help="Run in the original terminal mode instead of opening the GUI.",
    )
    args = parser.parse_args()

    if args.cli or not TK_AVAILABLE:
        if not TK_AVAILABLE and not args.cli:
            print("Tkinter isn't available in this Python install — falling back to CLI mode.")
        message = load_or_prompt_message_cli(args.message)
        stop_event = threading.Event()
        try:
            run_bot(message, stop_event)
        except KeyboardInterrupt:
            stop_event.set()
            print("\nStopped.")
    else:
        run_gui()


if __name__ == "__main__":
    main()
