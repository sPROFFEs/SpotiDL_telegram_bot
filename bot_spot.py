import os
import re
import json
import logging
import asyncio
import random
import subprocess
from pathlib import Path
from urllib.parse import quote
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

# --- CONFIGURATION ---
TELEGRAM_TOKEN = 'YOUR_TELEGRAM_BOT_TOKEN_HERE' # Replace with your actual bot token
DB_FILE = Path('playlist_db.json')
MUSIC_DIR = Path('/music/local')
LOGS_DIR = Path('logs')
SETTINGS_FILE = Path('bot_settings.json')

# --- RETRY CONFIGURATION ---
MAX_API_ATTEMPTS = 5       # Maximum attempts for API calls
MAX_DOWNLOAD_ATTEMPTS = 3  # Try to download each song up to 3 times
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
    """Check if a song file is complete by file size and basic validation"""
    try:
        if not file_path.exists():
            return False

        # Get file size
        file_size = file_path.stat().st_size

        # Very small files are likely corrupted (less than 100KB for any song is suspicious)
        if file_size < 100 * 1024:  # 100KB
            logger.info(f"File too small - {file_path.name}: {file_size} bytes")
            return False

        # Parse expected duration to estimate minimum expected file size
        duration_parts = expected_duration.split(':')
        if len(duration_parts) == 2:
            try:
                expected_minutes = int(duration_parts[0])
                expected_seconds = int(duration_parts[1])
                expected_total_seconds = expected_minutes * 60 + expected_seconds

                # Estimate minimum file size based on duration
                # Assuming very low quality MP3 (32kbps) as minimum
                min_bitrate_kbps = 32
                estimated_min_size = (expected_total_seconds * min_bitrate_kbps * 1024) // 8

                if file_size < estimated_min_size * 0.5:  # 50% tolerance
                    logger.info(f"File size too small for duration - {file_path.name}: {file_size} bytes for {expected_total_seconds}s")
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

                    # Allow 15% tolerance for duration differences
                    tolerance = max(5, expected_total_seconds * 0.15)
                    duration_diff = abs(actual_duration - expected_total_seconds)

                    is_valid = duration_diff <= tolerance

                    if not is_valid:
                        logger.info(f"Duration mismatch - {file_path.name}: expected {expected_total_seconds}s, got {actual_duration}s")

                    return is_valid

        except (FileNotFoundError, subprocess.SubprocessError):
            # ffprobe not available, rely on file size check
            pass

        # If we can't use ffprobe, check if the file is a valid audio file by reading its header
        try:
            with open(file_path, 'rb') as f:
                header = f.read(10)

            # Check for common audio file headers
            if header.startswith(b'ID3') or header[0:2] == b'\xff\xfb' or header[0:3] == b'TAG':
                # Looks like a valid MP3 file
                return True
            else:
                logger.info(f"Invalid audio header - {file_path.name}")
                return False

        except Exception:
            pass

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

    logger.info(f"Integrity check completed for {playlist_name}: {result['valid_songs']}/{result['checked_songs']} valid, {len(result['corrupted_songs'])} corrupted, {len(result['missing_songs'])} missing")

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
    """Manages free proxy servers for API requests"""

    def __init__(self):
        # List of free proxy APIs and sources
        self.proxy_sources = [
            "https://api.proxyscrape.com/v2/?request=get&protocol=http&timeout=10000&country=all&ssl=all&anonymity=all",
            "https://raw.githubusercontent.com/TheSpeedX/PROXY-List/master/http.txt",
            "https://raw.githubusercontent.com/monosans/proxy-list/main/proxies/http.txt"
        ]
        self.proxies = []
        self.last_update = None

    async def get_working_proxy(self, context: ContextTypes.DEFAULT_TYPE = None):
        """Get a working proxy from available sources"""
        try:
            # Update proxy list every 30 minutes
            if not self.proxies or not self.last_update or \
               (datetime.now() - self.last_update).total_seconds() > 1800:
                await self._update_proxies()

            # Test and return a working proxy
            for proxy in self.proxies[:10]:  # Test first 10 proxies
                if await self._test_proxy(proxy):
                    logger.info(f"Using proxy: {proxy}")
                    return proxy

            logger.warning("No working proxies found")
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
        """Test if a proxy is working"""
        try:
            import aiohttp
            proxy_url = f"http://{proxy}"

            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://httpbin.org/ip",
                    proxy=proxy_url,
                    timeout=aiohttp.ClientTimeout(total=5)
                ) as response:
                    return response.status == 200

        except Exception:
            return False

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

            total_playlists = len(db)
            synced_count = 0
            error_count = 0
            new_songs_count = 0

            sync_logger.info(f"Found {total_playlists} playlists to sync")

            for playlist_id, playlist_data in db.items():
                playlist_name = playlist_data.get('name', 'Unknown')
                playlist_url = playlist_data.get('url', '')

                sync_logger.info(f"Syncing playlist: {playlist_name}")

                try:
                    # Get current online playlist data
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
                'errors': error_count
            }

        except Exception as e:
            sync_logger.error(f"Critical error during sync: {e}")
            return None

    async def _download_new_songs(self, new_songs: List[Dict], playlist_data: Dict, playlist_id: str):
        """Download newly found songs and return only successfully downloaded ones"""
        playlist_name = playlist_data.get('name', 'Unknown')
        playlist_dir = MUSIC_DIR / playlist_name
        playlist_dir.mkdir(exist_ok=True)

        download_logger.info(f"Auto-downloading {len(new_songs)} new songs for {playlist_name}")
        successfully_downloaded_songs = []

        for song in new_songs:
            try:
                song_title = sanitize_filename(song.get('title', 'Unknown'))
                artist_name = sanitize_filename(song.get('artist', 'Unknown'))
                file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

                if file_path.exists():
                    successfully_downloaded_songs.append(song)  # Already exists, count as success
                    continue

                success = await self.api_client.download_song(song, file_path)
                if success:
                    download_logger.info(f"Auto-downloaded: {artist_name} - {song_title}")
                    successfully_downloaded_songs.append(song)  # Only add if successful
                else:
                    download_logger.warning(f"Failed to auto-download: {artist_name} - {song_title}")
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

    async def download_song(self, song_url_or_data, download_path: Path):
        """Download song with enhanced retry mechanism and proxy support"""
        # Handle both string URL and dict data
        if isinstance(song_url_or_data, dict):
            song_url = song_url_or_data.get('url', '')
            song_title = song_url_or_data.get('title', 'Unknown')
        else:
            song_url = song_url_or_data
            song_title = 'Unknown'

        for attempt in range(MAX_API_ATTEMPTS):
            try:
                # Rate limiting to avoid overwhelming the server
                await self._rate_limit()

                # Use current base URL (might be switched due to failures)
                api_url = f"{self.current_base_url}/api/download"
                payload = {"url": song_url}

                download_logger.info(f"Download attempt {attempt + 1}/{MAX_API_ATTEMPTS} for: {song_title} via {self.current_base_url}")

                # Determine proxy usage strategy
                use_proxy = attempt >= 2 or self._should_use_proxy_immediately()
                proxy = None

                if use_proxy:
                    proxy = await self.proxy_manager.get_working_proxy()
                    if proxy:
                        download_logger.info(f"Using proxy for download attempt {attempt + 1}: {proxy}")
                    else:
                        download_logger.info(f"No working proxy available for attempt {attempt + 1}, trying direct connection")

                success = await self._make_download_request(api_url, payload, download_path, proxy)
                if success:
                    download_logger.info(f"✅ Successfully downloaded {song_title} on attempt {attempt + 1}")
                    # Reset failure counter on success
                    if self.failed_requests > 0:
                        self.failed_requests = max(0, self.failed_requests - 1)
                    return True

                # Handle API failure
                await self._handle_api_failure()

            except Exception as e:
                download_logger.warning(f"Download attempt {attempt + 1} failed: {e}")
                await self._handle_api_failure()

                # Try fallback HTTP method on browser failures
                if "Failed to start browser" in str(e) and attempt >= 2:
                    download_logger.info("Trying HTTP fallback method due to browser issues...")
                    success = await self._try_http_fallback(song_url, download_path)
                    if success:
                        download_logger.info(f"✅ Successfully downloaded {song_title} using HTTP fallback")
                        return True

            # Wait before retrying (exponential backoff with jitter)
            if attempt < MAX_API_ATTEMPTS - 1:
                delay = RETRY_DELAY_SECONDS * (2 ** attempt) + random.uniform(1, 3)  # Add jitter
                download_logger.info(f"Waiting {delay:.1f}s before retry...")
                await asyncio.sleep(delay)

        download_logger.error(f"Failed to download {song_title} after {MAX_API_ATTEMPTS} attempts")
        return False

    async def _make_download_request(self, api_url: str, payload: dict, download_path: Path, proxy: str = None):
        """Make a single download request attempt"""
        async with async_playwright() as p:
            browser_options = self.BROWSER_LAUNCH_OPTIONS.copy()

            # Configure proxy if provided
            if proxy:
                browser_options['proxy'] = {'server': f'http://{proxy}'}

            # Add SSL ignore args for problematic proxies
            if 'args' not in browser_options:
                browser_options['args'] = []

            browser_options['args'].extend([
                '--ignore-certificate-errors',
                '--ignore-ssl-errors',
                '--ignore-certificate-errors-spki-list',
                '--disable-web-security',
                '--allow-running-insecure-content',
                '--disable-features=VizDisplayCompositor'
            ])

            try:
                browser = await p.chromium.launch(**browser_options)
            except Exception as e:
                download_logger.error(f"Failed to start browser for download: {e}")
                return False

            # Enhanced browser headers with rotating User-Agents
            user_agents = [
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            ]

            browser_headers = {
                "User-Agent": random.choice(user_agents),
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
                "Accept-Encoding": "gzip, deflate, br",
                "DNT": "1",
                "Connection": "keep-alive",
                "Upgrade-Insecure-Requests": "1",
            }

            context = await browser.new_context(
                extra_http_headers=browser_headers,
                viewport={'width': 1920, 'height': 1080},
                locale='en-US',
                ignore_https_errors=True
            )
            page = await context.new_page()

            try:
                # Get base URL from api_url for proper referrer
                from urllib.parse import urlparse
                parsed_url = urlparse(api_url)
                base_url = f"{parsed_url.scheme}://{parsed_url.netloc}"

                # Visit the playlist page to establish session
                playlist_url = f"{base_url}/playlist"
                await page.goto(playlist_url,
                               wait_until="domcontentloaded",
                               timeout=API_TIMEOUT)

                # Random delay to avoid detection
                await page.wait_for_timeout(random.randint(2000, 5000))

                # Enhanced download headers with proper referrer
                download_headers = {
                    "Content-Type": "application/json",
                    "Accept": "application/json, text/plain, */*",
                    "Origin": base_url,
                    "Referer": playlist_url,
                    "Sec-Fetch-Site": "same-origin",
                    "Sec-Fetch-Mode": "cors",
                    "Sec-Fetch-Dest": "empty",
                    "X-Requested-With": "XMLHttpRequest"
                }

                # Multiple timeout attempts for robustness
                for timeout_attempt in range(2):
                    try:
                        timeout = 45000 if timeout_attempt == 0 else 60000
                        response = await page.request.post(
                            api_url,
                            data=json.dumps(payload),
                            headers=download_headers,
                            timeout=timeout
                        )
                        break
                    except Exception as timeout_e:
                        if timeout_attempt == 1:
                            raise timeout_e
                        download_logger.warning(f"First timeout attempt failed, trying with longer timeout: {timeout_e}")
                        await asyncio.sleep(2)

                if response.ok:
                    content = await response.body()
                    if len(content) > 1000:  # Ensure we got actual audio data
                        # Validate it's actually audio content by checking headers
                        content_type = response.headers.get('content-type', '').lower()
                        if 'audio' in content_type or 'octet-stream' in content_type or len(content) > 100000:
                            with open(download_path, 'wb') as f:
                                f.write(content)
                            download_logger.info(f"Download successful (Size: {len(content)} bytes, Type: {content_type})")
                            return True
                        else:
                            download_logger.warning(f"Invalid content type: {content_type}, size: {len(content)} bytes")
                            return False
                    else:
                        download_logger.warning(f"Response too small ({len(content)} bytes), likely an error")
                        # Log the actual response for debugging
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
                        download_logger.debug(f"Error response body: {response_text[:500]}")

                        # Handle specific error responses
                        if response.status == 500:
                            download_logger.info("HTTP 500 error - server may be overloaded, will retry with different strategy")
                        elif response.status == 429:
                            download_logger.info("HTTP 429 - Rate limited, will increase delays")
                            # Increase minimum request interval
                            self.min_request_interval = min(5.0, self.min_request_interval * 2)
                        elif response.status >= 400:
                            download_logger.info(f"HTTP {response.status} error - API may be having issues")
                    except:
                        pass
                    return False

            except Exception as e:
                download_logger.error(f"Download request exception: {e}")
                return False
            finally:
                await browser.close()

