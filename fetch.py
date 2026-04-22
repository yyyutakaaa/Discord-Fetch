#!/usr/bin/env python3
"""
Discord Chat Fetcher — standalone, no installation required
Requires only Python 3.7+   |   Windows & macOS
"""

import os
import sys
import json
import csv
import re
import time
import getpass
import urllib.request
import urllib.error
import urllib.parse
from pathlib import Path
from datetime import datetime, timezone, timedelta

# ─────────────────────────────────────────────────────────
#  COLORS
# ─────────────────────────────────────────────────────────

def _enable_windows_ansi():
    if sys.platform == "win32":
        try:
            import ctypes
            kernel32 = ctypes.windll.kernel32
            kernel32.SetConsoleMode(kernel32.GetStdHandle(-11), 7)
        except Exception:
            pass

_enable_windows_ansi()

_COLOR = sys.stdout.isatty() if hasattr(sys.stdout, "isatty") else False

class C:
    RST  = "\033[0m"   if _COLOR else ""
    BOLD = "\033[1m"   if _COLOR else ""
    DIM  = "\033[2m"   if _COLOR else ""
    RED  = "\033[91m"  if _COLOR else ""
    GRN  = "\033[92m"  if _COLOR else ""
    YLW  = "\033[93m"  if _COLOR else ""
    CYN  = "\033[96m"  if _COLOR else ""

def ok(msg):   print(f"  {C.GRN}✓{C.RST}  {msg}")
def err(msg):  print(f"  {C.RED}✗{C.RST}  {msg}")
def warn(msg): print(f"  {C.YLW}!{C.RST}  {msg}")
def info(msg): print(f"  {C.DIM}{msg}{C.RST}")
def bold(msg): print(f"  {C.BOLD}{msg}{C.RST}")

# ─────────────────────────────────────────────────────────
#  CONSTANTS
# ─────────────────────────────────────────────────────────

API        = "https://discord.com/api/v9"
CFG_DIR    = Path.home() / ".discord_fetch"
TOKEN_FILE = CFG_DIR / "token"
SAVE_DIR   = Path.home() / "Discord_Messages"

# ─────────────────────────────────────────────────────────
#  BANNER
# ─────────────────────────────────────────────────────────

BANNER = f"""
{C.CYN}{C.BOLD}
  ╔══════════════════════════════════════════╗
  ║       Discord Chat Fetcher  v2.0         ║
  ╚══════════════════════════════════════════╝
{C.RST}"""

# ─────────────────────────────────────────────────────────
#  TOKEN
# ─────────────────────────────────────────────────────────

def _load_token():
    t = os.environ.get("DISCORD_TOKEN", "").strip().strip('"')
    if t:
        return t
    env = Path(".env")
    if env.exists():
        for line in env.read_text(encoding="utf-8").splitlines():
            if line.startswith("DISCORD_TOKEN="):
                t = line.split("=", 1)[1].strip().strip('"')
                if t:
                    return t
    if TOKEN_FILE.exists():
        t = TOKEN_FILE.read_text(encoding="utf-8").strip()
        if t:
            return t
    return None

def _save_token(token):
    CFG_DIR.mkdir(parents=True, exist_ok=True)
    TOKEN_FILE.write_text(token, encoding="utf-8")
    try:
        TOKEN_FILE.chmod(0o600)
    except Exception:
        pass

def get_token():
    token = _load_token()
    if token:
        return token

    print()
    bold("Discord Token Required")
    info("Open Discord in your browser → press F12 → Network tab")
    info("Click any request → find 'Authorization' in the request headers")
    print()

    try:
        token = getpass.getpass("  Enter your Discord token (hidden input): ").strip()
    except Exception:
        token = input("  Enter your Discord token: ").strip()

    if not token:
        err("No token entered.")
        return None

    ans = input("  Save token for next time? [Y/n]: ").strip().lower()
    if ans != "n":
        _save_token(token)
        ok(f"Token saved to {TOKEN_FILE}")

    return token

# ─────────────────────────────────────────────────────────
#  HTTP
# ─────────────────────────────────────────────────────────

_HEADERS = {
    "user-agent":       "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
    "accept":           "*/*",
    "accept-language":  "en-US,en;q=0.9",
    "content-type":     "application/json",
    "x-discord-locale": "en-US",
}

