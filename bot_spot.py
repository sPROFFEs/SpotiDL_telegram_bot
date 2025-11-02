import os
import re
import json
import logging
import asyncio
import random
import subprocess
from pathlib import Path
from urllib.parse import quote, urlparse, parse_qs
from datetime import datetime, time, timedelta
from typing import Dict, List, Optional

from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup, BotCommand, MenuButton, MenuButtonCommands
from telegram.ext import (
    Application,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    filters,
    ContextTypes,
    JobQueue,
)
from telegram.constants import ParseMode

# --- SPOTDL FALLBACK ---
try:
    from spotdl_fallback import try_spotdl_fallback, download_from_youtube_url, is_youtube_url
    SPOTDL_AVAILABLE = True
except ImportError:
    SPOTDL_AVAILABLE = False

# --- YT-DLP DOWNLOADER ---
try:
    from ytdlp_downloader import download_audio as download_audio_ytdlp, get_video_info, get_playlist_info, is_youtube_playlist_url
    YTDLP_AVAILABLE = True
except ImportError:
    YTDLP_AVAILABLE = False

# --- TUBETIFY CONVERTER ---
try:
    from tubetify_converter import spotify_to_youtube, get_youtube_for_spotify
    TUBETIFY_AVAILABLE = True
except ImportError:
    TUBETIFY_AVAILABLE = False

# --- CUSTOM CONVERTER ---
try:
    from custom_converter import spotify_to_youtube_custom, get_youtube_for_spotify_custom
    CUSTOM_CONVERTER_AVAILABLE = True
except ImportError:
    CUSTOM_CONVERTER_AVAILABLE = False

# --- CONFIGURATION ---
TELEGRAM_TOKEN = 'TOKEN' # Replace with your actual bot token
DB_FILE = Path('playlist_db.json')
MUSIC_DIR = Path('/music/local')
LOGS_DIR = Path('logs')
SETTINGS_FILE = Path('bot_settings.json')

# --- DOWNLOAD METHODS CONFIGURATION ---
DOWNLOAD_METHODS = {
    'spotify_youtube_ytdlp': {
        'name': '🎯 Spotify→YouTube→yt-dlp',
        'available': lambda: (TUBETIFY_AVAILABLE or CUSTOM_CONVERTER_AVAILABLE) and YTDLP_AVAILABLE,
        'description': 'Convert Spotify to YouTube, then download via yt-dlp'
    },
    'spotdl': {
        'name': '🎵 SpotDL',
        'available': lambda: SPOTDL_AVAILABLE,
        'description': 'Direct YouTube download via SpotDL'
    },
    'spotdown': {
        'name': '🌐 SpotDown',
        'available': lambda: True,  # Always available
        'description': 'Original SpotDown.app API method'
    }
}

# --- RETRY CONFIGURATION ---
MAX_API_ATTEMPTS = 3       # Maximum attempts for API calls (reduced to prevent infinite loops)
MAX_DOWNLOAD_ATTEMPTS = 2  # Try to download each song up to 2 times (reduced to prevent infinite loops)
RETRY_DELAY_SECONDS = 3    # Wait 3 seconds between attempts
API_TIMEOUT = 30000        # API request timeout in milliseconds

# --- LOGGING CONFIGURATION ---
LOGS_DIR.mkdir(exist_ok=True)

# Setup file handler for bot logs
file_handler = logging.FileHandler(LOGS_DIR / 'bot.log', encoding='utf-8')
file_handler.setLevel(logging.INFO)
file_formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
file_handler.setFormatter(file_formatter)

# Setup console handler
console_handler = logging.StreamHandler()
console_handler.setLevel(logging.INFO)
console_formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(message)s')
console_handler.setFormatter(console_formatter)

# Configure root logger
logging.basicConfig(
    level=logging.INFO,
    handlers=[file_handler, console_handler]
)
logger = logging.getLogger(__name__)

# Log SpotDL fallback availability
if SPOTDL_AVAILABLE:
    logger.info("✅ SpotDL fallback disponible")
else:
    logger.warning("⚠️ SpotDL fallback no disponible - instalar con: pip install spotdl")

# Log YtDlp downloader availability
if YTDLP_AVAILABLE:
    logger.info("✅ YtDlp YouTube downloader disponible")
else:
    logger.warning("⚠️ YtDlp YouTube downloader no disponible - instalar yt-dlp")

# Log Tubetify converter availability
if TUBETIFY_AVAILABLE:
    logger.info("✅ Tubetify Spotify→YouTube converter disponible")
else:
    logger.warning("⚠️ Tubetify converter no disponible - instalar beautifulsoup4")

# Log Custom converter availability
if CUSTOM_CONVERTER_AVAILABLE:
    logger.info("✅ Custom Spotify→YouTube converter disponible")
else:
    logger.warning("⚠️ Custom converter no disponible - instalar spotipy y ytmusicapi")

# Additional loggers for different components
sync_logger = logging.getLogger('sync')
sync_handler = logging.FileHandler(LOGS_DIR / 'sync.log', encoding='utf-8')
sync_handler.setFormatter(file_formatter)
sync_logger.addHandler(sync_handler)
sync_logger.setLevel(logging.INFO)

download_logger = logging.getLogger('download')
download_handler = logging.FileHandler(LOGS_DIR / 'download.log', encoding='utf-8')
download_handler.setFormatter(file_formatter)
download_logger.addHandler(download_handler)
download_logger.setLevel(logging.INFO)

# --- HELPER FUNCTIONS ---
def escape_markdown(text) -> str:
    """Escape markdown special characters for Telegram"""
    if text is None:
        return 'Unknown'

    text_str = str(text)

    # First handle any problematic Unicode characters by normalizing them
    try:
        import unicodedata
        # Normalize unicode characters and ensure proper UTF-8 encoding
        text_str = unicodedata.normalize('NFKC', text_str)
        text_str = text_str.encode('utf-8', errors='replace').decode('utf-8')

        # Replace potentially problematic characters that cause Telegram parsing issues
        replacements = {
            'ø': 'o',
            'Ø': 'O',
            'ł': 'l',
            'Ł': 'L',
            'đ': 'd',
            'Đ': 'D',
            'ß': 'ss',
            # Keep most accented characters as they usually work fine
        }

        for old, new in replacements.items():
            text_str = text_str.replace(old, new)

    except (UnicodeEncodeError, UnicodeDecodeError, ImportError):
        # If there are encoding issues, replace problematic characters
        text_str = str(text).encode('ascii', errors='replace').decode('ascii')

    # Then escape markdown special characters that are used by Telegram markdown
    # Order matters - do backslash first to avoid double escaping
    text_str = text_str.replace('\\', '\\\\')
    text_str = text_str.replace('*', '\\*')
    text_str = text_str.replace('_', '\\_')
    text_str = text_str.replace('[', '\\[')
    text_str = text_str.replace(']', '\\]')
    text_str = text_str.replace('`', '\\`')
    text_str = text_str.replace('~', '\\~')

    # Also escape parentheses that can cause issues in some contexts
    text_str = text_str.replace('(', '\\(')
    text_str = text_str.replace(')', '\\)')

    return text_str

def normalize_track_info(track_info: dict) -> dict:
    """Normalize track info to have consistent format"""
    # Safely get values and ensure they are strings
    title = str(track_info.get('title', 'Unknown')) if track_info.get('title') else 'Unknown'
    artist = str(track_info.get('artist', 'Unknown')) if track_info.get('artist') else 'Unknown'
    album = str(track_info.get('album', title)) if track_info.get('album') else title
    thumbnail = str(track_info.get('thumbnail', '')) if track_info.get('thumbnail') else ''
    url = str(track_info.get('url', '')) if track_info.get('url') else ''
    preview_url = str(track_info.get('previewUrl', '')) if track_info.get('previewUrl') else ''

    # Handle duration conversion from milliseconds to m:ss format
    duration = '0:00'
    if track_info.get('duration_ms'):
        try:
            total_seconds = int(track_info['duration_ms']) // 1000
            minutes = total_seconds // 60
            seconds = total_seconds % 60
            duration = f"{minutes}:{seconds:02d}"
        except (ValueError, TypeError):
            duration = '0:00'
    elif track_info.get('duration'):
        duration = str(track_info['duration'])

    return {
        'title': title,
        'artist': artist,
        'album': album,
        'thumbnail': thumbnail,
        'url': url,
        'duration': duration,
        'previewUrl': preview_url
    }

def setup_database():
    MUSIC_DIR.mkdir(parents=True, exist_ok=True)
    if not DB_FILE.exists():
        with open(DB_FILE, 'w') as f:
            json.dump({}, f)

def load_db():
    try:
        with open(DB_FILE, 'r') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError) as e:
        logger.error(f"Database corrupted or missing: {e}")
        # Create backup of corrupted file
        if DB_FILE.exists():
            backup_path = DB_FILE.with_suffix('.json.corrupted')
            DB_FILE.rename(backup_path)
            logger.info(f"Corrupted database backed up to: {backup_path}")

        # Return empty database
        logger.info("Creating new empty database")
        return {}

def remove_duplicates_from_playlist(songs: list) -> list:
    """Remove duplicate songs based on URL"""
    seen_urls = set()
    unique_songs = []

    for song in songs:
        song_url = song.get('url', '')
        if song_url and song_url not in seen_urls:
            seen_urls.add(song_url)
            unique_songs.append(song)
        else:
            logger.debug(f"Skipping duplicate song: {song.get('artist', 'Unknown')} - {song.get('title', 'Unknown')}")

    return unique_songs

def save_db(data):
    try:
        # Clean duplicates before saving
        for playlist_id, playlist_data in data.items():
            if 'songs' in playlist_data:
                original_count = len(playlist_data['songs'])
                playlist_data['songs'] = remove_duplicates_from_playlist(playlist_data['songs'])
                removed_count = original_count - len(playlist_data['songs'])
                if removed_count > 0:
                    playlist_name = playlist_data.get('name', 'Unknown')
                    logger.info(f"Removed {removed_count} duplicate songs from {playlist_name} before saving")

        # Create backup of current database before saving
        if DB_FILE.exists():
            backup_path = DB_FILE.with_suffix('.json.backup')
            with open(DB_FILE, 'r') as src, open(backup_path, 'w') as dst:
                dst.write(src.read())

        # Save new data
        with open(DB_FILE, 'w') as f:
            json.dump(data, f, indent=4)

        logger.debug("Database saved successfully")

    except Exception as e:
        logger.error(f"Error saving database: {e}")
        # Try to restore backup if save failed
        backup_path = DB_FILE.with_suffix('.json.backup')
        if backup_path.exists():
            backup_path.rename(DB_FILE)
            logger.info("Restored database from backup due to save error")
        raise

def load_settings():
    """Load bot settings from file"""
    if not SETTINGS_FILE.exists():
        default_settings = {
            'sync_enabled': False,
            'sync_day': 'monday',  # monday to sunday
            'sync_time': '09:00',  # HH:MM format
            'last_sync': None,
            'user_id': None,  # User who configured sync
            'notify_sync_results': True  # Send notifications for auto sync results
        }
        save_settings(default_settings)
        return default_settings

    with open(SETTINGS_FILE, 'r') as f:
        return json.load(f)

def save_settings(settings):
    """Save bot settings to file"""
    with open(SETTINGS_FILE, 'w') as f:
        json.dump(settings, f, indent=4)

def get_download_priority():
    """Get current download methods priority order"""
    settings = load_settings()
    default_priority = ['spotify_youtube_ytdlp', 'spotdl', 'spotdown']
    return settings.get('download_priority', default_priority)

def set_download_priority(new_priority):
    """Set new download methods priority order"""
    settings = load_settings()
    settings['download_priority'] = new_priority
    save_settings(settings)

def get_available_methods():
    """Get list of currently available download methods"""
    available = []
    for method_id, method_info in DOWNLOAD_METHODS.items():
        if method_info['available']():
            available.append({
                'id': method_id,
                'name': method_info['name'],
                'description': method_info['description']
            })
    return available

def sanitize_filename(name: str) -> str:
    """Removes invalid characters from file/folder names."""
    if not name or name.strip() == '':
        return "Unknown"

    # Remove invalid filesystem characters but keep Unicode characters
    sanitized = re.sub(r'[\\/*?:"<>|]', "", name.strip())

    # Replace multiple spaces with single space
    sanitized = re.sub(r'\s+', ' ', sanitized)

    # If the result is empty after sanitization, return Unknown
    if not sanitized:
        return "Unknown"

    return sanitized

# --- SONG INTEGRITY CHECKER ---
async def check_song_integrity(file_path: Path, expected_duration: str) -> bool:
    """Check if a song file is complete and not corrupted

    Enhanced for YouTube downloads which may have different durations:
    - Uses intelligent tolerance for duration differences (35-50% depending on song length)
    - Distinguishes between corrupted files and different versions
    - Focuses on detecting truly broken files rather than alternative versions
    - Considers download source for more accurate validation

    Args:
        file_path: Path to the audio file
        expected_duration: Expected duration in 'MM:SS' format

    Returns:
        bool: True if file appears valid, False if likely corrupted
    """
    try:
        if not file_path.exists():
            return False

        # Get file size
        file_size = file_path.stat().st_size

        # Very small files are likely corrupted (less than 500KB for any song is suspicious)
        min_file_size = 500 * 1024  # Increased from 100KB to 500KB for YouTube downloads
        if file_size < min_file_size:
            logger.warning(f"File too small (likely corrupted) - {file_path.name}: {file_size} bytes (minimum: {min_file_size})")
            return False

        # Parse expected duration to estimate minimum expected file size
        duration_parts = expected_duration.split(':')
        if len(duration_parts) == 2:
            try:
                expected_minutes = int(duration_parts[0])
                expected_seconds = int(duration_parts[1])
                expected_total_seconds = expected_minutes * 60 + expected_seconds

                # Estimate minimum file size based on duration (very conservative)
                # Using 64kbps as minimum reasonable quality for modern downloads
                min_bitrate_kbps = 64
                estimated_min_size = (expected_total_seconds * min_bitrate_kbps * 1024) // 8

                # Only flag as corrupted if significantly smaller than expected
                if file_size < estimated_min_size * 0.3:  # Very conservative 30% threshold
                    logger.warning(f"File significantly undersized (likely corrupted) - {file_path.name}: {file_size} bytes for {expected_total_seconds}s (expected minimum: {estimated_min_size * 0.3:.0f})")
                    return False

            except (ValueError, IndexError):
                pass  # If we can't parse duration, just rely on minimum size check

        # Try to use ffprobe if available, otherwise rely on file size check
        try:
            cmd = [
                'ffprobe', '-v', 'quiet', '-show_entries', 'format=duration',
                '-of', 'csv=p=0', str(file_path)
            ]

            result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if result.returncode == 0:
                actual_duration = float(result.stdout.strip())

                # Parse expected duration
                if len(duration_parts) == 2:
                    expected_minutes = int(duration_parts[0])
                    expected_seconds = int(duration_parts[1])
                    expected_total_seconds = expected_minutes * 60 + expected_seconds

                    # Intelligent tolerance calculation for YouTube downloads
                    # YouTube often has different versions (extended, live, remixes, etc.)

                    # Base tolerance: larger for longer songs
                    if expected_total_seconds < 120:  # Songs under 2 minutes
                        tolerance_percentage = 0.50  # 50% tolerance
                    elif expected_total_seconds < 300:  # Songs under 5 minutes
                        tolerance_percentage = 0.40  # 40% tolerance
                    else:  # Longer songs
                        tolerance_percentage = 0.35  # 35% tolerance

                    # Minimum tolerance of 15 seconds
                    tolerance = max(15, expected_total_seconds * tolerance_percentage)
                    duration_diff = abs(actual_duration - expected_total_seconds)

                    # Check if file is suspiciously short (likely corrupted)
                    is_too_short = actual_duration < expected_total_seconds * 0.5

                    # Check if file is way too long (likely wrong song or compilation)
                    is_too_long = actual_duration > expected_total_seconds * 3

                    # File is invalid only if clearly corrupted or completely wrong
                    is_valid = not (is_too_short or is_too_long)

                    if not is_valid:
                        if is_too_short:
                            logger.warning(f"Possibly corrupted (too short) - {file_path.name}: expected {expected_total_seconds}s, got {actual_duration:.1f}s")
                        elif is_too_long:
                            logger.info(f"Possibly wrong track (too long) - {file_path.name}: expected {expected_total_seconds}s, got {actual_duration:.1f}s")
                    elif duration_diff > tolerance:
                        # Log as info but don't mark as invalid (different version, not corrupted)
                        percentage_diff = (duration_diff / expected_total_seconds) * 100
                        logger.info(f"Different version detected - {file_path.name}: expected {expected_total_seconds}s, got {actual_duration:.1f}s ({percentage_diff:.1f}% difference)")
                        # Still consider valid since it's likely just a different version
                        is_valid = True

                    return is_valid

        except (FileNotFoundError, subprocess.SubprocessError, subprocess.TimeoutExpired):
            # ffprobe not available or failed, rely on file size and header check
            logger.debug(f"ffprobe not available for {file_path.name}, using basic validation")

        # If we can't use ffprobe, check if the file is a valid audio file by reading its header
        try:
            with open(file_path, 'rb') as f:
                header = f.read(16)  # Read more bytes for better detection

            # Check for common audio file headers
            is_mp3 = header.startswith(b'ID3') or header[0:2] == b'\xff\xfb' or header[0:2] == b'\xff\xfa'
            is_mp4 = header[4:8] == b'ftyp'
            is_ogg = header.startswith(b'OggS')
            is_flac = header.startswith(b'fLaC')

            if is_mp3 or is_mp4 or is_ogg or is_flac:
                # Looks like a valid audio file
                logger.debug(f"Valid audio header detected - {file_path.name}")
                return True
            else:
                logger.warning(f"Invalid or unrecognized audio header - {file_path.name}")
                return False

        except Exception as e:
            logger.error(f"Error reading file header for {file_path.name}: {e}")
            return False

        # If all checks pass and we can't verify otherwise, assume valid
        return True

    except Exception as e:
        logger.warning(f"Error checking integrity of {file_path}: {e}")
        return True  # Assume valid if we can't check

async def check_playlist_integrity(playlist_id: str, playlist_data: dict) -> dict:
    """Check integrity of all songs in a playlist"""
    playlist_name = playlist_data.get('name', 'Unknown')
    playlist_dir = MUSIC_DIR / playlist_name
    songs = playlist_data.get('songs', [])

    result = {
        'total_songs': len(songs),
        'valid_songs': 0,
        'corrupted_songs': [],
        'missing_songs': [],
        'checked_songs': 0
    }

    logger.info(f"Starting integrity check for playlist: {playlist_name}")

    for song in songs:
        song_title = sanitize_filename(song.get('title', 'Unknown'))
        artist_name = sanitize_filename(song.get('artist', 'Unknown'))
        duration = song.get('duration', '0:00')
        file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

        if not file_path.exists():
            result['missing_songs'].append({
                'title': song_title,
                'artist': artist_name,
                'file_path': str(file_path),
                'song_data': song
            })
            continue

        result['checked_songs'] += 1

        is_valid = await check_song_integrity(file_path, duration)
        if is_valid:
            result['valid_songs'] += 1
        else:
            result['corrupted_songs'].append({
                'title': song_title,
                'artist': artist_name,
                'file_path': str(file_path),
                'song_data': song
            })

    # Calculate statistics
    total_issues = len(result['corrupted_songs']) + len(result['missing_songs'])
    if total_issues == 0:
        logger.info(f"Integrity check completed for {playlist_name}: ✅ All {result['valid_songs']} songs are valid")
    else:
        logger.info(f"Integrity check completed for {playlist_name}: {result['valid_songs']}/{result['checked_songs']} valid, {len(result['corrupted_songs'])} potentially corrupted, {len(result['missing_songs'])} missing")

    return result

async def fix_corrupted_songs(playlist_id: str, corrupted_songs: list, missing_songs: list) -> dict:
    """Remove corrupted/missing songs and re-download them"""
    db = load_db()
    if playlist_id not in db:
        return {'success': False, 'error': 'Playlist not found'}

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')

    result = {
        'removed_files': 0,
        'redownloaded': 0,
        'failed_downloads': 0,
        'songs_to_redownload': []
    }

    # Collect all songs that need to be re-downloaded
    songs_to_fix = []

    # Add corrupted songs
    for corrupted in corrupted_songs:
        file_path = Path(corrupted['file_path'])
        if file_path.exists():
            file_path.unlink()  # Remove corrupted file
            result['removed_files'] += 1
            logger.info(f"Removed corrupted file: {file_path}")
        songs_to_fix.append(corrupted['song_data'])

    # Add missing songs
    for missing in missing_songs:
        songs_to_fix.append(missing['song_data'])

    result['songs_to_redownload'] = songs_to_fix

    # Re-download songs
    playlist_dir = MUSIC_DIR / playlist_name
    for song in songs_to_fix:
        try:
            song_title = sanitize_filename(song.get('title', 'Unknown'))
            artist_name = sanitize_filename(song.get('artist', 'Unknown'))
            file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

            success = await api_client.download_song(song, file_path)
            if success:
                result['redownloaded'] += 1
                logger.info(f"Re-downloaded: {artist_name} - {song_title}")
            else:
                result['failed_downloads'] += 1
                logger.warning(f"Failed to re-download: {artist_name} - {song_title}")

        except Exception as e:
            result['failed_downloads'] += 1
            logger.error(f"Error re-downloading {song.get('title', 'Unknown')}: {e}")

    return result

# --- FREE PROXY SYSTEM ---
class ProxyManager:
    """Manages free proxy servers for API requests - Enhanced for long playlists"""

    def __init__(self):
        # List of free proxy APIs and sources
        self.proxy_sources = [
            "https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"
        ]
        self.proxies = []
        self.working_proxies = []  # Cache of verified working proxies
        self.failed_proxies = set()  # Track failed proxies to avoid them temporarily
        self.last_update = None
        self.proxy_index = 0  # For rotation
        self.requests_per_proxy = 0  # Track requests per proxy
        self.max_requests_per_proxy = 15  # Switch proxy after X requests to avoid rate limits

    async def get_working_proxy(self, context: ContextTypes.DEFAULT_TYPE = None, force_new=False):
        """Get a working proxy from available sources - Enhanced with rotation"""
        try:
            # Update proxy list every 20 minutes (more frequent for long lists)
            if not self.proxies or not self.last_update or \
               (datetime.now() - self.last_update).total_seconds() > 1200:
                await self._update_proxies()

            # If we've used current proxy too much, force rotation
            if self.requests_per_proxy >= self.max_requests_per_proxy:
                force_new = True
                self.requests_per_proxy = 0

            # If we have working proxies cached and don't need new one, rotate through them
            if self.working_proxies and not force_new:
                proxy = self.working_proxies[self.proxy_index % len(self.working_proxies)]
                self.proxy_index = (self.proxy_index + 1) % len(self.working_proxies)
                self.requests_per_proxy += 1
                logger.debug(f"Using cached proxy: {proxy} (request #{self.requests_per_proxy})")
                return proxy

            # Test and cache working proxies (test more proxies for better pool)
            tested_count = 0
            max_tests = min(25, len(self.proxies))  # Test more proxies

            for proxy in self.proxies:
                if proxy in self.failed_proxies:
                    continue

                if tested_count >= max_tests:
                    break

                tested_count += 1
                if await self._test_proxy(proxy):
                    if proxy not in self.working_proxies:
                        self.working_proxies.append(proxy)
                        logger.info(f"Added working proxy to pool: {proxy}")

                    # Clean failed proxies periodically
                    if len(self.failed_proxies) > 50:
                        self.failed_proxies.clear()
                        logger.info("Cleared failed proxies cache")

                    self.requests_per_proxy = 1
                    return proxy
                else:
                    self.failed_proxies.add(proxy)

            logger.warning(f"No working proxies found after testing {tested_count} proxies")
            return None

        except Exception as e:
            logger.error(f"Error getting proxy: {e}")
            return None

    async def _update_proxies(self):
        """Update proxy list from sources"""
        try:
            import aiohttp
            self.proxies = []

            async with aiohttp.ClientSession() as session:
                for source in self.proxy_sources:
                    try:
                        async with session.get(source, timeout=aiohttp.ClientTimeout(total=10)) as response:
                            if response.status == 200:
                                text = await response.text()
                                # Parse proxy list
                                for line in text.strip().split('\n'):
                                    line = line.strip()
                                    if ':' in line and len(line.split(':')) == 2:
                                        self.proxies.append(line)
                    except Exception as e:
                        logger.debug(f"Failed to fetch from {source}: {e}")

            # Remove duplicates and shuffle
            self.proxies = list(set(self.proxies))
            random.shuffle(self.proxies)
            self.last_update = datetime.now()

            logger.info(f"Updated proxy list: {len(self.proxies)} proxies available")

        except Exception as e:
            logger.error(f"Error updating proxies: {e}")

    async def _test_proxy(self, proxy: str) -> bool:
        """Test if a proxy is working - Faster with reduced timeout"""
        try:
            import aiohttp
            proxy_url = f"http://{proxy}"

            # Use faster timeout for proxy testing to avoid hanging on bad proxies
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://httpbin.org/ip",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=3)  # Reduced from 5 to 3 seconds
                ) as response:
                    return response.status == 200

        except Exception:
            return False

    async def reset_proxy_stats(self):
        """Reset proxy statistics - useful for long operations"""
        self.requests_per_proxy = 0
        self.proxy_index = 0
        self.failed_proxies.clear()
        logger.info("Reset proxy statistics for fresh start")

    def get_proxy_stats(self):
        """Get current proxy statistics"""
        return {
            'total_proxies': len(self.proxies),
            'working_proxies': len(self.working_proxies),
            'failed_proxies': len(self.failed_proxies),
            'requests_per_current_proxy': self.requests_per_proxy,
            'current_proxy_index': self.proxy_index
        }

