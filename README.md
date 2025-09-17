# 🎵 Spotify Downloader Telegram Bot

A Telegram bot for downloading Spotify playlists to your Navidrome server with integrity checking and playlist management features.

## ⚠️ **Disclaimer**

**IMPORTANT**: This bot uses third-party web services (primarily spotdown.app) to download music content.

- **No Functionality Guarantee**: We cannot guarantee the continuous functionality of this bot as it depends on external services
- **Third-Party Dependencies**: The bot relies on external APIs that may change, become unavailable, or block access at any time
- **Legal Responsibility**: Users are responsible for ensuring they comply with copyright laws and Spotify's Terms of Service in their jurisdiction
- **Use at Own Risk**: This software is provided "as is" without warranty of any kind

## ✨ **Features**

### 🎵 **Playlist Management**
- Download complete Spotify playlists
- Custom folder naming for organized storage
- Automatic playlist updates with new songs
- Manual sync functionality
- Individual track addition from Spotify search or URL
- Custom playlist creation for single tracks
- Smart sync exclusion for custom playlists

### 🔍 **Integrity Checking**
- Verify downloaded song completeness
- Check individual playlists or all playlists at once
- Auto-fix corrupted/incomplete downloads
- Duration-based validation using ffmpeg (optional)

### 📋 **Song Management**
- List all songs in any playlist
- Delete individual songs with confirmation
- File existence verification
- Duplicate prevention system
- Enhanced deletion confirmations with detailed feedback
- Improved playlist and song browsing interface

### 🛡️ **Reliability Features**
- Multi-layer download strategy (direct, proxy, HTTP fallback)
- Automatic retry with exponential backoff
- Rate limiting to prevent API overload
- SSL certificate error handling
- Database backup and corruption recovery
- Enhanced Spotify token acquisition with browser simulation
- Robust callback data parsing for complex playlist IDs
- Improved error handling and user feedback

## 📁 **File Structure**

The bot downloads all music to: **`/music/local/`**

```
/music/local/
├── PlaylistName1/
│   ├── Artist - Song1.mp3
│   ├── Artist - Song2.mp3
│   └── ...
├── PlaylistName2/
│   ├── Artist - Song3.mp3
│   └── ...
└── ...
```

## 🚀 **Installation**

### **Prerequisites**
- Python 3.8+
- Linux/macOS (recommended) or Windows
- Write access to `/music/local/` directory

### **1. Clone Repository**
```bash
git clone <repository-url>
cd spot_bot
```

### **2. Install Dependencies**
```bash
pip install -r requirements.txt
playwright install chromium
```

### **3. Optional: Install ffmpeg for Enhanced Integrity Checking**
```bash
# Ubuntu/Debian
sudo apt install ffmpeg

# macOS with Homebrew
brew install ffmpeg

# Windows - Download from https://ffmpeg.org/
```

### **4. Create Telegram Bot**
1. Message [@BotFather](https://t.me/BotFather) on Telegram
2. Create a new bot with `/newbot`
3. Copy the bot token

### **5. Configure Bot**
Edit `bot_spot.py` and replace:
```python
TELEGRAM_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN_HERE'
```

### **6. Set Up Directory**
```bash
sudo mkdir -p /music/local
sudo chown $USER:$USER /music/local
```

### **7. Run Bot**
```bash
python3 bot_spot.py
```

## 🎮 **Usage**

### **Basic Commands**
- `/start` - Show main menu
- `/settings` - Configure bot settings
- `/sync` - Manual playlist synchronization
- `/track` - Add individual tracks from Spotify URL
- `/search` - Search songs in your downloaded library

### **Main Features**

#### **➕ Add Playlist**
1. Click "➕ Add Playlist"
2. Send Spotify playlist URL
3. Choose folder name
4. Confirm download

#### **🎵 Add Individual Tracks**
1. Click "➕ Add Track" or use `/track <spotify_url>`
2. **Search Method**: Search Spotify directly with song/artist name
3. **URL Method**: Paste a Spotify track URL
4. Select from search results or confirm URL track
5. Choose existing playlist or create a new one
6. Track is downloaded to the selected playlist

#### **🔍 Integrity Checking**
- **Individual**: Click "🔍 Check Integrity" on any playlist
- **Global**: Click "🔍 Check All Playlists"
- **Auto-fix**: Click "🔧 Fix Issues" to repair corrupted files

#### **📋 Song Management**
- Click "📋 Songs" on any playlist
- View all songs with file status indicators
- Delete individual songs with "🗑️ Delete" buttons

#### **⚙️ Auto-Sync**
Configure automatic playlist updates:
- Enable/disable auto-sync
- Set sync day and time
- Toggle sync notifications
- **Smart Sync**: Only syncs Spotify playlists (excludes custom playlists)
- **Detailed Reporting**: Shows total, syncable, and custom playlist counts

#### **🔍 Library Search**
- Use `/search <query>` to find downloaded songs
- Search by song title, artist, or playlist name
- Quick access to your entire music library

## 🔧 **Configuration**

### **Settings File** (`bot_settings.json`)
```json
{
    "sync_enabled": false,
    "sync_day": "monday",
    "sync_time": "09:00",
    "notify_sync_results": true
}
```

### **Directory Structure**
- `playlist_db.json` - Playlist and song database
- `bot_settings.json` - Bot configuration
- `logs/` - Application logs
  - `bot.log` - General bot logs
  - `sync.log` - Sync operation logs
  - `download.log` - Download logs

## 🛠️ **Technical Details**

### **Architecture**
- **Browser Automation**: Uses Playwright for web scraping
- **Proxy Support**: Automatic proxy rotation for reliability
- **Database**: JSON-based storage with automatic backups
- **Integrity Checking**: File size, duration, and header validation
- **Error Recovery**: Multi-layer fallback strategies
- **Spotify Integration**: Direct API access with browser-simulated token acquisition
- **Playlist Types**: Distinguishes between Spotify playlists and custom playlists

### **Download Strategy**
1. **Direct Connection** - Primary method
2. **Proxy Rotation** - When direct fails
3. **HTTP Fallback** - When browser automation fails
4. **Service Switching** - Adaptive endpoint selection

### **Reliability Features**
- Exponential backoff retry logic
- SSL certificate bypass for proxies
- Rate limiting to prevent blocking
- Automatic duplicate prevention
- Database corruption recovery
- Enhanced error handling with user-friendly messages
- Robust callback data parsing for complex operations
- Automatic sync exclusion for non-syncable playlists

## 📊 **Monitoring**

### **Log Files**
- Monitor `logs/bot.log` for general operations
- Check `logs/download.log` for download issues
- Review `logs/sync.log` for sync problems

### **Common Issues**
- **HTTP 500 errors**: Server overload - bot will retry automatically
- **SSL errors**: Proxy issues - bot will switch to direct connection
- **Timeouts**: Network issues - bot will retry with longer timeouts
- **Spotify Token Issues**: Automatic token refresh with browser simulation
- **Playlist Not Found**: Check if playlist ID parsing is working correctly
- **Sync Exclusions**: Custom playlists are automatically excluded from sync operations

## 🤝 **Contributing**

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Test thoroughly
5. Submit a pull request

## 📄 **License**

This project is provided as-is for educational purposes. Users are responsible for compliance with applicable laws and service terms.

## 🆘 **Support**

- **Issues**: Report bugs via GitHub Issues
- **Documentation**: Check this README and code comments
- **Logs**: Always check log files for debugging information

---

**Remember**: This bot depends on third-party services and may stop working if those services change or become unavailable. Always have backups of your important playlists!