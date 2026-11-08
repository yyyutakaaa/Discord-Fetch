#!/usr/bin/env python3
"""
Discord Fetch — export Discord DMs and channels from your terminal.

One file, no dependencies: Python 3.7+ on Windows, macOS and Linux.
Double-click it, or run it from a terminal. It updates itself from GitHub on start.

    python fetch.py                                      interactive
    python fetch.py --channel 123 --days 7 --format md   one-shot export
"""

import argparse
import atexit
import csv
import getpass
import io
import json
import os
import re
import shutil
import subprocess
import sys
import textwrap
import threading
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from collections import Counter
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

VERSION = "3.2"  # bump this on every release: older copies update to the GitHub version on start
UPDATE_URL = "https://raw.githubusercontent.com/yyyutakaaa/Discord-Fetch/main/fetch.py"
API = "https://discord.com/api/v9"
CFG_DIR = Path.home() / ".discord_fetch"
TOKEN_FILE = CFG_DIR / "token"
DEFAULT_SAVE_DIR = Path.home() / "Discord_Messages"
DISCORD_EPOCH_MS = 1420070400000
PREVIEW_LIMIT = 100


# ─────────────────────────────────────────────────────────
#  TERMINAL
# ─────────────────────────────────────────────────────────

def _setup_terminal():
    if sys.platform == "win32":
        try:
            import ctypes
            k = ctypes.windll.kernel32
            handle = k.GetStdHandle(-11)
            mode = ctypes.c_uint32()
            if k.GetConsoleMode(handle, ctypes.byref(mode)):
                k.SetConsoleMode(handle, mode.value | 0x0004)  # enable ANSI escapes
        except Exception:
            pass
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


_setup_terminal()


def _isatty(stream):
    try:
        return stream.isatty()
    except Exception:
        return False


class Term:
    tty = _isatty(sys.stdout)
    interactive = tty and _isatty(sys.stdin)
    color = tty and "NO_COLOR" not in os.environ and os.environ.get("TERM") != "dumb"


def raw(seq):
    """Write a control sequence, only when talking to a real terminal."""
    if Term.tty:
        sys.stdout.write(seq)
        sys.stdout.flush()


HIDE_CURSOR, SHOW_CURSOR = "\033[?25l", "\033[?25h"
atexit.register(lambda: raw(SHOW_CURSOR))


def W():
    """Usable line width (one column spare so lines never wrap)."""
    try:
        cols = shutil.get_terminal_size((80, 24)).columns
    except Exception:
        cols = 80
    return max(40, min(cols - 1, 100))


def term_rows():
    try:
        return shutil.get_terminal_size((80, 24)).lines
    except Exception:
        return 24


# ─────────────────────────────────────────────────────────
#  STYLE
# ─────────────────────────────────────────────────────────

BLURPLE = "#5865F2"
PINK    = "#EB459E"
GREEN   = "#57F287"
YELLOW  = "#FEE75C"
RED     = "#ED4245"
MUTED   = "#8E9297"
FAINT   = "#5C6068"
WHITE   = "#FFFFFF"
AUTHOR_COLORS = ["#5865F2", "#EB459E", "#57F287", "#FEE75C", "#F47B67",
                 "#45DDC0", "#B28DFF", "#FAA61A", "#00AFF4", "#FF7EB6"]

RESET = "\033[0m"
ANSI_RE = re.compile(r"\033\[[0-9;?]*[A-Za-z]")
CONTROL_RE = re.compile(r"[\x00-\x08\x0b-\x1f\x7f-\x9f]")


def _rgb(hexstr):
    h = hexstr.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def _blend(a, b, t):
    return tuple(round(x + (y - x) * t) for x, y in zip(a, b))


def _fg(rgb):
    return "\033[38;2;{};{};{}m".format(*rgb)


def _bg(rgb):
    return "\033[48;2;{};{};{}m".format(*rgb)


def paint(text, fg=None, bold=False, italic=False, bg=None):
    if not Term.color or not text:
        return text
    codes = ("\033[1m" if bold else "") + ("\033[3m" if italic else "")
    if fg:
        codes += _fg(_rgb(fg))
    if bg:
        codes += _bg(_rgb(bg))
    return codes + text + RESET if codes else text


def gradient(text, start=BLURPLE, end=PINK, shadow=False):
    """Colour text left→right. With shadow, non-block characters are toned down."""
    if not Term.color:
        return text
    a, b = _rgb(start), _rgb(end)
    span = max(len(text) - 1, 1)
    parts = []
    for i, ch in enumerate(text):
        if ch == " ":
            parts.append(ch)
            continue
        col = _blend(a, b, i / span)
        if shadow and ch != "█":
            col = _blend(col, (110, 112, 120), 0.55)
        parts.append(_fg(col) + ch)
    return "".join(parts) + RESET


def sanitize(text):
    """Strip control characters so message content can't inject terminal escapes."""
    return CONTROL_RE.sub("", (text or "").replace("\t", "    "))


def char_w(ch):
    if unicodedata.combining(ch) or unicodedata.category(ch) in ("Mn", "Me", "Cf"):
        return 0
    return 2 if unicodedata.east_asian_width(ch) in ("W", "F") else 1


def text_w(text):
    return sum(char_w(c) for c in text)


def vis_w(styled):
    return text_w(ANSI_RE.sub("", styled))


def trunc(text, width):
    text = sanitize(text).replace("\n", " ")
    if width <= 0:
        return ""
    if text_w(text) <= width:
        return text
    out, used = [], 0
    for ch in text:
        cw = char_w(ch)
        if used + cw > width - 1:
            break
        out.append(ch)
        used += cw
    return "".join(out) + "…"


# ─────────────────────────────────────────────────────────
#  OUTPUT
# ─────────────────────────────────────────────────────────

PAD = "  "
_lock = threading.Lock()
_live = None  # the active Spinner, if any


def out(line=""):
    with _lock:
        if _live is not None and Term.tty:
            sys.stdout.write("\r\033[2K")
        sys.stdout.write(line + "\n")
        sys.stdout.flush()


def ok(msg):
    out(PAD + paint("✓", GREEN, bold=True) + "  " + msg)


def fail(msg):
    out(PAD + paint("✗", RED, bold=True) + "  " + msg)


def warn(msg):
    out(PAD + paint("!", YELLOW, bold=True) + "  " + msg)


def note(msg):
    out(PAD + "   " + paint(msg, MUTED))


def tilde(path):
    p, home = str(path), str(Path.home())
    return "~" + p[len(home):] if p.startswith(home) else p


def box(rows, title="", color=BLURPLE):
    inner = max([vis_w(r) for r in rows] + [vis_w(title) + 2 if title else 0])
    inner = min(inner, W() - len(PAD) - 4)
    edge = lambda t: paint(t, color)
    if title:
        top = edge("╭─ ") + title + edge(" " + "─" * max(inner - vis_w(title) - 1, 0) + "╮")
    else:
        top = edge("╭" + "─" * (inner + 2) + "╮")
    out(PAD + top)
    for r in rows:
        out(PAD + edge("│") + " " + r + " " * max(inner - vis_w(r), 0) + " " + edge("│"))
    out(PAD + edge("╰" + "─" * (inner + 2) + "╯"))