def _request(path, token, params=None):
    """Returns (data, error_string)."""
    url = API + path
    if params:
        url += "?" + urllib.parse.urlencode(params)

    headers = {**_HEADERS, "authorization": token}
    req = urllib.request.Request(url, headers=headers)

    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                return json.loads(r.read().decode()), None
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = float(json.loads(e.read().decode()).get("retry_after", 1))
                except Exception:
                    wait = 1.0
                warn(f"Rate limited — waiting {wait:.1f}s…")
                time.sleep(wait)
            elif e.code == 401:
                return None, "Invalid or expired token (401)"
            elif e.code == 403:
                return None, "No permission (403)"
            else:
                return None, f"HTTP {e.code}"
        except Exception as exc:
            if attempt == 2:
                return None, str(exc)
            time.sleep(1)

    return None, "Request failed after 3 retries"

# ─────────────────────────────────────────────────────────
#  DISCORD HELPERS
# ─────────────────────────────────────────────────────────

def api_me(token):
    d, e = _request("/users/@me", token)
    if e:
        err(f"Login failed: {e}")
    return d

def api_dms(token):
    d, e = _request("/users/@me/channels", token)
    if e:
        warn(f"Could not load DMs: {e}")
        return []
    out = []
    for ch in d:
        if ch.get("type") == 1:
            rec = ch.get("recipients", [])
            name = f"DM with {rec[0]['username']}" if rec else "Unknown DM"
            out.append({"id": ch["id"], "name": name, "type": "DM"})
        elif ch.get("type") == 3:
            name = ch.get("name") or f"Group ({len(ch.get('recipients',[]))} members)"
            out.append({"id": ch["id"], "name": name, "type": "Group DM"})
    return out

def api_guilds(token):
    d, e = _request("/users/@me/guilds", token)
    if e:
        warn(f"Could not load servers: {e}")
        return []
    return d

def api_guild_channels(token, guild_id):
    time.sleep(0.3)
    d, _ = _request(f"/guilds/{guild_id}/channels", token)
    if not d:
        return []
    return [ch for ch in d if ch.get("type") == 0]

def api_messages(token, channel_id, limit=100, before=None):
    params = {"limit": min(limit, 100)}
    if before:
        params["before"] = before
    d, e = _request(f"/channels/{channel_id}/messages", token, params)
    if e and "403" not in e and "401" not in e:
        warn(f"Message fetch error: {e}")
    return d or []

def fetch_messages(token, channel_id, total_limit=None, cutoff_dt=None):
    all_msgs, before, cap = [], None, total_limit or 100_000
    print()
    while len(all_msgs) < cap:
        batch = min(100, cap - len(all_msgs)) if total_limit else 100
        msgs = api_messages(token, channel_id, batch, before)
        if not msgs:
            break
        if cutoff_dt:
            keep, stop = [], False
            for m in msgs:
                ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                if ts >= cutoff_dt:
                    keep.append(m)
                else:
                    stop = True
            all_msgs.extend(keep)
            _progress(len(all_msgs))
            if stop:
                break
        else:
            all_msgs.extend(msgs)
            _progress(len(all_msgs))
        before = msgs[-1]["id"]
        time.sleep(0.25)
    print()
    return all_msgs[:total_limit] if total_limit else all_msgs

def _progress(n):
    print(f"\r  {C.CYN}Fetching… {n:,} messages{C.RST}   ", end="", flush=True)

# ─────────────────────────────────────────────────────────
#  INTERACTIVE HELPERS
# ─────────────────────────────────────────────────────────

def ask(question, default=None, choices=None):
    hint = ""
    if choices:
        hint += f" [{'/'.join(choices)}]"
    if default is not None:
        hint += f" (default: {default})"
    while True:
        val = input(f"\n  {C.BOLD}{question}{hint}: {C.RST}").strip()
        if not val and default is not None:
            return str(default)
        if not val:
            warn("Please enter a value.")
            continue
        if choices and val not in choices:
            warn(f"Choose one of: {', '.join(choices)}")
            continue
        return val

def confirm(question, default=True):
    hint = "[Y/n]" if default else "[y/N]"
    val = input(f"\n  {C.BOLD}{question} {hint}: {C.RST}").strip().lower()
    return (val.startswith("y")) if val else default

