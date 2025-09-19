#!/usr/bin/env python3
"""
CustomConverter - Self-hosted Spotify to YouTube URL converter

Based on the excellent yt2spotify project: https://github.com/omijn/yt2spotify
Original work by @omijn - adapted for Telegram bot integration with additional
async support, error handling, and fallback mechanisms.

Credits:
- Original yt2spotify project: https://github.com/omijn/yt2spotify
- Author: @omijn (https://github.com/omijn)
- License: Check original repository for licensing information

Modifications for SpotiDL bot:
- Added async/await support for bot integration
- Enhanced error handling and fallback mechanisms
- Web scraping fallback when API credentials unavailable
- Simplified interface for single-track conversion
- Integration with ezconv downloader
"""

import asyncio
import re
import logging
import os
from typing import Optional, List, Dict, Any
from urllib.parse import quote_plus
import aiohttp
from bs4 import BeautifulSoup

# Load environment variables from .env file if it exists
def load_env_file():
    """Load environment variables from .env file"""
    env_file = os.path.join(os.path.dirname(__file__), '.env')
    if os.path.exists(env_file):
        try:
            with open(env_file, 'r') as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith('#') and '=' in line:
                        key, value = line.split('=', 1)
                        os.environ[key.strip()] = value.strip()
        except Exception as e:
            logger.warning(f"Error loading .env file: {e}")

# Load environment variables at module import
load_env_file()

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('custom_converter')