def rule(label=""):
    width = W() - len(PAD)
    if not label:
        out(PAD + paint("─" * width, FAINT))
        return
    side = max((width - text_w(label) - 2) // 2, 3)
    out(PAD + paint("─" * side, FAINT) + " " + paint(label, MUTED) + " " + paint("─" * side, FAINT))


class Spinner:
    """Animated status line for slow work. Use .update() to change its text."""
    FRAMES = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"

    def __init__(self, text):
        self.text, self.meta, self.progress = text, "", None
        self._stop = threading.Event()
        self._thread = None
        self._t0 = time.time()

    def __enter__(self):
        global _live
        if Term.tty:
            _live = self
            raw(HIDE_CURSOR)
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()
        return self

    def update(self, text=None, meta=None, progress=None):
        if text is not None:
            self.text = text
        if meta is not None:
            self.meta = meta
        if progress is not None:
            self.progress = progress

    def _line(self, frame):
        avail = W() - len(PAD) - 3
        text = trunc(self.text, avail)
        line = PAD + paint(frame, BLURPLE, bold=True) + "  " + text
        room = avail - text_w(text)
        if self.progress is not None and room > 30:
            filled = int(20 * min(self.progress, 1.0))
            line += "  " + gradient("━" * filled) + paint("─" * (20 - filled), FAINT)
            line += paint(" {:>3}%".format(int(self.progress * 100)), MUTED)
            room -= 27
        meta = self.meta or "{:.0f}s".format(time.time() - self._t0)
        if room > 6:
            line += "  " + paint(trunc(meta, room - 2), FAINT)
        return line

    def _loop(self):
        i = 0
        while not self._stop.wait(0.08):
            with _lock:
                sys.stdout.write("\r\033[2K" + self._line(self.FRAMES[i % len(self.FRAMES)]))
                sys.stdout.flush()
            i += 1

    def __exit__(self, *exc):
        global _live
        self._stop.set()
        if self._thread:
            self._thread.join()
            with _lock:
                sys.stdout.write("\r\033[2K")
                sys.stdout.flush()
            raw(SHOW_CURSOR)
        _live = None
        return False


# ─────────────────────────────────────────────────────────
#  BANNER
# ─────────────────────────────────────────────────────────

_GLYPHS = {
    "F": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "██║     ", "╚═╝     "],
    "E": ["███████╗", "██╔════╝", "█████╗  ", "██╔══╝  ", "███████╗", "╚══════╝"],
    "T": ["████████╗", "╚══██╔══╝", "   ██║   ", "   ██║   ", "   ██║   ", "   ╚═╝   "],
    "C": [" ██████╗", "██╔════╝", "██║     ", "██║     ", "╚██████╗", " ╚═════╝"],
    "H": ["██╗  ██╗", "██║  ██║", "███████║", "██╔══██║", "██║  ██║", "╚═╝  ╚═╝"],
}


def banner():
    out()
    if W() >= 50:
        out(PAD + " " + paint("d i s c o r d", MUTED))
        for i in range(6):
            out(PAD + gradient("".join(_GLYPHS[c][i] for c in "FETCH"), shadow=True))
        out(PAD + " " + paint("export your DMs & channels", MUTED) + paint("  ·  v" + VERSION, FAINT))
    else:
        out(PAD + gradient("◆ discord fetch") + paint("  v" + VERSION, FAINT))
    out()


# ─────────────────────────────────────────────────────────
#  KEYBOARD + PROMPTS
# ─────────────────────────────────────────────────────────

def read_key():
    """Read one keypress: 'up', 'down', 'enter', 'esc', 'backspace', … or a character."""
    if os.name == "nt":
        import msvcrt
        ch = msvcrt.getwch()
        if ch in ("\x00", "\xe0"):
            return {"H": "up", "P": "down", "K": "left", "M": "right", "I": "pgup",
                    "Q": "pgdn", "G": "home", "O": "end"}.get(msvcrt.getwch(), "")
        named = {"\r": "enter", "\n": "enter", "\x08": "backspace", "\x7f": "backspace",
                 "\x1b": "esc", "\t": "tab"}
        if ch == "\x03":
            raise KeyboardInterrupt
        return named.get(ch, ch)

    import select
    import termios
    import tty
    fd = sys.stdin.fileno()
    old = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        b = os.read(fd, 1)
        if b == b"\x1b":
            seq = b""
            while len(seq) < 6 and select.select([fd], [], [], 0.03)[0]:
                seq += os.read(fd, 1)
            if not seq:
                return "esc"
            return {b"[A": "up", b"[B": "down", b"[C": "right", b"[D": "left",
                    b"OA": "up", b"OB": "down", b"OC": "right", b"OD": "left",
                    b"[5~": "pgup", b"[6~": "pgdn", b"[H": "home", b"[F": "end",
                    b"OH": "home", b"OF": "end", b"[1~": "home", b"[4~": "end"}.get(seq, "")
        if b == b"\x03":
            raise KeyboardInterrupt
        named = {b"\r": "enter", b"\n": "enter", b"\x7f": "backspace", b"\x08": "backspace",
                 b"\x04": "esc", b"\t": "tab"}
        if b in named:
            return named[b]
        if b and b[0] >= 0xC0:  # multi-byte UTF-8
            b += os.read(fd, 2 if b[0] >= 0xE0 else 1) if b[0] < 0xF0 else os.read(fd, 3)
        return b.decode("utf-8", "ignore")
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old)


def _question(text):
    return PAD + paint("?", BLURPLE, bold=True) + " " + paint(text, bold=True)


def _answered(question, answer):
    out(PAD + paint("✓", GREEN, bold=True) + " " + paint(question, MUTED) + "  " + answer)