api_client = SpotDownAPI()

# --- BOT HANDLERS ---

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [InlineKeyboardButton("➕ Add Playlist", callback_data='add_playlist_prompt')],
        [InlineKeyboardButton("📚 My Playlists", callback_data='list_playlists_0')],
        [InlineKeyboardButton("⚙️ Settings", callback_data='show_settings'), InlineKeyboardButton("🔄 Manual Sync", callback_data='manual_sync')]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text('👋 Welcome! Use the menu to manage your playlists.', reply_markup=reply_markup)

async def handle_playlist_url(update: Update, context: ContextTypes.DEFAULT_TYPE):
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

async def handle_playlist_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    playlist_name = update.message.text
    context.user_data['playlist_info']['name'] = sanitize_filename(playlist_name)

    # Confirm download
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
                await update.callback_query.edit_message_text(
                    f"⚠️ Failed to download {song_title} (Attempt {attempt + 1}/{MAX_DOWNLOAD_ATTEMPTS}). Retrying in {RETRY_DELAY_SECONDS}s...",
                )
                await asyncio.sleep(RETRY_DELAY_SECONDS)

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
    for pl_id, data in db.items():
        playlist_name = data.get('name', 'Unknown')
        song_count = len(data.get('songs', []))
        playlist_url = data.get('url', '')

        text += f"📁 `{playlist_name}` ({song_count} songs)\n"

        # Create buttons row with Update, Link, and Delete
        buttons_row = [
            InlineKeyboardButton("🔄 Update", callback_data=f"update_{pl_id}"),
        ]

        # Add link button if URL exists
        if playlist_url:
            buttons_row.append(InlineKeyboardButton("🔗 Link", url=playlist_url))

        buttons_row.append(InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_{pl_id}"))

        keyboard.append(buttons_row)

        # Add integrity check and song management buttons
        integrity_row = [
            InlineKeyboardButton("🔍 Check Integrity", callback_data=f"check_integrity_{pl_id}"),
            InlineKeyboardButton("📋 Songs", callback_data=f"list_songs_{pl_id}")
        ]
        keyboard.append(integrity_row)

    # Add global integrity check button
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

    if not playlist_url:
        await update.callback_query.edit_message_text("❌ No URL found for this playlist.")
        return

    await update.callback_query.edit_message_text(f"🔄 Updating playlist '{playlist_name}'...")

    try:
        # Get current online playlist data
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
                success = await api_client.download_song(song, file_path)
                if success:
                    break
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
    db = load_db()
    if playlist_id not in db: return
    playlist_path = Path(db[playlist_id]['path'])
    if playlist_path.exists():
        for file in playlist_path.iterdir(): file.unlink()
        playlist_path.rmdir()
    del db[playlist_id]
    save_db(db)
    await update.callback_query.edit_message_text(f"🗑️ Playlist deleted.")
    await asyncio.sleep(1)
    await list_playlists(update, context)

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

Configure when the bot should automatically check for new songs in your saved playlists."""

    keyboard = [
        [InlineKeyboardButton("🔄 Toggle Auto Sync", callback_data='toggle_sync')],
        [InlineKeyboardButton("📅 Change Day", callback_data='change_sync_day')],
        [InlineKeyboardButton("⏰ Change Time", callback_data='change_sync_time')],
        [InlineKeyboardButton("🔔 Toggle Notifications", callback_data='toggle_notifications')],
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

*Playlists synced:* {result['synced']}/{result['total']}
*New songs found:* {result['new_songs']}
*Errors:* {result['errors']}"""
        else:
            message = "❌ Sync failed. Check logs for details."

    except Exception as e:
        logger.error(f"Manual sync error: {e}")
        message = f"❌ Sync error: {str(e)}"

    keyboard = [[InlineKeyboardButton("⬅️ Back to Menu", callback_data='main_menu')]]
    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
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
        message += f"▪️ Corrupted songs: {len(result['corrupted_songs'])}\n"
        message += f"▪️ Missing songs: {len(result['missing_songs'])}\n"

        keyboard = []

        if result['corrupted_songs'] or result['missing_songs']:
            # Show fix option if there are issues
            keyboard.append([InlineKeyboardButton("🔧 Fix Issues", callback_data=f"fix_integrity_{playlist_id}")])

            # Show details of corrupted/missing songs
            if result['corrupted_songs']:
                message += f"\n⚠️ *Corrupted songs:*\n"
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
        message += f"▪️ Corrupted songs: {total_corrupted}\n"
        message += f"▪️ Missing songs: {total_missing}\n"

        if playlists_with_issues:
            message += f"\n⚠️ *Playlists with issues:*\n"
            for playlist in playlists_with_issues[:10]:  # Show first 10
                message += f"▪️ {playlist['name']}: {playlist['corrupted']} corrupted, {playlist['missing']} missing\n"

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
            f"📋 *{playlist_name}*\n\nNo songs found.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data='list_playlists_0')]]),
            parse_mode=ParseMode.MARKDOWN
        )
        return

    # Pagination settings
    songs_per_page = 10
    total_pages = (len(songs) + songs_per_page - 1) // songs_per_page
    start_idx = page * songs_per_page
    end_idx = min(start_idx + songs_per_page, len(songs))

    message = f"📋 *{playlist_name}*\n"
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

        message += f"{i+1}. {status_icon} *{artist_name}* - {song_title} ({duration})\n"

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

    await update.callback_query.edit_message_text(
        message,
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN
    )