# --- PLAYLIST SYNC SYSTEM ---
class PlaylistSyncManager:
    """Manages automatic playlist synchronization"""

    def __init__(self, api_client):
        self.api_client = api_client

    async def sync_all_playlists(self, context: ContextTypes.DEFAULT_TYPE = None):
        """Sync all saved playlists with their online versions"""
        sync_logger.info("Starting playlist sync process")

        try:
            db = load_db()
            if not db:
                sync_logger.info("No playlists to sync")
                return

            # Count only syncable playlists (those with URLs and not custom)
            syncable_playlists = [
                (pid, pdata) for pid, pdata in db.items()
                if pdata.get('url') and not pdata.get('is_custom', False)
            ]
            total_playlists = len(syncable_playlists)
            total_in_db = len(db)
            custom_playlists = total_in_db - total_playlists
            synced_count = 0
            error_count = 0
            new_songs_count = 0
            playlists_with_new_songs = []  # Store detailed info about playlists with new songs

            sync_logger.info(f"Found {total_in_db} total playlists, {total_playlists} syncable (excluding custom playlists)")

            for playlist_id, playlist_data in db.items():
                playlist_name = playlist_data.get('name', 'Unknown')
                playlist_url = playlist_data.get('url', '')
                is_custom = playlist_data.get('is_custom', False)

                source = playlist_data.get('source', 'spotify')

                # Skip custom playlists without URL (created from individual tracks)
                if is_custom or not playlist_url:
                    sync_logger.info(f"Skipping custom playlist without URL: {playlist_name}")
                    continue

                sync_logger.info(f"Syncing playlist: {playlist_name}")

                try:
                    # Get current online playlist data
                    if source == 'youtube':
                        online_data = get_playlist_info(playlist_url)
                        if not online_data or 'entries' not in online_data:
                            sync_logger.warning(f"Could not fetch online data for {playlist_name}")
                            error_count += 1
                            continue
                        online_songs_raw = online_data['entries']
                        online_songs = [{'title': s.get('title', 'Unknown'), 'artist': 'YouTube', 'url': s.get('url'), 'source': 'youtube'} for s in online_songs_raw]
                    else: # spotify
                        online_data = await self.api_client.get_playlist_details(playlist_url)
                        if not online_data or 'songs' not in online_data:
                            sync_logger.warning(f"Could not fetch online data for {playlist_name}")
                            error_count += 1
                            continue
                        online_songs = online_data['songs']

                    saved_songs = playlist_data.get('songs', [])

                    # Find new songs by comparing URLs AND checking if files actually exist
                    playlist_dir = MUSIC_DIR / playlist_name
                    saved_urls = set()

                    # Only consider songs as "saved" if they exist both in JSON AND on disk
                    for song in saved_songs:
                        song_url = song.get('url', '')
                        if song_url:
                            # Check if file actually exists
                            if source == 'youtube':
                                song_title = sanitize_filename(song.get('title', 'Unknown'))
                                file_path = playlist_dir / f"{song_title}.mp3"
                            else:
                                song_title = sanitize_filename(song.get('title', 'Unknown'))
                                artist_name = sanitize_filename(song.get('artist', 'Unknown'))
                                file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

                            if file_path.exists():
                                saved_urls.add(song_url)
                            else:
                                sync_logger.info(f"File missing for '{song_title}' - will be re-downloaded")

                    new_songs = [song for song in online_songs if song.get('url', '') not in saved_urls]

                    if new_songs:
                        sync_logger.info(f"Found {len(new_songs)} new songs in {playlist_name}")

                        # Store detailed information about this playlist
                        playlists_with_new_songs.append({
                            'id': playlist_id,
                            'name': playlist_name,
                            'new_songs_count': len(new_songs),
                            'new_songs': new_songs[:3]  # Store first 3 songs for preview
                        })

                        # Download new songs and only add successful ones to database
                        settings = load_settings()
                        if settings.get('auto_download_new', False):
                            successfully_downloaded = await self._download_new_songs(new_songs, playlist_data, playlist_id)
                            # Add only successfully downloaded songs to saved data
                            playlist_data['songs'].extend(successfully_downloaded)
                            new_songs_count += len(successfully_downloaded)
                            sync_logger.info(f"Auto-downloaded and saved {len(successfully_downloaded)}/{len(new_songs)} new songs")
                        else:
                            # If auto-download is disabled, don't add new songs to database
                            # They'll be detected again on next sync or manual download
                            sync_logger.info(f"Auto-download disabled, {len(new_songs)} new songs not downloaded")
                            new_songs_count += len(new_songs)  # Still count them as "found"
                    else:
                        sync_logger.info(f"No new songs found in {playlist_name}")

                    synced_count += 1

                except Exception as e:
                    sync_logger.error(f"Error syncing {playlist_name}: {e}")
                    error_count += 1

            # Update database with synced data
            save_db(db)

            # Update last sync time
            settings = load_settings()
            settings['last_sync'] = datetime.now().isoformat()
            save_settings(settings)

            sync_logger.info(f"Sync completed: {synced_count}/{total_playlists} synced, {new_songs_count} new songs found, {error_count} errors")

            return {
                'synced': synced_count,
                'total': total_playlists,
                'new_songs': new_songs_count,
                'errors': error_count,
                'total_in_db': total_in_db,
                'custom_playlists': custom_playlists,
                'playlists_with_new_songs': playlists_with_new_songs
            }

        except Exception as e:
            sync_logger.error(f"Critical error during sync: {e}")
            return None

    async def _download_new_songs(self, new_songs: List[Dict], playlist_data: Dict, playlist_id: str):
        """Download newly found songs and return only successfully downloaded ones"""
        playlist_name = playlist_data.get('name', 'Unknown')
        playlist_dir = MUSIC_DIR / playlist_name
        playlist_dir.mkdir(exist_ok=True)
        source = playlist_data.get('source', 'spotify')

        download_logger.info(f"Auto-downloading {len(new_songs)} new songs for {playlist_name}")
        successfully_downloaded_songs = []

        for song in new_songs:
            try:
                if source == 'youtube':
                    song_title = sanitize_filename(song.get('title', 'Unknown'))
                    file_path = playlist_dir / f"{song_title}.mp3"
                else:
                    song_title = sanitize_filename(song.get('title', 'Unknown'))
                    artist_name = sanitize_filename(song.get('artist', 'Unknown'))
                    file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

                if file_path.exists():
                    successfully_downloaded_songs.append(song)  # Already exists, count as success
                    continue

                if source == 'youtube':
                    success = download_audio_ytdlp(song['url'], str(file_path.with_suffix('')))
                else:
                    success = await self.api_client.download_song(song, file_path)

                if success:
                    download_logger.info(f"Auto-downloaded: {song.get('artist', 'YouTube')} - {song.get('title', 'Unknown')}")
                    successfully_downloaded_songs.append(song)  # Only add if successful
                else:
                    download_logger.warning(f"Failed to auto-download: {song.get('artist', 'YouTube')} - {song.get('title', 'Unknown')}")
                    # Don't add to successfully_downloaded_songs

            except Exception as e:
                download_logger.error(f"Error auto-downloading {song.get('title', 'Unknown')}: {e}")
                # Don't add to successfully_downloaded_songs

        return successfully_downloaded_songs

sync_manager = None  # Will be initialized in main()
proxy_manager = ProxyManager()  # Global proxy manager