def choose(title, items, label=str, hint=None, back=True, searchable=None):
    """Arrow-key menu with type-to-filter. Returns the chosen item, or None (back)."""
    if not items:
        return None
    labels = [sanitize(label(it)).replace("\n", " ") for it in items]
    hints = [sanitize(hint(it)).replace("\n", " ") if hint else "" for it in items]
    if searchable is None:
        searchable = len(items) > 7
    if not Term.interactive:
        return _choose_plain(title, items, labels, hints, back)

    page = max(4, min(12, term_rows() - 8, len(items)))
    query, cur, top, drawn, choice = "", 0, 0, 0, None
    raw(HIDE_CURSOR)
    try:
        while True:
            q = query.casefold()
            idx = [k for k in range(len(items))
                   if not q or q in labels[k].casefold() or q in hints[k].casefold()]
            cur = min(cur, max(len(idx) - 1, 0))
            top = min(max(top, cur - page + 1), cur)
            top = max(0, min(top, max(len(idx) - page, 0)))

            lines = [_question(title) + (paint("  {} of {}".format(len(idx), len(items)), FAINT)
                                         if q else paint("  {}".format(len(items)), FAINT)
                                         if len(items) > page else "")]
            if searchable:
                if query:
                    lines.append(PAD + "  " + paint("⌕ ", BLURPLE) + trunc(query, W() - 10) + paint("▏", PINK))
                else:
                    lines.append(PAD + "  " + paint("⌕ type to filter", FAINT))
            lines += _menu_rows(labels, hints, idx, cur, top, page, query)
            keys = "↑↓ move · enter select" + (" · esc back" if back else "")
            lines.append(PAD + "  " + paint(keys, FAINT))

            frame = "\033[{}A".format(drawn) if drawn else ""
            frame += "".join("\r\033[2K" + ln + "\n" for ln in lines)
            sys.stdout.write(frame)
            sys.stdout.flush()
            drawn = len(lines)

            key = read_key()
            if key == "up" and idx:
                cur = (cur - 1) % len(idx)
            elif key == "down" and idx:
                cur = (cur + 1) % len(idx)
            elif key == "pgup":
                cur = max(0, cur - page)
            elif key == "pgdn" and idx:
                cur = min(len(idx) - 1, cur + page)
            elif key == "home":
                cur = 0
            elif key == "end" and idx:
                cur = len(idx) - 1
            elif key == "enter" and idx:
                choice = items[idx[cur]]
                break
            elif key == "esc":
                if query:
                    query, cur = "", 0
                elif back:
                    break
            elif key == "backspace" and searchable:
                query, cur = query[:-1], 0
            elif searchable and len(key) == 1 and key.isprintable():
                query, cur = query + key, 0
            elif not searchable and key.isdigit() and 1 <= int(key) <= len(idx):
                choice = items[idx[int(key) - 1]]
                break
    finally:
        if drawn:
            sys.stdout.write("\033[{}A\r\033[J".format(drawn))
            sys.stdout.flush()
        raw(SHOW_CURSOR)

    if choice is not None:
        _answered(title, paint(trunc(label(choice), W() - text_w(title) - 10), BLURPLE, bold=True))
    return choice


def _menu_rows(labels, hints, idx, cur, top, page, query):
    rows = []
    avail = W() - len(PAD) - 6
    scroll = len(idx) > page
    if scroll:
        thumb_size = max(1, round(page * page / len(idx)))
        thumb_top = round((page - thumb_size) * top / max(len(idx) - page, 1))
    for r in range(page):
        k = top + r
        if k >= len(idx):
            rows.append(PAD + "    " + paint("no matches", MUTED, italic=True) if r == 0 and not idx else "")
            continue
        i, selected = idx[k], k == cur
        h = trunc(hints[i], avail // 2) if hints[i] else ""
        lab = trunc(labels[i], avail - text_w(h) - (2 if h else 0))
        gap = avail - text_w(lab) - text_w(h)
        pointer = paint("❯ ", PINK, bold=True) if selected else "  "
        row = PAD + pointer + _highlight(lab, query, selected) + " " * gap
        row += paint(h, MUTED if selected else FAINT)
        if scroll:
            on_thumb = thumb_top <= r < thumb_top + thumb_size
            row += "  " + (paint("┃", BLURPLE) if on_thumb else paint("│", FAINT))
        rows.append(row)
    return rows


def _highlight(text, query, selected):
    base = lambda t: paint(t, bold=selected)
    pos = text.lower().find(query.lower()) if query else -1
    if pos < 0 or len(text.lower()) != len(text):
        return base(text)
    end = pos + len(query)
    return base(text[:pos]) + paint(text[pos:end], PINK, bold=True) + base(text[end:])


def _choose_plain(title, items, labels, hints, back):
    """Numbered fallback when stdin isn't a terminal."""
    out()
    out(_question(title))
    for n, (lab, h) in enumerate(zip(labels, hints), 1):
        out(PAD + paint("{:>4}".format(n), BLURPLE) + "  " + lab + ("  " + paint(h, FAINT) if h else ""))
    while True:
        try:
            val = input(PAD + paint("› number or search" + (" (b = back)" if back else "") + ": ", MUTED)).strip()
        except EOFError:
            return None
        if back and val.lower() in ("b", "back"):
            return None
        if val.isdigit() and 1 <= int(val) <= len(items):
            return items[int(val) - 1]
        hits = [it for it, lab in zip(items, labels) if val and val.lower() in lab.lower()]
        if len(hits) == 1:
            return hits[0]
        warn("{} matches — be more specific.".format(len(hits)) if hits else "No match.")


def confirm(question, default=True):
    if not Term.interactive:
        try:
            val = input(_question(question) + paint(" [Y/n] " if default else " [y/N] ", FAINT)).strip().lower()
        except EOFError:
            return default
        return val.startswith("y") if val else default

    value = default
    raw(HIDE_CURSOR)
    try:
        while True:
            yes = paint(" Yes ", WHITE, bold=True, bg=BLURPLE) if value else paint(" Yes ", MUTED)
            no = paint(" No ", WHITE, bold=True, bg=BLURPLE) if not value else paint(" No ", MUTED)
            if not Term.color:
                yes, no = ("[Yes]", " No ") if value else (" Yes ", "[No]")
            sys.stdout.write("\r\033[2K" + _question(question) + "   " + yes + " " + no)
            sys.stdout.flush()
            key = read_key()
            if key in ("left", "right", "tab", "h", "l"):
                value = not value
            elif key in ("y", "Y", "n", "N"):
                value = key.lower() == "y"
                break
            elif key == "enter":
                break
            elif key == "esc":
                value = False
                break
    finally:
        sys.stdout.write("\r\033[2K")
        raw(SHOW_CURSOR)
    _answered(question, paint("Yes" if value else "No", BLURPLE, bold=True))
    return value


def ask_number(question, default):
    while True:
        try:
            val = input(_question(question) + paint(" ({:,})".format(default), FAINT) + paint(" › ", MUTED)).strip()
        except EOFError:
            return default
        val = val.replace(",", "").replace(".", "").replace("_", "")
        if not val:
            return default
        if val.isdigit() and int(val) > 0:
            return int(val)
        warn("Enter a whole number above 0.")


# ─────────────────────────────────────────────────────────
#  TOKEN
# ─────────────────────────────────────────────────────────

def _clean_token(t):
    return (t or "").strip().strip('"').strip("'").strip()


def find_token():
    """Returns (token, source). Looks at env var, .env files, then the saved token file."""
    t = _clean_token(os.environ.get("DISCORD_TOKEN"))
    if t:
        return t, "env"
    for env in (Path.cwd() / ".env", Path(__file__).resolve().parent / ".env"):
        try:
            for line in env.read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("DISCORD_TOKEN="):
                    t = _clean_token(line.split("=", 1)[1])
                    if t:
                        return t, ".env"
        except OSError:
            pass
    try:
        t = _clean_token(TOKEN_FILE.read_text(encoding="utf-8"))
        if t:
            return t, "file"
    except OSError:
        pass
    return None, None


def save_token(token):
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except OSError:
        pass


def forget_token():
    try:
        TOKEN_FILE.unlink()
        return True
    except OSError:
        return False


def ask_token():
    n = lambda k: paint(k, BLURPLE, bold=True)
    box([
        n("1") + "  Open " + paint("discord.com/app", bold=True) + " in your browser",
        n("2") + "  Press " + paint("F12", bold=True) + " → " + paint("Network", bold=True) + " tab, then click any channel",
        n("3") + "  Pick a request and copy its " + paint("authorization", bold=True) + " header",
        "",
        paint("!", YELLOW, bold=True) + paint("  Your token gives full access to your account. Never share it.", MUTED),
    ], title=paint("Discord token needed", bold=True))
    out()
    if not Term.interactive:
        fail("No token found. Set DISCORD_TOKEN or run interactively.")
        return None
    try:
        sys.stdout.write(_question("Paste your token") + paint(" (hidden) › ", MUTED))
        sys.stdout.flush()
        token = _clean_token(getpass.getpass(""))
    except (EOFError, getpass.GetPassWarning):
        token = ""
    if not token:
        fail("No token entered.")
        return None
    return token


# ─────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────

_HEADERS = {
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
    "accept": "*/*",
    "accept-language": "en-US,en;q=0.9",
    "x-discord-locale": "en-US",
}


def api(path, token, params=None):
    """GET a Discord endpoint. Returns (data, error_text)."""
    url = API + path + ("?" + urllib.parse.urlencode(params) if params else "")
    req = urllib.request.Request(url, headers=dict(_HEADERS, authorization=token))
    last = "request failed"
    for attempt in range(5):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode("utf-8")), None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = float(json.loads(e.read().decode()).get("retry_after", 1))
                except Exception:
                    wait = 1.0
                wait = min(max(wait, 0.5), 60.0)
                if _live is not None:
                    _live.update(meta="rate limited · retrying in {:.1f}s".format(wait))
                else:
                    warn("Rate limited — waiting {:.1f}s".format(wait))
                time.sleep(wait)
                last = "rate limited"
                continue
            if e.code == 401:
                return None, "invalid or expired token"
            if e.code == 403:
                return None, "you don't have access"
            if e.code == 404:
                return None, "not found"
            if e.code >= 500:
                last = "Discord server error ({})".format(e.code)
                time.sleep(1 + attempt)
                continue
            return None, "HTTP {}".format(e.code)
        except (urllib.error.URLError, OSError, ValueError) as exc:
            last = str(getattr(exc, "reason", None) or exc)
            time.sleep(1 + attempt)
    return None, last