async def delete_song(update: Update, context: ContextTypes.DEFAULT_TYPE, playlist_id: str, song_index: int):
    """Delete a specific song from playlist and filesystem"""
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
    song_title = song.get('title', 'Unknown')
    artist_name = song.get('artist', 'Unknown')

    # Show confirmation
    message = f"🗑️ *Delete Song*\n\n"
    message += f"Playlist: {playlist_name}\n"
    message += f"Song: {artist_name} - {song_title}\n\n"
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
    song_title = sanitize_filename(song.get('title', 'Unknown'))
    artist_name = sanitize_filename(song.get('artist', 'Unknown'))

    try:
        # Delete file from filesystem
        playlist_dir = MUSIC_DIR / playlist_name
        file_path = playlist_dir / f"{artist_name} - {song_title}.mp3"

        if file_path.exists():
            file_path.unlink()
            logger.info(f"Deleted file: {file_path}")

        # Remove from database
        del songs[song_index]
        playlist_data['songs'] = songs
        db[playlist_id] = playlist_data
        save_db(db)

        await update.callback_query.edit_message_text(
            f"✅ Song deleted successfully!\n\n{artist_name} - {song.get('title', 'Unknown')} has been removed from '{playlist_name}'.",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back to Songs", callback_data=f"list_songs_{playlist_id}")]])
        )

    except Exception as e:
        logger.error(f"Error deleting song: {e}")
        await update.callback_query.edit_message_text(
            f"❌ Error deleting song: {str(e)}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("⬅️ Back", callback_data=f"list_songs_{playlist_id}")]])
        )

