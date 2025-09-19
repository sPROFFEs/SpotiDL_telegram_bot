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

### **🚀 Quick Setup (Recommended)**

**Use the automated setup script for easy installation:**

```bash
git clone <repository-url>
cd SpotiDL_telegram_bot
./setup.sh
```

The setup script will:
- ✅ Check Python version and create virtual environment
- ✅ Install all dependencies automatically
- ✅ Set up music directories with proper permissions
- ✅ Configure Spotify API credentials (optional but recommended)
- ✅ Set up Telegram bot token
- ✅ Create startup scripts and systemd service
- ✅ Test the installation

### **🔑 Spotify API Setup (Highly Recommended)**

For the best Custom Converter results, configure Spotify API credentials:

1. **Get Spotify API Credentials:**
   - Go to [Spotify Developer Dashboard](https://developer.spotify.com/dashboard)
   - Create a new app (name: "SpotiDL Bot")
   - Copy Client ID and Client Secret

2. **Configure during setup:**
   - The setup script will ask for your credentials
   - Or manually edit `.env` file:
   ```bash
   SPOTIPY_CLIENT_ID=your_client_id_here
   SPOTIPY_CLIENT_SECRET=your_client_secret_here
   ```

### **📱 Telegram Bot Setup**

1. **Create Telegram Bot:**
   - Message [@BotFather](https://t.me/BotFather) on Telegram
   - Send: `/newbot`
   - Choose name and username
   - Copy the bot token

2. **Configure during setup:**
   - The setup script will ask for your token
   - Or manually edit `bot_spot.py`

### **🔧 Manual Installation (Alternative)**

If you prefer manual setup:

```bash
# 1. Clone and navigate
git clone <repository-url>
cd SpotiDL_telegram_bot

# 2. Create virtual environment
python3 -m venv venv
source venv/bin/activate

# 3. Install dependencies
pip install -r requirements.txt
playwright install chromium

# 4. Create directories
sudo mkdir -p /music/local
sudo chown $USER:$USER /music/local

# 5. Configure credentials (optional)
echo "SPOTIPY_CLIENT_ID=your_id" > .env
echo "SPOTIPY_CLIENT_SECRET=your_secret" >> .env

# 6. Set bot token
sed -i "s/YOUR_TELEGRAM_BOT_TOKEN_HERE/your_bot_token/" bot_spot.py

# 7. Run bot
python3 bot_spot.py
```

**Dependencies automatically installed:**
- `python-telegram-bot` - Telegram bot framework
- `playwright` - Browser automation for web scraping
- `aiohttp` - Async HTTP client for new download methods
- `brotli` - Compression support for HTTP requests
- `yt-dlp` - YouTube downloading capabilities
- `spotdl` - Fallback downloader with 95%+ success rate
- `beautifulsoup4` - HTML parsing for video selection
- `spotipy` - Spotify API client for Custom Converter
- `ytmusicapi` - YouTube Music search for Custom Converter

### **🎵 Starting the Bot**

After setup completion:

```bash
# Using the startup script
./start_bot.sh

# Or manually
source venv/bin/activate
export $(cat .env | grep -v '^#' | xargs)  # Load env vars
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
The bot implements a robust 5-layer fallback download system:

1. **🥇 Primary: Tubetify → Ezconv**
   - Converts Spotify URLs to YouTube URLs via tubetify.com
   - Downloads high-quality MP3 from YouTube via ezconv.com API
   - **Manual Video Selection**: Choose from multiple YouTube matches
   - **Auto-Selection**: Intelligent best-match selection
   - **External Service**: Relies on tubetify.com availability

2. **🥈 Secondary: Custom Converter → Ezconv** *(NEW - Self-Hosted)*
   - **Self-hosted Spotify→YouTube conversion** using our own implementation
   - Uses Spotify API for precise track information (when configured)
   - YouTube Music search via ytmusicapi for accurate results
   - **Web scraping fallback** when API credentials not available
   - **No external dependencies** - fully self-contained
   - **Better accuracy** with proper Spotify API credentials

3. **🥉 Tertiary: SpotDL** *(User Preference)*
   - Only if user sets `preferred_method = 'spotdl'` in settings
   - YouTube-based download via [spotDL](https://github.com/spotDL/spotify-downloader)
   - Activated as fallback when primary methods fail

4. **🔄 Quaternary: SpotDown API** *(Original Method)*
   - Legacy spotdown.app API with enhanced proxy support
   - Browser automation for token acquisition
   - Multiple proxy rotation with intelligent caching

5. **🆘 Final: SpotDL Fallback**
   - Last resort SpotDL usage when all other methods fail
   - Ensures maximum download success rate

### **New YouTube Integration Features**
- **Direct YouTube URLs**: Support for `youtube.com` and `youtu.be` links
- **Video Selection Interface**: Choose from multiple YouTube matches for Spotify tracks
- **Playlist Integration**: Add YouTube videos to existing playlists
- **Auto-Title Detection**: Intelligent filename generation from video metadata
- **Quality Optimization**: 320kbps MP3 extraction with proper metadata

### **Custom Converter Features** *(Self-Hosted Solution)*
- **🏠 Self-Hosted**: No reliance on external conversion services
- **🔑 Spotify API Integration**: Precise track info extraction (when configured)
- **🎵 YouTube Music Search**: High-quality music-specific search results
- **🔄 Web Scraping Fallback**: Works without API credentials (reduced accuracy)
- **⚡ Async Performance**: Fully asynchronous implementation
- **🔍 Smart Parsing**: Multiple strategies for track information extraction
- **🐛 Error Resilience**: Graceful handling of API failures and rate limits
- **📊 Better Accuracy**: Superior results when Spotify API is configured

**🙏 Credits**: Custom Converter is based on the excellent [yt2spotify](https://github.com/omijn/yt2spotify) project by [@omijn](https://github.com/omijn), adapted for our Telegram bot integration with additional async support and fallback mechanisms.

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
- New Custom Converter automatically activates as Tubetify fallback
- Existing SpotDL fallback functionality is enhanced
- Manual video selection works with both Tubetify and Custom Converter
- Spotify API configuration is optional but highly recommended

### **Custom Converter Configuration**

**🔑 Recommended: Spotify API Setup**
```bash
# Run setup script (recommended)
./setup.sh

# Or manually create .env file
echo "SPOTIPY_CLIENT_ID=your_client_id" > .env
echo "SPOTIPY_CLIENT_SECRET=your_client_secret" >> .env
```

**⚙️ Without API Credentials:**
- Custom Converter will use web scraping fallback
- Reduced accuracy but still functional
- No additional setup required

**📋 Benefits of API Configuration:**
- ✅ Precise track, artist, and album information
- ✅ Better YouTube Music search accuracy
- ✅ Higher success rate for obscure tracks
- ✅ Proper metadata handling
- ✅ No rate limiting issues

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

## 🙏 **Credits & Acknowledgments**

### **Open Source Projects Used:**
- **[yt2spotify](https://github.com/omijn/yt2spotify)** by [@omijn](https://github.com/omijn) - Core inspiration and architecture for our Custom Converter implementation
- **[spotDL](https://github.com/spotDL/spotify-downloader)** - YouTube-based music downloader used as fallback
- **[python-telegram-bot](https://github.com/python-telegram-bot/python-telegram-bot)** - Telegram Bot API wrapper
- **[playwright](https://github.com/microsoft/playwright-python)** - Browser automation for web scraping
- **[spotipy](https://github.com/spotipy-dev/spotipy)** - Spotify Web API wrapper
- **[ytmusicapi](https://github.com/sigma67/ytmusicapi)** - YouTube Music API client

### **External Services:**
- **[tubetify.com](https://tubetify.com)** - Primary Spotify→YouTube conversion service
- **[ezconv.com](https://ezconv.com)** - YouTube audio extraction service
- **[spotdown.app](https://spotdown.app)** - Original Spotify download API

### **Special Thanks:**
- **[@omijn](https://github.com/omijn)** for the excellent yt2spotify project that served as the foundation for our Custom Converter
- All contributors to the open source projects that make this bot possible
- The Spotify and YouTube communities for their APIs and documentation

---

**Remember**: This bot depends on third-party services and may stop working if those services change or become unavailable. The new multi-layer approach significantly improves reliability, but always have backups of your important playlists!