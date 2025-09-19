#!/usr/bin/env python3
"""
TubetifyConverter - Spotify to YouTube URL converter using tubetify.com
Used to convert Spotify track URLs to YouTube URLs for download
"""

import asyncio
import aiohttp
import re
import logging
from pathlib import Path
from typing import Optional, List, Dict, Any
from urllib.parse import quote, unquote
from bs4 import BeautifulSoup

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('tubetify_converter')

class TubetifyConverter:
    """
    Converts Spotify track URLs to YouTube URLs using tubetify.com
    """

    def __init__(self):
        self.base_url = "https://tubetify.com"
        self.session_id = None
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0',
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'DNT': '1',
            'Sec-GPC': '1',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-User': '?1',
            'Priority': 'u=0, i'
        }

    def sanitize_spotify_url(self, spotify_url: str) -> str:
        """
        Remove query parameters from Spotify URL (everything after ?)

        Args:
            spotify_url: Original Spotify URL

        Returns:
            Sanitized URL without query parameters
        """
        # Remove everything from ? onwards (including ?si=...)
        if '?' in spotify_url:
            spotify_url = spotify_url.split('?')[0]

        logger.debug(f"Sanitized URL: {spotify_url}")
        return spotify_url

    async def get_session(self) -> Optional[str]:
        """Get session ID from tubetify.com"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/convert",
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        # Extract PHPSESSID from cookies
                        cookies = response.cookies
                        if 'PHPSESSID' in cookies:
                            session_id = cookies['PHPSESSID'].value
                            logger.debug(f"Session ID obtained: {session_id}")
                            return session_id
                        else:
                            logger.warning("No PHPSESSID found in response cookies")
                            return None
                    else:
                        logger.error(f"Failed to get session, status: {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error getting session: {e}")
            return None

    async def convert_spotify_to_youtube(self, spotify_url: str) -> List[Dict[str, Any]]:
        """
        Convert Spotify URL to YouTube URLs using tubetify.com

        Args:
            spotify_url: Spotify track URL

        Returns:
            List of dictionaries with YouTube video information
        """
        logger.info(f"🔄 Converting Spotify to YouTube: {spotify_url}")

        try:
            # Sanitize the Spotify URL
            clean_spotify_url = self.sanitize_spotify_url(spotify_url)

            # Get session ID
            session_id = await self.get_session()
            if not session_id:
                logger.error("Failed to get session ID")
                return []

            # Prepare form data
            form_data = {
                'spotify-tracks': clean_spotify_url,
                'spotify-tracks-send': 'Converting, Please Wait...'
            }

            # Prepare headers for POST request
            post_headers = self.headers.copy()
            post_headers.update({
                'Content-Type': 'application/x-www-form-urlencoded',
                'Origin': 'https://tubetify.com',
                'Referer': 'https://tubetify.com/',
                'Cookie': f'PHPSESSID={session_id}'
            })

            async with aiohttp.ClientSession() as session:
                async with session.post(
                    f"{self.base_url}/generate",
                    headers=post_headers,
                    data=form_data,
                    timeout=aiohttp.ClientTimeout(total=30)
                ) as response:
                    if response.status == 200:
                        html_content = await response.text()
                        youtube_videos = self.parse_youtube_results(html_content)

                        if youtube_videos:
                            logger.info(f"✅ Found {len(youtube_videos)} YouTube video(s)")
                            return youtube_videos
                        else:
                            logger.warning("No YouTube videos found in response")
                            return []
                    else:
                        logger.error(f"Conversion request failed with status: {response.status}")
                        response_text = await response.text()
                        logger.debug(f"Response content: {response_text[:500]}...")
                        return []

        except Exception as e:
            logger.error(f"Error converting Spotify to YouTube: {e}")
            return []

    def parse_youtube_results(self, html_content: str) -> List[Dict[str, Any]]:
        """
        Parse HTML response to extract YouTube video information

        Args:
            html_content: HTML response from tubetify.com

        Returns:
            List of dictionaries with video information
        """
        try:
            soup = BeautifulSoup(html_content, 'html.parser')
            videos = []

            # Find all table rows with video information
            table_rows = soup.find_all('tr')

            for row in table_rows:
                # Look for YouTube links in the format <a href="https://youtu.be/VIDEO_ID/">
                youtube_link = row.find('a', href=re.compile(r'https://youtu\.be/([^/]+)/'))

                if youtube_link:
                    href = youtube_link.get('href')
                    title = youtube_link.get('title', '')
                    video_id = youtube_link.text.strip('#') if youtube_link.text else ''

                    # Extract video info from the row
                    video_info = self.extract_video_info(row)

                    video_data = {
                        'youtube_url': href,
                        'youtube_id': video_id,
                        'title': title,
                        'spotify_track': video_info.get('spotify_track', ''),
                        'video_found': video_info.get('video_found', ''),
                        'thumbnail': video_info.get('thumbnail', '')
                    }

                    videos.append(video_data)
                    logger.debug(f"Found video: {video_id} - {title[:50]}...")

            return videos

        except Exception as e:
            logger.error(f"Error parsing YouTube results: {e}")
            return []

    def extract_video_info(self, row) -> Dict[str, str]:
        """
        Extract video information from a table row

        Args:
            row: BeautifulSoup table row element

        Returns:
            Dictionary with video information
        """
        info = {
            'spotify_track': '',
            'video_found': '',
            'thumbnail': ''
        }

        try:
            # Extract thumbnail
            img_tag = row.find('img')
            if img_tag:
                info['thumbnail'] = img_tag.get('src', '')

            # Extract track information from the list items
            list_items = row.find_all('li')
            for li in list_items:
                text = li.get_text(strip=True)
                if 'Spotify Track:' in text:
                    info['spotify_track'] = text.replace('Spotify Track:', '').strip()
                elif 'Video Found:' in text:
                    # Extract the bold text which contains the actual video title
                    strong_tag = li.find('strong')
                    if strong_tag:
                        info['video_found'] = strong_tag.get_text(strip=True)

        except Exception as e:
            logger.error(f"Error extracting video info: {e}")

        return info

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
            logger.info(f"Best match: {best_match['youtube_url']}")
            return best_match['youtube_url']
        else:
            logger.warning("No YouTube matches found")
            return None

# Convenience functions for integration
async def spotify_to_youtube(spotify_url: str) -> List[Dict[str, Any]]:
    """
    Convert Spotify URL to YouTube videos

    Args:
        spotify_url: Spotify track URL

    Returns:
        List of YouTube video options
    """
    converter = TubetifyConverter()
    return await converter.convert_spotify_to_youtube(spotify_url)

async def get_youtube_for_spotify(spotify_url: str) -> Optional[str]:
    """
    Get best YouTube URL for a Spotify track

    Args:
        spotify_url: Spotify track URL

    Returns:
        YouTube URL or None
    """
    converter = TubetifyConverter()
    return await converter.get_best_match(spotify_url)

# Test function
async def test_tubetify_converter():
    """Test the tubetify converter"""
    test_url = "https://open.spotify.com/track/0AAMnNeIc6CdnfNU85GwCH?si=748d7493a8ec476a"

    print("🧪 Testing Tubetify Converter...")
    print(f"Original URL: {test_url}")

    converter = TubetifyConverter()

    # Test sanitization
    clean_url = converter.sanitize_spotify_url(test_url)
    print(f"Sanitized URL: {clean_url}")

    # Test conversion
    videos = await converter.convert_spotify_to_youtube(test_url)

    if videos:
        print(f"✅ Found {len(videos)} video(s):")
        for i, video in enumerate(videos, 1):
            print(f"   {i}. {video['youtube_id']} - {video['video_found']}")
            print(f"      URL: {video['youtube_url']}")
            print(f"      Spotify: {video['spotify_track']}")
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
    asyncio.run(test_tubetify_converter())