def pick(items, label, title="Select"):
    """
    items   : list of anything
    label   : fn(item) -> plain string for display & search
    Returns chosen item.
    """
    while True:
        print()
        print(f"  {C.CYN}{C.BOLD}{title}{C.RST}")
        print(f"  {C.DIM}{'─' * 54}{C.RST}")
        for i, item in enumerate(items, 1):
            print(f"  {C.CYN}{i:>3}.{C.RST}  {label(item)}")
        print(f"  {C.DIM}{'─' * 54}{C.RST}")

        val = input(f"\n  {C.BOLD}Enter number or search: {C.RST}").strip()

        if val.isdigit():
            idx = int(val) - 1
            if 0 <= idx < len(items):
                return items[idx]
            warn(f"Enter a number between 1 and {len(items)}.")
        elif val:
            term = val.lower()
            found = [it for it in items if term in label(it).lower()]
            if not found:
                warn("No matches. Try a different search term.")
                continue
            if len(found) == 1:
                ok(f"Found: {label(found[0])}")
                return found[0]
            return pick(found, label, f"Results for '{val}'")

# ─────────────────────────────────────────────────────────
#  CHANNEL SELECTION
# ─────────────────────────────────────────────────────────

def choose_dm(token):
    info("Loading your DMs…")
    dms = api_dms(token)
    if not dms:
        warn("No DMs found.")
        return None, None
    ch = pick(dms, lambda x: x["name"], "Direct Messages")
    return ch["id"], ch["name"]

def choose_server_channel(token):
    guilds = api_guilds(token)
    if not guilds:
        warn("No servers found.")
        return None, None

    info(f"Loading channels from {len(guilds)} servers…")
    servers = []
    for i, g in enumerate(guilds, 1):
        print(f"\r  {C.CYN}Loading… {i}/{len(guilds)}{C.RST}   ", end="", flush=True)
        chs = api_guild_channels(token, g["id"])
        if chs:
            servers.append({
                "id":       g["id"],
                "name":     g["name"],
                "channels": [{"id": c["id"], "name": c["name"]} for c in chs],
            })
    print()

    if not servers:
        warn("No accessible servers found.")
        return None, None

    guild = pick(servers,
                 lambda g: f"{g['name']}  ({len(g['channels'])} channels)",
                 "Servers")

    ch = pick(guild["channels"],
              lambda c: f"# {c['name']}",
              f"Channels in {guild['name']}")

    return ch["id"], f"#{ch['name']} ({guild['name']})"

# ─────────────────────────────────────────────────────────
#  FETCH MODE
# ─────────────────────────────────────────────────────────

def ask_fetch_mode():
    print()
    bold("How many messages do you want?")
    info("1. By days   — fetch everything in the last N days")
    info("2. By count  — fetch the last N messages")

    choice = ask("Select", default="1", choices=["1", "2"])

    if choice == "1":
        print()
        info("  1 = today only")
        info("  2 = today + yesterday")
        info("  7 = last week")
        days_str = ask("Number of days", default="1")
        try:
            days = max(1, int(days_str))
        except ValueError:
            days = 1
        cutoff = (datetime.now(timezone.utc)
                  .replace(hour=0, minute=0, second=0, microsecond=0)
                  - timedelta(days=days - 1))
        return "days", cutoff
    else:
        count_str = ask("Number of messages", default="500")
        try:
            count = max(1, int(count_str))
        except ValueError:
            count = 500
        return "count", count

# ─────────────────────────────────────────────────────────
#  DISPLAY
# ─────────────────────────────────────────────────────────

def display_messages(messages, channel_name):
    if not messages:
        warn("No messages to display.")
        return
    msgs = sorted(messages, key=lambda m: m["timestamp"])
    print()
    print(f"  {C.CYN}{C.BOLD}── Messages from {channel_name} ──{C.RST}")
    current_date = None
    for m in msgs:
        ts = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
        if ts.date() != current_date:
            current_date = ts.date()
            print(f"\n  {C.DIM}{'─'*18} {current_date.strftime('%A, %B %d, %Y')} {'─'*18}{C.RST}\n")
        author  = m["author"]["username"]
        content = m.get("content") or "[no text]"
        print(f"  {C.DIM}{ts.strftime('%H:%M')}{C.RST}  {C.GRN}{C.BOLD}{author}:{C.RST}  {content}")
        for att in m.get("attachments", []):
            print(f"        {C.DIM}📎 {att['filename']}  {att['url']}{C.RST}")
    print()

# ─────────────────────────────────────────────────────────
#  SAVE
# ─────────────────────────────────────────────────────────