# --- ENHANCED API CLASS WITH RETRY AND PROXY SUPPORT ---
class SpotDownAPI:
    BASE_URL = "https://spotdown.app"
    # Only use real, verified backup services if/when they exist
    BACKUP_URLS = [
        "https://spotdown.app"
        # TODO: Add real backup services when discovered
        # For now, we'll rely on proxies and HTTP fallback instead of fake services
    ]
    COMMON_HEADERS = { "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36" }
    BROWSER_LAUNCH_OPTIONS = { "headless": True, "timeout": 60000 }

    def __init__(self):
        self.proxy_manager = proxy_manager
        self.current_base_url = self.BASE_URL
        self.failed_requests = 0
        self.last_reset_time = datetime.now()
        self.last_request_time = None
        self.min_request_interval = 1.0  # Minimum seconds between requests

    async def _handle_api_failure(self):
        """Handle API failures by adjusting retry strategy"""
        self.failed_requests += 1

        # Reset counter every hour to give the service a fresh chance
        if (datetime.now() - self.last_reset_time).total_seconds() > 3600:
            self.failed_requests = 0
            self.last_reset_time = datetime.now()
            download_logger.info("Resetting failure counter - giving the service a fresh chance")

        # Log failure count for monitoring
        if self.failed_requests % 5 == 0:
            download_logger.warning(f"API failure count: {self.failed_requests} - will use more aggressive retry strategies")

        # Increase minimum request interval to avoid overwhelming the server
        if self.failed_requests > 10:
            self.min_request_interval = min(3.0, 1.0 + (self.failed_requests * 0.1))
            download_logger.info(f"Increased request interval to {self.min_request_interval}s due to failures")

    def _should_use_proxy_immediately(self) -> bool:
        """Determine if we should use proxy from the first attempt"""
        return self.failed_requests > 5  # Use proxy immediately after many failures

    async def _rate_limit(self):
        """Implement rate limiting to avoid overwhelming the server"""
        if self.last_request_time:
            elapsed = (datetime.now() - self.last_request_time).total_seconds()
            if elapsed < self.min_request_interval:
                sleep_time = self.min_request_interval - elapsed
                await asyncio.sleep(sleep_time)

        self.last_request_time = datetime.now()

    async def _try_http_fallback(self, song_url: str, download_path: Path) -> bool:
        """Fallback HTTP method when browser fails"""
        try:
            import aiohttp

            timeout = aiohttp.ClientTimeout(total=60)
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept': 'application/json, text/plain, */*',
                'Content-Type': 'application/json',
                'Origin': self.current_base_url,
                'Referer': f'{self.current_base_url}/playlist'
            }

            # Try direct HTTP request
            async with aiohttp.ClientSession(timeout=timeout, headers=headers) as session:
                api_url = f"{self.current_base_url}/api/download"
                payload = {"url": song_url}

                async with session.post(api_url, json=payload, ssl=False) as response:
                    if response.status == 200:
                        content = await response.read()
                        if len(content) > 1000:
                            content_type = response.headers.get('content-type', '').lower()
                            if 'audio' in content_type or 'octet-stream' in content_type or len(content) > 100000:
                                with open(download_path, 'wb') as f:
                                    f.write(content)
                                download_logger.info(f"HTTP fallback successful (Size: {len(content)} bytes)")
                                return True

                    download_logger.warning(f"HTTP fallback failed (Status: {response.status})")
                    return False

        except Exception as e:
            download_logger.warning(f"HTTP fallback method failed: {e}")
            return False

    async def get_track_details(self, track_url: str):
        """Get details for a single Spotify track"""
        # Ensure it's a track URL, not playlist
        if "open.spotify.com/track/" not in track_url:
            return None

        api_url = f"{self.BASE_URL}/api/song-details?url={quote(track_url)}"

        try:
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(**self.BROWSER_LAUNCH_OPTIONS)
                except Exception as e:
                    download_logger.error(f"Failed to start Playwright: {e}")
                    return None

                page = await browser.new_page()
                await page.set_extra_http_headers(self.COMMON_HEADERS)

                try:
                    response = await page.goto(api_url, wait_until='networkidle', timeout=30000)

                    if response and response.status == 200:
                        content = await page.content()
                        if "songs" in content:
                            json_data = await response.json()
                            await browser.close()

                            # For single tracks, we expect just one song in the response
                            if json_data and "songs" in json_data and len(json_data["songs"]) > 0:
                                track_info = json_data["songs"][0]

                                # Debug logging to see what data we're getting
                                download_logger.info(f"🔍 Track data received: {track_info}")

                                title = track_info.get("title", "Unknown")
                                artist = track_info.get("artist", "Unknown")

                                # Try alternative field names if the standard ones are empty or "Unknown"
                                if title == "Unknown" or not title:
                                    title = track_info.get("name", track_info.get("song", "Unknown"))

                                if artist == "Unknown" or not artist:
                                    artist = track_info.get("artists", track_info.get("performer", "Unknown"))

                                download_logger.info(f"🎵 Extracted - Title: '{title}', Artist: '{artist}'")

                                return {
                                    "title": title,
                                    "artist": artist,
                                    "url": track_url,
                                    "download_url": track_info.get("url", "")
                                }

                    download_logger.warning(f"❌ No song data found in API response for: {track_url}")
                    await browser.close()
                    return None

                except Exception as e:
                    await browser.close()
                    download_logger.error(f"Error getting track details: {e}")
                    return None

        except Exception as e:
            download_logger.error(f"Failed to get track details: {e}")
            return None

    async def get_track_details_advanced(self, track_url: str, tokens: dict = None):
        """Get detailed track information using Spotify's real API flow"""
        try:
            # Extract track ID from URL
            if '/track/' not in track_url:
                return None

            track_id = track_url.split('/track/')[-1].split('?')[0]
            track_uri = f"spotify:track:{track_id}"

            # If we don't have tokens, get them first
            if not tokens:
                tokens = await self._get_spotify_tokens()

            if not tokens or not tokens.get('auth_token'):
                download_logger.warning("No tokens available for advanced track details")
                return await self.get_track_details(track_url)

            import aiohttp

            # GraphQL query for track details
            payload = {
                "variables": {
                    "uri": track_uri
                },
                "operationName": "getTrack",
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": "612585ae06ba435ad26369870deaae23b5c8800a256cd8a57e08eddc25a37294"
                    }
                }
            }

            headers = {
                'Authorization': f'Bearer {tokens.get("auth_token", "")}',
                'Client-Token': tokens.get('client_token', ''),
                'Content-Type': 'application/json;charset=UTF-8',
                'Accept': 'application/json',
                'Accept-Language': 'en',
                'Origin': 'https://open.spotify.com',
                'Referer': 'https://open.spotify.com/',
                'User-Agent': self.COMMON_HEADERS['User-Agent'],
                'Sec-Fetch-Site': 'same-site',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Dest': 'empty'
            }

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    'https://api-partner.spotify.com/pathfinder/v2/query',
                    json=payload,
                    headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return self._extract_track_details_from_response(data, track_url)
                    else:
                        download_logger.warning(f"Advanced track details failed with status: {response.status}")
                        return await self.get_track_details(track_url)

        except Exception as e:
            download_logger.error(f"Error getting advanced track details: {e}")
            return await self.get_track_details(track_url)

    def _extract_track_details_from_response(self, data: dict, track_url: str):
        """Extract track details from advanced API response"""
        try:
            track_data = data.get('data', {}).get('trackUnion', {})

            if track_data.get('__typename') == 'Track':
                title = track_data.get('name', 'Unknown')

                # Extract artists
                artists = track_data.get('artists', {}).get('items', [])
                artist_names = []
                for artist in artists:
                    profile = artist.get('profile', {})
                    if 'name' in profile:
                        artist_names.append(profile['name'])

                artist = ', '.join(artist_names) if artist_names else 'Unknown'

                # Extract album info
                album = track_data.get('albumOfTrack', {})
                album_name = album.get('name', '')

                # Extract thumbnail from album cover art
                thumbnail = ''
                cover_art = album.get('coverArt', {})
                if cover_art:
                    sources = cover_art.get('sources', [])
                    if sources:
                        # Get the largest image available
                        largest_image = max(sources, key=lambda x: x.get('width', 0) * x.get('height', 0))
                        thumbnail = largest_image.get('url', '')

                # Extract additional metadata
                duration = track_data.get('duration', {}).get('totalMilliseconds', 0)
                explicit = track_data.get('contentRating', {}).get('label') == 'EXPLICIT'

                return {
                    'title': title,
                    'artist': artist,
                    'url': track_url,
                    'album': album_name,
                    'thumbnail': thumbnail,
                    'duration_ms': duration,
                    'explicit': explicit,
                    'download_url': ''  # Will be filled by the download process
                }

        except Exception as e:
            download_logger.error(f"Error parsing advanced track details: {e}")

        # Fallback to basic track info
        return {
            'title': 'Unknown',
            'artist': 'Unknown',
            'url': track_url,
            'download_url': ''
        }

    async def _get_spotify_tokens(self):
        """Get Spotify tokens by visiting the site"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(**self.BROWSER_LAUNCH_OPTIONS)
                page = await browser.new_page()

                captured_tokens = {}
                requests_seen = []

                # Simple request interceptor that doesn't block
                async def simple_intercept(route):
                    try:
                        request = route.request
                        url = request.url
                        headers = request.headers

                        # Log important requests
                        if any(endpoint in url for endpoint in ['spotify.com', 'clienttoken.spotify.com']):
                            requests_seen.append(url)
                            download_logger.info(f"🔍 Intercepted: {url}")

                            # Capture tokens from request headers (non-blocking)
                            if any(endpoint in url for endpoint in ['api-partner.spotify.com', 'spclient.wg.spotify.com']):
                                auth_header = headers.get('authorization', '')
                                client_header = headers.get('client-token', '')

                                if auth_header and 'Bearer' in auth_header:
                                    captured_tokens['auth_token'] = auth_header.replace('Bearer ', '')
                                    download_logger.info(f"🎯 Got auth token from headers!")

                                if client_header:
                                    captured_tokens['client_token'] = client_header
                                    download_logger.info(f"🎯 Got client token from headers!")

                        # Continue request without blocking
                        await route.continue_()

                    except Exception as e:
                        download_logger.warning(f"Intercept error: {e}")
                        try:
                            await route.continue_()
                        except:
                            pass

                await page.route("**/*", simple_intercept)

                # Visit Spotify main page
                download_logger.info("Playwright: Visiting Spotify main page")
                await page.goto('https://open.spotify.com/', wait_until='domcontentloaded', timeout=15000)
                await page.wait_for_timeout(1000)

                # Wait for initial load and check for tokens already captured
                await page.wait_for_timeout(2000)
                download_logger.info(f"After initial load - tokens captured: auth={bool(captured_tokens.get('auth_token'))}, client={bool(captured_tokens.get('client_token'))}")

                # If we don't have tokens yet, try to trigger API calls with navigation
                if not captured_tokens.get('auth_token'):
                    try:
                        download_logger.info("Playwright: Navigating to search to trigger API calls")
                        # Direct navigation to search page to trigger token generation
                        await page.goto('https://open.spotify.com/search/eminem', timeout=15000)
                        await page.wait_for_timeout(3000)

                        download_logger.info(f"After search navigation - tokens captured: auth={bool(captured_tokens.get('auth_token'))}, client={bool(captured_tokens.get('client_token'))}")

                    except Exception as e:
                        download_logger.warning(f"Search navigation failed: {e}")

                # If still no tokens, try one more approach
                if not captured_tokens.get('auth_token'):
                    try:
                        download_logger.info("Playwright: Trying to trigger token generation with page reload")
                        await page.reload(timeout=10000)
                        await page.wait_for_timeout(2000)

                    except Exception as e:
                        download_logger.warning(f"Page reload failed: {e}")

                # Extract tokens from page context if not captured from requests
                if not captured_tokens.get('auth_token'):
                    try:
                        download_logger.info("Playwright: Extracting tokens from page context")

                        # Try to get tokens from localStorage or window object
                        tokens_from_page = await page.evaluate("""
                            () => {
                                const result = {};

                                // Try localStorage
                                try {
                                    const stored = localStorage.getItem('spotify-token') || localStorage.getItem('accessToken');
                                    if (stored) result.from_storage = stored;
                                } catch(e) {}

                                // Try window object
                                try {
                                    if (window.Spotify && window.Spotify.token) result.from_window = window.Spotify.token;
                                    if (window.accessToken) result.from_window_direct = window.accessToken;
                                } catch(e) {}

                                // Try to find in script tags
                                try {
                                    const scripts = document.querySelectorAll('script');
                                    for (let script of scripts) {
                                        const content = script.textContent || script.innerHTML;
                                        const tokenMatch = content.match(/"accessToken":"(BQ[^"]+)"/);
                                        if (tokenMatch) {
                                            result.from_script = tokenMatch[1];
                                            break;
                                        }
                                    }
                                } catch(e) {}

                                return result;
                            }
                        """)

                        download_logger.info(f"Playwright: Tokens from page: {tokens_from_page}")

                        # Use any token found
                        if tokens_from_page.get('from_script'):
                            captured_tokens['auth_token'] = tokens_from_page['from_script']
                        elif tokens_from_page.get('from_storage'):
                            captured_tokens['auth_token'] = tokens_from_page['from_storage']
                        elif tokens_from_page.get('from_window'):
                            captured_tokens['auth_token'] = tokens_from_page['from_window']
                        elif tokens_from_page.get('from_window_direct'):
                            captured_tokens['auth_token'] = tokens_from_page['from_window_direct']

                    except Exception as e:
                        download_logger.warning(f"Playwright: Could not extract tokens from page: {e}")

                download_logger.info(f"Playwright: Captured tokens: {bool(captured_tokens.get('auth_token'))}, {bool(captured_tokens.get('client_token'))}")
                download_logger.info(f"Playwright: Requests seen: {len(requests_seen)}")

                await browser.close()
                return captured_tokens

        except Exception as e:
            download_logger.error(f"Error getting Spotify tokens: {e}")
            return {}

    async def search_spotify_tracks(self, query: str, limit: int = 10):
        """Search for tracks on Spotify using direct API call"""
        try:
            # Try direct API call with fixed tokens first
            return await self._direct_spotify_api_search(query, limit)
        except Exception as e:
            download_logger.error(f"Direct API search failed: {e}")
            # Fallback to browser simulation
            try:
                return await self._simple_spotify_search(query, limit)
            except Exception as e2:
                download_logger.error(f"Browser search also failed: {e2}")
                # Final fallback to HTML scraping
                return await self._fallback_search(query, limit)

    async def _get_public_spotify_tokens(self):
        """Get public Spotify tokens with proper browser simulation"""
        import aiohttp
        import random
        import time
        import json
        import uuid

        try:
            timeout = aiohttp.ClientTimeout(total=30)

            # Create session with cookie jar - no cookies set initially
            jar = aiohttp.CookieJar()
            async with aiohttp.ClientSession(timeout=timeout, cookie_jar=jar) as session:

                # Step 1: Visit main page exactly as browser does
                main_headers = {
                    'Host': 'open.spotify.com',
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Connection': 'keep-alive',
                    'Upgrade-Insecure-Requests': '1',
                    'Sec-Fetch-Dest': 'document',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-User': '?1',
                    'Priority': 'u=0, i'
                }

                download_logger.info("Step 1: Visiting main Spotify page to establish session")
                async with session.get('https://open.spotify.com/', headers=main_headers, allow_redirects=True) as main_response:
                    if main_response.status == 200:
                        # Wait a bit to simulate real browser behavior
                        await asyncio.sleep(1)
                        download_logger.info("Main page visited successfully, cookies established")

                        # Check if we got cookies and log them
                        cookies_count = len(session.cookie_jar)
                        download_logger.info(f"Received {cookies_count} cookies from main page")

                        # Log cookie details for debugging
                        for cookie in session.cookie_jar:
                            try:
                                domain = getattr(cookie, 'domain', 'unknown')
                                download_logger.info(f"Cookie: {cookie.key}={cookie.value[:50]}... (domain: {domain})")
                            except Exception as e:
                                download_logger.info(f"Cookie: {cookie.key}={str(cookie.value)[:50]}...")
                    else:
                        download_logger.error(f"Main page visit failed: {main_response.status}")
                        return None

                # Step 1.5: Visit consent page to get more cookies
                download_logger.info("Step 1.5: Visiting consent page")
                consent_headers = main_headers.copy()
                consent_headers['Referer'] = 'https://open.spotify.com/'

                async with session.get('https://open.spotify.com/?consent=true', headers=consent_headers) as consent_response:
                    if consent_response.status == 200:
                        download_logger.info("Consent page visited successfully")
                        await asyncio.sleep(0.5)
                    else:
                        download_logger.warning(f"Consent page visit failed: {consent_response.status}")

                # Step 2: OPTIONS request to clienttoken (CORS preflight)
                options_headers = {
                    'Host': 'clienttoken.spotify.com',
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Access-Control-Request-Method': 'POST',
                    'Access-Control-Request-Headers': 'content-type',
                    'Origin': 'https://open.spotify.com',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-site',
                    'Dnt': '1',
                    'Sec-Gpc': '1',
                    'Priority': 'u=4',
                    'Te': 'trailers'
                }

                download_logger.info("Step 2: Sending OPTIONS preflight request")
                async with session.options('https://clienttoken.spotify.com/v1/clienttoken', headers=options_headers) as options_response:
                    download_logger.info(f"OPTIONS response status: {options_response.status}")

                # Step 3: Get client token with exact payload from your trace
                client_token_headers = {
                    'Host': 'clienttoken.spotify.com',
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0',
                    'Accept': 'application/json',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Content-Type': 'application/json',
                    'Origin': 'https://open.spotify.com',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'same-site',
                    'Dnt': '1',
                    'Sec-Gpc': '1',
                    'Priority': 'u=4',
                    'Te': 'trailers'
                }

                # Use the exact payload structure from your trace but with dynamic device_id
                device_id = str(uuid.uuid4()).replace('-', '')[:24]  # Generate realistic device ID
                client_token_payload = {
                    "client_data": {
                        "client_version": "1.2.73.375.gf083a0b6",
                        "client_id": "d8a5ed958d274c2e8ee717e6a4b0971d",
                        "js_sdk_data": {
                            "device_brand": "unknown",
                            "device_model": "unknown",
                            "os": "linux",
                            "os_version": "unknown",
                            "device_id": device_id,
                            "device_type": "computer"
                        }
                    }
                }

                download_logger.info("Step 3: Requesting client token")
                async with session.post('https://clienttoken.spotify.com/v1/clienttoken', headers=client_token_headers, json=client_token_payload) as response:
                    download_logger.info(f"Token request status: {response.status}")

                    if response.status == 200:
                        try:
                            client_data = await response.json()
                            download_logger.info(f"Client token response: {json.dumps(client_data, indent=2)}")

                            client_token = client_data.get('granted_token', {}).get('token')

                            if not client_token:
                                download_logger.error("No client token in response")
                                return None

                            download_logger.info("Successfully obtained client token")

                            # Step 4: Visit the main page again to establish proper session state
                            download_logger.info("Step 4: Re-visiting main page to establish session for token API")
                            await asyncio.sleep(1)

                            async with session.get('https://open.spotify.com/', headers=main_headers) as revisit_response:
                                if revisit_response.status == 200:
                                    download_logger.info("Re-visited main page successfully")
                                    await asyncio.sleep(1)
                                else:
                                    download_logger.warning(f"Re-visit failed: {revisit_response.status}")

                            # Step 5: Get authorization token using /api/token endpoint
                            import random
                            totp = random.randint(600000, 699999)
                            token_url = f"https://open.spotify.com/api/token?reason=init&productType=web-player&totp={totp}&totpServer={totp}&totpVer=46"

                            token_headers = {
                                'Host': 'open.spotify.com',
                                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0',
                                'Accept': '*/*',
                                'Accept-Language': 'en-US,en;q=0.5',
                                'Accept-Encoding': 'gzip, deflate, br',
                                'Referer': 'https://open.spotify.com/',
                                'Sentry-Trace': f'{device_id[:32]}-{device_id[32:48]}-0',
                                'Baggage': 'sentry-environment=production,sentry-release=open-server_2025-09-17_1758071143284_f083a0b,sentry-public_key=de32132fc06e4b28965ecf25332c3a25,sentry-trace_id=8089be3b178246539e77b8c1537297c5,sentry-sample_rate=0.008,sentry-sampled=false',
                                'Sec-Fetch-Dest': 'empty',
                                'Sec-Fetch-Mode': 'cors',
                                'Sec-Fetch-Site': 'same-origin',
                                'Dnt': '1',
                                'Sec-Gpc': '1',
                                'Priority': 'u=4',
                                'Te': 'trailers'
                            }

                            download_logger.info("Step 5: Getting authorization token from /api/token")
                            download_logger.info(f"Token URL: {token_url}")

                            # Log cookies that will be sent
                            cookie_count = len(session.cookie_jar)
                            download_logger.info(f"Sending {cookie_count} cookies with token request")

                            async with session.get(token_url, headers=token_headers) as token_response:
                                download_logger.info(f"Token API response status: {token_response.status}")

                                if token_response.status == 200:
                                    try:
                                        token_data = await token_response.json()
                                        download_logger.info(f"Token API response: {json.dumps(token_data, indent=2)}")

                                        auth_token = token_data.get('accessToken')
                                        if auth_token:
                                            download_logger.info("Successfully obtained authorization token from API")
                                            return {
                                                'auth_token': auth_token,
                                                'client_token': client_token
                                            }
                                        else:
                                            download_logger.warning("No accessToken in API response")
                                            return {
                                                'client_token': client_token
                                            }

                                    except json.JSONDecodeError as e:
                                        download_logger.error(f"Failed to parse token API response as JSON: {e}")
                                        response_text = await token_response.text()
                                        download_logger.error(f"Raw token API response: {response_text}")
                                        return {
                                            'client_token': client_token
                                        }
                                else:
                                    download_logger.error(f"Token API request failed: {token_response.status}")
                                    response_text = await token_response.text()
                                    download_logger.error(f"Token API error response: {response_text}")
                                    return {
                                        'client_token': client_token
                                    }

                        except json.JSONDecodeError as e:
                            download_logger.error(f"Failed to parse client token response as JSON: {e}")
                            response_text = await response.text()
                            download_logger.error(f"Raw client token response: {response_text}")
                            return None
                    else:
                        download_logger.error(f"Failed to get client token: {response.status}")
                        response_text = await response.text()
                        download_logger.error(f"Client token error response: {response_text}")
                        return None

        except Exception as e:
            download_logger.error(f"Error getting public tokens: {e}")
            import traceback
            download_logger.error(f"Full traceback: {traceback.format_exc()}")
            return None

    async def _direct_spotify_api_search(self, query: str, limit: int):
        """Direct API call to Spotify using public tokens"""
        import aiohttp

        # Try Playwright method first (most reliable)
        try:
            tokens = await self._get_spotify_tokens()
            if tokens and tokens.get('auth_token') and tokens.get('client_token'):
                auth_token = tokens['auth_token']
                client_token = tokens['client_token']
                download_logger.info("Using Playwright tokens for GraphQL API")
            else:
                raise Exception("Playwright method failed")
        except Exception as e:
            download_logger.warning(f"Playwright method failed: {e}, trying aiohttp method")

            # Fallback to aiohttp method
            tokens = await self._get_public_spotify_tokens()
            if tokens and tokens.get('auth_token') and tokens.get('client_token'):
                auth_token = tokens['auth_token']
                client_token = tokens['client_token']
                download_logger.info("Using aiohttp tokens for GraphQL API")
            else:
                download_logger.warning("Both methods failed, using hardcoded fallback")
                # Final fallback to your working example tokens
                auth_token = "BQBTosS3THqhAWB2SSu07-Cu7D-FWi2yNtDbOkf6atpI02UMnAMTjb4YkymmYSw5J6CJcbqvHPy393ED7q-XjweDfR8xnS4bPWv_0kG_ecsiEiBYWHDwwH53AhErQcrFRPLrUYAmrE4"
                client_token = "AAA+jiLlwP6qxwt9cF3VGbaBjekNty6mPqC7YjsZD2wjLYIcU3UwjNIZDVohYjrCYJecUA43D259PxgYPC1tlrIAAFpCpcH+8VssxAIGgiUhFIBUUvl7gvLT9TtcHcZ+G73fsN8ehgQLwPYSrj33pn7TWreySAL6e7nfSDVKnr7UOsqAlUoM+8piqY1PPb/OeN1A9Sc+xKnanhocTYRfEDYWXofcev6VjPY9bA2/XnRbUll4/Cc5OI9vSpCekmg6ciygpzWWi2/A130WrJtt4dqwYiOVnCmYa3Uu4dl66LoEmMkksk4OyiAJtVL48U7sDDtKtQonckCuH645A1w5jI/YOg=="

        payload = {
            "variables": {
                "searchTerm": query,
                "offset": 0,
                "limit": limit,
                "numberOfTopResults": 5,
                "includeAudiobooks": True,
                "includeArtistHasConcertsField": False,
                "includePreReleases": True,
                "includeLocalConcertsField": False,
                "includeAuthors": False
            },
            "operationName": "searchDesktop",
            "extensions": {
                "persistedQuery": {
                    "version": 1,
                    "sha256Hash": "d9f785900f0710b31c07818d617f4f7600c1e21217e80f5b043d1e78d74e6026"
                }
            }
        }

        headers = {
            'Host': 'api-partner.spotify.com',
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0',
            'Accept': 'application/json',
            'Accept-Language': 'en',
            'Accept-Encoding': 'gzip, deflate, br',
            'Content-Type': 'application/json;charset=UTF-8',
            'App-Platform': 'WebPlayer',
            'Spotify-App-Version': '1.2.73.375.gf083a0b6',
            'Client-Token': client_token,
            'Origin': 'https://open.spotify.com',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-site',
            'Authorization': f'Bearer {auth_token}',
            'Dnt': '1',
            'Sec-Gpc': '1',
            'Priority': 'u=4',
            'Te': 'trailers'
        }

        timeout = aiohttp.ClientTimeout(total=30)
        async with aiohttp.ClientSession(timeout=timeout) as session:
            async with session.post(
                'https://api-partner.spotify.com/pathfinder/v2/query',
                json=payload,
                headers=headers
            ) as response:
                if response.status == 200:
                    data = await response.json()
                    return self._extract_tracks_from_spotify_response(data, limit)
                else:
                    download_logger.error(f"Spotify API call failed with status: {response.status}")
                    response_text = await response.text()
                    download_logger.error(f"Response: {response_text}")
                    if response.status == 401:
                        raise Exception("Spotify API tokens expired - using fallback search")
                    else:
                        raise Exception(f"API call failed with status {response.status}")

    def _extract_tracks_from_spotify_response(self, data: dict, limit: int):
        """Extract track information from Spotify API response"""
        tracks = []

        try:
            # Log the full response structure for debugging
            download_logger.debug(f"Full API response keys: {list(data.keys())}")
            if 'data' in data:
                download_logger.debug(f"Data section keys: {list(data['data'].keys())}")
                if 'searchV2' in data['data']:
                    search_data = data['data']['searchV2']
                    download_logger.debug(f"SearchV2 section keys: {list(search_data.keys())}")

                    # Log sample of each section for debugging
                    for section_name in search_data.keys():
                        section = search_data[section_name]
                        if isinstance(section, dict) and 'items' in section:
                            items = section['items']
                            download_logger.debug(f"{section_name} has {len(items)} items")
                            if items:
                                download_logger.debug(f"First item in {section_name}: {items[0].keys() if isinstance(items[0], dict) else type(items[0])}")
                else:
                    download_logger.debug("No searchV2 section found in data")
            else:
                download_logger.debug("No data section found in response")

            # Navigate to the search data
            search_data = data.get('data', {}).get('searchV2', {})

            # Log what sections we have available for debugging
            available_sections = list(search_data.keys())
            download_logger.debug(f"Available search sections: {available_sections}")

            # Try multiple approaches to find tracks

            # 1. Check tracksV2 section (most common)
            tracks_section = search_data.get('tracksV2', {})
            if tracks_section:
                tracks_items = tracks_section.get('items', [])
                download_logger.debug(f"Found {len(tracks_items)} items in tracksV2")

                for track_item in tracks_items[:limit]:
                    # Extract the actual item from the wrapper
                    item = track_item.get('item', {})
                    download_logger.debug(f"Processing tracksV2 item: {item.get('__typename', 'no_typename')}")
                    track_info = self._extract_track_from_item(item)
                    if track_info:
                        download_logger.debug(f"Successfully extracted track: {track_info['display_name']}")
                        tracks.append(track_info)
                    else:
                        download_logger.debug("Failed to extract track from item")

            # 2. Check topResults for tracks (old format)
            if len(tracks) < limit:
                top_results = search_data.get('topResults', {}).get('items', [])
                download_logger.debug(f"Found {len(top_results)} items in topResults")

                for item in top_results:
                    if len(tracks) >= limit:
                        break

                    download_logger.debug(f"Processing topResults item: {item.get('__typename', 'no_typename')}")
                    track_info = self._extract_track_from_item(item)
                    if track_info:
                        # Avoid duplicates
                        track_id = track_info.get('id', '')
                        if not any(t.get('id') == track_id for t in tracks):
                            download_logger.debug(f"Successfully extracted track from topResults: {track_info['display_name']}")
                            tracks.append(track_info)
                        else:
                            download_logger.debug(f"Skipping duplicate track: {track_info['display_name']}")
                    else:
                        download_logger.debug("Failed to extract track from topResults item")

            # 2.5. Check topResultsV2 for tracks (new format)
            if len(tracks) < limit:
                top_results_v2 = search_data.get('topResultsV2', {}).get('itemsV2', [])
                download_logger.debug(f"Found {len(top_results_v2)} items in topResultsV2")

                for result_item in top_results_v2:
                    if len(tracks) >= limit:
                        break

                    item = result_item.get('item', {})
                    download_logger.debug(f"Processing topResultsV2 item: {item.get('__typename', 'no_typename')}")
                    track_info = self._extract_track_from_item(item)
                    if track_info:
                        # Avoid duplicates
                        track_id = track_info.get('id', '')
                        if not any(t.get('id') == track_id for t in tracks):
                            download_logger.debug(f"Successfully extracted track from topResultsV2: {track_info['display_name']}")
                            tracks.append(track_info)
                        else:
                            download_logger.debug(f"Skipping duplicate track from topResultsV2: {track_info['display_name']}")
                    else:
                        download_logger.debug("Failed to extract track from topResultsV2 item")

            # 3. Check for more tracks in other sections if needed
            if len(tracks) < limit:
                download_logger.debug("Looking for additional tracks in other sections...")

                # Check if there are any other sections with tracks
                for section_name, section_data in search_data.items():
                    if len(tracks) >= limit:
                        break

                    if section_name not in ['tracksV2', 'topResults'] and isinstance(section_data, dict):
                        items = section_data.get('items', [])
                        if items:
                            download_logger.debug(f"Checking {section_name} for additional tracks, found {len(items)} items")

                            for item in items:
                                if len(tracks) >= limit:
                                    break

                                # Try to extract track info from any item
                                track_info = self._extract_track_from_item(item)
                                if track_info:
                                    # Avoid duplicates
                                    track_id = track_info.get('id', '')
                                    if not any(t.get('id') == track_id for t in tracks):
                                        download_logger.debug(f"Successfully extracted track from {section_name}: {track_info['display_name']}")
                                        tracks.append(track_info)
                                    else:
                                        download_logger.debug(f"Skipping duplicate track from {section_name}: {track_info['display_name']}")

            download_logger.debug(f"Extracted {len(tracks)} tracks total")

        except Exception as e:
            download_logger.error(f"Error parsing Spotify API response: {e}")

        return tracks[:limit]

    def _extract_track_from_item(self, item):
        """Extract track info from a generic item"""
        try:
            if item.get('__typename') == 'TrackResponseWrapper':
                track_data = item.get('data', {})
                if track_data.get('__typename') == 'Track':
                    return self._build_track_info(track_data)

        except Exception as e:
            download_logger.debug(f"Error extracting track from item: {e}")

        return None

    def _extract_track_from_single(self, album_data):
        """Extract track info from a single album"""
        try:
            album_uri = album_data.get('uri', '')
            if album_uri.startswith('spotify:album:'):
                # Convert album to track-like structure
                album_id = album_uri.split(':')[-1]
                title = album_data.get('name', 'Unknown')

                # Extract artists
                artists_data = album_data.get('artists', {}).get('items', [])
                artist_names = []
                for artist in artists_data:
                    profile = artist.get('profile', {})
                    name = profile.get('name', '')
                    if name:
                        artist_names.append(name)

                artist = ', '.join(artist_names) if artist_names else 'Unknown'

                # For singles, we'll use the album URL but treat it as track
                track_url = f"https://open.spotify.com/album/{album_id}"

                # Check playability
                playability = album_data.get('playability', {})
                is_playable = playability.get('playable', True)

                if album_id and is_playable:
                    return {
                        'id': album_id,
                        'title': title,
                        'artist': artist,
                        'album': title,  # For singles, album name = track name
                        'url': track_url,
                        'uri': album_uri,
                        'display_name': f"{artist} - {title}",
                        'is_single': True  # Flag to indicate this is from a single
                    }

        except Exception as e:
            download_logger.debug(f"Error extracting track from single: {e}")

        return None

    def _build_track_info(self, track_data):
        """Build track info from track data"""
        try:
            track_id = track_data.get('id', '')
            title = track_data.get('name', 'Unknown')

            # Extract artists
            artists_data = track_data.get('artists', {}).get('items', [])
            artist_names = []
            for artist in artists_data:
                profile = artist.get('profile', {})
                name = profile.get('name', '')
                if name:
                    artist_names.append(name)

            artist = ', '.join(artist_names) if artist_names else 'Unknown'

            # Extract album info
            album_data = track_data.get('albumOfTrack', {})
            album_name = album_data.get('name', '') if album_data else ''

            # Build track URL from ID
            track_url = f"https://open.spotify.com/track/{track_id}"

            # Check if track is playable
            playability = track_data.get('playability', {})
            is_playable = playability.get('playable', True)

            if track_id and is_playable:
                track_info = {
                    'id': track_id,
                    'title': title,
                    'artist': artist,
                    'album': album_name,
                    'url': track_url,
                    'uri': track_data.get('uri', f'spotify:track:{track_id}'),
                    'display_name': f"{artist} - {title}"
                }

                if album_name:
                    track_info['display_name'] += f" ({album_name})"

                return track_info

        except Exception as e:
            download_logger.debug(f"Error building track info: {e}")

        return None

    async def _simple_spotify_search(self, query: str, limit: int):
        """Simplified Spotify search using direct browser automation"""
        async with async_playwright() as p:
            browser = await p.chromium.launch(**self.BROWSER_LAUNCH_OPTIONS)
            context = await browser.new_context()
            page = await context.new_page()

            try:
                # Set realistic headers
                await page.set_extra_http_headers({
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
                    'Accept-Language': 'en-US,en;q=0.9',
                    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                    'Sec-Fetch-Site': 'none',
                    'Sec-Fetch-Mode': 'navigate',
                    'Sec-Fetch-User': '?1',
                    'Sec-Fetch-Dest': 'document'
                })

                # Navigate to Spotify search page
                search_url = f"https://open.spotify.com/search/{quote(query)}"
                await page.goto(search_url, wait_until='networkidle', timeout=30000)

                # Wait for the page to load completely
                await page.wait_for_timeout(5000)

                # Try to trigger search by clicking tracks if available
                try:
                    await page.click('[data-testid="search-tracks-nav-item"]', timeout=3000)
                    await page.wait_for_timeout(2000)
                except:
                    # If tracks tab doesn't exist, try other selectors
                    try:
                        await page.click('button[role="tab"]:has-text("Songs")', timeout=2000)
                        await page.wait_for_timeout(2000)
                    except:
                        pass

                # Extract track information from the page
                tracks = []

                # Multiple selectors to try for track elements
                selectors_to_try = [
                    '[data-testid="tracklist-row"]',
                    '[data-testid="track-item"]',
                    'div[role="row"]',
                    '.track-item',
                    'article[data-testid]'
                ]

                track_elements = []
                for selector in selectors_to_try:
                    try:
                        elements = await page.query_selector_all(selector)
                        if elements:
                            track_elements = elements
                            break
                    except:
                        continue

                # If no specific track elements found, try to find links to tracks
                if not track_elements:
                    track_elements = await page.query_selector_all('a[href*="/track/"]')

                for element in track_elements[:limit]:
                    try:
                        # Try different methods to extract track info
                        track_info = await self._extract_track_from_element(page, element)
                        if track_info:
                            tracks.append(track_info)

                    except Exception as e:
                        download_logger.debug(f"Error extracting track from element: {e}")
                        continue

                await browser.close()
                return tracks

            except Exception as e:
                await browser.close()
                raise e

    async def _extract_track_from_element(self, page, element):
        """Extract track information from a DOM element"""
        try:
            # Try to get track URL first
            track_url = None

            # Method 1: Check if element itself is a link
            href = await element.get_attribute('href')
            if href and '/track/' in href:
                track_url = f"https://open.spotify.com{href}" if href.startswith('/') else href

            # Method 2: Look for track link within element
            if not track_url:
                link = await element.query_selector('a[href*="/track/"]')
                if link:
                    href = await link.get_attribute('href')
                    track_url = f"https://open.spotify.com{href}" if href.startswith('/') else href

            if not track_url:
                return None

            # Extract title and artist text
            title = "Unknown"
            artist = "Unknown"

            # Try different selectors for title
            title_selectors = [
                '[data-testid="internal-track-link"]',
                'a[href*="/track/"]',
                '.track-name',
                'h3',
                'h4',
                '[role="button"]'
            ]

            for selector in title_selectors:
                try:
                    title_element = await element.query_selector(selector)
                    if title_element:
                        title_text = await title_element.inner_text()
                        if title_text and title_text.strip():
                            title = title_text.strip()
                            break
                except:
                    continue

            # Try different selectors for artist
            artist_selectors = [
                'a[href*="/artist/"]',
                '.artist-name',
                'span:has-text("•")',
                'div:has(a[href*="/artist/"])',
                'p'
            ]

            artists = []
            for selector in artist_selectors:
                try:
                    artist_elements = await element.query_selector_all(selector)
                    for artist_el in artist_elements:
                        artist_text = await artist_el.inner_text()
                        if artist_text and artist_text.strip() and artist_text != title:
                            artists.append(artist_text.strip())
                except:
                    continue

            if artists:
                artist = ', '.join(list(dict.fromkeys(artists))[:3])  # Remove duplicates, max 3

            # Clean up title and artist
            title = title.replace('\n', ' ').strip()
            artist = artist.replace('\n', ' ').strip()

            if title and title != "Unknown" and artist and artist != "Unknown":
                return {
                    'title': title,
                    'artist': artist,
                    'url': track_url,
                    'display_name': f"{artist} - {title}"
                }

        except Exception as e:
            download_logger.debug(f"Error in _extract_track_from_element: {e}")

        return None

    async def _make_spotify_search_api_call(self, query: str, limit: int, tokens: dict):
        """Make the actual GraphQL API call to search Spotify"""
        try:
            import aiohttp

            # GraphQL query payload
            payload = {
                "variables": {
                    "searchTerm": query,
                    "offset": 0,
                    "limit": limit,
                    "numberOfTopResults": 5,
                    "includeAudiobooks": True,
                    "includeArtistHasConcertsField": False,
                    "includePreReleases": True,
                    "includeLocalConcertsField": False,
                    "includeAuthors": False
                },
                "operationName": "searchDesktop",
                "extensions": {
                    "persistedQuery": {
                        "version": 1,
                        "sha256Hash": "d9f785900f0710b31c07818d617f4f7600c1e21217e80f5b043d1e78d74e6026"
                    }
                }
            }

            headers = {
                'Authorization': f'Bearer {tokens.get("auth_token", "")}',
                'Client-Token': tokens.get('client_token', ''),
                'Content-Type': 'application/json;charset=UTF-8',
                'Accept': 'application/json',
                'Accept-Language': 'en',
                'Origin': 'https://open.spotify.com',
                'Referer': 'https://open.spotify.com/',
                'User-Agent': self.COMMON_HEADERS['User-Agent'],
                'Sec-Fetch-Site': 'same-site',
                'Sec-Fetch-Mode': 'cors',
                'Sec-Fetch-Dest': 'empty'
            }

            timeout = aiohttp.ClientTimeout(total=30)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    'https://api-partner.spotify.com/pathfinder/v2/query',
                    json=payload,
                    headers=headers
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        return self._extract_tracks_from_search_response(data, limit)
                    else:
                        download_logger.error(f"Spotify API search failed with status: {response.status}")
                        return await self._fallback_search(query, limit)

        except Exception as e:
            download_logger.error(f"Error making Spotify API call: {e}")
            return await self._fallback_search(query, limit)

    def _extract_tracks_from_search_response(self, data: dict, limit: int):
        """Extract track information from Spotify API response"""
        tracks = []
        try:
            # Navigate through the JSON structure
            search_data = data.get('data', {}).get('searchV2', {})

            # Look for tracks in different sections
            tracks_data = []

            # Check tracksV2 section
            if 'tracksV2' in search_data:
                tracks_section = search_data['tracksV2'].get('items', [])
                for item in tracks_section:
                    if item.get('__typename') == 'TrackResponseWrapper':
                        track_data = item.get('data', {})
                        if track_data.get('__typename') == 'Track':
                            tracks_data.append(track_data)

            # Check topResults section as well
            if 'topResults' in search_data:
                top_results = search_data['topResults'].get('items', [])
                for item in top_results:
                    if item.get('__typename') == 'TrackResponseWrapper':
                        track_data = item.get('data', {})
                        if track_data.get('__typename') == 'Track':
                            tracks_data.append(track_data)

            # Extract track information
            for track_data in tracks_data[:limit]:
                try:
                    title = track_data.get('name', 'Unknown')

                    # Extract artist names
                    artists = track_data.get('artists', {}).get('items', [])
                    artist_names = []
                    for artist in artists:
                        profile = artist.get('profile', {})
                        if 'name' in profile:
                            artist_names.append(profile['name'])

                    artist = ', '.join(artist_names) if artist_names else 'Unknown'

                    # Extract track URI and convert to URL
                    track_uri = track_data.get('uri', '')
                    if track_uri.startswith('spotify:track:'):
                        track_id = track_uri.split(':')[-1]
                        track_url = f"https://open.spotify.com/track/{track_id}"

                        tracks.append({
                            'title': title,
                            'artist': artist,
                            'url': track_url,
                            'uri': track_uri,
                            'display_name': f"{artist} - {title}"
                        })

                except Exception as e:
                    download_logger.debug(f"Error extracting track data: {e}")
                    continue

        except Exception as e:
            download_logger.error(f"Error parsing search response: {e}")

        return tracks

    async def _fallback_search(self, query: str, limit: int):
        """Fallback search method using HTML parsing"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(**self.BROWSER_LAUNCH_OPTIONS)
                page = await browser.new_page()
                await page.set_extra_http_headers(self.COMMON_HEADERS)

                # Navigate to search page
                search_url = f"https://open.spotify.com/search/{quote(query)}/tracks"
                await page.goto(search_url, wait_until='networkidle', timeout=30000)
                await page.wait_for_timeout(3000)

                tracks = []
                try:
                    # Try to extract tracks from the DOM
                    track_elements = await page.query_selector_all('[data-testid="tracklist-row"]')

                    for element in track_elements[:limit]:
                        try:
                            # Extract track title
                            title_element = await element.query_selector('[data-testid="internal-track-link"]')
                            title = await title_element.inner_text() if title_element else "Unknown"

                            # Extract artist
                            artist_elements = await element.query_selector_all('a[href*="/artist/"]')
                            artists = []
                            for artist_el in artist_elements:
                                artist_name = await artist_el.inner_text()
                                if artist_name:
                                    artists.append(artist_name)

                            artist = ', '.join(artists) if artists else "Unknown"

                            # Extract track URL
                            link_element = await element.query_selector('[data-testid="internal-track-link"]')
                            track_href = await link_element.get_attribute('href') if link_element else None

                            if track_href and '/track/' in track_href:
                                if track_href.startswith('/'):
                                    track_url = f"https://open.spotify.com{track_href}"
                                else:
                                    track_url = track_href

                                tracks.append({
                                    'title': title.strip(),
                                    'artist': artist.strip(),
                                    'url': track_url,
                                    'display_name': f"{artist.strip()} - {title.strip()}"
                                })

                        except Exception as e:
                            download_logger.debug(f"Error extracting track info in fallback: {e}")
                            continue

                except Exception as e:
                    download_logger.error(f"Error in fallback search DOM parsing: {e}")

                await browser.close()
                return tracks

        except Exception as e:
            download_logger.error(f"Fallback search failed: {e}")
            return []

    async def get_playlist_details(self, playlist_url: str):
        # First try with spotdown.app
        api_url = f"{self.BASE_URL}/api/song-details?url={quote(playlist_url)}"

        try:
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(**self.BROWSER_LAUNCH_OPTIONS)
                except Exception as e:
                    download_logger.error(f"Failed to start Playwright: {e}")
                    return None

                # Create browser context with proper headers
                browser_headers = {
                    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36",
                    "Accept-Language": "en-GB,en;q=0.9",
                    "Sec-Ch-Ua": '"Not=A?Brand";v="24", "Chromium";v="140"',
                    "Sec-Ch-Ua-Platform": '"Windows"',
                    "Sec-Ch-Ua-Mobile": "?0",
                }

                context = await browser.new_context(extra_http_headers=browser_headers)
                page = await context.new_page()
                try:
                    # First visit the main page to establish session
                    await page.goto(f"{self.BASE_URL}/playlist", wait_until="domcontentloaded", timeout=30000)

                    # Wait a moment for JS to load
                    await page.wait_for_timeout(1000)

                    # Make API request with proper headers
                    headers = {
                        "Accept": "application/json, text/plain, */*",
                        "Referer": "https://spotdown.app/playlist",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Dest": "empty"
                    }

                    response = await page.request.get(api_url, headers=headers, timeout=30000)
                    if response.ok:
                        result = await response.json()
                        download_logger.info("✅ Successfully got playlist details from spotdown.app")
                        return result
                    else:
                        download_logger.warning(f"API request failed (Status: {response.status})")
                        return None
                except Exception as e:
                    download_logger.error(f"Request exception: {e}")
                    return None
                finally:
                    await browser.close()
        except Exception as e:
            download_logger.error(f"General request error: {e}")

        download_logger.error(f"Failed to get playlist details after {MAX_API_ATTEMPTS} attempts")
        return None

    async def get_song_details(self, song_url: str):
        """Get song details using the proper API flow"""
        # Use the same flow as playlist but for individual songs
        api_url = f"{self.BASE_URL}/api/song-details?url={quote(song_url)}"

        try:
            async with async_playwright() as p:
                try:
                    browser = await p.chromium.launch(**self.BROWSER_LAUNCH_OPTIONS)
                except Exception as e:
                    download_logger.error(f"Failed to start Playwright for song details: {e}")
                    return None

                # Create browser context with proper headers
                browser_headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0",
                    "Accept-Language": "en-US,en;q=0.5",
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "DNT": "1",
                    "Sec-GPC": "1",
                }

                context = await browser.new_context(
                    extra_http_headers=browser_headers,
                    ignore_https_errors=True
                )
                page = await context.new_page()

                try:
                    # First visit the main page to establish session
                    await page.goto(f"{self.BASE_URL}/", wait_until="domcontentloaded", timeout=30000)

                    # Wait for session establishment
                    await page.wait_for_timeout(2000)

                    # Make API request with proper headers matching browser
                    headers = {
                        "Accept": "application/json, text/plain, */*",
                        "Referer": "https://spotdown.app/",
                        "Sec-Fetch-Site": "same-origin",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Dest": "empty"
                    }

                    response = await page.request.get(api_url, headers=headers, timeout=30000)
                    if response.ok:
                        result = await response.json()
                        download_logger.info("✅ Successfully got song details from spotdown.app")
                        return result
                    else:
                        download_logger.warning(f"Song details API request failed (Status: {response.status})")
                        return None

                except Exception as e:
                    download_logger.error(f"Song details request exception: {e}")
                    return None
                finally:
                    await browser.close()

        except Exception as e:
            download_logger.error(f"Failed to get song details: {e}")
            return None

    async def _download_with_session(self, song_url: str, download_path: Path, song_title: str) -> bool:
        """Download song using the established session (Step 2 of the flow) - with proxy fallback"""
        try:
            async with async_playwright() as p:
                browser = await p.chromium.launch(**self.BROWSER_LAUNCH_OPTIONS)

                # Enhanced browser headers matching Firefox from your capture
                browser_headers = {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64; rv:142.0) Gecko/20100101 Firefox/142.0",
                    "Accept-Language": "en-US,en;q=0.5",
                    "DNT": "1",
                    "Sec-GPC": "1",
                }

                context = await browser.new_context(
                    extra_http_headers=browser_headers,
                    ignore_https_errors=True,
                    java_script_enabled=True,
                    bypass_csp=True
                )
                page = await context.new_page()

                try:
                    # Visit main page to establish session
                    await page.goto(f"{self.BASE_URL}/", wait_until="domcontentloaded", timeout=30000)

                    # Simulate human behavior
                    await page.mouse.move(random.randint(100, 800), random.randint(100, 600))
                    await page.wait_for_timeout(random.randint(2000, 4000))

                    # Prepare download request headers (exactly like browser)
                    download_headers = {
                        "Accept": "application/json, text/plain, */*",
                        "Accept-Language": "en-US,en;q=0.5",
                        "Accept-Encoding": "gzip, deflate, br",
                        "Referer": "https://spotdown.app/",
                        "Content-Type": "application/json",
                        "Origin": "https://spotdown.app",
                        "Sec-Fetch-Dest": "empty",
                        "Sec-Fetch-Mode": "cors",
                        "Sec-Fetch-Site": "same-origin",
                        "DNT": "1",
                        "Sec-GPC": "1",
                        "Priority": "u=0",
                        "Te": "trailers"
                    }

                    # Make download request
                    payload = {"url": song_url}
                    api_url = f"{self.BASE_URL}/api/download"

                    download_logger.debug(f"Making download request to {api_url} with payload: {payload}")

                    response = await page.request.post(
                        api_url,
                        data=json.dumps(payload),
                        headers=download_headers,
                        timeout=120000  # 2 minutes timeout
                    )

                    if response.ok:
                        content = await response.body()
                        content_type = response.headers.get('content-type', '').lower()

                        # Check if response is JSON error message (like your 500 error example)
                        if 'application/json' in content_type:
                            try:
                                json_response = json.loads(content.decode('utf-8'))
                                if not json_response.get('success', True):
                                    error_msg = json_response.get('message', 'Unknown error')
                                    download_logger.warning(f"API returned error: {error_msg}")
                                    return False
                            except json.JSONDecodeError:
                                pass  # Not valid JSON, continue with audio validation

                        # Validate audio content
                        if len(content) > 1000:
                            if ('audio' in content_type or 'octet-stream' in content_type or
                                len(content) > 100000 or content.startswith(b'ID3')):
                                with open(download_path, 'wb') as f:
                                    f.write(content)
                                download_logger.info(f"✅ Download successful (Size: {len(content)} bytes, Type: {content_type})")
                                return True
                            else:
                                download_logger.warning(f"Invalid content type: {content_type}, size: {len(content)} bytes")
                                download_logger.debug(f"Content starts with: {content[:50]}")
                                return False
                        else:
                            download_logger.warning(f"Response too small ({len(content)} bytes)")
                            try:
                                response_text = content.decode('utf-8')[:200]
                                download_logger.debug(f"Small response content: {response_text}")
                            except:
                                pass
                            return False
                    else:
                        download_logger.warning(f"Download request failed (Status: {response.status})")
                        try:
                            response_text = await response.text()
                            download_logger.debug(f"Error response: {response_text[:500]}")
                        except:
                            pass
                        return False

                except Exception as e:
                    download_logger.error(f"Download request exception: {e}")
                    return False
                finally:
                    await browser.close()

        except Exception as e:
            download_logger.error(f"Session download failed: {e}")
            return False


    async def _try_spotify_youtube_ytdlp(self, song_url: str, song_title: str, download_path: Path) -> bool:
        """Try Spotify→YouTube→yt-dlp method"""
        youtube_videos = []
        converter_used = "None"

        # Try Tubetify first
        if TUBETIFY_AVAILABLE:
            download_logger.info(f"🔄 Trying Tubetify→yt-dlp for: {song_title}")
            try:
                youtube_videos = await spotify_to_youtube(song_url)
                if youtube_videos:
                    converter_used = "Tubetify"
                    download_logger.info(f"🎯 Tubetify found {len(youtube_videos)} YouTube match(es)")
            except Exception as e:
                download_logger.error(f"Tubetify method error: {e}")

        # Try Custom converter if Tubetify failed
        if not youtube_videos and CUSTOM_CONVERTER_AVAILABLE:
            download_logger.info(f"🔄 Trying Custom→yt-dlp for: {song_title}")
            try:
                from custom_converter import spotify_to_youtube_custom
                youtube_videos = await spotify_to_youtube_custom(song_url)
                if youtube_videos:
                    converter_used = "Custom"
                    download_logger.info(f"🎯 Custom converter found {len(youtube_videos)} YouTube match(es)")
            except Exception as e:
                download_logger.error(f"Custom converter method error: {e}")

        # If we have YouTube videos, try to download them
        if youtube_videos and YTDLP_AVAILABLE:
            download_logger.info(f"Using converter: {converter_used}")

            # Try up to 3 matches with yt-dlp
            max_attempts = min(3, len(youtube_videos))
            for i, video in enumerate(youtube_videos[:max_attempts]):
                youtube_url = video['youtube_url']
                download_logger.info(f"Trying yt-dlp attempt {i+1}: {youtube_url}")

                try:
                    # Pass the path without the extension
                    output_path_without_ext = download_path.with_suffix('')
                    success = download_audio_ytdlp(youtube_url, str(output_path_without_ext))
                    if success:
                        download_logger.info(f"✅ Successfully downloaded {song_title} using {converter_used}→yt-dlp")
                        return True
                    else:
                        download_logger.warning(f"yt-dlp attempt {i+1} failed for {youtube_url}")
                except Exception as e:
                    download_logger.warning(f"yt-dlp attempt {i+1} error: {e}")

        download_logger.warning(f"Spotify→YouTube→yt-dlp method failed for: {song_title}")
        return False


    async def _try_spotdl(self, song_url: str, song_title: str, download_path: Path) -> bool:
        """Try SpotDL method"""
        if not SPOTDL_AVAILABLE:
            download_logger.warning(f"SpotDL not available for: {song_title}")
            return False

        download_logger.info(f"🎵 Trying SpotDL for: {song_title}")
        try:
            success = await try_spotdl_fallback(song_url, download_path)
            if success:
                download_logger.info(f"✅ Successfully downloaded {song_title} using SpotDL")
                return True
            else:
                download_logger.warning(f"SpotDL method failed for: {song_title}")
                return False
        except Exception as e:
            download_logger.error(f"SpotDL method error: {e}")
            return False

    async def _try_spotdown(self, song_url: str, song_title: str, download_path: Path) -> bool:
        """Try SpotDown API method"""
        download_logger.info(f"🌐 Trying SpotDown API for: {song_title}")

        for attempt in range(MAX_API_ATTEMPTS):
            try:
                await self._rate_limit()
                download_logger.info(f"SpotDown attempt {attempt + 1}/{MAX_API_ATTEMPTS} for: {song_title}")

                song_details = await self.get_song_details(song_url)
                if not song_details:
                    download_logger.warning(f"Failed to get song details on attempt {attempt + 1}")
                    await self._handle_api_failure()
                    continue

                success = await self._download_with_session(song_url, download_path, song_title)
                if success:
                    download_logger.info(f"✅ Successfully downloaded {song_title} using SpotDown on attempt {attempt + 1}")
                    if self.failed_requests > 0:
                        self.failed_requests = max(0, self.failed_requests - 1)
                    return True

                await self._handle_api_failure()

            except Exception as e:
                download_logger.warning(f"SpotDown attempt {attempt + 1} failed: {e}")
                await self._handle_api_failure()

            if attempt < MAX_API_ATTEMPTS - 1:
                delay = RETRY_DELAY_SECONDS * (2 ** attempt) + random.uniform(1, 3)
                download_logger.info(f"Waiting {delay:.1f}s before retry...")
                await asyncio.sleep(delay)

        download_logger.warning(f"SpotDown method failed for: {song_title}")
        return False

    async def download_song(self, song_url_or_data, download_path: Path):
        """Download song using configurable priority order"""
        # Handle both string URL and dict data
        if isinstance(song_url_or_data, dict):
            song_url = song_url_or_data.get('url', '')
            song_title = song_url_or_data.get('title', 'Unknown')
        else:
            song_url = song_url_or_data
            song_title = 'Unknown'

        # Get configured priority order
        priority_order = get_download_priority()
        download_logger.info(f"🎯 Starting download for: {song_title}")
        download_logger.info(f"📋 Method priority order: {[DOWNLOAD_METHODS[m]['name'] for m in priority_order if m in DOWNLOAD_METHODS]}")

        # Try each method in priority order
        for method_id in priority_order:
            if method_id not in DOWNLOAD_METHODS:
                continue

            method_info = DOWNLOAD_METHODS[method_id]
            if not method_info['available']():
                download_logger.info(f"⏭️ Skipping {method_info['name']} - not available")
                continue

            download_logger.info(f"🔄 Trying method: {method_info['name']}")

            try:
                if method_id == 'spotify_youtube_ytdlp':
                    success = await self._try_spotify_youtube_ytdlp(song_url, song_title, download_path)
                elif method_id == 'spotdl':
                    success = await self._try_spotdl(song_url, song_title, download_path)
                elif method_id == 'spotdown':
                    success = await self._try_spotdown(song_url, song_title, download_path)
                else:
                    download_logger.warning(f"Unknown method: {method_id}")
                    continue

                if success:
                    download_logger.info(f"✅ Successfully downloaded {song_title} using {method_info['name']}")
                    return True

            except Exception as e:
                download_logger.error(f"Method {method_info['name']} failed with error: {e}")

            download_logger.info(f"❌ Method {method_info['name']} failed, trying next method...")

        download_logger.error(f"❌ Failed to download {song_title} after trying all configured methods")
        return False


api_client = SpotDownAPI()

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Add Playlist", callback_data='add_playlist_prompt')],
        [InlineKeyboardButton("🎵 Add Track", callback_data='add_track_prompt')],
        [InlineKeyboardButton("📚 My Playlists", callback_data='list_playlists_0')],
        [InlineKeyboardButton("🔍 Search Songs", callback_data='search_prompt')],
        [InlineKeyboardButton("⚙️ Settings", callback_data='show_settings'), InlineKeyboardButton("🔄 Manual Sync", callback_data='manual_sync')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('👋 Welcome! Use the menu to manage your playlists.', reply_markup=reply_markup)

async def handle_spotify_playlist_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    playlist_url = update.message.text
    if "open.spotify.com/playlist/" not in playlist_url:
        await update.message.reply_text("Invalid Spotify playlist URL.")
        return

    sent_message = await update.message.reply_text("🔍 Analyzing URL...")

    response_data = await api_client.get_playlist_details(playlist_url)
    if not response_data or "songs" not in response_data:
        await sent_message.edit_text("❌ Could not get playlist information.")
        return

    songs = response_data["songs"]
    playlist_title = response_data.get("title", f"Playlist with {len(songs)} songs")

    context.user_data['playlist_info'] = {
        'url': playlist_url,
        'suggested_name': playlist_title,
        'songs': songs
    }

    # New step: ask user for folder name
    context.user_data['state'] = 'awaiting_playlist_name'
    await sent_message.edit_text(
        f"✅ Playlist found: *{playlist_title}* ({len(songs)} songs).\n\n"
        f"Please send me the name you want for the download folder. Or press the button to use the suggested name.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Use suggested name", callback_data='use_suggested_name')]]),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_playlist_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    playlist_url = update.message.text
    if is_youtube_playlist_url(playlist_url):
        await handle_youtube_playlist_url(update, context)
    elif "open.spotify.com/playlist/" in playlist_url:
        await handle_spotify_playlist_url(update, context)
    else:
        await update.message.reply_text("Invalid playlist URL. Please send a Spotify or YouTube playlist URL.")

async def handle_youtube_playlist_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
    playlist_url = update.message.text
    sent_message = await update.message.reply_text("🔍 Analyzing YouTube playlist URL...")

    playlist_info = get_playlist_info(playlist_url)
    if not playlist_info or 'entries' not in playlist_info:
        await sent_message.edit_text("❌ Could not get YouTube playlist information.")
        return

    videos = playlist_info['entries']
    playlist_title = playlist_info.get('title', f"YouTube Playlist with {len(videos)} videos")

    context.user_data['playlist_info'] = {
        'url': playlist_url,
        'suggested_name': playlist_title,
        'songs': videos,
        'source': 'youtube'
    }

    context.user_data['state'] = 'awaiting_playlist_name'
    await sent_message.edit_text(
        f"✅ YouTube Playlist found: *{playlist_title}* ({len(videos)} videos).\n\n"
        f"Please send me the name you want for the download folder. Or press the button to use the suggested name.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("Use suggested name", callback_data='use_suggested_name')]]),
        parse_mode=ParseMode.MARKDOWN
    )

async def confirm_youtube_playlist_download_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = context.user_data['playlist_info']
    playlist_name = info['name']
    num_songs = len(info['songs'])

    keyboard = [
        [InlineKeyboardButton(f"✅ Download now", callback_data='confirm_youtube_playlist_download')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
    ]

    message_text = (
        f"A folder will be created named:\n`{playlist_name}`\n\n"
        f"{num_songs} songs will be downloaded to it. Do you confirm?"
    )

    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    context.user_data['state'] = None

async def perform_youtube_playlist_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = context.user_data.get('playlist_info')
    if not info:
        await update.callback_query.edit_message_text("Error: playlist information not found.")
        return

    videos, playlist_name, playlist_url = info['songs'], info['name'], info['url']
    
    try:
        parsed_url = urlparse(playlist_url)
        query_params = parse_qs(parsed_url.query)
        playlist_id_list = query_params.get('list', [])
        if playlist_id_list:
            playlist_id = f"youtube_{playlist_id_list[0]}"
        else:
            logger.warning(f"Could not find 'list' parameter in YouTube URL: {playlist_url}")
            playlist_id = f"youtube_fallback_{int(time.time())}"
    except Exception as e:
        logger.error(f"Error parsing YouTube playlist URL '{playlist_url}': {e}")
        playlist_id = f"youtube_error_{int(time.time())}"

    playlist_dir = MUSIC_DIR / playlist_name
    playlist_dir.mkdir(exist_ok=True)

    await update.callback_query.edit_message_text(f"Starting download of YouTube playlist '{playlist_name}'... ⏳")

    total_videos, downloaded_count, failed_videos = len(videos), 0, []
    successfully_downloaded_songs = []

    for i, video in enumerate(videos):
        video_title = sanitize_filename(video.get('title', 'Unknown'))
        video_url = video.get('url', None)
        if not video_url:
            failed_videos.append(video_title)
            continue
        
        file_path = playlist_dir / f"{video_title}"

        if file_path.with_suffix('.mp3').exists():
            downloaded_count += 1
            successfully_downloaded_songs.append({'title': video_title, 'artist': 'YouTube', 'url': video_url, 'source': 'youtube'})
            continue

        await update.callback_query.edit_message_text(
            f"📥 Downloading {i+1}/{total_videos}: *{video_title}*\n"
            f"Playlist: *{playlist_name}*",
            parse_mode=ParseMode.MARKDOWN
        )

        success = download_audio_ytdlp(video_url, str(file_path))

        if success:
            downloaded_count += 1
            successfully_downloaded_songs.append({'title': video_title, 'artist': 'YouTube', 'url': video_url, 'source': 'youtube'})
        else:
            failed_videos.append(video_title)

    db = load_db()
    db[playlist_id] = {
        'name': playlist_name,
        'url': playlist_url,
        'songs': successfully_downloaded_songs,
        'path': str(playlist_dir),
        'source': 'youtube'
    }
    save_db(db)

    final_message = f"✅ Download completed for '{playlist_name}'!\n\n▪️ Successful: {downloaded_count}/{total_videos}\n"
    if failed_videos:
        final_message += f"▪️ Failed: {len(failed_videos)}\n"

    keyboard = [[InlineKeyboardButton("⬅️ Back to menu", callback_data='main_menu')]]
    await update.callback_query.edit_message_text(final_message, reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.pop('playlist_info', None)

async def handle_playlist_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    playlist_name = update.message.text
    context.user_data['playlist_info']['name'] = sanitize_filename(playlist_name)

    if context.user_data.get('playlist_info', {}).get('source') == 'youtube':
        await confirm_youtube_playlist_download_prompt(update, context)
    else:
        await confirm_download_prompt(update, context)

async def confirm_download_prompt(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = context.user_data['playlist_info']
    playlist_name = info['name']
    num_songs = len(info['songs'])

    keyboard = [
        [InlineKeyboardButton(f"✅ Download now", callback_data='confirm_download')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
    ]

    message_text = (
        f"A folder will be created named:\n`{playlist_name}`\n\n"
        f"{num_songs} songs will be downloaded to it. Do you confirm?"
    )

    # If it comes from a text message, reply. If it's a button, edit.
    if update.callback_query:
        await update.callback_query.edit_message_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(message_text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)

    context.user_data['state'] = None  # Clear state

async def perform_download(update: Update, context: ContextTypes.DEFAULT_TYPE):
    info = context.user_data.get('playlist_info')
    if not info:
        await update.callback_query.edit_message_text("Error: playlist information not found.")
        return

    songs, playlist_name, playlist_url = info['songs'], info['name'], info['url']
    playlist_id = playlist_url.split('/')[-1].split('?')[0]
    if not playlist_id:
        parts = [p for p in playlist_url.split('?')[0].split('/') if p]
        if parts:
            playlist_id = parts[-1]

    # Use custom name for folder
    playlist_dir = MUSIC_DIR / playlist_name
    playlist_dir.mkdir(exist_ok=True)

    await update.callback_query.edit_message_text(f"Starting download in '{playlist_name}'... ⏳")

    total_songs, downloaded_count, failed_songs = len(songs), 0, []
    successfully_downloaded_songs = []  # Track only successfully downloaded songs

    for i, song in enumerate(songs):
        song_title = sanitize_filename(song.get('title', 'Unknown'))
        artist_name = sanitize_filename(song.get('artist', 'Unknown'))
        file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

        if file_path.exists():
            downloaded_count += 1
            successfully_downloaded_songs.append(song)  # Add to successfully downloaded
            continue

        await update.callback_query.edit_message_text(
            f"📥 Downloading {i+1}/{total_songs}: *{song_title}*\n"
            f"Playlist: *{playlist_name}*",
            parse_mode=ParseMode.MARKDOWN
        )

        # --- RETRY LOGIC ---
        success = False
        for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
            # Use the song URL like in the original working code
            success = await api_client.download_song(song, file_path)
            if success:
                break
            else:
                if attempt < MAX_DOWNLOAD_ATTEMPTS - 1:  # Only show retry message if not the last attempt
                    await update.callback_query.edit_message_text(
                        f"⚠️ Failed to download {song_title} (Attempt {attempt + 1}/{MAX_DOWNLOAD_ATTEMPTS}). Retrying in {RETRY_DELAY_SECONDS}s...",
                    )
                    await asyncio.sleep(RETRY_DELAY_SECONDS)
                else:
                    await update.callback_query.edit_message_text(
                        f"❌ Failed to download {song_title} after {MAX_DOWNLOAD_ATTEMPTS} attempts.",
                    )

        if success:
            downloaded_count += 1
            successfully_downloaded_songs.append(song)  # Only add if successful
            download_logger.info(f"Successfully downloaded and saved to DB: {song_title}")
        else:
            failed_songs.append(song_title)
            download_logger.warning(f"Failed to download, NOT saving to DB: {song_title}")

    # Only save successfully downloaded songs to database
    db = load_db()
    db[playlist_id] = {
        'name': playlist_name,
        'url': playlist_url,
        'songs': successfully_downloaded_songs,  # Only successful downloads
        'path': str(playlist_dir)
    }
    save_db(db)

    download_logger.info(f"Saved {len(successfully_downloaded_songs)}/{total_songs} songs to database for '{playlist_name}'")

    final_message = f"✅ Download completed for '{playlist_name}'!\n\n▪️ Successful: {downloaded_count}/{total_songs}\n"
    if failed_songs:
        final_message += f"▪️ Failed: {len(failed_songs)}\n"

    keyboard = [[InlineKeyboardButton("⬅️ Back to menu", callback_data='main_menu')]]
    await update.callback_query.edit_message_text(final_message, reply_markup=InlineKeyboardMarkup(keyboard))
    context.user_data.pop('playlist_info', None)

async def list_playlists(update: Update, context: ContextTypes.DEFAULT_TYPE):
    db = load_db()
    if not db:
        await update.callback_query.edit_message_text("You have no saved playlists.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Add one", callback_data='add_playlist_prompt')]]))
        return

    keyboard = []
    text = "📚 *Your Playlists:*\n\n"

    for i, (pl_id, data) in enumerate(db.items(), 1):
        if not pl_id or pl_id.isspace():
            logger.warning(f"Skipping playlist with invalid ID: '{pl_id}'")
            continue
        playlist_name = data.get('name', 'Unknown')
        song_count = len(data.get('songs', []))
        playlist_url = data.get('url', '')
        is_custom = data.get('is_custom', False)

        # Add playlist header with number for clarity - add indicator for custom playlists
        source = data.get('source', 'spotify')
        source_icon = '📺' if source == 'youtube' else '🎵'
        if is_custom:
            text += f"**{i}.** 📁 `{playlist_name}` ({song_count} songs) 🏷️ *Custom*\n"
        else:
            text += f"**{i}.** {source_icon} `{playlist_name}` ({song_count} songs)\n"

        # Create first row with primary actions - exclude update for custom playlists
        primary_row = [
            InlineKeyboardButton(f"{i}. 📋 Songs", callback_data=f"list_songs_{pl_id}")
        ]

        # Only add update button for syncable playlists
        if not is_custom and playlist_url:
            primary_row.insert(0, InlineKeyboardButton(f"{i}. 🔄 Update", callback_data=f"update_{pl_id}"))

        keyboard.append(primary_row)

        # Create second row with secondary actions
        secondary_row = [
            InlineKeyboardButton(f"{i}. 🔍 Check", callback_data=f"check_integrity_{pl_id}"),
            InlineKeyboardButton(f"{i}. 🗑️ Delete", callback_data=f"delete_{pl_id}")
        ]

        # Add link button if URL exists
        if playlist_url:
            secondary_row.append(InlineKeyboardButton(f"{i}. 🔗 Link", url=playlist_url))

        keyboard.append(secondary_row)

        # Add separator line after each playlist (except last)
        if i < len(db):
            text += "─────────────────\n"

    # Add global actions
    keyboard.append([InlineKeyboardButton("🔍 Check All Playlists", callback_data='check_all_integrity')])
    keyboard.append([InlineKeyboardButton("⬅️ Back to menu", callback_data='main_menu')])

    await update.callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode=ParseMode.MARKDOWN)


async def perform_update(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Update a specific playlist with new songs"""
    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    playlist_url = playlist_data.get('url', '')
    is_custom = playlist_data.get('is_custom', False)

    source = playlist_data.get('source', 'spotify')

    if is_custom or not playlist_url:
        await update.callback_query.edit_message_text("❌ Cannot sync custom playlists - they don't have a URL.")
        return

    await update.callback_query.edit_message_text(f"🔄 Updating playlist '{playlist_name}'...")

    try:
        # Get current online playlist data based on source
        if source == 'youtube':
            online_data = get_playlist_info(playlist_url)
            if not online_data or 'entries' not in online_data:
                await update.callback_query.edit_message_text("❌ Could not fetch updated YouTube playlist data.")
                return
            online_songs_raw = online_data['entries']
            online_songs = [{'title': s.get('title', 'Unknown'), 'artist': 'YouTube', 'url': s.get('url'), 'source': 'youtube'} for s in online_songs_raw]
        else:  # spotify
            online_data = await api_client.get_playlist_details(playlist_url)
            if not online_data or 'songs' not in online_data:
                await update.callback_query.edit_message_text("❌ Could not fetch updated Spotify playlist data.")
                return
            online_songs = online_data['songs']

        saved_songs = playlist_data.get('songs', [])

        # Find new songs by comparing URLs AND checking if files actually exist
        playlist_dir = MUSIC_DIR / playlist_name
        saved_urls = set()

        # Only consider songs as "saved" if they exist both in JSON AND on disk
        for song in saved_songs:
            song_url = song.get('url', '')
            if song_url:
                # Check if file actually exists
                if source == 'youtube':
                    song_title = sanitize_filename(song.get('title', 'Unknown'))
                    file_path = playlist_dir / f"{song_title}.mp3"
                else:
                    song_title = sanitize_filename(song.get('title', 'Unknown'))
                    artist_name = sanitize_filename(song.get('artist', 'Unknown'))
                    file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

                if file_path.exists():
                    saved_urls.add(song_url)

        new_songs = [song for song in online_songs if song.get('url', '') not in saved_urls]

        if new_songs:
            # Store new songs temporarily for download but DON'T save to JSON yet
            # They'll be added to JSON only after successful download
            context.user_data[f'new_songs_{playlist_id}'] = new_songs

            message = f"✅ *Playlist Update Available!*\n\n"
            message += f"*{playlist_name}*\n"
            message += f"▪️ Found {len(new_songs)} new songs\n"
            message += f"▪️ Current saved songs: {len(playlist_data['songs'])}\n\n"
            message += "New songs will only be saved to database after successful download."

            # Ask if user wants to download new songs
            keyboard = [
                [InlineKeyboardButton("📥 Download New Songs", callback_data=f'download_new_{playlist_id}')],
                [InlineKeyboardButton("⬅️ Back to Playlists", callback_data='list_playlists_0')]
            ]

            await update.callback_query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

        else:
            message = f"✅ *Playlist Up to Date*\n\n"
            message += f"*{playlist_name}*\n"
            message += f"▪️ No new songs found\n"
            message += f"▪️ Total songs: {len(saved_songs)}"

            keyboard = [[InlineKeyboardButton("⬅️ Back to Playlists", callback_data='list_playlists_0')]]

            await update.callback_query.edit_message_text(
                message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    except Exception as e:
        logger.error(f"Error updating playlist {playlist_name}: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error updating playlist: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]])
        )

async def download_new_songs(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Download only the new songs found during update"""
    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    playlist_url = playlist_data.get('url', '')

    # Use the new songs stored from the update process
    new_songs = context.user_data.get(f'new_songs_{playlist_id}', [])

    source = playlist_data.get('source', 'spotify')

    try:
        if not new_songs:
            await update.callback_query.edit_message_text(
                "ℹ️ No new songs to download.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]])
            )
            return

        # Set up download directory
        playlist_dir = MUSIC_DIR / playlist_name
        playlist_dir.mkdir(exist_ok=True)

        await update.callback_query.edit_message_text(f"📥 Downloading {len(new_songs)} new songs...")

        downloaded_count = 0
        failed_songs = []
        successfully_downloaded_songs = []

        for i, song in enumerate(new_songs):
            if source == 'youtube':
                song_title = sanitize_filename(song.get('title', 'Unknown'))
                file_path = playlist_dir / f"{song_title}.mp3"
            else:
                song_title = sanitize_filename(song.get('title', 'Unknown'))
                artist_name = sanitize_filename(song.get('artist', 'Unknown'))
                file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

            if file_path.exists():
                downloaded_count += 1
                successfully_downloaded_songs.append(song)  # Already exists, count as success
                continue

            await update.callback_query.edit_message_text(
                f"📥 Downloading {i+1}/{len(new_songs)}: *{song_title}*\n"
                f"Playlist: *{playlist_name}*",
                parse_mode=ParseMode.MARKDOWN
            )

            # Try to download with retries
            success = False
            for attempt in range(MAX_DOWNLOAD_ATTEMPTS):
                if source == 'youtube':
                    success = download_audio_ytdlp(song['url'], str(file_path.with_suffix('')))
                else:
                    success = await api_client.download_song(song, file_path)
                if success:
                    break
                if attempt < MAX_DOWNLOAD_ATTEMPTS - 1:  # Only sleep if not the last attempt
                    await asyncio.sleep(RETRY_DELAY_SECONDS)

            if success:
                downloaded_count += 1
                successfully_downloaded_songs.append(song)  # Only add if successful
                download_logger.info(f"Successfully downloaded new song: {song_title}")
            else:
                failed_songs.append(song_title)
                download_logger.warning(f"Failed to download new song: {song_title}")

        # Only add successfully downloaded songs to the database
        if successfully_downloaded_songs:
            playlist_data['songs'].extend(successfully_downloaded_songs)
            db[playlist_id] = playlist_data
            save_db(db)
            download_logger.info(f"Added {len(successfully_downloaded_songs)} new songs to database for '{playlist_name}'")

        final_message = f"✅ *New Songs Downloaded!*\n\n"
        final_message += f"*{playlist_name}*\n"
        final_message += f"▪️ Downloaded: {downloaded_count}/{len(new_songs)}\n"
        if failed_songs:
            final_message += f"▪️ Failed: {len(failed_songs)}\n"

        keyboard = [[InlineKeyboardButton("⬅️ Back to Playlists", callback_data='list_playlists_0')]]
        await update.callback_query.edit_message_text(
            final_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

        # Clean up temporary data
        context.user_data.pop(f'new_songs_{playlist_id}', None)

    except Exception as e:
        logger.error(f"Error downloading new songs for {playlist_name}: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error downloading: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]])
        )

async def perform_delete(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Delete playlist with confirmation"""
    db = load_db()
    logger.info(f"Attempting to delete playlist: '{playlist_id}'")
    logger.info(f"Available playlists in DB: {list(db.keys())}")

    if playlist_id not in db:
        logger.error(f"Playlist '{playlist_id}' not found in database")
        await update.callback_query.edit_message_text(f"❌ Playlist not found: {playlist_id}")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    song_count = len(playlist_data.get('songs', []))

    # Show confirmation message first
    message = f"🗑️ *Delete Playlist*\n\n"
    message += f"📁 **Playlist:** {escape_markdown(playlist_name)}\n"
    message += f"📊 **Songs:** {song_count}\n\n"
    message += "⚠️ This will permanently delete:\n"
    message += "• All song files from storage\n"
    message += "• The playlist folder\n"
    message += "• Database entries\n\n"
    message += "This action cannot be undone. Are you sure?"

    keyboard = [
        [InlineKeyboardButton("✅ Yes, Delete Everything", callback_data=f"confirm_delete_playlist_{playlist_id}")],
        [InlineKeyboardButton("❌ Cancel", callback_data="list_playlists")]
    ]

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def confirm_delete_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Actually delete the playlist and show success message"""
    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    song_count = len(playlist_data.get('songs', []))

    try:
        # Delete files from filesystem - use stored path or fallback
        stored_path = playlist_data.get('path')
        if stored_path:
            playlist_path = Path(stored_path)
        else:
            # Fallback to old method if no path stored
            playlist_path = MUSIC_DIR / playlist_name

        files_deleted = 0

        logger.info(f"Attempting to delete playlist folder: {playlist_path}")

        if playlist_path.exists():
            for file in playlist_path.iterdir():
                if file.is_file():
                    file.unlink()
                    files_deleted += 1
            playlist_path.rmdir()
            logger.info(f"Deleted playlist folder: {playlist_path}")
        else:
            logger.warning(f"Playlist folder not found at path: {playlist_path}")

        # Remove from database
        del db[playlist_id]
        save_db(db)

        # Show success message
        message = f"✅ *Playlist Deleted Successfully!*\n\n"
        message += f"📁 **Playlist:** {escape_markdown(playlist_name)}\n"
        message += f"🗂️ **Files deleted:** {files_deleted}\n"
        message += f"📊 **Songs removed:** {song_count}\n\n"
        message += "🗃️ Database entries cleared\n"
        message += "🗂️ Storage folder removed"

        keyboard = [
            [InlineKeyboardButton("📚 View Playlists", callback_data="list_playlists")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]

        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error deleting playlist: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error deleting playlist: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data="list_playlists")]])
        )

async def show_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show current bot settings"""
    settings = load_settings()

    # Schedule initial sync if enabled and not yet scheduled
    if settings.get('sync_enabled', False):
        await schedule_next_sync(context)

    last_sync = settings.get('last_sync')
    if last_sync:
        try:
            last_sync_dt = datetime.fromisoformat(last_sync)
            last_sync_str = last_sync_dt.strftime('%Y-%m-%d %H:%M')
        except:
            last_sync_str = "Unknown"
    else:
        last_sync_str = "Never"

    sync_status = "🟢 Enabled" if settings.get('sync_enabled', False) else "🔴 Disabled"
    sync_day = settings.get('sync_day', 'monday').title()
    sync_time = settings.get('sync_time', '09:00')
    notify_status = "🟢 Enabled" if settings.get('notify_sync_results', True) else "🔴 Disabled"

    # Download priority setting
    priority_order = get_download_priority()
    priority_display = []
    for i, method_id in enumerate(priority_order, 1):
        if method_id in DOWNLOAD_METHODS:
            method_info = DOWNLOAD_METHODS[method_id]
            status = "✅" if method_info['available']() else "❌"
            priority_display.append(f"{i}. {status} {method_info['name']}")

    priority_text = "\n".join(priority_display) if priority_display else "No methods configured"

    # Show next sync time if enabled
    next_sync_info = ""
    if settings.get('sync_enabled', False):
        next_sync_time = get_next_sync_time(settings)
        if next_sync_time:
            next_sync_str = next_sync_time.strftime('%Y-%m-%d %H:%M')
            next_sync_info = f"\n*Next Sync:* {next_sync_str}"

    text = f"""⚙️ *Bot Settings*

*Automatic Sync:* {sync_status}
*Sync Day:* {sync_day}
*Sync Time:* {sync_time}
*Sync Notifications:* {notify_status}
*Last Sync:* {last_sync_str}{next_sync_info}

*Download Priority Order:*
{priority_text}

Configure bot behavior and download preferences."""

    keyboard = [
        [InlineKeyboardButton("🔄 Toggle Auto Sync", callback_data='toggle_sync')],
        [InlineKeyboardButton("📅 Change Day", callback_data='change_sync_day')],
        [InlineKeyboardButton("⏰ Change Time", callback_data='change_sync_time')],
        [InlineKeyboardButton("🔔 Toggle Notifications", callback_data='toggle_notifications')],
        [InlineKeyboardButton("🎯 Configure Download Priority", callback_data='configure_priority')],
        [InlineKeyboardButton("⬅️ Back to Menu", callback_data='main_menu')]
    ]

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def toggle_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle automatic sync on/off"""
    settings = load_settings()
    settings['sync_enabled'] = not settings.get('sync_enabled', False)

    # Save user_id when enabling sync for notifications
    if settings['sync_enabled']:
        settings['user_id'] = update.effective_user.id

    save_settings(settings)

    # Reschedule sync based on new setting
    await schedule_next_sync(context)

    status = "enabled" if settings['sync_enabled'] else "disabled"
    await update.callback_query.answer(f"Auto sync {status}")
    await show_settings(update, context)

async def configure_priority(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show download priority configuration menu"""
    priority_order = get_download_priority()
    available_methods = get_available_methods()

    text = "🎯 *Configure Download Priority*\n\n"
    text += "Current order (methods are tried in this sequence):\n\n"

    for i, method_id in enumerate(priority_order, 1):
        if method_id in DOWNLOAD_METHODS:
            method_info = DOWNLOAD_METHODS[method_id]
            status = "✅" if method_info['available']() else "❌"
            text += f"{i}. {status} {method_info['name']}\n"

    text += "\nUse buttons below to reorder methods:"

    keyboard = []
    # Create buttons for each method to move up/down
    for i, method_id in enumerate(priority_order):
        if method_id not in DOWNLOAD_METHODS:
            continue

        method_name = DOWNLOAD_METHODS[method_id]['name']
        row = []

        # Move up button (if not first)
        if i > 0:
            row.append(InlineKeyboardButton(f"⬆️ {method_name}", callback_data=f"priority_up_{method_id}"))

        # Move down button (if not last)
        if i < len(priority_order) - 1:
            row.append(InlineKeyboardButton(f"⬇️ {method_name}", callback_data=f"priority_down_{method_id}"))

        if row:  # Only add row if it has buttons
            keyboard.append(row)

    keyboard.append([InlineKeyboardButton("↩️ Reset to Default", callback_data='priority_reset')])
    keyboard.append([InlineKeyboardButton("⬅️ Back to Settings", callback_data='show_settings')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(text, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def handle_priority_change(update: Update, context: ContextTypes.DEFAULT_TYPE, action: str, method_id: str):
    """Handle priority order changes"""
    priority_order = get_download_priority()

    if method_id not in priority_order:
        await update.callback_query.answer("❌ Method not found")
        return

    current_index = priority_order.index(method_id)

    if action == 'up' and current_index > 0:
        # Swap with previous method
        priority_order[current_index], priority_order[current_index - 1] = \
            priority_order[current_index - 1], priority_order[current_index]
        set_download_priority(priority_order)
        await update.callback_query.answer(f"✅ Moved {DOWNLOAD_METHODS[method_id]['name']} up")
    elif action == 'down' and current_index < len(priority_order) - 1:
        # Swap with next method
        priority_order[current_index], priority_order[current_index + 1] = \
            priority_order[current_index + 1], priority_order[current_index]
        set_download_priority(priority_order)
        await update.callback_query.answer(f"✅ Moved {DOWNLOAD_METHODS[method_id]['name']} down")
    elif action == 'reset':
        default_priority = ['spotify_youtube_ytdlp', 'spotdl', 'spotdown']
        set_download_priority(default_priority)
        await update.callback_query.answer("✅ Priority order reset to default")
    else:
        await update.callback_query.answer("❌ Cannot move method")
        return

    # Refresh the priority configuration menu
    await configure_priority(update, context)

async def toggle_notifications(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle sync result notifications on/off"""
    settings = load_settings()
    settings['notify_sync_results'] = not settings.get('notify_sync_results', True)
    save_settings(settings)

    status = "enabled" if settings['notify_sync_results'] else "disabled"
    await update.callback_query.answer(f"Sync notifications {status}")
    await show_settings(update, context)

async def change_sync_day(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show day selection for sync"""
    days = ['monday', 'tuesday', 'wednesday', 'thursday', 'friday', 'saturday', 'sunday']

    keyboard = []
    for day in days:
        keyboard.append([InlineKeyboardButton(day.title(), callback_data=f'set_day_{day}')])

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data='show_settings')])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.callback_query.edit_message_text(
        "📅 Select sync day:",
        reply_markup=reply_markup
    )

async def set_sync_day(update: Update, context: ContextTypes.DEFAULT_TYPE, day: str):
    """Set the sync day"""
    settings = load_settings()
    settings['sync_day'] = day
    save_settings(settings)

    # Reschedule sync with new day
    await schedule_next_sync(context)

    await update.callback_query.answer(f"Sync day set to {day.title()}")
    await show_settings(update, context)

async def change_sync_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt user to enter sync time"""
    await update.callback_query.edit_message_text(
        "⏰ Send me the sync time in HH:MM format (24-hour)\nExample: 09:30 for 9:30 AM\n\nSend /cancel to go back.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='show_settings')]])
    )
    context.user_data['state'] = 'awaiting_sync_time'

async def handle_sync_time(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle sync time input"""
    time_str = update.message.text.strip()

    # Validate time format
    try:
        time_obj = datetime.strptime(time_str, '%H:%M').time()

        settings = load_settings()
        settings['sync_time'] = time_str
        save_settings(settings)

        await update.message.reply_text(f"✅ Sync time set to {time_str}")
        context.user_data['state'] = None

        # Show settings again
        keyboard = [[InlineKeyboardButton("⚙️ Back to Settings", callback_data='show_settings')]]
        await update.message.reply_text("Settings updated!", reply_markup=InlineKeyboardMarkup(keyboard))

    except ValueError:
        await update.message.reply_text("❌ Invalid time format. Please use HH:MM (e.g., 09:30)")

async def manual_sync(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Manually trigger playlist sync"""
    await update.callback_query.edit_message_text("🔄 Starting manual sync...")

    try:
        result = await sync_manager.sync_all_playlists(context)

        if result:
            message = f"""✅ *Sync Completed*

📊 *Summary:*
▪️ Total playlists in database: {result['total_in_db']}
▪️ Syncable playlists: {result['total']}
▪️ Custom playlists (excluded): {result['custom_playlists']}
▪️ Successfully synced: {result['synced']}/{result['total']}
▪️ New songs found: {result['new_songs']}
▪️ Errors: {result['errors']}"""

            keyboard = []

            # Show playlists with new songs
            playlists_with_new = result.get('playlists_with_new_songs', [])
            if playlists_with_new:
                message += f"\n\n🎵 *Playlists with new songs:*\n"

                for playlist in playlists_with_new[:5]:  # Show first 5
                    name = playlist['name']
                    count = playlist['new_songs_count']

                    # Preview of new songs
                    preview_songs = []
                    for song in playlist['new_songs']:
                        artist = song.get('artist', 'Unknown')
                        title = song.get('title', 'Unknown')
                        preview_songs.append(f"{artist} - {title}")

                    preview_text = ", ".join(preview_songs)
                    if len(preview_text) > 60:
                        preview_text = preview_text[:57] + "..."

                    message += f"▪️ *{name}* ({count} new)\n"
                    message += f"   __{preview_text}__\n"

                    # Add resync button for this playlist
                    keyboard.append([
                        InlineKeyboardButton(
                            f"🔄 Sync {name} ({count} songs)",
                            callback_data=f"resync_playlist_{playlist['id']}"
                        )
                    ])

                if len(playlists_with_new) > 5:
                    message += f"▪️ ... and {len(playlists_with_new) - 5} more\n"

            else:
                message += f"\n\n✅ All playlists are up to date!"

        else:
            message = "❌ Sync failed. Check logs for details."

    except Exception as e:
        logger.error(f"Manual sync error: {e}")
        message = f"❌ Sync error: {str(e)}"
        keyboard = []

    keyboard.append([InlineKeyboardButton("⬅️ Back to Menu", callback_data='main_menu')])
    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def resync_individual_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Resync a specific playlist and download new songs"""
    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    playlist_url = playlist_data.get('url', '')
    is_custom = playlist_data.get('is_custom', False)

    source = playlist_data.get('source', 'spotify')

    if is_custom or not playlist_url:
        await update.callback_query.edit_message_text("❌ Cannot sync custom playlists - they don't have a URL.")
        return

    await update.callback_query.edit_message_text(f"🔄 Syncing playlist '{playlist_name}'...")

    try:
        # Get current online playlist data
        if source == 'youtube':
            online_data = get_playlist_info(playlist_url)
            if not online_data or 'entries' not in online_data:
                await update.callback_query.edit_message_text("❌ Could not fetch updated YouTube playlist data.")
                return
            online_songs_raw = online_data['entries']
            online_songs = [{'title': s.get('title', 'Unknown'), 'artist': 'YouTube', 'url': s.get('url'), 'source': 'youtube'} for s in online_songs_raw]
        else: # spotify
            online_data = await api_client.get_playlist_details(playlist_url)
            if not online_data or 'songs' not in online_data:
                await update.callback_query.edit_message_text("❌ Could not fetch updated playlist data.")
                return
            online_songs = online_data['songs']

        saved_songs = playlist_data.get('songs', [])

        # Find new songs by comparing URLs AND checking if files actually exist
        playlist_dir = MUSIC_DIR / playlist_name
        saved_urls = set()

        # Only consider songs as "saved" if they exist both in JSON AND on disk
        for song in saved_songs:
            song_url = song.get('url', '')
            if song_url:
                # Check if file actually exists
                if source == 'youtube':
                    song_title = sanitize_filename(song.get('title', 'Unknown'))
                    file_path = playlist_dir / f"{song_title}.mp3"
                else:
                    song_title = sanitize_filename(song.get('title', 'Unknown'))
                    artist_name = sanitize_filename(song.get('artist', 'Unknown'))
                    file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

                if file_path.exists():
                    saved_urls.add(song_url)
                else:
                    logger.info(f"File missing for '{song_title}' - will be re-downloaded")

        new_songs = [song for song in online_songs if song.get('url', '') not in saved_urls]

        if not new_songs:
            message = f"✅ *{playlist_name}*\n\nNo new songs found. Playlist is up to date!"
        else:
            # Download new songs
            await update.callback_query.edit_message_text(f"🔄 Downloading {len(new_songs)} new songs from '{playlist_name}'...")

            successfully_downloaded = await sync_manager._download_new_songs(new_songs, playlist_data, playlist_id)

            # Add successfully downloaded songs to database
            playlist_data['songs'].extend(successfully_downloaded)
            db[playlist_id] = playlist_data
            save_db(db)

            if successfully_downloaded:
                message = f"✅ *{playlist_name}* - Sync Complete\n\n"
                message += f"📊 *Results:*\n"
                message += f"▪️ New songs found: {len(new_songs)}\n"
                message += f"▪️ Successfully downloaded: {len(successfully_downloaded)}\n"
                message += f"▪️ Failed downloads: {len(new_songs) - len(successfully_downloaded)}\n"

                if successfully_downloaded:
                    message += f"\n🎵 *Downloaded songs:*\n"
                    for i, song in enumerate(successfully_downloaded[:5]):  # Show first 5
                        artist = song.get('artist', 'Unknown')
                        title = song.get('title', 'Unknown')
                        message += f"▪️ {artist} - {title}\n"

                    if len(successfully_downloaded) > 5:
                        message += f"▪️ ... and {len(successfully_downloaded) - 5} more\n"
            else:
                message = f"❌ *{playlist_name}*\n\nFound {len(new_songs)} new songs but failed to download any. Check logs for details."

        keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data='main_menu')]]
        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error syncing playlist {playlist_name}: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error syncing playlist: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='main_menu')]])
        )

