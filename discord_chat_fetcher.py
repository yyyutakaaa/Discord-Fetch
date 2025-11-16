#!/usr/bin/env python3

import os
import json
import getpass
import csv
import asyncio
import aiohttp
from pathlib import Path
from typing import Dict, List, Optional, Union
from datetime import datetime, timezone

from dotenv import load_dotenv
from rich.console import Console
from rich.prompt import Prompt, Confirm
from rich.progress import Progress
from rich.panel import Panel
from rich.table import Table
import keyring

# Initialize Rich console for better output
console = Console()

# Constants
CONFIG_DIR = Path.home() / ".discord_chat_fetcher"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
CONFIG_FILE = CONFIG_DIR / "config.json"
DEFAULT_MESSAGE_COUNT = 1000
DEFAULT_SAVE_DIR = Path.home() / "Discord_Chat_Fetcher_Messages"
KEYRING_SERVICE = "discord_chat_fetcher"
DISCORD_API_BASE = "https://discord.com/api/v9"

def load_config() -> Dict[str, str]:
    """Load configuration settings."""
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r") as f:
                config = json.load(f)
                return config
        except (json.JSONDecodeError, KeyError):
            console.print("[yellow]Config file is invalid.[/yellow]")
    
    return {
        "save_dir": str(DEFAULT_SAVE_DIR),
        "credential_storage": "keyring"
    }

def save_config(config: Dict[str, str]) -> None:
    """Save configuration settings."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(config, f)
    console.print("[green]Configuration saved.[/green]")

def setup_config_dir() -> None:
    """Create configuration directory if it doesn't exist."""
    if not CONFIG_DIR.exists():
        CONFIG_DIR.mkdir(parents=True)
        console.print("[green]Created config directory.[/green]")
    
    config = load_config()
    save_dir = Path(config.get("save_dir", str(DEFAULT_SAVE_DIR)))
    
    if not save_dir.exists():
        try:
            save_dir.mkdir(parents=True, exist_ok=True)
            console.print(f"[green]Created save directory: {save_dir}[/green]")
        except Exception as e:
            console.print(f"[yellow]Could not create save directory: {str(e)}[/yellow]")
            config["save_dir"] = str(DEFAULT_SAVE_DIR)
            DEFAULT_SAVE_DIR.mkdir(parents=True, exist_ok=True)
            save_config(config)

def save_token_keyring(token: str) -> bool:
    """Save Discord token to system keyring."""
    try:
        keyring.set_password(KEYRING_SERVICE, "discord_token", token)
        console.print("[green]Token saved securely to system keyring.[/green]")
        return True
    except Exception as e:
        console.print(f"[yellow]Could not save to keyring: {e}[/yellow]")
        return False

def load_token_keyring() -> Optional[str]:
    """Load Discord token from system keyring."""
    try:
        token = keyring.get_password(KEYRING_SERVICE, "discord_token")
        return token
    except Exception as e:
        console.print(f"[yellow]Could not load from keyring: {e}[/yellow]")
        return None

def load_token() -> Optional[str]:
    """Load saved Discord token using configured method."""
    load_dotenv()
    token = os.getenv("DISCORD_TOKEN")
    
    if token:
        return token.strip().strip('"')  # Remove quotes if present
    
    config = load_config()
    storage_method = config.get("credential_storage", "keyring")
    
    if storage_method == "keyring":
        return load_token_keyring()
    
    return None