def save_messages(messages, channel_name):
    print()
    bold("Save format")
    info("1. TXT  — plain text, easy to read")
    info("2. JSON — structured data")
    info("3. CSV  — open in Excel / Sheets")

    fmt_choice = ask("Select format", default="1", choices=["1", "2", "3"])
    fmt = {"1": "txt", "2": "json", "3": "csv"}[fmt_choice]

    safe = re.sub(r'[^\w\-_.]', '_', channel_name)
    safe = re.sub(r'_{2,}', '_', safe).strip('_') or "discord_channel"

    folder = SAVE_DIR / safe
    folder.mkdir(parents=True, exist_ok=True)

    ts  = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = folder / f"{safe}_{ts}.{fmt}"

    msgs = sorted(messages, key=lambda m: m["timestamp"])

    if fmt == "txt":
        with open(out, "w", encoding="utf-8") as f:
            f.write(f"Discord Messages — {channel_name}\n{'='*50}\n\n")
            cur = None
            for m in msgs:
                dt = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                if dt.date() != cur:
                    cur = dt.date()
                    f.write(f"\n── {cur.strftime('%A, %B %d, %Y')} ──\n\n")
                content = m.get("content") or "[no text]"
                f.write(f"{dt.strftime('%H:%M:%S')}  {m['author']['username']}: {content}\n")
                for att in m.get("attachments", []):
                    f.write(f"  [Attachment: {att['filename']} — {att['url']}]\n")
                f.write("\n")

    elif fmt == "json":
        with open(out, "w", encoding="utf-8") as f:
            json.dump({
                "channel_name":  channel_name,
                "export_time":   datetime.now().isoformat(),
                "message_count": len(msgs),
                "messages":      msgs,
            }, f, indent=2, ensure_ascii=False, default=str)

    elif fmt == "csv":
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Timestamp", "Date", "Time", "Author", "Message", "Attachments"])
            for m in msgs:
                dt   = datetime.fromisoformat(m["timestamp"].replace("Z", "+00:00"))
                atts = "; ".join(f"{a['filename']} ({a['url']})" for a in m.get("attachments", []))
                w.writerow([m["timestamp"], dt.strftime("%Y-%m-%d"),
                            dt.strftime("%H:%M:%S"), m["author"]["username"],
                            m.get("content") or "", atts])

    ok(f"Saved {len(msgs):,} messages → {out}")

# ─────────────────────────────────────────────────────────
#  MAIN
# ─────────────────────────────────────────────────────────

def main():
    print(BANNER)
    CFG_DIR.mkdir(parents=True, exist_ok=True)

    # ── Token ──
    token = get_token()
    if not token:
        sys.exit(1)

    # ── Login ──
    info("Connecting to Discord…")
    user = api_me(token)
    if not user:
        if TOKEN_FILE.exists() and confirm("Token looks invalid. Delete saved token and re-enter?"):
            TOKEN_FILE.unlink()
            token = get_token()
            if not token:
                sys.exit(1)
            user = api_me(token)
        if not user:
            sys.exit(1)

    disc = f"#{user.get('discriminator', '0')}" if user.get("discriminator", "0") != "0" else ""
    ok(f"Logged in as {C.BOLD}{user['username']}{disc}{C.RST}")

    # ── Main loop ──
    while True:
        print()
        bold("── Main Menu ──")
        info("1. Direct Messages")
        info("2. Server Channels")
        info("3. Exit")

        choice = ask("Select", default="1", choices=["1", "2", "3"])

        if choice == "3":
            break

        if choice == "1":
            channel_id, channel_name = choose_dm(token)
        else:
            channel_id, channel_name = choose_server_channel(token)

        if not channel_id:
            continue

        ok(f"Selected: {channel_name}")

        mode, value = ask_fetch_mode()

        if mode == "days":
            messages = fetch_messages(token, channel_id, cutoff_dt=value)
        else:
            messages = fetch_messages(token, channel_id, total_limit=value)

        if not messages:
            warn("No messages retrieved.")
        else:
            ok(f"Fetched {len(messages):,} messages")

            if confirm("Show messages in terminal?", default=False):
                display_messages(messages, channel_name)

            if confirm("Save to file?"):
                save_messages(messages, channel_name)

        if not confirm("Fetch from another channel?", default=False):
            break

    print()
    ok("Done. Goodbye!")
    print()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print()
        warn("Stopped.")
        sys.exit(0)
