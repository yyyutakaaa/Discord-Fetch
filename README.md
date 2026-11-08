# Discord Fetch

A small tool to export your Discord DMs and server channels from the terminal. You pick a chat, pick how far back you want to go, and it saves everything as txt, markdown, json or csv.

It's just one file. No pip install, no requirements, nothing to set up. If you have Python 3.7 or newer it works on Windows, macOS and Linux.

```
   d i s c o r d
  ███████╗███████╗████████╗ ██████╗██╗  ██╗
  ██╔════╝██╔════╝╚══██╔══╝██╔════╝██║  ██║
  █████╗  █████╗     ██║   ██║     ███████║
  ██╔══╝  ██╔══╝     ██║   ██║     ██╔══██║
  ██║     ███████╗   ██║   ╚██████╗██║  ██║
  ╚═╝     ╚══════╝   ╚═╝    ╚═════╝╚═╝  ╚═╝

  ? Pick a conversation  48
    ⌕ type to filter
  ❯ Alice                            @alice  ·  2h ago
    Study group                group · 5 people  ·  1d ago
    Bob                                  @bob  ·  3d ago
```

## How to use it

Install [Python](https://www.python.org/downloads/) if you don't have it yet. On Windows, make sure you tick Add python.exe to PATH during the install.

Then download [fetch.py](https://raw.githubusercontent.com/yyyutakaaa/Discord-Fetch/main/fetch.py) and double-click it. That's it. If you prefer the terminal you can also just run it:

```bash
python fetch.py
```

The first time it asks for your Discord token. You paste it once and it can remember it for next time.

If double-clicking opens the file in an editor instead of running it, right-click it, go to Open with and choose Python. Tick the box so it always does that.

## It updates itself

Every time you open it, it checks GitHub for a newer version. If there is one it downloads it, replaces itself and starts again, so you never have to download it twice. When you're offline it just runs the version you already have.

Don't want that? Set `DISCORD_FETCH_NO_UPDATE=1` and it skips the check.

## What it can do

You move through every menu with the arrow keys, and in longer lists you can just start typing to filter. Esc takes you back a step.

Your DMs are sorted by who you talked to last, like in Discord itself. For servers you first pick the server and then the channel, and the channels are grouped by category so you find them fast.

How far back you go is up to you. You can grab today, the last 7 or 30 days, the last few hundred messages or the whole history. While it's fetching you see a progress bar and how far back it already is. If it takes too long you can press Ctrl+C, and you still keep everything it already got.

After that you get a small summary with how many messages there are, which period they cover and who talked the most. You can preview the chat right in the terminal or save it in as many formats as you want.

Don't need a file? Pick Copy to clipboard and the whole chat goes straight to your clipboard, as text, markdown, json or csv. Then you just paste it wherever you want, like in a doc or in ChatGPT.

Mentions, custom emoji and Discord timestamps are turned into normal text, so your export is actually readable. All times are shown in your own time zone.

## Using it without the menus

You can also do everything in one command. Handy if you export the same channel a lot.

```bash
python fetch.py --channel <id or link> --days 7
python fetch.py --channel <id> --all --format md
python fetch.py --channel <id> --days 1 --copy
python fetch.py --count 500 --out D:\exports
python fetch.py --logout
```

`--channel` takes a channel id or a normal discord.com/channels link. With `--days`, `--count` or `--all` you choose how much you want. If you add `--format` it saves straight away and closes. `--copy` puts it on your clipboard instead, as plain text unless you also give a format. `--out` changes the folder, `--no-color` gives plain text and `--logout` removes your saved token.

## Getting your token

You need your own user token for this, not a bot token.

Open [discord.com/app](https://discord.com/app) in your browser and press F12. Go to the Network tab and click on any channel. Pick one of the requests to discord.com/api and look for the authorization header in the request headers. That value is your token.

The tool first looks for a `DISCORD_TOKEN` environment variable, then for a `.env` file with `DISCORD_TOKEN=...` in it, and after that for the token you saved before.

Be careful with it though. Your token gives full access to your account, so never share it with anyone. The saved token is stored as plain text in `~/.discord_fetch/token`. If you want it gone, use `--logout` or the option in the menu.

## Where your files go

Everything is saved in `~/Discord_Messages`, with a separate folder for every chat.

```
Discord_Messages/
├── DM_alice/
│   └── DM_alice_20261108_143025.txt
└── Test_Server_general/
    └── Test_Server_general_20261108_143025.md
```

Txt is the easiest to just read. Markdown looks nice in VS Code or Obsidian. Json has all the raw data from Discord, and csv opens fine in Excel or Google Sheets, emoji included.

## New version

Change `VERSION` at the top of `fetch.py` and push it to main. Everyone who has the file gets the update the next time they open it. It only updates to a higher version, so it never overwrites a newer local copy with an older one.

## Heads up

Running scripts on a user token goes against Discord's terms, and in theory your account can get flagged for it. Only export chats you're actually part of and be careful with other people's messages. This project is for personal backups and for learning, use it at your own risk.