async def perform_integrity_check(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Check integrity of a specific playlist"""
    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')

    await update.callback_query.edit_message_text(f"🔍 Checking integrity of '{playlist_name}'...")

    try:
        result = await check_playlist_integrity(playlist_id, playlist_data)

        message = f"✅ *Integrity Check: {playlist_name}*\n\n"
        message += f"📊 *Results:*\n"
        message += f"▪️ Total songs: {result['total_songs']}\n"
        message += f"▪️ Valid songs: {result['valid_songs']}\n"
        message += f"▪️ Quality issues: {len(result['corrupted_songs'])}\n"
        message += f"▪️ Missing songs: {len(result['missing_songs'])}\n"

        keyboard = []

        if result['corrupted_songs'] or result['missing_songs']:
            # Show fix option if there are issues
            keyboard.append([InlineKeyboardButton("🔧 Fix Issues", callback_data=f"fix_integrity_{playlist_id}")])

            # Show details of corrupted/missing songs
            if result['corrupted_songs']:
                message += f"\n⚠️ *Songs with quality issues:*\n"
                for i, song in enumerate(result['corrupted_songs'][:5]):  # Show first 5
                    message += f"▪️ {song['artist']} - {song['title']}\n"
                if len(result['corrupted_songs']) > 5:
                    message += f"▪️ ... and {len(result['corrupted_songs']) - 5} more\n"

            if result['missing_songs']:
                message += f"\n❌ *Missing songs:*\n"
                for i, song in enumerate(result['missing_songs'][:5]):  # Show first 5
                    message += f"▪️ {song['artist']} - {song['title']}\n"
                if len(result['missing_songs']) > 5:
                    message += f"▪️ ... and {len(result['missing_songs']) - 5} more\n"

            # Store the results for fixing
            context.user_data[f'integrity_result_{playlist_id}'] = result

        keyboard.append([InlineKeyboardButton("⬅️ Back to Playlists", callback_data='list_playlists_0')])

        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error checking integrity of {playlist_name}: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error checking integrity: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]])
        )

async def fix_integrity_issues(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Fix integrity issues by re-downloading corrupted/missing songs"""
    result_key = f'integrity_result_{playlist_id}'
    integrity_result = context.user_data.get(result_key)

    if not integrity_result:
        await update.callback_query.edit_message_text(
            "❌ No integrity results found. Please run the integrity check first.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]])
        )
        return

    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_name = db[playlist_id].get('name', 'Unknown')
    corrupted_songs = integrity_result.get('corrupted_songs', [])
    missing_songs = integrity_result.get('missing_songs', [])

    total_to_fix = len(corrupted_songs) + len(missing_songs)
    await update.callback_query.edit_message_text(f"🔧 Fixing {total_to_fix} songs in '{playlist_name}'...")

    try:
        fix_result = await fix_corrupted_songs(playlist_id, corrupted_songs, missing_songs)

        message = f"✅ *Integrity Fix Complete: {playlist_name}*\n\n"
        message += f"📊 *Results:*\n"
        message += f"▪️ Files removed: {fix_result['removed_files']}\n"
        message += f"▪️ Successfully re-downloaded: {fix_result['redownloaded']}\n"
        message += f"▪️ Failed downloads: {fix_result['failed_downloads']}\n"

        if fix_result['failed_downloads'] > 0:
            message += f"\n⚠️ Some songs failed to download. You may want to try again later."

        keyboard = [[InlineKeyboardButton("⬅️ Back to Playlists", callback_data='list_playlists_0')]]

        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

        # Clean up stored results
        context.user_data.pop(result_key, None)

    except Exception as e:
        logger.error(f"Error fixing integrity issues for {playlist_name}: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error fixing issues: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]])
        )

async def check_all_playlists_integrity(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Check integrity of all playlists"""
    db = load_db()
    if not db:
        await update.callback_query.edit_message_text(
            "No playlists found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='main_menu')]])
        )
        return

    await update.callback_query.edit_message_text("🔍 Checking integrity of all playlists...")

    total_playlists = len(db)
    total_songs = 0
    total_valid = 0
    total_corrupted = 0
    total_missing = 0
    playlists_with_issues = []

    try:
        for i, (playlist_id, playlist_data) in enumerate(db.items()):
            playlist_name = playlist_data.get('name', 'Unknown')

            await update.callback_query.edit_message_text(
                f"🔍 Checking {i+1}/{total_playlists}: {playlist_name}"
            )

            result = await check_playlist_integrity(playlist_id, playlist_data)

            total_songs += result['total_songs']
            total_valid += result['valid_songs']
            total_corrupted += len(result['corrupted_songs'])
            total_missing += len(result['missing_songs'])

            if result['corrupted_songs'] or result['missing_songs']:
                playlists_with_issues.append({
                    'id': playlist_id,
                    'name': playlist_name,
                    'corrupted': len(result['corrupted_songs']),
                    'missing': len(result['missing_songs'])
                })

        message = f"✅ *Global Integrity Check Complete*\n\n"
        message += f"📊 *Summary:*\n"
        message += f"▪️ Playlists checked: {total_playlists}\n"
        message += f"▪️ Total songs: {total_songs}\n"
        message += f"▪️ Valid songs: {total_valid}\n"
        message += f"▪️ Quality issues: {total_corrupted}\n"
        message += f"▪️ Missing songs: {total_missing}\n"

        if playlists_with_issues:
            message += f"\n⚠️ *Playlists with issues:*\n"
            for playlist in playlists_with_issues[:10]:  # Show first 10
                message += f"▪️ {playlist['name']}: {playlist['corrupted']} quality issues, {playlist['missing']} missing\n"

        keyboard = [[InlineKeyboardButton("⬅️ Back to Playlists", callback_data='list_playlists_0')]]

        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error in global integrity check: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error during integrity check: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]])
        )

async def list_playlist_songs(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str, page: int = 0):
    """List all songs in a playlist with delete buttons"""
    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    songs = playlist_data.get('songs', [])

    if not songs:
        await update.callback_query.edit_message_text(
            f"📋 *{escape_markdown(playlist_name)}*\n\nNo songs found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Pagination settings
    songs_per_page = 10
    total_pages = (len(songs) + songs_per_page - 1) // songs_per_page
    start_idx = page * songs_per_page
    end_idx = min(start_idx + songs_per_page, len(songs))

    message = f"📋 *{escape_markdown(playlist_name)}*\n"
    message += f"Page {page + 1}/{total_pages} • {len(songs)} total songs\n\n"

    keyboard = []

    for i in range(start_idx, end_idx):
        song = songs[i]
        song_title = song.get('title', 'Unknown')
        artist_name = song.get('artist', 'Unknown')
        duration = song.get('duration', '0:00')

        # Check if file exists
        playlist_dir = MUSIC_DIR / playlist_name
        file_path = playlist_dir / f"{sanitize_filename(artist_name)} - {sanitize_filename(song_title)}.mp3"
        status_icon = "✅" if file_path.exists() else "❌"

        message += f"{i+1}. {status_icon} *{escape_markdown(artist_name)}* - {escape_markdown(song_title)} ({escape_markdown(str(duration))})\n"

        # Add delete button for each song
        keyboard.append([InlineKeyboardButton(f"🗑️ Delete #{i+1}", callback_data=f"delete_song_{playlist_id}_{i}")])

    # Pagination buttons
    nav_buttons = []
    if page > 0:
        nav_buttons.append(InlineKeyboardButton("⬅️ Prev", callback_data=f"songs_page_{playlist_id}_{page-1}"))
    if page < total_pages - 1:
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"songs_page_{playlist_id}_{page+1}"))

    if nav_buttons:
        keyboard.append(nav_buttons)

    keyboard.append([InlineKeyboardButton("⬅️ Back to Playlists", callback_data='list_playlists_0')])

    # Check message length and truncate if necessary to avoid parsing errors
    if len(message.encode('utf-8')) > 4000:  # Leave some margin from Telegram's 4096 limit
        lines = message.split('\n')
        truncated_message = ""
        for line in lines:
            if len((truncated_message + line + '\n').encode('utf-8')) < 3800:
                truncated_message += line + '\n'
            else:
                break
        truncated_message += f"\n... (message truncated for length)"
        message = truncated_message

    try:
        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    except Exception as e:
        # If markdown parsing fails, try without markdown
        download_logger.warning(f"Markdown parsing failed for playlist songs, retrying without markdown: {e}")
        # Remove all markdown formatting and try again
        plain_message = message.replace('*', '').replace('_', '').replace('[', '').replace(']', '').replace('`', '').replace('\\', '')
        await update.callback_query.edit_message_text(
            plain_message,
            reply_markup=InlineKeyboardMarkup(keyboard)
        )

async def delete_song(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str, song_index: int):
    """Delete a specific song from playlist and filesystem"""
    db = load_db()
    logger.info(f"Attempting to delete song {song_index} from playlist: '{playlist_id}'")
    logger.info(f"Available playlists in DB: {list(db.keys())}")

    if playlist_id not in db:
        logger.error(f"Playlist '{playlist_id}' not found in database")
        await update.callback_query.edit_message_text(f"❌ Playlist not found: {playlist_id}")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    songs = playlist_data.get('songs', [])

    if song_index >= len(songs) or song_index < 0:
        await update.callback_query.edit_message_text("❌ Song not found.")
        return

    song = songs[song_index]
    song_title = song.get('title', 'Unknown')
    artist_name = song.get('artist', 'Unknown')

    # Show confirmation
    message = f"🗑️ *Delete Song*\n\n"
    message += f"Playlist: {escape_markdown(playlist_name)}\n"
    message += f"Song: {escape_markdown(artist_name)} - {escape_markdown(song_title)}\n\n"
    message += "This will delete both the file and remove it from the database. Are you sure?"

    keyboard = [
        [InlineKeyboardButton("✅ Yes, Delete", callback_data=f"confirm_delete_song_{playlist_id}_{song_index}")],
        [InlineKeyboardButton("❌ Cancel", callback_data=f"list_songs_{playlist_id}")]
    ]

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def confirm_delete_song(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str, song_index: int):
    """Confirm and delete a song"""
    db = load_db()
    logger.info(f"Confirming delete song {song_index} from playlist: '{playlist_id}'")
    logger.info(f"Available playlists in DB: {list(db.keys())}")

    if playlist_id not in db:
        logger.error(f"Playlist '{playlist_id}' not found in database")
        await update.callback_query.edit_message_text(f"❌ Playlist not found: {playlist_id}")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    songs = playlist_data.get('songs', [])

    if song_index >= len(songs) or song_index < 0:
        await update.callback_query.edit_message_text("❌ Song not found.")
        return

    song = songs[song_index]
    song_title = sanitize_filename(song.get('title', 'Unknown'))
    artist_name = sanitize_filename(song.get('artist', 'Unknown'))

    try:
        # Delete file from filesystem - use stored path
        playlist_path = playlist_data.get('path')
        if playlist_path:
            playlist_dir = Path(playlist_path)
        else:
            # Fallback to old method if no path stored
            playlist_dir = MUSIC_DIR / playlist_name

        file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"
        file_deleted = False

        logger.info(f"Attempting to delete file: {file_path}")

        if file_path.exists():
            file_path.unlink()
            file_deleted = True
            logger.info(f"Deleted file: {file_path}")
        else:
            logger.warning(f"File not found at path: {file_path}")

        # Remove from database
        del songs[song_index]
        playlist_data['songs'] = songs
        db[playlist_id] = playlist_data
        save_db(db)

        # Enhanced feedback message
        remaining_songs = len(songs)
        message = f"✅ *Song Deleted Successfully!*\n\n"
        message += f"🎵 **Song:** {escape_markdown(artist_name)} - {escape_markdown(song.get('title', 'Unknown'))}\n"
        message += f"📁 **Playlist:** {escape_markdown(playlist_name)}\n"
        message += f"📊 **Remaining songs:** {remaining_songs}\n\n"

        if file_deleted:
            message += "🗂️ File removed from storage\n"
            message += "🗃️ Entry removed from database"
        else:
            message += "⚠️ File not found in storage (already deleted)\n"
            message += "🗃️ Entry removed from database"

        keyboard = [
            [InlineKeyboardButton("⬅️ Back to Songs", callback_data=f"list_songs_{playlist_id}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]

        await update.callback_query.edit_message_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error deleting song: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error deleting song: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"list_songs_{playlist_id}")]])
        )

async def show_song_details(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str, song_index: int):
    """Show details of a specific song from search results"""
    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    songs = playlist_data.get('songs', [])

    if song_index >= len(songs) or song_index < 0:
        await update.callback_query.edit_message_text("❌ Song not found.")
        return

    song = songs[song_index]
    title = song.get('title', 'Unknown')
    artist = song.get('artist', 'Unknown')
    url = song.get('url', '')

    # Check if file exists
    playlist_dir = MUSIC_DIR / playlist_name
    file_path = playlist_dir / f"{sanitize_filename(artist)} - {sanitize_filename(title)}.mp3"
    file_exists = file_path.exists()

    message = f"🎵 *Song Details*\n\n"
    message += f"**Title:** {escape_markdown(title)}\n"
    message += f"**Artist:** {escape_markdown(artist)}\n"
    message += f"**Playlist:** {escape_markdown(playlist_name)}\n"
    message += f"**Status:** {'✅ Downloaded' if file_exists else '❌ Not Downloaded'}\n"

    if url:
        message += f"**URL:** [Open in Spotify]({url})\n"

    keyboard = [
        [InlineKeyboardButton("📁 View Playlist", callback_data=f"list_songs_{playlist_id}")],
        [InlineKeyboardButton("🗑️ Delete Song", callback_data=f"delete_song_{playlist_id}_{song_index}")],
        [InlineKeyboardButton("🔍 New Search", callback_data="main_menu")]
    ]

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

# --- STATE AND BUTTON HANDLERS ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    logger.info(f"Button handler received callback_data: '{data}'")

    if data == 'add_playlist_prompt':
        await query.edit_message_text("Send me a Spotify or YouTube playlist URL.")
        context.user_data['state'] = 'awaiting_url'
    elif data == 'add_track_prompt':
        keyboard = [
            [InlineKeyboardButton("🔍 Search on Spotify", callback_data='search_spotify_tracks')],
            [InlineKeyboardButton("🔗 Paste URL", callback_data='paste_track_url')],
            [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
        ]
        await query.edit_message_text(
            "🎵 *Add Individual Track*\n\n"
            "How would you like to add a track?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    elif data == 'search_spotify_tracks':
        await query.edit_message_text(
            "🔍 *Search Spotify*\n\n"
            "Send me the name of the song or artist you want to search for.\n"
            "Example: 'bohemian rhapsody queen' or 'imagine dragons thunder'",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['state'] = 'awaiting_spotify_search'
    elif data == 'paste_track_url':
        if YTDLP_AVAILABLE:                                                                                                                                       
            youtube_status = "✅ Ready to go!"                                                                                                                    
            youtube_example = "`https://www.youtube.com/watch?v=dQw4w9WgXcQ`" 
        else:
            youtube_example = "_(YouTube downloader is not configured)_"
        await query.edit_message_text(
            "🔗 *Paste a URL to begin...*\n\n"                                                                                                                    
            "I can download from:\n\n"                                                                                                                            
            "🎵 **Spotify:** Just paste a track link!\n"
            "`https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh`\n\n"
            f"📺 **YouTube:** {youtube_status}\n"                                                                                                                 
            f"{youtube_example}\n\n"                                                                                                                              
            "✨ **My Superpowers:**\n"                                                                                                                            
            "-  I'll automatically grab the song title and artist.\n"                                                                                             
            "-  You can add songs to any of your playlists.\n"                                                                                                    
            "-  I'll download the best audio quality available.\n\n"                                                                                              
            "Ready? Just send me a link!",                                                                                                                        
            parse_mode=ParseMode.MARKDOWN,                                                                                                                        
            disable_web_page_preview=True 
        )
        context.user_data['state'] = 'awaiting_track_url'
    elif data == 'search_prompt':
        await query.edit_message_text(
            "🔍 *Search Songs*\n\n"
            "Send me a song name or artist to search for.\n"
            "Example: 'bohemian rhapsody' or 'the beatles'",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['state'] = 'awaiting_search'
    elif data == 'list_playlists_0':  # Assuming simple pagination for now
        await list_playlists(update, context)
    elif data == 'list_playlists':
        await list_playlists(update, context)
    elif data == 'show_settings':
        await show_settings(update, context)
    elif data == 'toggle_sync':
        await toggle_sync(update, context)
    elif data == 'toggle_notifications':
        await toggle_notifications(update, context)
    elif data == 'configure_priority':
        await configure_priority(update, context)
    elif data.startswith('priority_up_'):
        method_id = data.replace('priority_up_', '')
        await handle_priority_change(update, context, 'up', method_id)
    elif data.startswith('priority_down_'):
        method_id = data.replace('priority_down_', '')
        await handle_priority_change(update, context, 'down', method_id)
    elif data == 'priority_reset':
        await handle_priority_change(update, context, 'reset', '')
    elif data == 'change_sync_day':
        await change_sync_day(update, context)
    elif data == 'change_sync_time':
        await change_sync_time(update, context)
    elif data.startswith('set_day_'):
        day = data.split('set_day_')[1]
        await set_sync_day(update, context, day)
    elif data == 'manual_sync':
        await manual_sync(update, context)
    elif data.startswith('resync_playlist_'):
        playlist_id = data.split('resync_playlist_')[1]
        await resync_individual_playlist(update, context, playlist_id)
    elif data == 'use_suggested_name':
        info = context.user_data['playlist_info']
        info['name'] = sanitize_filename(info['suggested_name'])
        if info.get('source') == 'youtube':
            await confirm_youtube_playlist_download_prompt(update, context)
        else:
            await confirm_download_prompt(update, context)
    elif data == 'confirm_download':
        await perform_download(update, context)
    elif data == 'confirm_youtube_playlist_download':
        await perform_youtube_playlist_download(update, context)
    elif data.startswith('update_'):
        playlist_id = data.split('update_')[1]
        await perform_update(update, context, playlist_id)
    elif data.startswith('download_new_'):
        playlist_id = data.split('download_new_')[1]
        await download_new_songs(update, context, playlist_id)
    elif data.startswith('delete_song_'):
        # Format: delete_song_{playlist_id}_{song_index}
        remaining = data.replace('delete_song_', '', 1)
        parts = remaining.rsplit('_', 1)
        playlist_id = parts[0]
        song_index = int(parts[1])
        await delete_song(update, context, playlist_id, song_index)
    elif data.startswith('delete_'):
        playlist_id = data.split('delete_')[1]
        await perform_delete(update, context, playlist_id)
    elif data.startswith('check_integrity_'):
        playlist_id = data.split('check_integrity_')[1]
        await perform_integrity_check(update, context, playlist_id)
    elif data.startswith('fix_integrity_'):
        playlist_id = data.split('fix_integrity_')[1]
        await fix_integrity_issues(update, context, playlist_id)
    elif data == 'check_all_integrity':
        await check_all_playlists_integrity(update, context)
    elif data.startswith('list_songs_'):
        playlist_id = data.split('list_songs_')[1]
        await list_playlist_songs(update, context, playlist_id)
    elif data.startswith('songs_page_'):
        # Format: songs_page_{playlist_id}_{page}
        remaining = data.replace('songs_page_', '', 1)
        parts = remaining.rsplit('_', 1)
        playlist_id = parts[0]
        page = int(parts[1])
        await list_playlist_songs(update, context, playlist_id, page)
    elif data.startswith('confirm_delete_playlist_'):
        playlist_id = data.split('confirm_delete_playlist_')[1]
        await confirm_delete_playlist(update, context, playlist_id)
    elif data.startswith('confirm_delete_song_'):
        # Format: confirm_delete_song_{playlist_id}_{song_index}
        remaining = data.replace('confirm_delete_song_', '', 1)
        parts = remaining.rsplit('_', 1)
        playlist_id = parts[0]
        song_index = int(parts[1])
        await confirm_delete_song(update, context, playlist_id, song_index)
    elif data.startswith('show_song_'):
        # Format: show_song_{playlist_id}_{song_index}
        remaining = data.replace('show_song_', '', 1)
        parts = remaining.rsplit('_', 1)
        playlist_id = parts[0]
        song_index = int(parts[1])
        await show_song_details(update, context, playlist_id, song_index)
    elif data == 'select_playlist_for_track':
        await show_playlists_for_track(update, context)
    elif data == 'create_playlist_for_track':
        await query.edit_message_text(
            "📝 *Create New Playlist*\n\nSend me the name for the new playlist:",
            parse_mode=ParseMode.MARKDOWN
        )
        context.user_data['state'] = 'awaiting_track_playlist_name'
    elif data.startswith('add_track_to_'):
        playlist_id = data.split('add_track_to_')[1]
        await add_track_to_playlist(update, context, playlist_id)
    elif data.startswith('select_spotify_track_'):
        track_index = int(data.split('select_spotify_track_')[1])
        await select_spotify_track(update, context, track_index)
    elif data == 'youtube_auto_filename':
        await perform_youtube_download(update, context)
    elif data == 'youtube_new_folder':
        await youtube_download_to_new_folder(update, context)
    elif data == 'youtube_select_playlist':
        await youtube_select_playlist(update, context)
    elif data == 'youtube_create_playlist':
        await youtube_create_playlist(update, context)
    elif data.startswith('youtube_add_to_'):
        playlist_id = data.split('youtube_add_to_')[1]
        await youtube_add_to_playlist(update, context, playlist_id)
    elif data.startswith('select_youtube_video_'):
        video_index = int(data.split('select_youtube_video_')[1])
        await select_youtube_video(update, context, video_index)
    elif data == 'auto_select_youtube':
        await auto_select_youtube_video(update, context)
    elif data == 'youtube_back_to_options':
        # Recreate the original YouTube options menu
        youtube_info = context.user_data.get('youtube_track_info')
        if youtube_info:
            video_title = youtube_info['title']
            youtube_url = youtube_info['url']
            keyboard = [
                [InlineKeyboardButton("📥 Download to new folder", callback_data='youtube_new_folder')],
                [InlineKeyboardButton("📂 Add to existing playlist", callback_data='youtube_select_playlist')],
                [InlineKeyboardButton("🆕 Create new playlist", callback_data='youtube_create_playlist')],
                [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
            ]
            await update.callback_query.edit_message_text(
                f"🎵 *YouTube Video Found*\n\n"
                f"📺 **Title:** {video_title[:80]}{'...' if len(video_title) > 80 else ''}\n"
                f"🔗 **URL:** `{youtube_url[:50]}...`\n\n"
                f"What would you like to do with this video?",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
    # ... (other handlers like update)
    elif data in ('cancel_action', 'main_menu'):
        context.user_data.clear()
        # Show main menu directly instead of calling start
        keyboard = [
            [InlineKeyboardButton("➕ Add Playlist", callback_data='add_playlist_prompt')],
            [InlineKeyboardButton("🎵 Add Track", callback_data='add_track_prompt')],
            [InlineKeyboardButton("📚 My Playlists", callback_data='list_playlists_0')],
            [InlineKeyboardButton("🔍 Search Songs", callback_data='search_prompt')],
            [InlineKeyboardButton("⚙️ Settings", callback_data='show_settings'), InlineKeyboardButton("🔄 Manual Sync", callback_data='manual_sync')]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text('👋 Welcome! Use the menu to manage your playlists.', reply_markup=reply_markup)

async def message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    state = context.user_data.get('state')
    if state == 'awaiting_url':
        await handle_playlist_url(update, context)
    elif state == 'awaiting_playlist_name':
        await handle_playlist_name(update, context)
    elif state == 'awaiting_sync_time':
        await handle_sync_time(update, context)
    elif state == 'awaiting_search':
        await handle_search_query(update, context)
    elif state == 'awaiting_track_playlist_name':
        await handle_track_playlist_name(update, context)
    elif state == 'awaiting_track_url':
        await handle_track_url(update, context)
    elif state == 'awaiting_spotify_search':
        await handle_spotify_search(update, context)
    elif state == 'awaiting_youtube_filename':
        await handle_youtube_filename(update, context)
    elif state == 'awaiting_youtube_playlist_name':
        await handle_youtube_playlist_name(update, context)

async def handle_track_url(update: Update, context: ContextTypes.DEFAULT_TYPE, track_url: str = None):
    """Handle Spotify track URL or YouTube URL for individual song download"""
    if not track_url:
        track_url = update.message.text

    # Check if it's a YouTube URL
    if is_youtube_url(track_url):
        await handle_youtube_url(update, context, track_url)
        return

    # Validate Spotify track URL
    if "open.spotify.com/track/" not in track_url:
        await update.message.reply_text("❌ Invalid URL. Please provide a Spotify track URL or YouTube URL.")
        return

    sent_message = await update.message.reply_text("🔍 Getting track information...")

    track_info = await api_client.get_track_details(track_url)
    if not track_info:
        await sent_message.edit_text("❌ Could not get track information. The track may be unavailable or region-locked.")
        return

    # Store track info in user data
    context.user_data['track_info'] = track_info
    context.user_data['track_info']['url'] = track_url

    # Check for multiple YouTube video options
    youtube_videos = []
    converter_used = "None"

    # Try Tubetify first
    if TUBETIFY_AVAILABLE:
        await sent_message.edit_text("🔍 Searching for YouTube matches (Tubetify)...")
        try:
            from tubetify_converter import spotify_to_youtube
            youtube_videos = await spotify_to_youtube(track_url)
            if youtube_videos:
                converter_used = "Tubetify"
                logger.info(f"✅ Tubetify found {len(youtube_videos)} video(s)")
        except Exception as e:
            logger.warning(f"Tubetify error: {e}")

    # Try Custom converter as fallback if Tubetify failed or unavailable
    if not youtube_videos and CUSTOM_CONVERTER_AVAILABLE:
        await sent_message.edit_text("🔍 Searching for YouTube matches (Custom)...")
        try:
            from custom_converter import spotify_to_youtube_custom
            youtube_videos = await spotify_to_youtube_custom(track_url)
            if youtube_videos:
                converter_used = "Custom"
                logger.info(f"✅ Custom converter found {len(youtube_videos)} video(s)")
        except Exception as e:
            logger.warning(f"Custom converter error: {e}")

    # If we found multiple videos, show selection menu
    if len(youtube_videos) > 1:
        # Store video options for manual selection
        context.user_data['youtube_video_options'] = youtube_videos
        context.user_data['converter_used'] = converter_used

        # Show video selection menu
        message = f"🎵 *Track Found*\n\n"
        message += f"**Title:** {track_info['title']}\n"
        message += f"**Artist:** {track_info['artist']}\n\n"
        message += f"🎯 Found {len(youtube_videos)} YouTube matches ({converter_used}). Choose the best one:\n\n"

        keyboard = []
        for i, video in enumerate(youtube_videos[:5]):  # Limit to 5 options
            video_title = video.get('video_found', video.get('title', 'Unknown'))
            if len(video_title) > 40:
                video_title = video_title[:37] + "..."
            keyboard.append([InlineKeyboardButton(
                f"🎬 {i+1}. {video_title}",
                callback_data=f'select_youtube_video_{i}'
            )])

        keyboard.append([InlineKeyboardButton("🔄 Auto-select Best Match", callback_data='auto_select_youtube')])
        keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')])

        await sent_message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return
    elif len(youtube_videos) == 1:
        # Single match found, store it for automatic use
        context.user_data['selected_youtube_video'] = youtube_videos[0]
        context.user_data['converter_used'] = converter_used
        logger.info(f"Single match found using {converter_used}, will use automatically")
    else:
        logger.warning("No YouTube matches found with any converter")

    # Show track info and ask for playlist selection (default behavior)
    message = f"🎵 *Track Found*\n\n"
    message += f"**Title:** {track_info['title']}\n"
    message += f"**Artist:** {track_info['artist']}\n\n"
    message += "Where would you like to save this track?"

    keyboard = [
        [InlineKeyboardButton("📁 Select Existing Playlist", callback_data='select_playlist_for_track')],
        [InlineKeyboardButton("➕ Create New Playlist", callback_data='create_playlist_for_track')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
    ]

    await sent_message.edit_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_search_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle search query from user input"""
    search_query = update.message.text.strip().lower()

    # Clear the state
    context.user_data.pop('state', None)

    db = load_db()

    if not db:
        await update.message.reply_text("❌ No playlists found in database.")
        return

    results = []

    # Search through all playlists and songs
    for playlist_id, playlist_data in db.items():
        playlist_name = playlist_data.get('name', 'Unknown')
        songs = playlist_data.get('songs', [])

        for idx, song in enumerate(songs):
            title = song.get('title', '').lower()
            artist = song.get('artist', '').lower()

            # Check if search query matches title or artist
            if search_query in title or search_query in artist:
                results.append({
                    'playlist_id': playlist_id,
                    'playlist_name': playlist_name,
                    'song_index': idx,
                    'title': song.get('title', 'Unknown'),
                    'artist': song.get('artist', 'Unknown'),
                    'url': song.get('url', '')
                })

    if not results:
        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]
        await update.message.reply_text(
            f"🔍 No songs found matching: *{search_query}*",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Limit results to avoid message length issues
    max_results = 10
    if len(results) > max_results:
        message = f"🔍 *Search Results* (showing {max_results} of {len(results)} matches)\n"
        message += f"Query: *{escape_markdown(search_query)}*\n\n"
        results = results[:max_results]
    else:
        message = f"🔍 *Search Results* ({len(results)} matches)\n"
        message += f"Query: *{escape_markdown(search_query)}*\n\n"

    # Create inline keyboard with results
    keyboard = []
    for i, result in enumerate(results):
        button_text = f"🎵 {result['artist']} - {result['title']}"
        if len(button_text) > 60:  # Truncate long titles
            button_text = button_text[:57] + "..."

        callback_data = f"show_song_{result['playlist_id']}_{result['song_index']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

        # Add playlist info to message
        message += f"{i+1}. *{escape_markdown(result['artist'])}* - {escape_markdown(result['title'])}\n"
        message += f"   📁 Playlist: {escape_markdown(result['playlist_name'])}\n\n"

    if len(results) == max_results and len(db) > 0:
        message += f"💡 *Tip:* Use a more specific search term to narrow results."

    # Add main menu button at the end
    keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

async def handle_spotify_search(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle Spotify search query from user input"""
    search_query = update.message.text.strip()

    # Clear the state
    context.user_data.pop('state', None)

    if not search_query:
        await update.message.reply_text("❌ Please provide a search term.")
        return

    sent_message = await update.message.reply_text("🔍 Searching Spotify...")

    try:
        # Search for tracks on Spotify
        search_results = await api_client.search_spotify_tracks(search_query, limit=8)

        if not search_results:
            keyboard = [
                [InlineKeyboardButton("🔍 Try Another Search", callback_data='search_spotify_tracks')],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
            await sent_message.edit_text(
                f"🔍 No tracks found for: *{escape_markdown(search_query)}*\n\n"
                "Try using different keywords or check the spelling.\n"
                "If this persists, Spotify API tokens may need refreshing.",
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Store search results in user data
        context.user_data['spotify_search_results'] = search_results

        # Create message with results
        message = f"🎵 *Spotify Search Results*\n"
        message += f"Query: *{escape_markdown(search_query)}*\n\n"
        message += "Select a track to download:\n\n"

        keyboard = []
        for i, track in enumerate(search_results):
            # Add track info to message
            message += f"{i+1}. **{track['artist']}** - {track['title']}\n"

            # Create button for this track
            button_text = f"🎵 {track['artist']} - {track['title']}"
            if len(button_text) > 60:
                button_text = button_text[:57] + "..."

            keyboard.append([InlineKeyboardButton(button_text, callback_data=f"select_spotify_track_{i}")])

        # Add navigation buttons
        keyboard.append([InlineKeyboardButton("🔍 New Search", callback_data='search_spotify_tracks')])
        keyboard.append([InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")])

        await sent_message.edit_text(
            message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error searching Spotify: {e}")
        keyboard = [
            [InlineKeyboardButton("🔍 Try Again", callback_data='search_spotify_tracks')],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]
        await sent_message.edit_text(
            f"❌ *Search Error*\n\n"
            f"There was an error searching Spotify. Please try again.\n\n"
            f"Error: {str(e)}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

async def select_spotify_track(update: Update, context: ContextTypes.DEFAULT_TYPE, track_index: int):
    """Handle selection of a track from Spotify search results"""
    search_results = context.user_data.get('spotify_search_results', [])

    if track_index >= len(search_results) or track_index < 0:
        await update.callback_query.edit_message_text("❌ Invalid track selection.")
        return

    selected_track = search_results[track_index]

    # Show loading message while getting track details
    await update.callback_query.edit_message_text("🔍 Getting track details...")

    # OPTIMIZED FLOW: Try spotdown.app API first (fast and direct)
    track_info = None

    try:
        # Use the new spotdown.app get_song_details method
        song_details = await api_client.get_song_details(selected_track['url'])
        if song_details:
            track_info = {
                'title': selected_track['title'],  # Use search result data as it's more reliable
                'artist': selected_track['artist'],
                'url': selected_track['url'],
                'download_url': selected_track['url'],  # spotdown.app will handle this
                'source': 'spotdown_api'
            }
            download_logger.info(f"✅ Got track details from spotdown.app API for: {selected_track['title']}")
    except Exception as e:
        download_logger.warning(f"Failed to get details from spotdown.app API: {e}")

    # FALLBACK 1: Spotify advanced API (if spotdown.app failed)
    if not track_info:
        await update.callback_query.edit_message_text("🔍 Trying Spotify API...")
        cached_tokens = context.user_data.get('spotify_tokens', {})
        track_info = await api_client.get_track_details_advanced(selected_track['url'], cached_tokens)
        if track_info:
            track_info['source'] = 'spotify_advanced'

    # FALLBACK 2: Basic Playwright method (last resort)
    if not track_info:
        await update.callback_query.edit_message_text("🔍 Using fallback method...")
        track_info = await api_client.get_track_details(selected_track['url'])
        if track_info:
            track_info['source'] = 'spotify_basic'

    # FINAL FALLBACK: Use search result data
    if not track_info:
        track_info = {
            'title': selected_track['title'],
            'artist': selected_track['artist'],
            'url': selected_track['url'],
            'download_url': selected_track['url'],
            'source': 'search_data'
        }

    # Store track info in user data
    context.user_data['track_info'] = track_info
    context.user_data['track_info']['url'] = selected_track['url']

    # Show track info and ask for playlist selection
    message = f"🎵 *Track Selected*\n\n"
    message += f"**Title:** {track_info['title']}\n"
    message += f"**Artist:** {track_info['artist']}\n\n"
    message += "Where would you like to save this track?"

    keyboard = [
        [InlineKeyboardButton("📁 Select Existing Playlist", callback_data='select_playlist_for_track')],
        [InlineKeyboardButton("➕ Create New Playlist", callback_data='create_playlist_for_track')],
        [InlineKeyboardButton("🔍 Back to Search", callback_data='search_spotify_tracks')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
    ]

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def show_playlists_for_track(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show existing playlists to select for adding a track"""
    db = load_db()

    if not db:
        await update.callback_query.edit_message_text(
            "❌ No playlists found. Create a new playlist first.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("➕ Create New Playlist", callback_data='create_playlist_for_track')]])
        )
        return

    keyboard = []
    message = "📁 *Select Playlist*\n\nChoose a playlist to add the track to:\n\n"

    for playlist_id, playlist_data in db.items():
        playlist_name = playlist_data.get('name', 'Unknown')
        song_count = len(playlist_data.get('songs', []))

        button_text = f"📁 {playlist_name} ({song_count} songs)"
        if len(button_text) > 60:
            button_text = button_text[:57] + "..."

        keyboard.append([InlineKeyboardButton(button_text, callback_data=f"add_track_to_{playlist_id}")])
        message += f"• {playlist_name} ({song_count} songs)\n"

    keyboard.append([InlineKeyboardButton("➕ Create New Playlist", callback_data='create_playlist_for_track')])
    keyboard.append([InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')])

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_youtube_url(update: Update, context: ContextTypes.DEFAULT_TYPE, youtube_url: str):
    """Handle YouTube URL - get info and allow adding to playlist or standalone download"""
    sent_message = await update.message.reply_text("🎵 Getting video information...")

    try:
        if not YTDLP_AVAILABLE:
            await sent_message.edit_text("❌ YouTube downloader not available. Please install required dependencies.")
            return

        video_info = get_video_info(youtube_url)

        if not video_info:
            await sent_message.edit_text("❌ Failed to process YouTube video. Please check the URL.")
            return

        video_title = video_info.get('title', 'Unknown Video')
        sanitized_title = sanitize_filename(video_title)

        # Store video info for later use
        context.user_data['youtube_track_info'] = {
            'url': youtube_url,
            'title': video_title,
            'sanitized_title': sanitized_title,
        }

        # Show options similar to Spotify tracks
        keyboard = [
            [InlineKeyboardButton("📥 Download to new folder", callback_data='youtube_new_folder')],
            [InlineKeyboardButton("📂 Add to existing playlist", callback_data='youtube_select_playlist')],
            [InlineKeyboardButton("🆕 Create new playlist", callback_data='youtube_create_playlist')],
            [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
        ]

        await sent_message.edit_text(
            f"🎵 *YouTube Video Found*\n\n"
            f"📺 **Title:** {video_title[:80]}{'...' if len(video_title) > 80 else ''}\n"
            f"🔗 **URL:** `{youtube_url[:50]}...`\n\n"
            f"What would you like to do with this video?",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        logger.error(f"Error handling YouTube URL: {e}")
        await sent_message.edit_text("❌ Error processing YouTube URL.")

async def youtube_download_to_new_folder(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Download YouTube video to new folder"""
    youtube_info = context.user_data.get('youtube_track_info')
    if not youtube_info:
        await update.callback_query.edit_message_text("❌ YouTube video information not found.")
        return

    video_title = youtube_info['title']
    sanitized_title = youtube_info['sanitized_title']

    # Create YouTube downloads directory
    youtube_dir = MUSIC_DIR / "YouTube Downloads"
    youtube_dir.mkdir(exist_ok=True)
    file_path = youtube_dir / f"{sanitized_title}"

    await update.callback_query.edit_message_text(
        f"📥 Downloading: *{video_title[:50]}{'...' if len(video_title) > 50 else ''}*\n\nPlease wait...",
        parse_mode=ParseMode.MARKDOWN
    )

    success = download_audio_ytdlp(youtube_info['url'], str(file_path))

    if success:
        # The actual file path will have an extension added by yt-dlp, so we need to find it.
        # Assuming mp3 for now.
        final_file_path = file_path.with_suffix('.mp3')
        file_size = final_file_path.stat().st_size if final_file_path.exists() else 0
        size_mb = file_size / (1024 * 1024)

        final_message = (
            f"✅ *YouTube Download Complete!*\n\n"
            f"📁 File: `{sanitized_title}.mp3`\n"
            f"💾 Size: {size_mb:.1f} MB\n"
            f"📂 Location: `YouTube Downloads/`"
        )

        keyboard = [[InlineKeyboardButton("⬅️ Back to menu", callback_data='main_menu')]]
        await update.callback_query.edit_message_text(
            final_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.callback_query.edit_message_text(
            f"❌ *YouTube Download Failed*\n\nCould not download the video. Please try again.",
            parse_mode=ParseMode.MARKDOWN
        )

    # Clean up
    context.user_data.pop('youtube_track_info', None)

async def youtube_select_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Show playlists for YouTube video"""
    db = load_db()
    if not db:
        await update.callback_query.edit_message_text(
            "You have no playlists. Create one first!",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🆕 Create playlist", callback_data='youtube_create_playlist')]])
        )
        return

    keyboard = []
    for playlist_id, playlist_data in db.items():
        playlist_name = playlist_data.get('name', 'Unknown')
        song_count = len(playlist_data.get('songs', []))
        keyboard.append([InlineKeyboardButton(
            f"📁 {playlist_name} ({song_count} songs)",
            callback_data=f'youtube_add_to_{playlist_id}'
        )])

    keyboard.append([InlineKeyboardButton("⬅️ Back", callback_data='youtube_back_to_options')])

    await update.callback_query.edit_message_text(
        "📂 *Select a playlist:*",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def youtube_create_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Prompt for new playlist name for YouTube video"""
    context.user_data['state'] = 'awaiting_youtube_playlist_name'
    await update.callback_query.edit_message_text(
        "📝 *Create New Playlist*\n\nSend me the name for the new playlist:",
        parse_mode=ParseMode.MARKDOWN
    )

async def youtube_add_to_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Add YouTube video to existing playlist"""
    youtube_info = context.user_data.get('youtube_track_info')
    if not youtube_info:
        await update.callback_query.edit_message_text("❌ YouTube video information not found.")
        return

    db = load_db()
    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')
    playlist_dir = MUSIC_DIR / playlist_name
    playlist_dir.mkdir(exist_ok=True)

    video_title = youtube_info['title']
    sanitized_title = youtube_info['sanitized_title']
    file_path = playlist_dir / f"{sanitized_title}"

    await update.callback_query.edit_message_text(
        f"📥 Adding to playlist: *{playlist_name}*\n\nDownloading: *{video_title[:40]}{'...' if len(video_title) > 40 else ''}*",
        parse_mode=ParseMode.MARKDOWN
    )

    success = download_audio_ytdlp(youtube_info['url'], str(file_path))

    if success:
        # Create song entry for database
        song_entry = {
            'title': video_title,
            'artist': 'YouTube',  # Default artist for YouTube videos
            'url': youtube_info['url'],
            'duration': '0:00',
            'source': 'youtube'
        }

        # Add to playlist
        playlist_data['songs'].append(song_entry)
        db[playlist_id] = playlist_data
        save_db(db)

        final_file_path = file_path.with_suffix('.mp3')
        file_size = final_file_path.stat().st_size if final_file_path.exists() else 0
        size_mb = file_size / (1024 * 1024)

        final_message = (
            f"✅ *Added to Playlist!*\n\n"
            f"📂 Playlist: `{playlist_name}`\n"
            f"📁 File: `{sanitized_title}.mp3`\n"
            f"💾 Size: {size_mb:.1f} MB\n"
            f"🎵 Total songs: {len(playlist_data['songs'])}"
        )

        keyboard = [[InlineKeyboardButton("⬅️ Back to menu", callback_data='main_menu')]]
        await update.callback_query.edit_message_text(
            final_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await update.callback_query.edit_message_text(
            f"❌ *Download Failed*\n\nCould not download the video. Please try again.",
            parse_mode=ParseMode.MARKDOWN
        )

    # Clean up
    context.user_data.pop('youtube_track_info', None)

async def handle_youtube_playlist_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new playlist name for YouTube video"""
    playlist_name = update.message.text.strip()
    youtube_info = context.user_data.get('youtube_track_info')

    if not youtube_info:
        await update.message.reply_text("❌ YouTube video information not found.")
        return

    playlist_name = sanitize_filename(playlist_name)
    playlist_dir = MUSIC_DIR / playlist_name
    playlist_dir.mkdir(exist_ok=True)

    video_title = youtube_info['title']
    sanitized_title = youtube_info['sanitized_title']
    file_path = playlist_dir / f"{sanitized_title}"

    sent_message = await update.message.reply_text(
        f"📝 Creating playlist: *{playlist_name}*\n\nDownloading: *{video_title[:40]}{'...' if len(video_title) > 40 else ''}*",
        parse_mode=ParseMode.MARKDOWN
    )

    success = download_audio_ytdlp(youtube_info['url'], str(file_path))

    if success:
        # Create song entry
        song_entry = {
            'title': video_title,
            'artist': 'YouTube',
            'url': youtube_info['url'],
            'duration': '0:00',
            'source': 'youtube'
        }

        # Create new playlist
        import time
        playlist_id = f"youtube_{int(time.time())}"
        db = load_db()
        db[playlist_id] = {
            'name': playlist_name,
            'url': '',  # No URL for custom playlists
            'songs': [song_entry],
            'path': str(playlist_dir),
            'is_custom': True,
            'source': 'youtube'
        }
        save_db(db)

        final_file_path = file_path.with_suffix('.mp3')
        file_size = final_file_path.stat().st_size if final_file_path.exists() else 0
        size_mb = file_size / (1024 * 1024)

        final_message = (
            f"✅ *Playlist Created!*\n\n"
            f"📂 Playlist: `{playlist_name}`\n"
            f"📁 File: `{sanitized_title}.mp3`\n"
            f"💾 Size: {size_mb:.1f} MB\n"
            f"🎵 Songs: 1"
        )

        keyboard = [[InlineKeyboardButton("⬅️ Back to menu", callback_data='main_menu')]]
        await sent_message.edit_text(
            final_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
    else:
        await sent_message.edit_text(
            f"❌ *Download Failed*\n\nCould not download the video. Please try again.",
            parse_mode=ParseMode.MARKDOWN
        )

    # Clean up
    context.user_data.pop('youtube_track_info', None)
    context.user_data.pop('state', None)

async def handle_youtube_filename(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle custom filename for YouTube download"""
    filename = update.message.text.strip()
    youtube_info = context.user_data.get('youtube_info')

    if not youtube_info:
        await update.message.reply_text("❌ YouTube download session expired.")
        return

    await perform_youtube_download(update, context, filename)

async def perform_youtube_download(update: Update, context: ContextTypes.DEFAULT_TYPE, filename: str = None):
    """Perform the actual YouTube download"""
    youtube_info = context.user_data.get('youtube_info')
    if not youtube_info:
        await update.message.reply_text("❌ YouTube download session expired.")
        return

    youtube_url = youtube_info['url']

    # Generate filename if not provided
    if not filename:
        import time
        filename = f"youtube_download_{int(time.time())}"

    # Sanitize filename
    filename = sanitize_filename(filename)

    # Create YouTube downloads directory
    youtube_dir = MUSIC_DIR / "YouTube Downloads"
    youtube_dir.mkdir(exist_ok=True)

    file_path = youtube_dir / f"{filename}.mp3"

    # Update message to show download progress
    try:
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=youtube_info.get('message_id'),
            text=f"📥 Downloading: *{filename}*\n\nPlease wait...",
            parse_mode=ParseMode.MARKDOWN
        )
    except:
        pass

    # Perform download
    success = await download_from_youtube_url(youtube_url, file_path, filename)

    if success:
        # Get file size for info
        file_size = file_path.stat().st_size if file_path.exists() else 0
        size_mb = file_size / (1024 * 1024)

        final_message = (
            f"✅ *YouTube Download Complete!*\n\n"
            f"📁 File: `{filename}.mp3`\n"
            f"💾 Size: {size_mb:.1f} MB\n"
            f"📂 Location: `YouTube Downloads/`"
        )

        keyboard = [[InlineKeyboardButton("⬅️ Back to menu", callback_data='main_menu')]]

        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=youtube_info.get('message_id'),
                text=final_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            await update.message.reply_text(final_message, parse_mode=ParseMode.MARKDOWN)
    else:
        error_message = (
            f"❌ *YouTube Download Failed*\n\n"
            f"Could not download from: `{youtube_url[:50]}...`\n\n"
            "Please try again or check if the URL is valid."
        )

        try:
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=youtube_info.get('message_id'),
                text=error_message,
                parse_mode=ParseMode.MARKDOWN
            )
        except:
            await update.message.reply_text(error_message, parse_mode=ParseMode.MARKDOWN)

    # Clean up state
    context.user_data.pop('youtube_info', None)
    context.user_data.pop('state', None)

async def select_youtube_video(update: Update, context: ContextTypes.DEFAULT_TYPE, video_index: int):
    """Handle manual YouTube video selection from multiple options"""
    youtube_videos = context.user_data.get('youtube_video_options', [])
    track_info = context.user_data.get('track_info', {})

    if video_index >= len(youtube_videos):
        await update.callback_query.edit_message_text("❌ Invalid video selection.")
        return

    selected_video = youtube_videos[video_index]
    youtube_url = selected_video['youtube_url']
    video_title = selected_video.get('video_found', selected_video.get('title', 'Unknown'))

    # Store the selected video for download
    context.user_data['selected_youtube_video'] = selected_video

    message = f"🎯 *Video Selected*\n\n"
    message += f"**Track:** {track_info.get('title', 'Unknown')}\n"
    message += f"**Artist:** {track_info.get('artist', 'Unknown')}\n\n"
    message += f"**Selected Video:** {video_title}\n"
    message += f"**YouTube URL:** `{youtube_url}`\n\n"
    message += "Where would you like to save this track?"

    keyboard = [
        [InlineKeyboardButton("📁 Select Existing Playlist", callback_data='select_playlist_for_track')],
        [InlineKeyboardButton("➕ Create New Playlist", callback_data='create_playlist_for_track')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
    ]

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def auto_select_youtube_video(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Auto-select the best YouTube video match"""
    youtube_videos = context.user_data.get('youtube_video_options', [])
    track_info = context.user_data.get('track_info', {})

    if not youtube_videos:
        await update.callback_query.edit_message_text("❌ No video options found.")
        return

    # Use the first (best) match
    selected_video = youtube_videos[0]
    youtube_url = selected_video['youtube_url']
    video_title = selected_video.get('video_found', selected_video.get('title', 'Unknown'))

    # Store the selected video for download
    context.user_data['selected_youtube_video'] = selected_video

    message = f"🤖 *Auto-Selected Best Match*\n\n"
    message += f"**Track:** {track_info.get('title', 'Unknown')}\n"
    message += f"**Artist:** {track_info.get('artist', 'Unknown')}\n\n"
    message += f"**Selected Video:** {video_title}\n"
    message += f"**YouTube URL:** `{youtube_url}`\n\n"
    message += "Where would you like to save this track?"

    keyboard = [
        [InlineKeyboardButton("📁 Select Existing Playlist", callback_data='select_playlist_for_track')],
        [InlineKeyboardButton("➕ Create New Playlist", callback_data='create_playlist_for_track')],
        [InlineKeyboardButton("❌ Cancel", callback_data='cancel_action')]
    ]

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def handle_track_playlist_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle new playlist name for track"""
    playlist_name = update.message.text.strip()

    # Clear state
    context.user_data.pop('state', None)

    # Validate playlist name
    if not playlist_name or len(playlist_name.strip()) == 0:
        await update.message.reply_text("❌ Please provide a valid playlist name.")
        return

    # Sanitize filename
    sanitized_name = sanitize_filename(playlist_name)

    # Create new playlist and add track
    db = load_db()
    playlist_id = f"track_playlist_{datetime.now().strftime('%Y%m%d_%H%M%S')}"

    # Get track info
    track_info = context.user_data.get('track_info')
    if not track_info:
        await update.message.reply_text("❌ Track information lost. Please try again.")
        return

    # Create new playlist with the track (normalized)
    normalized_track_info = normalize_track_info(track_info)
    playlist_path = str(MUSIC_DIR / sanitized_name)

    db[playlist_id] = {
        'name': sanitized_name,
        'url': '',  # Not a Spotify playlist, just a custom folder
        'songs': [normalized_track_info],
        'path': playlist_path,
        'created_at': datetime.now().isoformat(),
        'is_custom': True  # Mark as custom playlist for tracks
    }

    save_db(db)

    # Download the track
    await download_track_to_playlist(update, context, playlist_id, track_info)

async def add_track_to_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str):
    """Add track to existing playlist"""
    db = load_db()

    if playlist_id not in db:
        await update.callback_query.edit_message_text("❌ Playlist not found.")
        return

    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')

    # Get track info
    track_info = context.user_data.get('track_info')
    if not track_info:
        await update.callback_query.edit_message_text("❌ Track information lost. Please try again.")
        return

    # Check if track already exists in playlist
    existing_songs = playlist_data.get('songs', [])
    track_url = track_info.get('url', '')

    for song in existing_songs:
        if song.get('url') == track_url:
            await update.callback_query.edit_message_text(
                f"⚠️ This track already exists in '{playlist_name}'.",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]])
            )
            return

    # Normalize and add track to playlist
    normalized_track_info = normalize_track_info(track_info)
    existing_songs.append(normalized_track_info)
    playlist_data['songs'] = existing_songs
    db[playlist_id] = playlist_data
    save_db(db)

    # Download the track
    await download_track_to_playlist(update, context, playlist_id, track_info, is_existing_playlist=True)

async def download_track_to_playlist(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str, track_info: dict, is_existing_playlist: bool = False):
    """Download a single track to a playlist"""
    db = load_db()
    playlist_data = db[playlist_id]
    playlist_name = playlist_data.get('name', 'Unknown')

    # Create playlist directory
    playlist_dir = MUSIC_DIR / playlist_name
    playlist_dir.mkdir(exist_ok=True)

    track_title = sanitize_filename(track_info.get('title', 'Unknown'))
    artist_name = sanitize_filename(track_info.get('artist', 'Unknown'))
    file_path = playlist_dir / f"{artist_name} - {track_title}.mp3"

    # Update message based on context
    if hasattr(update, 'callback_query') and update.callback_query is not None:
        message_method = update.callback_query.edit_message_text
    else:
        message_method = update.message.reply_text

    if file_path.exists():
        success_message = f"✅ *Track Added Successfully!*\n\n"
        success_message += f"🎵 **Track:** {track_info.get('title', 'Unknown')} - {track_info.get('artist', 'Unknown')}\n"
        success_message += f"📁 **Playlist:** {playlist_name}\n"
        success_message += f"💡 **Status:** File already exists\n\n"
        success_message += f"The track was added to the playlist database."

        keyboard = [
            [InlineKeyboardButton("📁 View Playlist", callback_data=f"list_songs_{playlist_id}")],
            [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
        ]

        await message_method(
            success_message,
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Show download starting message
    download_message = f"⏳ *Downloading Track*\n\n"
    download_message += f"🎵 **Track:** {track_info.get('title', 'Unknown')} - {track_info.get('artist', 'Unknown')}\n"
    download_message += f"📁 **Playlist:** {playlist_name}\n\n"
    download_message += "Please wait..."

    await message_method(download_message, parse_mode=ParseMode.MARKDOWN)

    try:
        # Check if a specific YouTube video was selected for this track
        selected_video = context.user_data.get('selected_youtube_video')
        if selected_video and YTDLP_AVAILABLE:
            # Use the selected YouTube video for download
            youtube_url = selected_video['youtube_url']

            download_logger.info(f"🎯 Using manually selected YouTube video: {youtube_url}")

            output_path_without_ext = file_path.with_suffix('')
            download_success = download_audio_ytdlp(youtube_url, str(output_path_without_ext))

            # Clean up the selected video from context
            context.user_data.pop('selected_youtube_video', None)
            context.user_data.pop('youtube_video_options', None)
        else:
            # Use regular download method
            download_success = await api_client.download_song(track_info, file_path)

        if download_success and file_path.exists():
            success_message = f"✅ *Track Downloaded Successfully!*\n\n"
            success_message += f"🎵 **Track:** {track_info.get('title', 'Unknown')} - {track_info.get('artist', 'Unknown')}\n"
            success_message += f"📁 **Playlist:** {playlist_name}\n"
            success_message += f"📂 **Location:** {file_path}\n\n"

            if is_existing_playlist:
                total_songs = len(playlist_data.get('songs', []))
                success_message += f"📊 **Total songs in playlist:** {total_songs}"

            keyboard = [
                [InlineKeyboardButton("📁 View Playlist", callback_data=f"list_songs_{playlist_id}")],
                [InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]
            ]
        else:
            # Remove from database if download failed
            songs = playlist_data.get('songs', [])
            songs = [s for s in songs if s.get('url') != track_info.get('url')]
            playlist_data['songs'] = songs
            db[playlist_id] = playlist_data
            save_db(db)

            success_message = f"❌ *Download Failed*\n\n"
            success_message += f"🎵 **Track:** {track_info.get('title', 'Unknown')} - {track_info.get('artist', 'Unknown')}\n"
            success_message += f"📁 **Playlist:** {playlist_name}\n\n"
            success_message += "The track could not be downloaded. It may be unavailable or region-locked."

            keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]

        # Send final message
        if hasattr(update, 'callback_query') and update.callback_query is not None:
            await update.callback_query.edit_message_text(
                success_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                success_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

    except Exception as e:
        logger.error(f"Error downloading track: {e}")

        # Remove from database if download failed
        songs = playlist_data.get('songs', [])
        songs = [s for s in songs if s.get('url') != track_info.get('url')]
        playlist_data['songs'] = songs
        db[playlist_id] = playlist_data
        save_db(db)

        error_message = f"❌ *Download Error*\n\n"
        error_message += f"🎵 **Track:** {track_info.get('title', 'Unknown')} - {track_info.get('artist', 'Unknown')}\n"
        error_message += f"📁 **Playlist:** {playlist_name}\n\n"
        error_message += f"Error: {str(e)}"

        keyboard = [[InlineKeyboardButton("🏠 Main Menu", callback_data="main_menu")]]

        if hasattr(update, 'callback_query') and update.callback_query is not None:
            await update.callback_query.edit_message_text(
                error_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )
        else:
            await update.message.reply_text(
                error_message,
                reply_markup=InlineKeyboardMarkup(keyboard),
                parse_mode=ParseMode.MARKDOWN
            )

async def error_handler(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    logger.error("Exception while handling an update:", exc_info=context.error)

# --- SCHEDULING FUNCTIONS ---
def get_next_sync_time(settings: Dict) -> Optional[datetime]:
    """Calculate next sync time based on settings"""
    if not settings.get('sync_enabled', False):
        return None

    sync_day = settings.get('sync_day', 'monday')
    sync_time_str = settings.get('sync_time', '09:00')

    try:
        sync_time = datetime.strptime(sync_time_str, '%H:%M').time()
    except ValueError:
        logger.error(f"Invalid sync time format: {sync_time_str}")
        return None

    # Map day names to weekday numbers (0=Monday)
    day_mapping = {
        'monday': 0, 'tuesday': 1, 'wednesday': 2, 'thursday': 3,
        'friday': 4, 'saturday': 5, 'sunday': 6
    }

    target_weekday = day_mapping.get(sync_day.lower())
    if target_weekday is None:
        logger.error(f"Invalid sync day: {sync_day}")
        return None

    # Get current time
    now = datetime.now()
    current_weekday = now.weekday()

    # Calculate days until target day
    days_ahead = (target_weekday - current_weekday) % 7

    # If it's the same day, check if time has passed
    if days_ahead == 0:
        target_datetime = now.replace(hour=sync_time.hour, minute=sync_time.minute, second=0, microsecond=0)
        if target_datetime <= now:
            # Time has passed, schedule for next week
            days_ahead = 7

    # Calculate next sync datetime
    next_sync = now + timedelta(days=days_ahead)
    next_sync = next_sync.replace(hour=sync_time.hour, minute=sync_time.minute, second=0, microsecond=0)

    return next_sync

async def schedule_next_sync(context: ContextTypes.DEFAULT_TYPE):
    """Schedule the next automatic sync"""
    settings = load_settings()
    next_sync_time = get_next_sync_time(settings)

    if next_sync_time and hasattr(context, 'job_queue') and context.job_queue:
        try:
            # Remove existing sync jobs
            current_jobs = context.job_queue.get_jobs_by_name('auto_sync')
            for job in current_jobs:
                job.schedule_removal()

            # Schedule new sync
            context.job_queue.run_once(
                auto_sync_job,
                when=next_sync_time,
                name='auto_sync'
            )

            logger.info(f"Next auto sync scheduled for: {next_sync_time}")
            sync_logger.info(f"Next auto sync scheduled for: {next_sync_time}")
        except Exception as e:
            logger.warning(f"Could not schedule sync: {e}")
    elif next_sync_time:
        logger.info(f"Would schedule sync for: {next_sync_time} (job queue not available)")
    else:
        logger.info("Auto sync is disabled")

async def auto_sync_job(context: ContextTypes.DEFAULT_TYPE):
    """Job function for automatic sync"""
    sync_logger.info("Starting scheduled automatic sync")

    try:
        result = await sync_manager.sync_all_playlists(context)

        # Send notification to user if enabled
        settings = load_settings()
        user_id = settings.get('user_id')
        notify_enabled = settings.get('notify_sync_results', True)

        if user_id and notify_enabled and context.bot:
            await send_sync_notification(context.bot, user_id, result)

        if result:
            sync_logger.info(f"Scheduled sync completed: {result['synced']}/{result['total']} synced, {result['new_songs']} new songs")
        else:
            sync_logger.error("Scheduled sync failed")

    except Exception as e:
        sync_logger.error(f"Error in scheduled sync: {e}")

    # Schedule next sync
    await schedule_next_sync(context)

async def send_sync_notification(bot, user_id: int, result: dict):
    """Send sync results notification to user"""
    try:
        if not result:
            # Sync failed
            message = "⚠️ *Automatic Sync Failed*\n\n"
            message += "The scheduled playlist sync encountered an error. "
            message += "Please check the logs or try a manual sync."

            await bot.send_message(
                chat_id=user_id,
                text=message,
                parse_mode=ParseMode.MARKDOWN
            )
            return

        # Sync completed successfully
        synced = result.get('synced', 0)
        total = result.get('total', 0)
        new_songs = result.get('new_songs', 0)
        errors = result.get('errors', 0)

        if synced == 0 and total == 0:
            # No playlists to sync
            message = "ℹ️ *Automatic Sync Completed*\n\n"
            message += "No playlists found to sync."
        else:
            # Show detailed results
            message = "✅ *Automatic Sync Completed*\n\n"
            message += f"📊 *Results:*\n"
            message += f"▪️ Playlists synced: {synced}/{total}\n"

            if new_songs > 0:
                message += f"▪️ New songs found: {new_songs}\n"
            else:
                message += "▪️ No new songs found\n"

            if errors > 0:
                message += f"▪️ Errors: {errors}\n"

            # Add timestamp
            from datetime import datetime
            timestamp = datetime.now().strftime("%Y-%m-%d %H:%M")
            message += f"\n🕒 Completed at: {timestamp}"

            # Add manual sync option if there were errors
            if errors > 0:
                message += "\n\n_You can try a manual sync to retry failed playlists._"

        await bot.send_message(
            chat_id=user_id,
            text=message,
            parse_mode=ParseMode.MARKDOWN
        )

    except Exception as e:
        sync_logger.error(f"Failed to send notification to user {user_id}: {e}")

async def setup_menu_button(application):
    """Setup the menu button for Telegram"""
    try:
        commands = [
            BotCommand("start", "Start the bot and show main menu"),
            BotCommand("sync", "Manual playlist sync"),
            BotCommand("settings", "Bot settings"),
            BotCommand("search", "Search for songs in database"),
            BotCommand("track", "Add individual track from Spotify URL"),
        ]

        await application.bot.set_my_commands(commands)

        # Set menu button
        menu_button = MenuButtonCommands()
        await application.bot.set_chat_menu_button(menu_button=menu_button)

        logger.info("Menu button and commands set successfully")

    except Exception as e:
        logger.error(f"Error setting up menu button: {e}")

# Add command handlers for menu commands
async def sync_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /sync command"""
    keyboard = [[InlineKeyboardButton("🔄 Start Sync", callback_data='manual_sync')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('🔄 Manual playlist sync', reply_markup=reply_markup)

async def settings_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /settings command"""
    keyboard = [[InlineKeyboardButton("⚙️ Open Settings", callback_data='show_settings')]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('⚙️ Bot Settings', reply_markup=reply_markup)

async def track_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /track command - add individual songs from Spotify"""
    if not context.args:
        await update.message.reply_text(
            "🎵 *Add Individual Track*\n\n"
            "Use: `/track <spotify track URL>`\n"
            "Example: `/track https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh`\n\n"
            "Or use the '➕ Add Track' button in the main menu to search interactively.",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    track_url = " ".join(context.args).strip()

    # Validate Spotify track URL
    if "open.spotify.com/track/" not in track_url:
        await update.message.reply_text(
            "❌ Invalid Spotify track URL.\n"
            "Please provide a valid Spotify track URL like:\n"
            "`https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    await handle_track_url(update, context, track_url)

async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /search command - search for songs in database"""
    if not context.args:
        await update.message.reply_text(
            "🔍 *Search Songs*\n\n"
            "Use: `/search <song name or artist>`\n"
            "Example: `/search bohemian rhapsody`",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    search_query = " ".join(context.args).lower()
    db = load_db()

    if not db:
        await update.message.reply_text("❌ No playlists found in database.")
        return

    results = []

    # Search through all playlists and songs
    for playlist_id, playlist_data in db.items():
        playlist_name = playlist_data.get('name', 'Unknown')
        songs = playlist_data.get('songs', [])

        for idx, song in enumerate(songs):
            title = song.get('title', '').lower()
            artist = song.get('artist', '').lower()

            # Check if search query matches title or artist
            if search_query in title or search_query in artist:
                results.append({
                    'playlist_id': playlist_id,
                    'playlist_name': playlist_name,
                    'song_index': idx,
                    'title': song.get('title', 'Unknown'),
                    'artist': song.get('artist', 'Unknown'),
                    'url': song.get('url', '')
                })

    if not results:
        await update.message.reply_text(
            f"🔍 No songs found matching: *{escape_markdown(search_query)}*",
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Limit results to avoid message length issues
    max_results = 10
    if len(results) > max_results:
        message = f"🔍 *Search Results* (showing {max_results} of {len(results)} matches)\n"
        message += f"Query: *{escape_markdown(search_query)}*\n\n"
        results = results[:max_results]
    else:
        message = f"🔍 *Search Results* ({len(results)} matches)\n"
        message += f"Query: *{escape_markdown(search_query)}*\n\n"

    # Create inline keyboard with results
    keyboard = []
    for i, result in enumerate(results):
        button_text = f"🎵 {result['artist']} - {result['title']}"
        if len(button_text) > 60:  # Truncate long titles
            button_text = button_text[:57] + "..."

        callback_data = f"show_song_{result['playlist_id']}_{result['song_index']}"
        keyboard.append([InlineKeyboardButton(button_text, callback_data=callback_data)])

        # Add playlist info to message
        message += f"{i+1}. *{escape_markdown(result['artist'])}* - {escape_markdown(result['title'])}\n"
        message += f"   📁 Playlist: {escape_markdown(result['playlist_name'])}\n\n"

    if len(results) == max_results and len(db) > 0:
        message += f"💡 *Tip:* Use a more specific search term to narrow results."

    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(message, reply_markup=reply_markup, parse_mode=ParseMode.MARKDOWN)

def main():
    global sync_manager

    setup_database()
    application = (
        Application.builder()
        .token(TELEGRAM_TOKEN)
        .connect_timeout(20.0).read_timeout(30.0)
        .build()
    )

    # Initialize sync manager
    sync_manager = PlaylistSyncManager(api_client)

    # Setup handlers
    application.add_error_handler(error_handler)
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("sync", sync_command))
    application.add_handler(CommandHandler("settings", settings_command))
    application.add_handler(CommandHandler("search", search_command))
    application.add_handler(CommandHandler("track", track_command))
    application.add_handler(CallbackQueryHandler(button_handler))
    application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, message_handler))

    # Setup menu button after application starts
    async def post_init(application):
        await setup_menu_button(application)
        logger.info("Bot initialization completed")

    application.post_init = post_init

    logger.info("Bot has started and is listening...")
    application.run_polling()

if __name__ == '__main__':
    main()