class CustomConverter:
    """
    Self-hosted Spotify to YouTube URL converter
    Uses spotipy for Spotify track info and ytmusicapi for YouTube search
    """

    def __init__(self):
        self.spotify_pattern = re.compile(r'(?:https://)?open\.spotify\.com/(track|artist|album)/.+')
        self.spotipy_client = None
        self.ytmusic_client = None
        self._init_clients()

    def _init_clients(self):
        """Initialize Spotify and YouTube Music clients if dependencies are available"""
        try:
            # Try to initialize Spotify client
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials

            # Use environment variables if available, otherwise use default/anonymous mode
            client_id = os.environ.get('SPOTIPY_CLIENT_ID')
            client_secret = os.environ.get('SPOTIPY_CLIENT_SECRET')

            if client_id and client_secret:
                client_credentials_manager = SpotifyClientCredentials(
                    client_id=client_id,
                    client_secret=client_secret
                )
                self.spotipy_client = spotipy.Spotify(client_credentials_manager=client_credentials_manager)
                logger.info("✅ Spotify client initialized with credentials")
            else:
                # Try anonymous mode (limited functionality)
                self.spotipy_client = spotipy.Spotify()
                logger.warning("⚠️  Spotify client initialized without credentials (limited functionality)")

        except ImportError:
            logger.warning("⚠️  spotipy not available - install with: pip install spotipy")
        except Exception as e:
            logger.error(f"Error initializing Spotify client: {e}")

        try:
            # Try to initialize YouTube Music client
            from ytmusicapi import YTMusic

            # Check if browser JSON is provided
            browser_json = os.environ.get('YOUTUBE_MUSIC_BROWSER_JSON')
            if browser_json:
                self.ytmusic_client = YTMusic(browser_json)
                logger.info("✅ YouTube Music client initialized with browser auth")
            else:
                # Try without authentication (public data only)
                self.ytmusic_client = YTMusic()
                logger.info("✅ YouTube Music client initialized (public mode)")

        except ImportError:
            logger.warning("⚠️  ytmusicapi not available - install with: pip install ytmusicapi")
        except Exception as e:
            logger.error(f"Error initializing YouTube Music client: {e}")

    def is_spotify_url(self, url: str) -> bool:
        """Check if URL is a valid Spotify URL"""
        return bool(self.spotify_pattern.match(url))

    async def extract_spotify_track_info(self, spotify_url: str) -> Optional[Dict[str, str]]:
        """Extract track information from Spotify URL using API or web scraping fallback"""
        if not self.is_spotify_url(spotify_url):
            return None

        # Try API first if available
        if self.spotipy_client:
            try:
                result = self._extract_with_api(spotify_url)
                if result:
                    return result
                else:
                    logger.warning("API returned no results, trying web scraping...")
            except Exception as e:
                logger.warning(f"API extraction failed: {e}, trying web scraping...")

        # Fallback to web scraping
        logger.info("Using web scraping to extract Spotify track info...")
        return await self._extract_with_scraping(spotify_url)

    def _extract_with_api(self, spotify_url: str) -> Optional[Dict[str, str]]:
        """Extract track info using Spotify API"""
        try:
            # Clean URL (remove query parameters)
            clean_url = spotify_url.split('?')[0]

            # Extract URL type
            url_type = self.spotify_pattern.findall(clean_url)[0]

            if url_type == "track":
                track_info = self.spotipy_client.track(clean_url)
                return {
                    'name': track_info['name'],
                    'artist': track_info['artists'][0]['name'],
                    'album': track_info['album']['name'],
                    'type': 'track'
                }
            elif url_type == "artist":
                artist_info = self.spotipy_client.artist(clean_url)
                return {
                    'name': artist_info['name'],
                    'artist': artist_info['name'],
                    'type': 'artist'
                }
            elif url_type == "album":
                album_info = self.spotipy_client.album(clean_url)
                return {
                    'name': album_info['name'],
                    'artist': album_info['artists'][0]['name'],
                    'album': album_info['name'],
                    'type': 'album'
                }
        except Exception as e:
            logger.error(f"API extraction error: {e}")
            return None

    async def _extract_with_scraping(self, spotify_url: str) -> Optional[Dict[str, str]]:
        """Extract track info using web scraping as fallback"""
        try:
            # Clean URL (remove query parameters)
            clean_url = spotify_url.split('?')[0]

            headers = {
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'DNT': '1',
                'Sec-GPC': '1',
                'Upgrade-Insecure-Requests': '1'
            }

            async with aiohttp.ClientSession() as session:
                async with session.get(clean_url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        html_content = await response.text()
                        return self._parse_spotify_page(html_content, clean_url)
                    else:
                        logger.error(f"Failed to fetch Spotify page: {response.status}")
                        return None

        except Exception as e:
            logger.error(f"Web scraping error: {e}")
            return None

    def _parse_spotify_page(self, html_content: str, url: str) -> Optional[Dict[str, str]]:
        """Parse Spotify page HTML to extract track information"""
        try:
            soup = BeautifulSoup(html_content, 'html.parser')

            # Extract URL type
            url_type = self.spotify_pattern.findall(url)[0]

            if url_type == "track":
                # Try multiple methods to extract track info
                track_name = 'Unknown'
                artist_name = 'Unknown'

                # Method 1: Try og:title meta tag
                title_tag = soup.find('meta', property='og:title')
                if title_tag:
                    title_content = title_tag.get('content', '')
                    logger.debug(f"og:title: {title_content}")
                    # Usually in format "Song Title - song by Artist | Spotify"
                    if ' - song by ' in title_content:
                        parts = title_content.split(' - song by ')
                        track_name = parts[0].strip()
                        artist_name = parts[1].split(' | ')[0].strip()
                    elif ' by ' in title_content and ' | Spotify' in title_content:
                        parts = title_content.split(' by ')
                        track_name = parts[0].strip()
                        artist_name = parts[1].replace(' | Spotify', '').strip()

                # Method 2: Try page title as fallback
                if track_name == 'Unknown':
                    title_tag = soup.find('title')
                    if title_tag:
                        title_content = title_tag.text
                        logger.debug(f"page title: {title_content}")
                        if ' | Spotify' in title_content and title_content != 'Spotify – Web Player':
                            clean_title = title_content.replace(' | Spotify', '').strip()
                            if ' - ' in clean_title:
                                parts = clean_title.split(' - ', 1)
                                track_name = parts[0].strip()
                                artist_name = parts[1].strip()

                # Method 3: Try JSON-LD structured data
                if track_name == 'Unknown':
                    json_scripts = soup.find_all('script', type='application/ld+json')
                    for script in json_scripts:
                        try:
                            import json
                            data = json.loads(script.string)
                            if isinstance(data, dict) and data.get('@type') == 'MusicRecording':
                                track_name = data.get('name', 'Unknown')
                                if data.get('byArtist') and isinstance(data['byArtist'], dict):
                                    artist_name = data['byArtist'].get('name', 'Unknown')
                                break
                        except:
                            continue

                # If we got something useful, return it
                if track_name != 'Unknown' and track_name != 'Spotify – Web Player':
                    return {
                        'name': track_name,
                        'artist': artist_name,
                        'album': 'Unknown',  # Hard to extract from scraping
                        'type': 'track'
                    }

            # If we couldn't extract proper info, try to extract track ID and use a simple search
            track_id_match = re.search(r'/track/([a-zA-Z0-9]+)', url)
            if track_id_match:
                track_id = track_id_match.group(1)
                # Use track ID as a last resort search term
                logger.warning(f"Could not parse Spotify page for {url_type}, using track ID for search")
                return {
                    'name': track_id,  # Use track ID as search term
                    'artist': '',
                    'album': 'Unknown',
                    'type': 'track'
                }

            logger.warning(f"Could not parse Spotify page for {url_type}")
            return None

        except Exception as e:
            logger.error(f"Error parsing Spotify page: {e}")
            return None

    def search_youtube_music(self, track_info: Dict[str, str], limit: int = 10) -> List[Dict[str, Any]]:
        """Search YouTube Music for matching tracks"""
        if not self.ytmusic_client or not track_info:
            return []

        try:
            # Build search query
            if track_info['type'] == 'track':
                search_query = f"{track_info['name']} {track_info['artist']}"
                search_filter = "songs"
            elif track_info['type'] == 'artist':
                search_query = track_info['artist']
                search_filter = "artists"
            elif track_info['type'] == 'album':
                search_query = f"{track_info['album']} {track_info['artist']}"
                search_filter = "albums"
            else:
                return []

            logger.info(f"🔍 Searching YouTube Music: '{search_query}'")

            # Search YouTube Music
            results = self.ytmusic_client.search(search_query, filter=search_filter, limit=limit)

            videos = []
            for item in results:
                if track_info['type'] == 'track' and 'videoId' in item:
                    # Convert YouTube Music URL to standard YouTube URL
                    youtube_url = f"https://youtu.be/{item['videoId']}"
                    video_data = {
                        'youtube_url': youtube_url,
                        'youtube_id': item['videoId'],
                        'title': item.get('title', 'Unknown'),
                        'artist': ', '.join([artist['name'] for artist in item.get('artists', [])]) if item.get('artists') else 'Unknown',
                        'album': item.get('album', {}).get('name', 'Unknown') if item.get('album') else 'Unknown',
                        'duration': item.get('duration', 'Unknown'),
                        'thumbnail': item.get('thumbnails', [{}])[-1].get('url', '') if item.get('thumbnails') else '',
                        'source': 'youtube_music'
                    }
                    videos.append(video_data)
                elif track_info['type'] == 'artist' and 'browseId' in item:
                    # Artist result
                    video_data = {
                        'youtube_url': f"https://music.youtube.com/channel/{item['browseId']}",
                        'youtube_id': item['browseId'],
                        'title': item.get('artist', 'Unknown'),
                        'artist': item.get('artist', 'Unknown'),
                        'type': 'artist',
                        'thumbnail': item.get('thumbnails', [{}])[-1].get('url', '') if item.get('thumbnails') else '',
                        'source': 'youtube_music'
                    }
                    videos.append(video_data)
                elif track_info['type'] == 'album' and 'browseId' in item:
                    # Album result
                    video_data = {
                        'youtube_url': f"https://music.youtube.com/browse/{item['browseId']}",
                        'youtube_id': item['browseId'],
                        'title': item.get('title', 'Unknown'),
                        'artist': ', '.join([artist['name'] for artist in item.get('artists', [])]) if item.get('artists') else 'Unknown',
                        'year': item.get('year', 'Unknown'),
                        'type': 'album',
                        'thumbnail': item.get('thumbnails', [{}])[-1].get('url', '') if item.get('thumbnails') else '',
                        'source': 'youtube_music'
                    }
                    videos.append(video_data)

            logger.info(f"✅ Found {len(videos)} YouTube Music result(s)")
            return videos

        except Exception as e:
            logger.error(f"Error searching YouTube Music: {e}")
            return []

    async def convert_spotify_to_youtube(self, spotify_url: str) -> List[Dict[str, Any]]:
        """
        Convert Spotify URL to YouTube URLs using custom implementation

        Args:
            spotify_url: Spotify track/artist/album URL

        Returns:
            List of YouTube video options with metadata
        """
        logger.info(f"🔄 Converting Spotify to YouTube (custom): {spotify_url}")

        try:
            # Step 1: Extract Spotify track information
            track_info = await self.extract_spotify_track_info(spotify_url)
            if not track_info:
                logger.warning("Failed to extract Spotify track information")
                return []

            logger.info(f"📋 Track info: {track_info['name']} by {track_info['artist']}")

            # Step 2: Search YouTube Music for matches
            youtube_videos = self.search_youtube_music(track_info, limit=10)

            if youtube_videos:
                logger.info(f"✅ Found {len(youtube_videos)} YouTube match(es) using custom converter")
                return youtube_videos
            else:
                logger.warning("No YouTube matches found using custom converter")
                return []

        except Exception as e:
            logger.error(f"Error in custom Spotify→YouTube conversion: {e}")
            return []

    async def get_best_match(self, spotify_url: str) -> Optional[str]:
        """
        Get the best YouTube match for a Spotify URL (first result)

        Args:
            spotify_url: Spotify track URL

        Returns:
            YouTube URL of the best match, or None if no match found
        """
        videos = await self.convert_spotify_to_youtube(spotify_url)

        if videos:
            best_match = videos[0]  # Take the first result as best match
            logger.info(f"🎯 Best match: {best_match['youtube_url']}")
            return best_match['youtube_url']
        else:
            logger.warning("No YouTube matches found")
            return None

# Convenience functions for integration
async def spotify_to_youtube_custom(spotify_url: str) -> List[Dict[str, Any]]:
    """
    Convert Spotify URL to YouTube videos using custom converter

    Args:
        spotify_url: Spotify track URL

    Returns:
        List of YouTube video options
    """
    converter = CustomConverter()
    return await converter.convert_spotify_to_youtube(spotify_url)

async def get_youtube_for_spotify_custom(spotify_url: str) -> Optional[str]:
    """
    Get best YouTube URL for a Spotify track using custom converter

    Args:
        spotify_url: Spotify track URL

    Returns:
        YouTube URL or None
    """
    converter = CustomConverter()
    return await converter.get_best_match(spotify_url)

def check_dependencies() -> Dict[str, bool]:
    """
    Check if required dependencies are available

    Returns:
        Dict with dependency availability status
    """
    deps = {
        'spotipy': False,
        'ytmusicapi': False
    }

    try:
        import spotipy
        deps['spotipy'] = True
    except ImportError:
        pass

    try:
        from ytmusicapi import YTMusic
        deps['ytmusicapi'] = True
    except ImportError:
        pass

    return deps

# Test function
async def test_custom_converter():
    """Test the custom converter with a sample Spotify URL"""
    test_url = "https://open.spotify.com/track/0AAMnNeIc6CdnfNU85GwCH?si=748d7493a8ec476a"

    print("🧪 Testing Custom Converter...")
    print(f"Original URL: {test_url}")

    # Check dependencies
    deps = check_dependencies()
    print(f"Dependencies: {deps}")

    if not all(deps.values()):
        print("❌ Missing dependencies. Install with:")
        if not deps['spotipy']:
            print("   pip install spotipy")
        if not deps['ytmusicapi']:
            print("   pip install ytmusicapi")
        return False

    converter = CustomConverter()

    # Test conversion
    videos = await converter.convert_spotify_to_youtube(test_url)

    if videos:
        print(f"✅ Found {len(videos)} video(s):")
        for i, video in enumerate(videos[:5], 1):  # Show first 5
            print(f"   {i}. {video['youtube_id']} - {video['title']}")
            print(f"      URL: {video['youtube_url']}")
            print(f"      Artist: {video['artist']}")
            print()

        # Test best match
        best_match = await converter.get_best_match(test_url)
        print(f"🎯 Best match: {best_match}")

        return True
    else:
        print("❌ No videos found")
        return False

if __name__ == "__main__":
    # Test the converter
    asyncio.run(test_custom_converter())