class DiscordHTTPClient:
    """Direct HTTP client for Discord API using user tokens."""
    
    def __init__(self, token: str):
        self.token = token.strip().strip('"')
        self.session = None
        self.user_info = None
        
        # Headers that mimic Discord web client
        self.headers = {
            "authorization": self.token,
            "accept": "*/*",
            "accept-language": "en-US,en;q=0.9",
            "content-type": "application/json",
            "user-agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36",
            "x-discord-locale": "en-US",
            "x-discord-timezone": "Europe/Brussels",
        }
    
    async def __aenter__(self):
        self.session = aiohttp.ClientSession()
        return self
    
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self.session:
            await self.session.close()
    
    async def test_connection(self) -> bool:
        """Test if the token is valid by getting user info."""
        try:
            async with self.session.get(
                f"{DISCORD_API_BASE}/users/@me",
                headers=self.headers
            ) as response:
                if response.status == 200:
                    self.user_info = await response.json()
                    return True
                else:
                    console.print(f"[red]API returned status {response.status}[/red]")
                    if response.status == 401:
                        console.print("[red]Token is invalid or expired[/red]")
                    elif response.status == 403:
                        console.print("[red]Token doesn't have required permissions[/red]")
                    return False
        except Exception as e:
            console.print(f"[red]Connection test failed: {str(e)}[/red]")
            return False
    
    async def get_guilds(self) -> List[Dict]:
        """Get all guilds (servers) the user is in."""
        try:
            async with self.session.get(
                f"{DISCORD_API_BASE}/users/@me/guilds",
                headers=self.headers
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    console.print(f"[red]Failed to get guilds: {response.status}[/red]")
                    return []
        except Exception as e:
            console.print(f"[red]Error getting guilds: {str(e)}[/red]")
            return []
    
    async def get_dm_channels(self) -> List[Dict]:
        """Get all DM channels."""
        try:
            async with self.session.get(
                f"{DISCORD_API_BASE}/users/@me/channels",
                headers=self.headers
            ) as response:
                if response.status == 200:
                    return await response.json()
                else:
                    console.print(f"[red]Failed to get DM channels: {response.status}[/red]")
                    return []
        except Exception as e:
            console.print(f"[red]Error getting DM channels: {str(e)}[/red]")
            return []
    
    async def get_guild_channels(self, guild_id: str) -> List[Dict]:
        """Get all channels in a guild with rate limiting protection."""
        try:
            # Add delay to prevent rate limiting
            await asyncio.sleep(0.5)
            
            async with self.session.get(
                f"{DISCORD_API_BASE}/guilds/{guild_id}/channels",
                headers=self.headers
            ) as response:
                if response.status == 200:
                    channels = await response.json()
                    # Filter for text channels only
                    return [ch for ch in channels if ch.get("type") == 0]  # Type 0 = text channel
                elif response.status == 429:
                    # Rate limited - wait and skip this guild
                    console.print(f"[yellow]Rate limited, skipping guild {guild_id}[/yellow]")
                    retry_after = response.headers.get('retry-after', '1')
                    await asyncio.sleep(float(retry_after))
                    return []
                elif response.status == 403:
                    # No permission - skip silently
                    return []
                else:
                    console.print(f"[yellow]Failed to get guild channels: {response.status}[/yellow]")
                    return []
        except Exception as e:
            console.print(f"[yellow]Error getting guild channels: {str(e)[:30]}...[/yellow]")
            return []
    
    async def get_messages(self, channel_id: str, limit: int = 100, before: str = None) -> List[Dict]:
        """Get messages from a channel."""
        params = {"limit": min(limit, 100)}  # Discord API limit is 100 per request
        if before:
            params["before"] = before
        
        url = f"{DISCORD_API_BASE}/channels/{channel_id}/messages"
        
        retries = 3
        while retries > 0:
            try:
                async with self.session.get(
                    url,
                    headers=self.headers,
                    params=params
                ) as response:
                    if response.status == 200:
                        return await response.json()
                    if response.status == 403:
                        console.print(f"[red]No permission to read messages in this channel[/red]")
                        return []
                    if response.status == 429:
                        retry_after = float(response.headers.get("retry-after", "1"))
                        console.print(f"[yellow]Rate limited, retrying in {retry_after} seconds...[/yellow]")
                        await asyncio.sleep(retry_after)
                        retries -= 1
                        continue
                    
                    console.print(f"[red]Failed to get messages: {response.status}[/red]")
                    return []
            except Exception as e:
                retries -= 1
                if retries <= 0:
                    console.print(f"[red]Error getting messages: {str(e)}[/red]")
                    return []
                await asyncio.sleep(1)
        
        return []
    
    async def fetch_all_messages(self, channel_id: str, total_limit: int) -> List[Dict]:
        """Fetch multiple batches of messages."""
        all_messages = []
        before = None
        
        with Progress() as progress:
            task = progress.add_task(f"[cyan]Fetching messages...", total=total_limit)
            
            while len(all_messages) < total_limit:
                remaining = total_limit - len(all_messages)
                batch_size = min(100, remaining)
                
                messages = await self.get_messages(channel_id, batch_size, before)
                
                if not messages:
                    console.print("[yellow]Stopped fetching messages early (empty response or error).[/yellow]")
                    progress.update(task, completed=len(all_messages))
                    break
                
                all_messages.extend(messages)
                progress.update(task, advance=len(messages))
                
                # Set before to the ID of the last message for pagination
                before = messages[-1]["id"]
                
                # Small delay to avoid rate limits
                await asyncio.sleep(0.2)
        
        return all_messages[:total_limit]

async def create_discord_client() -> DiscordHTTPClient:
    """Create and test Discord HTTP client."""
    token = load_token()
    
    if not token:
        console.print("\n[bold red]Discord Token Required[/bold red]")
        console.print("\n[cyan]You can:[/cyan]")
        console.print("1. Set it as environment variable: export DISCORD_TOKEN=your_token")
        console.print("2. Or enter it below")
        
        try:
            token = getpass.getpass("\nEnter your Discord token (hidden): ")
            if not token:
                token = Prompt.ask("Enter your Discord token", password=True)
        except Exception:
            token = Prompt.ask("Enter your Discord token", password=True)
    
    client = DiscordHTTPClient(token)
    return client

async def get_all_channels(
    client: DiscordHTTPClient,
    include_dm: bool = True,
    include_guilds: bool = True
) -> Dict:
    """Get accessible channels with optional filters and rate limiting protection."""
    channels_data = {
        "dm_channels": [],
        "guilds": {}
    }
    
    # Get DM channels if requested
    if include_dm:
        dm_channels = await client.get_dm_channels()
        for channel in dm_channels:
            if channel.get("type") == 1:  # DM
                recipients = channel.get("recipients", [])
                if recipients:
                    recipient_name = recipients[0].get("username", "Unknown")
                    channels_data["dm_channels"].append({
                        "id": channel["id"],
                        "name": f"DM with {recipient_name}",
                        "type": "DM"
                    })
            elif channel.get("type") == 3:  # Group DM
                name = channel.get("name") or f"Group with {len(channel.get('recipients', []))} members"
                channels_data["dm_channels"].append({
                    "id": channel["id"],
                    "name": name,
                    "type": "Group DM"
                })
    
    # Get guild channels with progress and rate limiting if requested
    if include_guilds:
        guilds = await client.get_guilds()
        
        if guilds:
            console.print(f"[cyan]Loading channels from {len(guilds)} servers...[/cyan]")
            
            with Progress() as progress:
                task = progress.add_task("[cyan]Loading server channels...", total=len(guilds))
                
                for guild in guilds:
                    try:
                        guild_channels = await client.get_guild_channels(guild["id"])
                        if guild_channels:
                            channels_data["guilds"][guild["id"]] = {
                                "id": guild["id"],
                                "name": guild["name"],
                                "channels": [
                                    {
                                        "id": ch["id"],
                                        "name": ch["name"],
                                        "category": "No Category"  # Simplified for now
                                    }
                                    for ch in guild_channels
                                ]
                            }
                        progress.update(task, advance=1)
                        
                        # Small delay between guild requests to prevent rate limiting
                        await asyncio.sleep(0.2)
                        
                    except Exception as e:
                        console.print(f"[yellow]Skipped guild {guild['name']}: {str(e)[:30]}...[/yellow]")
                        progress.update(task, advance=1)
                        continue
    
    loaded_parts = []
    if include_dm:
        loaded_parts.append(f"{len(channels_data['dm_channels'])} DMs")
    if include_guilds:
        loaded_parts.append(f"{len(channels_data['guilds'])} servers")
    if loaded_parts:
        console.print(f"[green]Loaded {' and '.join(loaded_parts)}[/green]")
    else:
        console.print("[yellow]No channel types selected to load.[/yellow]")
    return channels_data

def display_dm_channels(dm_channels: List[Dict]) -> None:
    """Display only DM channels in a table."""
    if not dm_channels:
        console.print("[yellow]No DM channels found.[/yellow]")
        return
        
    dm_table = Table(title="Direct Messages")
    dm_table.add_column("ID", justify="right", style="cyan")
    dm_table.add_column("Name", style="green")
    dm_table.add_column("Type", style="white")
    
    for i, channel in enumerate(dm_channels, 1):
        dm_table.add_row(str(i), channel["name"], channel["type"])
    
    console.print(dm_table)

def display_servers(guilds: Dict) -> None:
    """Display available servers."""
    if not guilds:
        console.print("[yellow]No servers found.[/yellow]")
        return
        
    server_table = Table(title="Available Servers")
    server_table.add_column("ID", justify="right", style="cyan")
    server_table.add_column("Server Name", style="green")
    server_table.add_column("Channels", style="white")
    
    for i, guild_data in enumerate(guilds.values(), 1):
        channel_count = len(guild_data["channels"])
        server_table.add_row(str(i), guild_data["name"], f"{channel_count} channels")
    
    console.print(server_table)

def display_server_channels(guild_name: str, channels: List[Dict]) -> None:
    """Display channels for a specific server."""
    if not channels:
        console.print(f"[yellow]No channels found in {guild_name}.[/yellow]")
        return
        
    channel_table = Table(title=f"Channels in {guild_name}")
    channel_table.add_column("ID", justify="right", style="cyan")
    channel_table.add_column("Channel", style="green")
    
    for i, channel in enumerate(channels, 1):
        channel_table.add_row(str(i), f"#{channel['name']}")
    
    console.print(channel_table)

def select_dm_channel(dm_channels: List[Dict]) -> Optional[str]:
    """Let user select a DM channel."""
    display_dm_channels(dm_channels)
    
    while True:
        choice = Prompt.ask(
            "Select a DM by number or enter a name to search",
            default="1"
        )
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(dm_channels):
                channel = dm_channels[idx]
                return channel["id"]
            else:
                console.print("[red]Invalid selection. Please try again.[/red]")
        else:
            # Search functionality
            search_term = choice.lower()
            matches = []
            
            for i, channel in enumerate(dm_channels):
                channel_name = channel.get("name", "").lower()
                if search_term in channel_name:
                    matches.append((i, channel))
            
            if matches:
                if len(matches) == 1:
                    _, channel = matches[0]
                    console.print(f"[green]Found DM: {channel['name']}[/green]")
                    return channel["id"]
                else:
                    console.print(f"[yellow]Found {len(matches)} matching DMs:[/yellow]")
                    for i, (_, channel) in enumerate(matches, 1):
                        console.print(f"{i}. {channel['name']}")
                    
                    sub_choice = Prompt.ask("Select a DM by number", default="1")
                    if sub_choice.isdigit() and 1 <= int(sub_choice) <= len(matches):
                        _, channel = matches[int(sub_choice) - 1]
                        return channel["id"]
            else:
                console.print("[red]No DMs found matching that name.[/red]")

def select_server(guilds: Dict) -> Optional[tuple]:
    """Let user select a server and return (guild_name, guild_data)."""
    display_servers(guilds)
    
    guild_list = list(guilds.values())
    
    while True:
        choice = Prompt.ask(
            "Select a server by number or enter a name to search",
            default="1"
        )
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(guild_list):
                guild_data = guild_list[idx]
                console.print(f"[green]Selected server: {guild_data['name']}[/green]")
                return guild_data["name"], guild_data
            else:
                console.print("[red]Invalid selection. Please try again.[/red]")
        else:
            # Search functionality
            search_term = choice.lower()
            matches = []
            
            for i, guild_data in enumerate(guild_list):
                guild_name = guild_data["name"]
                if search_term in guild_name.lower():
                    matches.append((i, guild_name, guild_data))
            
            if matches:
                if len(matches) == 1:
                    _, guild_name, guild_data = matches[0]
                    console.print(f"[green]Found server: {guild_name}[/green]")
                    return guild_name, guild_data
                else:
                    console.print(f"[yellow]Found {len(matches)} matching servers:[/yellow]")
                    for i, (_, guild_name, _) in enumerate(matches, 1):
                        console.print(f"{i}. {guild_name}")
                    
                    sub_choice = Prompt.ask("Select a server by number", default="1")
                    if sub_choice.isdigit() and 1 <= int(sub_choice) <= len(matches):
                        _, guild_name, guild_data = matches[int(sub_choice) - 1]
                        return guild_name, guild_data
            else:
                console.print("[red]No servers found matching that name.[/red]")

def select_server_channel(guild_name: str, channels: List[Dict]) -> Optional[str]:
    """Let user select a channel from a server."""
    display_server_channels(guild_name, channels)
    
    while True:
        choice = Prompt.ask(
            "Select a channel by number or enter a name to search",
            default="1"
        )
        
        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(channels):
                channel = channels[idx]
                console.print(f"[green]Selected channel: #{channel['name']}[/green]")
                return channel["id"]
            else:
                console.print("[red]Invalid selection. Please try again.[/red]")
        else:
            # Search functionality
            search_term = choice.lower()
            matches = []
            
            for i, channel in enumerate(channels):
                channel_name = channel.get("name", "").lower()
                if search_term in channel_name:
                    matches.append((i, channel))
            
            if matches:
                if len(matches) == 1:
                    _, channel = matches[0]
                    console.print(f"[green]Found channel: #{channel['name']}[/green]")
                    return channel["id"]
                else:
                    console.print(f"[yellow]Found {len(matches)} matching channels:[/yellow]")
                    for i, (_, channel) in enumerate(matches, 1):
                        console.print(f"{i}. #{channel['name']}")
                    
                    sub_choice = Prompt.ask("Select a channel by number", default="1")
                    if sub_choice.isdigit() and 1 <= int(sub_choice) <= len(matches):
                        _, channel = matches[int(sub_choice) - 1]
                        return channel["id"]
            else:
                console.print("[red]No channels found matching that name.[/red]")

def select_channel_interactive(channels_data: Dict, preselected: Optional[str] = None) -> Optional[tuple]:
    """Interactive channel selection with better organization."""
    while True:
        if preselected == "dm":
            choice = "1"
        elif preselected == "server":
            choice = "2"
        else:
            console.print("\n[bold]What would you like to access?[/bold]")
            console.print("1. Direct Messages")
            console.print("2. Server Channels") 
            console.print("3. Back to main menu")
            
            choice = Prompt.ask("Select option", choices=["1", "2", "3"], default="1")
        
        if choice == "1":
            # DM channels
            if not channels_data["dm_channels"]:
                console.print("[yellow]No DM channels found.[/yellow]")
                if preselected:
                    return None, None
                continue
            
            channel_id = select_dm_channel(channels_data["dm_channels"])
            if channel_id:
                # Find channel name for display
                channel_name = "Unknown DM"
                for dm in channels_data["dm_channels"]:
                    if dm["id"] == channel_id:
                        channel_name = dm["name"]
                        break
                return channel_id, channel_name
                
        elif choice == "2":
            # Server channels
            if not channels_data["guilds"]:
                console.print("[yellow]No servers found.[/yellow]")
                if preselected:
                    return None, None
                continue
            
            # First select server
            server_result = select_server(channels_data["guilds"])
            if not server_result:
                continue
                
            guild_name, guild_data = server_result
            
            # Then select channel in that server
            channel_id = select_server_channel(guild_name, guild_data["channels"])
            if channel_id:
                # Find channel name for display
                channel_name = "Unknown Channel"
                for ch in guild_data["channels"]:
                    if ch["id"] == channel_id:
                        channel_name = f"#{ch['name']} (from {guild_name})"
                        break
                return channel_id, channel_name
                
        elif choice == "3":
            return None, None

def display_messages(messages: List[Dict], channel_name: str) -> None:
    """Display messages in a readable format."""
    if not messages:
        console.print("[yellow]No messages to display.[/yellow]")
        return
    
    # Sort messages by timestamp (oldest first for display)
    sorted_messages = sorted(messages, key=lambda m: m["timestamp"])
    
    console.print(Panel(f"[bold]Messages from {channel_name}[/bold]"))
    
    current_date = None
    
    for msg in sorted_messages:
        # Parse timestamp
        timestamp = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
        msg_date = timestamp.date()
        
        if current_date != msg_date:
            if current_date is not None:
                console.print()
            
            date_str = msg_date.strftime("%A, %B %d, %Y")
            console.print(f"\n[bold]――――― {date_str} ―――――[/bold]\n")
            current_date = msg_date
        
        time_str = timestamp.strftime("%H:%M:%S")
        author = msg["author"]["username"]
        content = msg.get("content", "[No text content]")
        
        console.print(f"[cyan]{time_str}[/cyan] [bold green]{author}:[/bold green] {content}")
        
        # Display attachments
        if msg.get("attachments"):
            for attachment in msg["attachments"]:
                console.print(f"[italic]📎 Attachment: {attachment['filename']} ({attachment['url']})[/italic]")
        
        console.print()

def save_messages_to_file(messages: List[Dict], channel_name: str, file_format: str = None) -> str:
    """Save messages to a file in specified format."""
    import re
    
    if file_format is None:
        formats = {
            "1": "txt",
            "2": "json", 
            "3": "csv"
        }
        
        console.print("\n[bold]Choose file format:[/bold]")
        console.print("1. TXT - Plain text file (human readable)")
        console.print("2. JSON - Structured data format")
        console.print("3. CSV - Spreadsheet compatible format")
        
        choice = Prompt.ask("Select format", choices=["1", "2", "3"], default="1")
        file_format = formats[choice]
    
    try:
        config = load_config()
        base_save_dir = Path(config.get("save_dir", str(DEFAULT_SAVE_DIR)))
        
        # Clean channel name for filename - remove all problematic characters
        safe_channel_name = re.sub(r'[^\w\-_.]', '_', channel_name)
        safe_channel_name = re.sub(r'_{2,}', '_', safe_channel_name)  # Replace multiple underscores with single
        safe_channel_name = safe_channel_name.strip('_')  # Remove leading/trailing underscores
        
        # If the name becomes empty, use a default
        if not safe_channel_name:
            safe_channel_name = "discord_channel"
        
        save_folder = base_save_dir / safe_channel_name
        save_folder.mkdir(parents=True, exist_ok=True)
        
        # Generate clean filename with timestamp
        current_time = datetime.now().strftime("%Y%m%d_%H%M%S")
        filename = save_folder / f"{safe_channel_name}_{current_time}.{file_format}"
        
        sorted_messages = sorted(messages, key=lambda m: m["timestamp"])
        
        if file_format == "txt":
            with open(filename, "w", encoding="utf-8") as f:
                f.write(f"Discord Messages from {channel_name}\n")
                f.write("=" * 50 + "\n\n")
                
                current_date = None
                
                for msg in sorted_messages:
                    timestamp = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                    msg_date = timestamp.date()
                    
                    if current_date != msg_date:
                        if current_date is not None:
                            f.write("\n")
                        
                        date_str = msg_date.strftime("%A, %B %d, %Y")
                        f.write(f"\n――――― {date_str} ―――――\n\n")
                        current_date = msg_date
                    
                    time_str = timestamp.strftime("%H:%M:%S")
                    author = msg["author"]["username"]
                    content = msg.get("content", "[No text content]")
                    
                    f.write(f"{time_str} - {author}: {content}\n")
                    
                    for attachment in msg.get("attachments", []):
                        f.write(f"[Attachment: {attachment['filename']} - {attachment['url']}]\n")
                    
                    f.write("\n")
        
        elif file_format == "json":
            messages_data = {
                "channel_info": {
                    "channel_name": channel_name,
                    "export_time": datetime.now().isoformat(),
                    "message_count": len(sorted_messages)
                },
                "messages": sorted_messages
            }
            
            with open(filename, "w", encoding="utf-8") as f:
                json.dump(messages_data, f, indent=2, ensure_ascii=False, default=str)
        
        elif file_format == "csv":
            with open(filename, "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                
                writer.writerow([
                    "Timestamp", "Date", "Time", "Author", "Username", "Author_ID", 
                    "Message", "Attachments"
                ])
                
                for msg in sorted_messages:
                    timestamp = datetime.fromisoformat(msg["timestamp"].replace("Z", "+00:00"))
                    attachments_str = "; ".join([f"{att['filename']} ({att['url']})" for att in msg.get("attachments", [])])
                    
                    writer.writerow([
                        msg["timestamp"],
                        timestamp.strftime("%Y-%m-%d"),
                        timestamp.strftime("%H:%M:%S"),
                        msg["author"]["username"],
                        f"{msg['author']['username']}#{msg['author']['discriminator']}",
                        msg["author"]["id"],
                        msg.get("content", "[No text content]"),
                        attachments_str
                    ])
        
        return str(filename)
        
    except Exception as e:
        console.print(f"[bold red]Error saving messages: {str(e)}[/bold red]")
        # Create fallback file with clean name
        fallback_name = f"discord_messages_{datetime.now().strftime('%Y%m%d_%H%M%S')}.txt"
        fallback_file = Path.home() / fallback_name
        try:
            with open(fallback_file, "w", encoding="utf-8") as f:
                f.write(f"Discord Messages from {channel_name}\n")
                f.write("=" * 50 + "\n\n")
                f.write(f"Error occurred while saving original file: {str(e)}\n\n")
                
                for msg in sorted_messages[:10]:  # Save at least first 10 messages
                    f.write(f"{msg['timestamp']} - {msg['author']['username']}: {msg.get('content', '[No text content]')}\n\n")
            
            return str(fallback_file)
        except Exception as fallback_error:
            console.print(f"[bold red]Failed to save fallback file: {str(fallback_error)}[/bold red]")
            return "Error: Could not save file"

async def main():
    """Main function."""
    console.print(Panel.fit("[bold cyan]Discord HTTP Chat Fetcher[/bold cyan]", subtitle="Fetch Discord messages using direct HTTP"))
    
    setup_config_dir()
    
    try:
        async with await create_discord_client() as client:
            console.print("[cyan]Testing connection...[/cyan]")
            
            if not await client.test_connection():
                console.print("[red]Failed to connect to Discord. Please check your token.[/red]")
                return
            
            console.print(f"[green]Successfully connected as {client.user_info['username']}![/green]")
            
            while True:
                try:
                    console.print("\n[bold]What do you want to fetch?[/bold]")
                    console.print("1. Direct Messages")
                    console.print("2. Server Channels")
                    console.print("3. Exit")
                    
                    selection = Prompt.ask("Select option", choices=["1", "2", "3"], default="1")
                    
                    if selection == "3":
                        break
                    
                    fetch_dm = selection == "1"
                    fetch_guilds = selection == "2"
                    
                    channels_data = await get_all_channels(
                        client,
                        include_dm=fetch_dm,
                        include_guilds=fetch_guilds
                    )
                    
                    if fetch_dm and not channels_data["dm_channels"]:
                        console.print("[yellow]No accessible DM channels found.[/yellow]")
                        continue
                    if fetch_guilds and not channels_data["guilds"]:
                        console.print("[yellow]No accessible servers found.[/yellow]")
                        continue
                    
                    result = select_channel_interactive(
                        channels_data,
                        preselected="dm" if fetch_dm else "server"
                    )
                    if not result or not result[0]:
                        continue  # Back to main choice
                    
                    channel_id, channel_name = result
                    
                    count = Prompt.ask(
                        "How many messages do you want to fetch?",
                        default=str(DEFAULT_MESSAGE_COUNT)
                    )
                    
                    try:
                        count = int(count)
                        if count <= 0:
                            raise ValueError("Count must be positive")
                    except ValueError:
                        console.print("[yellow]Invalid count. Using default value.[/yellow]")
                        count = DEFAULT_MESSAGE_COUNT
                    
                    messages = await client.fetch_all_messages(channel_id, count)
                    
                    if messages:
                        display_messages(messages, channel_name)
                        
                        if Confirm.ask("Do you want to save these messages to a file?"):
                            filename = save_messages_to_file(messages, channel_name)
                            console.print(f"[green]Messages saved to {filename}[/green]")
                    else:
                        console.print("[yellow]No messages were retrieved.[/yellow]")
                    
                    if not Confirm.ask("Do you want to fetch messages from another channel?"):
                        break
                        
                except Exception as fetch_error:
                    console.print(f"[red]Error during message fetching: {str(fetch_error)}[/red]")
                    
                    if not Confirm.ask("Do you want to try again?"):
                        break
        
        console.print("[bold green]Thank you for using Discord HTTP Chat Fetcher![/bold green]")
    
    except Exception as e:
        console.print(f"[bold red]Error: {str(e)}[/bold red]")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    asyncio.run(main())