# ─────────────────────────────────────────────────────────
#  DISCORD DATA
# ─────────────────────────────────────────────────────────

def display_name(user):
    return (user or {}).get("global_name") or (user or {}).get("username") or "unknown"


def parse_ts(stamp):
    stamp = stamp.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(stamp)
    except ValueError:  # Python < 3.11 is picky about fraction digits
        return datetime.fromisoformat(re.sub(r"\.\d+", "", stamp))


def snowflake_time(sid):
    return datetime.fromtimestamp(((int(sid) >> 22) + DISCORD_EPOCH_MS) / 1000, tz=timezone.utc)


def ago(dt):
    secs = (datetime.now(timezone.utc) - dt).total_seconds()
    for limit, size, unit in ((60, 1, "s"), (3600, 60, "m"), (86400, 3600, "h"),
                              (86400 * 14, 86400, "d"), (86400 * 60, 604800, "w"),
                              (86400 * 365, 2629800, "mo")):
        if secs < limit:
            return "just now" if unit == "s" else "{}{} ago".format(int(secs // size), unit)
    return "{}y ago".format(int(secs // 31557600))


def compact(n):
    if n >= 1_000_000:
        return "{:.1f}M".format(n / 1_000_000).replace(".0M", "M")
    if n >= 1_000:
        return "{:.1f}k".format(n / 1_000).replace(".0k", "k")
    return str(n)


def plural(n, word):
    return "{:,} {}{}".format(n, word, "" if n == 1 else "s")


def human_size(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return "{:.0f} {}".format(n, unit) if unit == "B" else "{:.1f} {}".format(n, unit)
        n /= 1024


def _describe_dm(ch):
    rec = ch.get("recipients") or []
    if ch.get("type") == 1:
        u = rec[0] if rec else {}
        name = display_name(u)
        handle = "@" + u.get("username", "unknown")
        return {"id": ch["id"], "title": name if handle[1:] == name else "{} ({})".format(name, handle),
                "label": name, "kind": handle, "file": "DM " + u.get("username", name)}
    names = ", ".join(display_name(u) for u in rec[:4]) + ("…" if len(rec) > 4 else "")
    name = ch.get("name") or names or "Unnamed group"
    return {"id": ch["id"], "title": name, "label": name,
            "kind": "group · {} people".format(len(rec) + 1), "file": "Group " + name}


def pick_dm(token):
    with Spinner("Loading your conversations"):
        data, e = api("/users/@me/channels", token)
    if e:
        fail("Couldn't load DMs: " + e)
        return None
    dms = []
    for ch in data:
        if ch.get("type") in (1, 3):
            d = _describe_dm(ch)
            d["last"] = int(ch.get("last_message_id") or 0)
            dms.append(d)
    if not dms:
        warn("No DMs found.")
        return None
    dms.sort(key=lambda d: d["last"], reverse=True)  # most recent first, like Discord
    hint = lambda d: d["kind"] + ("  ·  " + ago(snowflake_time(d["last"])) if d["last"] else "")
    return choose("Pick a conversation", dms, label=lambda d: d["label"], hint=hint)


_CHANNEL_ICON = {0: "#", 5: "»", 2: "♪"}  # text, announcement, voice-text


def _organize_channels(chans):
    cats = {c["id"]: c for c in chans if c.get("type") == 4}
    readable = [c for c in chans if c.get("type") in _CHANNEL_ICON]

    def order(c):
        parent = cats.get(c.get("parent_id"))
        return (1 if parent else 0, parent.get("position", 0) if parent else 0,
                parent["id"] if parent else "", c.get("type") == 2, c.get("position", 0))

    readable.sort(key=order)
    for c in readable:
        parent = cats.get(c.get("parent_id"))
        c["_category"] = parent["name"] if parent else ""
    return readable


def pick_server_channel(token):
    with Spinner("Loading your servers"):
        guilds, e = api("/users/@me/guilds", token, {"with_counts": "true"})
    if e:
        fail("Couldn't load servers: " + e)
        return None
    if not guilds:
        warn("You're not in any servers.")
        return None
    guilds.sort(key=lambda g: g["name"].casefold())

    def guild_hint(g):
        parts = []
        if g.get("owner"):
            parts.append("★ owner")
        if g.get("approximate_member_count"):
            parts.append(compact(g["approximate_member_count"]) + " members")
        return "  ·  ".join(parts)

    while True:
        g = choose("Pick a server", guilds, label=lambda g: g["name"], hint=guild_hint)
        if g is None:
            return None
        with Spinner("Loading channels in " + g["name"]):
            chans, e = api("/guilds/{}/channels".format(g["id"]), token)
        if e:
            fail("Couldn't load channels: " + e)
            continue
        chans = _organize_channels(chans)
        if not chans:
            warn("No text channels in this server.")
            continue
        c = choose("Pick a channel in " + g["name"], chans,
                   label=lambda c: _CHANNEL_ICON[c["type"]] + " " + c["name"],
                   hint=lambda c: c["_category"].lower())
        if c is None:
            continue  # back to the server list
        return {"id": c["id"], "title": "#{} · {}".format(c["name"], g["name"]),
                "file": "{} #{}".format(g["name"], c["name"])}


def lookup_channel(token, raw_id):
    """Resolve a channel ID or discord.com/channels/... link."""
    ids = re.findall(r"\d{15,22}", raw_id or "")
    if not ids:
        fail("That doesn't look like a channel ID or link.")
        return None
    cid = ids[-1]
    with Spinner("Looking up channel"):
        ch, e = api("/channels/" + cid, token)
        guild = None
        if ch and ch.get("guild_id"):
            guild, _ = api("/guilds/" + ch["guild_id"], token)
    if e:
        fail("Channel {}: {}".format(cid, e))
        return None
    if ch.get("type") in (1, 3):
        return _describe_dm(ch)
    gname = (guild or {}).get("name")
    title = "#" + ch.get("name", cid) + (" · " + gname if gname else "")
    return {"id": cid, "title": title, "file": (gname + " " if gname else "") + "#" + ch.get("name", cid)}


def ask_channel_id(token):
    try:
        val = input(_question("Channel ID or link") + paint(" › ", MUTED)).strip()
    except EOFError:
        return None
    return lookup_channel(token, val) if val else None


# ─────────────────────────────────────────────────────────
#  FETCHING
# ─────────────────────────────────────────────────────────

def start_of_day(days_back):
    """Local midnight, (days_back - 1) days ago. 1 = today."""
    return datetime.combine(date.today() - timedelta(days=days_back - 1), dtime.min).astimezone()


def choose_range():
    options = [
        ("days", 1, "Today", "since midnight"),
        ("days", 2, "Today and yesterday", ""),
        ("days", 7, "Last 7 days", ""),
        ("days", 30, "Last 30 days", ""),
        ("count", 100, "Last 100 messages", ""),
        ("count", 1000, "Last 1,000 messages", ""),
        ("all", None, "Entire history", "can take a while"),
        ("days", "?", "Custom number of days…", ""),
        ("count", "?", "Custom number of messages…", ""),
    ]
    pick = choose("How far back?", options, label=lambda o: o[2], hint=lambda o: o[3], searchable=False)
    if pick is None:
        return None
    mode, value = pick[0], pick[1]
    if value == "?":
        value = ask_number("How many days" if mode == "days" else "How many messages",
                           7 if mode == "days" else 500)
    return mode, value


def fetch_messages(token, channel_id, mode, value):
    """Page backwards through history. Returns (messages newest-first, seconds, interrupted)."""
    limit = value if mode == "count" else None
    since = start_of_day(value) if mode == "days" else None
    msgs, before, t0, interrupted = [], None, time.time(), False

    with Spinner("Fetching messages") as sp:
        try:
            while limit is None or len(msgs) < limit:
                want = min(100, limit - len(msgs)) if limit else 100
                params = {"limit": want}
                if before:
                    params["before"] = before
                batch, e = api("/channels/{}/messages".format(channel_id), token, params)
                if e:
                    fail("Couldn't read messages: " + e)
                    break
                if not batch:
                    break
                reached_cutoff = False
                for m in batch:
                    if since and parse_ts(m["timestamp"]) < since:
                        reached_cutoff = True
                        break
                    msgs.append(m)
                oldest = parse_ts(batch[-1]["timestamp"]).astimezone()
                sp.update(text="Fetching messages  {:,}".format(len(msgs)),
                          meta="back to " + oldest.strftime("%a %d %b %Y"),
                          progress=len(msgs) / limit if limit else None)
                if reached_cutoff or len(batch) < want:
                    break
                before = batch[-1]["id"]
                time.sleep(0.25)
        except KeyboardInterrupt:
            interrupted = True

    if interrupted:
        warn("Stopped early — keeping the {:,} messages fetched so far.".format(len(msgs)))
    return msgs, time.time() - t0, interrupted


# ─────────────────────────────────────────────────────────
#  MESSAGE FORMATTING
# ─────────────────────────────────────────────────────────

_SYSTEM_TEXT = {1: "added someone to the group", 2: "removed someone from the group",
                3: "started a call", 4: "changed the channel name", 5: "changed the channel icon",
                6: "pinned a message", 7: "joined the server", 8: "boosted the server",
                9: "boosted the server", 10: "boosted the server", 11: "boosted the server",
                18: "started a thread"}


def message_text(m):
    """Readable message content: mentions, emoji and timestamps resolved."""
    text = m.get("content") or ""
    users = {u["id"]: display_name(u) for u in m.get("mentions") or []}
    text = re.sub(r"<@!?(\d+)>", lambda x: "@" + users.get(x.group(1), "someone"), text)
    text = re.sub(r"<@&\d+>", "@role", text)
    text = re.sub(r"<#\d+>", "#channel", text)
    text = re.sub(r"<a?:(\w+):\d+>", r":\1:", text)
    text = re.sub(r"<t:(\d+)(?::\w)?>", lambda x: datetime.fromtimestamp(
        int(x.group(1)), timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M"), text)
    if not text and m.get("type") in _SYSTEM_TEXT:
        text = "[{}]".format(_SYSTEM_TEXT[m["type"]])
    return sanitize(text)


def message_extras(m):
    """List of (kind, text) for attachments, embeds and stickers."""
    extras = []
    for a in m.get("attachments") or []:
        extras.append(("file", "{}  ({})".format(a.get("filename", "file"), human_size(a.get("size", 0)))))
    for e in m.get("embeds") or []:
        t = e.get("title") or e.get("description") or e.get("url")
        if t and t not in (m.get("content") or ""):
            extras.append(("embed", sanitize(t).replace("\n", " ")))
    for s in m.get("sticker_items") or []:
        extras.append(("sticker", s.get("name", "sticker")))
    return extras


def chronological(messages):
    return sorted(messages, key=lambda m: int(m["id"]))


def author_color(user_id):
    return AUTHOR_COLORS[int(user_id) % len(AUTHOR_COLORS)]


def preview(messages, chan):
    msgs = chronological(messages)
    shown = msgs[-PREVIEW_LIMIT:]
    width = W() - len(PAD) - 2
    out()
    rule(trunc(chan["title"], 50))
    if len(msgs) > len(shown):
        note("showing the latest {} of {:,} — save to a file for everything".format(len(shown), len(msgs)))

    day, prev_author, prev_ts = None, None, None
    for m in shown:
        ts = parse_ts(m["timestamp"]).astimezone()
        if ts.date() != day:
            day, prev_author = ts.date(), None
            out()
            rule(ts.strftime("%A %d %B %Y"))
        ref = m.get("referenced_message") if m.get("type") == 19 else None
        author = m.get("author") or {}
        grouped = (author.get("id") == prev_author and not ref
                   and (ts - prev_ts).total_seconds() < 420)
        if not grouped:
            out()
            if ref:
                snippet = message_text(ref) or "…"
                out(PAD + "  " + paint("╭ ", FAINT) + paint("@" + display_name(ref.get("author")), MUTED)
                    + " " + paint(trunc(snippet, width - 20), FAINT))
            tag = "  " + paint(" BOT ", WHITE, bold=True, bg=BLURPLE) if author.get("bot") else ""
            out(PAD + paint(trunc(display_name(author), 32), author_color(author.get("id", 0)), bold=True)
                + tag + "  " + paint(ts.strftime("%H:%M"), FAINT))

        text = message_text(m)
        is_system = m.get("type") in _SYSTEM_TEXT and not m.get("content")
        lines = []
        for para in text.split("\n") if text else []:
            lines += textwrap.wrap(para, max(width - 2, 20)) or [""]
        for n, line in enumerate(lines):
            styled = paint(line, MUTED, italic=True) if is_system else line
            if n == len(lines) - 1 and m.get("edited_timestamp"):
                styled += paint(" (edited)", FAINT)
            out(PAD + "  " + styled)
        for kind, t in message_extras(m):
            icon = {"file": "◇", "embed": "▍", "sticker": "◈"}[kind]
            out(PAD + "  " + paint(icon, BLURPLE) + " " + paint(trunc(t, width - 4), MUTED))
        prev_author, prev_ts = author.get("id"), ts
    out()
    rule()


def summary(messages, chan, seconds):
    msgs = chronological(messages)
    first = parse_ts(msgs[0]["timestamp"]).astimezone()
    last = parse_ts(msgs[-1]["timestamp"]).astimezone()
    counts = Counter(m["author"]["id"] for m in msgs)
    names = {m["author"]["id"]: display_name(m["author"]) for m in msgs}
    files = sum(len(m.get("attachments") or []) for m in msgs)

    num = lambda n, one, many: paint("{:,}".format(n), bold=True) + " " + paint(one if n == 1 else many, MUTED)
    rows = [
        num(len(msgs), "message", "messages") + "    " + num(len(counts), "person", "people")
        + "    " + num(files, "attachment", "attachments"),
        paint(first.strftime("%d %b %Y %H:%M") + "  →  " + last.strftime("%d %b %Y %H:%M"), MUTED),
        "",
    ]
    top = counts.most_common(3)
    for uid, n in top:
        nm = trunc(names[uid], 16)
        bar = max(1, round(18 * n / top[0][1]))
        rows.append(paint(nm, author_color(uid)) + " " * (17 - text_w(nm)) + gradient("━" * bar)
                    + " " * (19 - bar) + paint("{:,}".format(n), MUTED))
    rows += ["", paint("fetched in {:.1f}s".format(seconds), FAINT)]
    out()
    box(rows, title=paint(trunc(chan["title"], 40), bold=True))


# ─────────────────────────────────────────────────────────
#  SAVING
# ─────────────────────────────────────────────────────────

FORMATS = [
    ("txt", "Save as text", ".txt · easy to read"),
    ("md", "Save as Markdown", ".md · nice in editors"),
    ("json", "Save as JSON", ".json · full raw data"),
    ("csv", "Save as CSV", ".csv · Excel / Sheets"),
]


def slug(name):
    s = re.sub(r"[^\w\-.]+", "_", name, flags=re.UNICODE)
    return re.sub(r"_{2,}", "_", s).strip("_.")[:80] or "discord_channel"


def save_messages(messages, chan, fmt, out_dir):
    folder = Path(out_dir) / slug(chan["file"])
    folder.mkdir(parents=True, exist_ok=True)
    base = "{}_{}".format(slug(chan["file"]), datetime.now().strftime("%Y%m%d_%H%M%S"))
    path, n = folder / "{}.{}".format(base, fmt), 2
    while path.exists():
        path, n = folder / "{}_{}.{}".format(base, n, fmt), n + 1
    # CSV gets a BOM so Excel reads UTF-8; its rows already end in \r\n
    with open(path, "w", encoding="utf-8-sig" if fmt == "csv" else "utf-8",
              newline="" if fmt == "csv" else None) as f:
        f.write(render_export(messages, chan, fmt))
    return path


def render_export(messages, chan, fmt):
    """The export as one string, in txt, md, json or csv."""
    msgs = chronological(messages)
    exported = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M")
    f = io.StringIO()
    if fmt in ("txt", "md"):
        _write_text(f, msgs, chan, exported, markdown=(fmt == "md"))
    elif fmt == "json":
        json.dump({"channel": chan["title"], "channel_id": chan["id"],
                   "exported_at": datetime.now().astimezone().isoformat(),
                   "message_count": len(msgs), "messages": msgs},
                  f, indent=2, ensure_ascii=False, default=str)
    elif fmt == "csv":
        w = csv.writer(f)
        w.writerow(["timestamp_utc", "date", "time", "author", "username", "author_id",
                    "message", "reply_to", "edited", "attachments"])
        for m in msgs:
            dt = parse_ts(m["timestamp"]).astimezone()
            a = m.get("author") or {}
            w.writerow([m["timestamp"], dt.strftime("%Y-%m-%d"), dt.strftime("%H:%M:%S"),
                        display_name(a), a.get("username", ""), a.get("id", ""),
                        message_text(m), (m.get("message_reference") or {}).get("message_id", ""),
                        "yes" if m.get("edited_timestamp") else "",
                        "; ".join(x.get("url", "") for x in m.get("attachments") or [])])
    return f.getvalue()


def copy_to_clipboard(text):
    """Put text on the system clipboard. Raises OSError when that isn't possible."""
    if sys.platform == "win32":
        _windows_clipboard(text)
        return
    if sys.platform == "darwin":
        tools = [["pbcopy"]]
    else:
        tools = [["wl-copy"], ["xclip", "-selection", "clipboard"], ["xsel", "--clipboard", "--input"]]
    for cmd in tools:
        if shutil.which(cmd[0]):
            env = dict(os.environ, LANG=os.environ.get("LANG") or "en_US.UTF-8")
            subprocess.run(cmd, input=text.encode("utf-8"), check=True, env=env)
            return
    raise OSError("no clipboard tool found (install wl-clipboard or xclip)")


def _windows_clipboard(text):
    import ctypes
    from ctypes import wintypes
    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    kernel32.GlobalAlloc.argtypes = [wintypes.UINT, ctypes.c_size_t]
    kernel32.GlobalAlloc.restype = wintypes.HGLOBAL
    kernel32.GlobalLock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalLock.restype = ctypes.c_void_p
    kernel32.GlobalUnlock.argtypes = [wintypes.HGLOBAL]
    kernel32.GlobalFree.argtypes = [wintypes.HGLOBAL]
    user32.OpenClipboard.argtypes = [wintypes.HWND]
    user32.SetClipboardData.argtypes = [wintypes.UINT, wintypes.HANDLE]
    user32.SetClipboardData.restype = wintypes.HANDLE

    buf = ctypes.create_unicode_buffer(text.replace("\r\n", "\n").replace("\n", "\r\n"))
    size = ctypes.sizeof(buf)
    for _ in range(20):  # another app may hold the clipboard for a moment
        if user32.OpenClipboard(None):
            break
        time.sleep(0.05)
    else:
        raise OSError("the clipboard is busy, try again")
    try:
        user32.EmptyClipboard()
        handle = kernel32.GlobalAlloc(0x0002, size)  # GMEM_MOVEABLE
        if not handle:
            raise OSError("out of memory")
        ctypes.memmove(kernel32.GlobalLock(handle), buf, size)
        kernel32.GlobalUnlock(handle)
        if not user32.SetClipboardData(13, handle):  # CF_UNICODETEXT
            kernel32.GlobalFree(handle)
            raise OSError("couldn't write to the clipboard")
    finally:
        user32.CloseClipboard()


def copy_messages(messages, chan, fmt="txt"):
    try:
        with Spinner("Copying to clipboard"):
            copy_to_clipboard(render_export(messages, chan, fmt))
    except (OSError, subprocess.CalledProcessError) as e:
        fail("Couldn't copy: {}".format(e))
        return False
    ok("Copied {} to your clipboard".format(plural(len(messages), "message"))
       + paint("  as " + fmt, FAINT))
    return True


def _write_text(f, msgs, chan, exported, markdown):
    first = parse_ts(msgs[0]["timestamp"]).astimezone().strftime("%Y-%m-%d")
    last = parse_ts(msgs[-1]["timestamp"]).astimezone().strftime("%Y-%m-%d")
    meta = "{:,} messages · {} → {} · exported {}".format(len(msgs), first, last, exported)
    if markdown:
        f.write("# {}\n\n> {}\n".format(chan["title"], meta))
    else:
        f.write("{}\n{}\n{}\n".format(chan["title"], meta, "═" * 60))

    day = None
    for m in msgs:
        dt = parse_ts(m["timestamp"]).astimezone()
        if dt.date() != day:
            day = dt.date()
            heading = dt.strftime("%A %d %B %Y")
            f.write("\n## {}\n".format(heading) if markdown else "\n── {} {}\n".format(heading, "─" * 30))
        author = display_name(m.get("author"))
        ref = m.get("referenced_message") if m.get("type") == 19 else None
        edited = " (edited)" if m.get("edited_timestamp") else ""
        text = message_text(m)
        if markdown:
            f.write("\n**{}** · `{}`{}\n".format(author, dt.strftime("%H:%M"), edited))
            if ref:
                f.write("> ↳ @{}: {}\n".format(display_name(ref.get("author")), trunc(message_text(ref), 80)))
            if text:
                f.write(text + "\n")
            for a in m.get("attachments") or []:
                f.write("- 📎 [{}]({})\n".format(a.get("filename", "file"), a.get("url", "")))
        else:
            if ref:
                f.write("{}↳ @{}: {}\n".format(" " * 10, display_name(ref.get("author")),
                                               trunc(message_text(ref), 80)))
            body = text.replace("\n", "\n" + " " * 10)
            f.write("{}  {}: {}{}\n".format(dt.strftime("%H:%M:%S"), author, body, edited))
            for a in m.get("attachments") or []:
                f.write("{}[file] {}  {}\n".format(" " * 10, a.get("filename", "file"), a.get("url", "")))
            for kind, t in message_extras(m):
                if kind != "file":
                    f.write("{}[{}] {}\n".format(" " * 10, kind, t))


def open_folder(path):
    try:
        if sys.platform == "win32":
            os.startfile(str(path))
        elif sys.platform == "darwin":
            subprocess.Popen(["open", str(path)])
        else:
            subprocess.Popen(["xdg-open", str(path)])
    except Exception as e:
        warn("Couldn't open folder: {}".format(e))


# ─────────────────────────────────────────────────────────
#  SELF-UPDATE
# ─────────────────────────────────────────────────────────

def _version_tuple(v):
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def check_for_update(argv):
    """Replace this file with a newer GitHub version and rerun it.
    Returns the new run's exit code, or None when no update happened."""
    if os.environ.get("DISCORD_FETCH_NO_UPDATE"):
        return None
    me = Path(__file__).resolve()
    with Spinner("Checking for updates"):
        try:
            req = urllib.request.Request(UPDATE_URL, headers={"user-agent": "discord-fetch/" + VERSION,
                                                              "cache-control": "no-cache"})
            with urllib.request.urlopen(req, timeout=4) as r:
                source = r.read().decode("utf-8")
        except Exception:
            return None  # offline or GitHub unreachable: just run this version
    found = re.search(r'^VERSION\s*=\s*"([^"]+)"', source, re.M)
    if not found or _version_tuple(found.group(1)) <= _version_tuple(VERSION):
        return None
    try:
        compile(source, str(me), "exec")  # never install a broken download
        tmp = me.with_name(me.name + ".new")
        tmp.write_text(source, encoding="utf-8", newline="\n")
        os.replace(str(tmp), str(me))
    except (SyntaxError, OSError) as e:
        warn("Update to v{} failed: {}".format(found.group(1), e))
        return None
    ok("Updated  v{} → v{}".format(VERSION, paint(found.group(1), bold=True)))
    env = dict(os.environ, DISCORD_FETCH_NO_UPDATE="1")
    return subprocess.call([sys.executable, str(me)] + list(argv), env=env)


def launched_by_double_click():
    """True when Windows Explorer started us, so the window would close right away."""
    if sys.platform != "win32" or not Term.interactive:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        class Entry(ctypes.Structure):
            _fields_ = [("dwSize", wintypes.DWORD), ("cntUsage", wintypes.DWORD),
                        ("th32ProcessID", wintypes.DWORD), ("th32DefaultHeapID", ctypes.c_void_p),
                        ("th32ModuleID", wintypes.DWORD), ("cntThreads", wintypes.DWORD),
                        ("th32ParentProcessID", wintypes.DWORD), ("pcPriClassBase", ctypes.c_long),
                        ("dwFlags", wintypes.DWORD), ("szExeFile", ctypes.c_wchar * 260)]

        k = ctypes.windll.kernel32
        k.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        snap = k.CreateToolhelp32Snapshot(0x2, 0)
        procs, e = {}, Entry()
        e.dwSize = ctypes.sizeof(Entry)
        more = k.Process32FirstW(snap, ctypes.byref(e))
        while more:
            procs[e.th32ProcessID] = (e.th32ParentProcessID, e.szExeFile.lower())
            more = k.Process32NextW(snap, ctypes.byref(e))
        k.CloseHandle(snap)

        pid = os.getpid()
        for _ in range(6):  # walk up past the py launcher / python itself
            parent = procs.get(pid, (None, ""))[0]
            if parent not in procs:
                return False
            name = procs[parent][1]
            if name in ("py.exe", "pyw.exe", "python.exe", "python3.exe", "pythonw.exe"):
                pid = parent
                continue
            return name == "explorer.exe"
    except Exception:
        pass
    return False


# ─────────────────────────────────────────────────────────
#  FLOW
# ─────────────────────────────────────────────────────────

def login(token, source):
    """Validate the token, re-asking when needed. Returns (user, token, source) or Nones."""
    while True:
        if not token:
            token, source = ask_token(), "prompt"
            if not token:
                return None, None, None
        with Spinner("Connecting to Discord"):
            user, e = api("/users/@me", token)
        if user:
            if source == "prompt" and confirm("Remember this token on this computer?"):
                save_token(token)
                note("saved to " + tilde(TOKEN_FILE))
                source = "file"
            return user, token, source
        fail("Login failed: " + e)
        if e != "invalid or expired token" or not Term.interactive:
            return None, None, None
        if source == "file":
            forget_token()
            note("removed the saved token — paste a fresh one")
        elif source in ("env", ".env"):
            note("update DISCORD_TOKEN in your " + ("environment" if source == "env" else ".env file"))
            return None, None, None
        token = None


def session_card(user, source):
    where = {"env": "token from DISCORD_TOKEN", ".env": "token from .env",
             "file": "token saved in " + tilde(TOKEN_FILE), "prompt": "token entered this session"}
    name = display_name(user)
    handle = "@" + user.get("username", "")
    line = paint("●", GREEN) + "  " + paint(name, bold=True)
    if handle[1:] != name:
        line += "  " + paint(handle, MUTED)
    box([line, "   " + paint(where.get(source, ""), FAINT)], title=paint("connected", GREEN))


def after_fetch(messages, chan, out_dir):
    """Save / preview menu. Returns 'again' or 'quit'."""
    saved, last_path, copied = {}, None, None
    while True:
        items = [("copy", "Copy to clipboard", "✓ copied as " + copied if copied else "paste it anywhere")]
        items += [(k, lab, "✓ saved" if k in saved else h) for k, lab, h in FORMATS]
        items.append(("preview", "Preview in terminal", "latest {}".format(PREVIEW_LIMIT)))
        if last_path:
            items.append(("open", "Open export folder", tilde(last_path.parent)))
        items += [("again", "Export another channel", ""), ("quit", "Quit", "")]
        pick = choose("What now?", items, label=lambda i: i[1], hint=lambda i: i[2],
                      back=False, searchable=False)
        action = pick[0] if pick else "quit"
        if action in ("again", "quit"):
            return action
        if action == "copy":
            fmt = choose("Copy as", [(k, lab.replace("Save as", "Copy as"), h) for k, lab, h in FORMATS],
                         label=lambda i: i[1], hint=lambda i: i[2], searchable=False)
            if fmt and copy_messages(messages, chan, fmt[0]):
                copied = fmt[0]
        elif action == "preview":
            preview(messages, chan)
        elif action == "open":
            open_folder(last_path.parent)
        else:
            try:
                last_path = save_messages(messages, chan, action, out_dir)
                saved[action] = last_path
                ok("Saved " + plural(len(messages), "message"))
                note(tilde(last_path))
            except OSError as e:
                fail("Couldn't save: {}".format(e))


def export_channel(token, chan, out_dir, rng=None, fmt=None, copy=False):
    out()
    if rng is None:
        rng = choose_range()
        if rng is None:
            return "again"
    messages, seconds, _ = fetch_messages(token, chan["id"], *rng)
    if not messages:
        warn("No messages in that range.")
        return "quit" if fmt or copy else "again"
    ok("Fetched " + plural(len(messages), "message"))
    if fmt or copy:
        if fmt:
            path = save_messages(messages, chan, fmt, out_dir)
            ok("Saved → " + tilde(path))
        if copy:
            copy_messages(messages, chan, fmt or "txt")
        return "quit"
    summary(messages, chan, seconds)
    out()
    return after_fetch(messages, chan, out_dir)


def goodbye():
    out()
    out(PAD + gradient("◆ see you next time"))
    out()


def parse_args(argv):
    p = argparse.ArgumentParser(
        prog="fetch.py",
        description="Export Discord DMs and channels from your terminal.",
        epilog="examples:\n"
               "  python fetch.py\n"
               "  python fetch.py --channel 123456789012345678 --days 7\n"
               "  python fetch.py --channel https://discord.com/channels/1/2 --all --format md\n"
               "  python fetch.py --channel 123456789012345678 --days 1 --copy",
        formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--channel", metavar="ID", help="channel ID or link (skips the menus)")
    rng = p.add_mutually_exclusive_group()
    rng.add_argument("--days", type=int, metavar="N", help="messages from the last N days (1 = today)")
    rng.add_argument("--count", type=int, metavar="N", help="the last N messages")
    rng.add_argument("--all", action="store_true", help="the entire history")
    p.add_argument("--format", choices=[f[0] for f in FORMATS], help="save straight to this format")
    p.add_argument("--copy", action="store_true",
                   help="copy the result to your clipboard (as --format, default txt)")
    p.add_argument("--out", metavar="DIR", help="export folder (default: ~/Discord_Messages)")
    p.add_argument("--no-color", action="store_true", help="plain output")
    p.add_argument("--logout", action="store_true", help="forget the saved token and exit")
    p.add_argument("--version", action="version", version="discord fetch " + VERSION)
    args = p.parse_args(argv)
    for n in (args.days, args.count):
        if n is not None and n < 1:
            p.error("--days and --count need a number above 0")
    return args


def main(argv=None):
    args = parse_args(argv)
    if args.no_color:
        Term.color = False
    out_dir = Path(args.out or os.environ.get("DISCORD_FETCH_DIR") or DEFAULT_SAVE_DIR).expanduser()

    if args.logout:
        ok("Saved token removed.") if forget_token() else note("No saved token.")
        return 0

    raw("\033]0;Discord Fetch\007")
    code = check_for_update(sys.argv[1:] if argv is None else argv)
    if code is not None:
        raise Relaunched(code)
    banner()

    token, source = find_token()
    user, token, source = login(token, source)
    if not user:
        return 1
    session_card(user, source)

    rng = None
    if args.days:
        rng = ("days", args.days)
    elif args.count:
        rng = ("count", args.count)
    elif args.all:
        rng = ("all", None)

    if args.channel:
        chan = lookup_channel(token, args.channel)
        if not chan:
            return 1
        ok("Channel: " + paint(chan["title"], bold=True))
        if export_channel(token, chan, out_dir, rng, args.format, args.copy) == "quit":
            goodbye()
            return 0

    while True:
        out()
        menu = [("dm", "Direct messages", "1:1 and group chats"),
                ("server", "Server channel", "pick a server, then a channel"),
                ("id", "Channel by ID or link", "paste it")]
        if find_token()[1] == "file":
            menu.append(("logout", "Forget saved token", tilde(TOKEN_FILE)))
        menu.append(("quit", "Quit", ""))
        pick = choose("What do you want to export?", menu, label=lambda i: i[1],
                      hint=lambda i: i[2], back=False, searchable=False)
        action = pick[0] if pick else "quit"

        if action == "quit":
            break
        if action == "logout":
            forget_token()
            ok("Saved token removed. You'll be asked for it next time.")
            continue
        chan = {"dm": pick_dm, "server": pick_server_channel, "id": ask_channel_id}[action](token)
        if not chan:
            continue
        if export_channel(token, chan, out_dir, rng) == "quit":
            break

    goodbye()
    return 0


class Relaunched(Exception):
    """The updated script ran in a child process; its exit code is the result."""
    def __init__(self, code):
        super().__init__(code)
        self.code = code


def run():
    pause = launched_by_double_click()
    try:
        code = main()
    except Relaunched as r:
        return r.code  # the updated copy already handled the pause
    except KeyboardInterrupt:
        raw(SHOW_CURSOR)
        out()
        out(PAD + paint("✗", MUTED) + "  " + paint("cancelled", MUTED))
        code = 130
    except SystemExit as e:
        code = e.code if isinstance(e.code, int) else (0 if e.code is None else 1)
    except Exception:
        import traceback
        raw(SHOW_CURSOR)
        out()
        fail("Something went wrong:")
        out(paint(traceback.format_exc(), MUTED))
        code = 1
    if pause:
        try:
            input(PAD + paint("press enter to close", FAINT))
        except (EOFError, KeyboardInterrupt):
            pass
    return code


if __name__ == "__main__":
    sys.exit(run())
