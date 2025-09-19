# 🎵 Spotify Downloader Telegram Bot

A Telegram bot for downloading Spotify playlists to your Navidrome server with enhanced YouTube integration, manual video selection, and comprehensive playlist management features.

## ⚠️ **Disclaimer**

**IMPORTANT**: This bot uses multiple third-party web services to download music content.

- **No Functionality Guarantee**: We cannot guarantee continuous functionality as it depends on external services
- **Third-Party Dependencies**: The bot relies on external APIs that may change, become unavailable, or block access at any time
- **Legal Responsibility**: Users are responsible for ensuring they comply with copyright laws and Spotify's Terms of Service in their jurisdiction
- **Use at Own Risk**: This software is provided "as is" without warranty of any kind

## ✨ **Features**

### 🎯 **Enhanced Download System**
- **Primary Method**: Spotify → YouTube conversion via tubetify.com + ezconv.com download
- **Manual Video Selection**: Choose from multiple YouTube matches when available
- **Auto-Selection**: Intelligent best-match selection for seamless experience
- **YouTube Direct Downloads**: Support for direct YouTube URL downloads
- **SpotDL Fallback**: Reliable fallback using spotDL for maximum success rate
- **SpotDown API**: Original API-based downloading with proxy support

### 🎵 **Playlist Management**
- Download complete Spotify playlists
- Custom folder naming for organized storage
- Automatic playlist updates with new songs
- Manual sync functionality
- Individual track addition from Spotify search or URL
- Custom playlist creation for single tracks
- Smart sync exclusion for custom playlists
- YouTube video integration with playlist management

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
- Multi-layer download strategy with 4 fallback methods
- Enhanced proxy rotation with caching and failure detection
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
cd SpotiDL_telegram_bot
```

### **2. Install Dependencies**
```bash
pip install -r requirements.txt
playwright install chromium
```

**Dependencies include**:
- `python-telegram-bot` - Telegram bot framework
- `playwright` - Browser automation for web scraping
- `aiohttp` - Async HTTP client for new download methods
- `brotli` - Compression support for HTTP requests
- `yt-dlp` - YouTube downloading capabilities
- `spotdl` - Fallback downloader with 95%+ success rate
- `beautifulsoup4` - HTML parsing for video selection

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
- `/track` - Add individual tracks from Spotify URL or YouTube URL
- `/search` - Search songs in your downloaded library

### **Main Features**

#### **➕ Add Playlist**
1. Click "➕ Add Playlist"
2. Send Spotify playlist URL
3. Choose folder name
4. Confirm download

#### **🎵 Add Individual Tracks**
1. Click "➕ Add Track" or use `/track <spotify_or_youtube_url>`
2. **Search Method**: Search Spotify directly with song/artist name
3. **URL Method**: Paste a Spotify track URL or YouTube video URL
4. **Video Selection**: When multiple YouTube matches are found:
   - Choose manually from up to 5 video options
   - Use "Auto-select Best Match" for convenience
   - Preview video titles before selection
5. Select from search results or confirm URL track
6. Choose existing playlist or create a new one
7. Track is downloaded to the selected playlist

#### **🎯 **Enhanced Video Selection**
- **Multiple Matches**: When Spotify tracks have multiple YouTube versions
- **Manual Choice**: Select the exact video you want (live, official, acoustic, etc.)
- **Smart Titles**: Shortened video titles for better readability
- **Auto-Selection**: Intelligent best-match when you prefer automation
- **Seamless Integration**: Selected videos download through existing playlist system

#### **📺 YouTube Direct Downloads**
- **Direct URLs**: Paste YouTube video URLs for instant download
- **Playlist Integration**: Add YouTube videos to any existing playlist
- **Auto-Title Detection**: Automatic filename generation from video metadata
- **Quality**: High-quality MP3 extraction (320kbps)

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
    "notify_sync_results": true,
    "download_method": "spotdown"
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
- **Enhanced Proxy System**: Smart rotation with caching and failure detection
- **Database**: JSON-based storage with automatic backups
- **Integrity Checking**: File size, duration, and header validation
- **Multi-Source Downloads**: 4-layer fallback system for maximum reliability
- **YouTube Integration**: Direct video processing and conversion
- **Spotify Integration**: Direct API access with browser-simulated token acquisition

### **Enhanced Download Strategy**
The bot implements a robust 4-layer fallback download system:

1. **🥇 Primary: Tubetify → Ezconv** *(NEW)*
   - Converts Spotify URLs to YouTube URLs via tubetify.com
   - Downloads high-quality MP3 from YouTube via ezconv.com API
   - **Manual Video Selection**: Choose from multiple YouTube matches
   - **Auto-Selection**: Intelligent best-match selection
   - **95%+ Success Rate**: Most reliable method for current conditions

2. **🥈 Secondary: SpotDL** *(User Preference)*
   - Only if user sets `preferred_method = 'spotdl'` in settings
   - YouTube-based download via [spotDL](https://github.com/spotDL/spotify-downloader)
   - Activated as fallback when primary method fails

3. **🥉 Tertiary: SpotDown API** *(Original Method)*
   - Legacy spotdown.app API with enhanced proxy support
   - Browser automation for token acquisition
   - Multiple proxy rotation with intelligent caching

4. **🔄 Quaternary: SpotDL Final Fallback**
   - Last resort SpotDL usage when all other methods fail
   - Ensures maximum download success rate

### **New YouTube Integration Features**
- **Direct YouTube URLs**: Support for `youtube.com` and `youtu.be` links
- **Video Selection Interface**: Choose from multiple YouTube matches for Spotify tracks
- **Playlist Integration**: Add YouTube videos to existing playlists
- **Auto-Title Detection**: Intelligent filename generation from video metadata
- **Quality Optimization**: 320kbps MP3 extraction with proper metadata

### **Enhanced Proxy System**
- **Smart Rotation**: Automatic proxy switching with performance tracking
- **Failure Detection**: Identifies and avoids problematic proxies
- **Caching**: Remembers successful proxy configurations
- **Rate Limiting**: Prevents API overload and blocking
- **SSL Bypass**: Handles certificate issues gracefully

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
- **Tubetify/Ezconv Errors**: New primary method issues - bot will retry with SpotDL
- **YouTube Video Selection**: Multiple matches found - use manual selection interface
- **HTTP 500 errors**: Server overload - bot will retry automatically
- **SSL errors**: Proxy issues - bot will switch to direct connection
- **Timeouts**: Network issues - bot will retry with longer timeouts
- **Spotify Token Issues**: Automatic token refresh with browser simulation
- **Video Not Found**: Try alternative YouTube matches or manual selection

## 🆕 **Recent Updates**

### **v2.0 - Enhanced YouTube Integration**
- ✅ New primary download method: Tubetify → Ezconv
- ✅ Manual video selection from multiple YouTube matches
- ✅ Direct YouTube URL support with playlist integration
- ✅ Enhanced proxy system with smart rotation and caching
- ✅ Improved error handling and user feedback
- ✅ 4-layer fallback system for maximum reliability

### **Migration Notes**
- All existing playlists and settings are preserved
- New download method is automatically used for new downloads
- Existing SpotDL fallback functionality is enhanced
- Manual video selection is optional - auto-selection available

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

**Remember**: This bot depends on third-party services and may stop working if those services change or become unavailable. The new multi-layer approach significantly improves reliability, but always have backups of your important playlists!