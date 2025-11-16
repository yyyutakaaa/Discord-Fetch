# Discord Chat Fetcher

A Python tool to fetch and export Discord messages from DMs and server channels using your Discord user token.

## Features

- Export DMs and Server Messages - Access both private messages and server channels
- Multiple Export Formats - Save as TXT, JSON, or CSV
- User-Friendly Interface - Clean menus and progress tracking
- Rate Limiting Protection - Built-in delays to prevent API limits
- Secure Token Storage - Save your token securely using system keyring
- Search Functionality - Find channels by name
- Clean Filenames - No spaces or special characters in exported files

## Requirements

- Python 3.8 or higher
- Discord account with access to the channels you want to export

## Installation

### 1. Clone or download this repository

```bash
git clone <repository-url>
cd discord-chat-fetcher
```

### 2. Create a virtual environment (recommended)

```bash
python -m venv discord_fetcher_env
source discord_fetcher_env/bin/activate  # On Windows: discord_fetcher_env\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

## Getting Your Discord Token

**Important:** This tool requires your Discord USER token, not a bot token.

### Method

1. Open Discord in your web browser
2. Press F12 to open Developer Tools
3. Go to the Network tab
4. Clear the network log
5. Send any message in any channel/DM
6. Look for a request containing 'messages' in the URL
7. Click on that request
8. In Request Headers, find 'authorization:' - copy everything after it

**Warning:** Never share your token with anyone. This gives full access to your Discord account.

## Usage

### Basic Usage

Run the script:

```bash
python discord_chat_fetcher.py
```

### Setting Token as Environment Variable

For easier usage, set your token as an environment variable:

```bash
# Linux/Mac
export DISCORD_TOKEN="your_token_here"

# Windows
set DISCORD_TOKEN=your_token_here
```

Or create a `.env` file in the project directory:

```
DISCORD_TOKEN=your_token_here
```

### Using the Interface

1. **Main Menu Options:**
   - Fetch Discord messages
   - Configure save directory
   - Configure token storage
   - Manage saved token
   - Exit

2. **Channel Selection:**
   - Choose between Direct Messages or Server Channels
   - For servers: first select server, then select channel
   - Use search functionality to find specific channels

3. **Message Export:**
   - Choose number of messages to fetch
   - Select export format (TXT, JSON, CSV)
   - Messages are saved to configured directory

## Export Formats

### TXT Format
Human-readable format with date separators and timestamps:
```
Discord Messages from #general

═══════════════════════════════════════════════════════

─────── Monday, January 15, 2024 ───────

14:30:25 - username: Hello world!
14:31:02 - username2: How are you?
```

### JSON Format
Structured data format with full message metadata:
```json
{
  "channel_info": {
    "channel_name": "#general",
    "export_time": "2024-01-15T14:30:25",
    "message_count": 100
  },
  "messages": [...]
}
```

### CSV Format
Spreadsheet-compatible format with columns:
- Timestamp, Date, Time, Author, Username, Author_ID, Message, Attachments

## Configuration

### Save Directory
By default, messages are saved to `~/Discord_Chat_Fetcher_Messages/`

To change the save directory:
1. Run the script
2. Select "Configure save directory"
3. Enter your preferred path

### Token Storage
The tool supports multiple token storage methods:

1. **System Keyring (Recommended)** - Secure storage using your OS keyring
2. **File Storage** - Saves token to a local file (less secure)
3. **Environment Variables** - Use DISCORD_TOKEN environment variable
4. **No Storage** - Enter token each time

## File Organization

Exported files are organized as follows:
```
Discord_Chat_Fetcher_Messages/
├── DM_with_username/
│   ├── DM_with_username_20240115_143025.txt
│   └── DM_with_username_20240115_150030.json
├── general_from_ServerName/
│   └── general_from_ServerName_20240115_143025.csv
└── ...
```

## Rate Limiting

The tool includes built-in rate limiting protection:
- Automatic delays between API requests
- Graceful handling of Discord's rate limits
- Progress tracking for large servers

## Troubleshooting

### Common Issues

**"Invalid Discord token" error:**
- Make sure you copied the entire token
- Verify the token is from Discord web (not mobile app)
- Try getting a fresh token by logging out and back in

**"Rate limited" warnings:**
- This is normal for accounts in many servers
- The tool will automatically handle this
- Some servers may be skipped if rate limited

**"No permission" errors:**
- You can only export messages from channels you have access to
- Private channels you're not in will be skipped

**Empty message exports:**
- Check if you have permission to read message history
- Some channels may have restrictions on older messages

### Getting Help

If you encounter issues:
1. Check that your token is valid and recent
2. Ensure you have Python 3.8+
3. Verify all dependencies are installed
4. Try with a smaller number of messages first

## Legal and Ethical Considerations

- Only export messages from channels you have legitimate access to
- Respect Discord's Terms of Service
- Be mindful of privacy when handling exported data
- This tool is for personal use and data backup purposes

## Security Notes

- Your Discord token provides full access to your account
- Never share your token with others
- Use environment variables or keyring storage when possible
- Regularly rotate your token for security

## License

This project is provided as-is for educational and personal use purposes.