# --- STATE AND BUTTON HANDLERS ---

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data

    if data == 'add_playlist_prompt':
        await query.edit_message_text("Send me the Spotify playlist URL.")
        context.user_data['state'] = 'awaiting_url'
    elif data == 'list_playlists_0':  # Assuming simple pagination for now
        await list_playlists(update, context)
    elif data == 'show_settings':
        await show_settings(update, context)
    elif data == 'toggle_sync':
        await toggle_sync(update, context)
    elif data == 'toggle_notifications':
        await toggle_notifications(update, context)
    elif data == 'change_sync_day':
        await change_sync_day(update, context)
    elif data == 'change_sync_time':
        await change_sync_time(update, context)
    elif data.startswith('set_day_'):
        day = data.split('set_day_')[1]
        await set_sync_day(update, context, day)
    elif data == 'manual_sync':
        await manual_sync(update, context)
    elif data == 'use_suggested_name':
        info = context.user_data['playlist_info']
        info['name'] = sanitize_filename(info['suggested_name'])
        await confirm_download_prompt(update, context)
    elif data == 'confirm_download':
        await perform_download(update, context)
    elif data.startswith('update_'):
        await perform_update(update, context, data.split('_')[1])
    elif data.startswith('download_new_'):
        await download_new_songs(update, context, data.split('download_new_')[1])
    elif data.startswith('delete_'):
        await perform_delete(update, context, data.split('_')[1])
    elif data.startswith('check_integrity_'):
        await perform_integrity_check(update, context, data.split('check_integrity_')[1])
    elif data.startswith('fix_integrity_'):
        await fix_integrity_issues(update, context, data.split('fix_integrity_')[1])
    elif data == 'check_all_integrity':
        await check_all_playlists_integrity(update, context)
    elif data.startswith('list_songs_'):
        await list_playlist_songs(update, context, data.split('list_songs_')[1])
    elif data.startswith('songs_page_'):
        parts = data.split('_')
        playlist_id = parts[2]
        page = int(parts[3])
        await list_playlist_songs(update, context, playlist_id, page)
    elif data.startswith('delete_song_'):
        parts = data.split('_')
        playlist_id = parts[2]
        song_index = int(parts[3])
        await delete_song(update, context, playlist_id, song_index)
    elif data.startswith('confirm_delete_song_'):
        parts = data.split('_')
        playlist_id = parts[3]
        song_index = int(parts[4])
        await confirm_delete_song(update, context, playlist_id, song_index)
    # ... (other handlers like update)
    elif data in ('cancel_action', 'main_menu'):
        context.user_data.clear()
        # Show main menu directly instead of calling start
        keyboard = [
            [InlineKeyboardButton("➕ Add Playlist", callback_data='add_playlist_prompt')],
            [InlineKeyboardButton("📚 My Playlists", callback_data='list_playlists_0')],
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
