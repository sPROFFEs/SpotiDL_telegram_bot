#!/usr/bin/env python3
"""
EzconvDownloader - YouTube downloader using ezconv.com API
Replaces SpotDL for YouTube URL downloads
"""

import asyncio
import aiohttp
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any
import re

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('ezconv_downloader')

class EzconvDownloader:
    """
    YouTube downloader using ezconv.com API
    Follows the flow: /api/country -> /api/token -> /api/convert -> download
    """

    def __init__(self):
        self.base_url = "https://ezconv.com"
        self.api_url = "https://ds4.ezsrv.net"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0',
            'Accept': '*/*',
            'Accept-Language': 'en-US,en;q=0.5',
            'Accept-Encoding': 'gzip, deflate, br',
            'Referer': 'https://ezconv.com/',
            'Sec-Fetch-Dest': 'empty',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Site': 'same-origin',
            'DNT': '1',
            'Sec-GPC': '1',
            'Priority': 'u=4'
        }

    async def _make_request_with_ssl_fallback(self, method: str, url: str, **kwargs):
        """Make HTTP request with SSL fallback if needed"""
        try:
            # First try with default SSL settings
            async with aiohttp.ClientSession() as session:
                async with session.request(method, url, **kwargs) as response:
                    return response, await response.text() if response.content_type.startswith('text') else await response.read()
        except aiohttp.ClientSSLError as e:
            logger.warning(f"SSL error for {url}: {e}")
            logger.info("Attempting request with SSL verification disabled...")
            try:
                # Fallback: Try with SSL verification disabled
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.request(method, url, **kwargs) as response:
                        return response, await response.text() if response.content_type.startswith('text') else await response.read()
            except Exception as fallback_e:
                logger.error(f"SSL fallback also failed for {url}: {fallback_e}")
                raise fallback_e
        except Exception as e:
            logger.error(f"Request failed for {url}: {e}")
            raise e

    async def get_country(self) -> Optional[str]:
        """Get country information from ezconv API"""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    f"{self.base_url}/api/country",
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        country = data.get('country', 'US')
                        logger.debug(f"Country detected: {country}")
                        return country
                    else:
                        logger.warning(f"Country API returned status {response.status}")
                        return 'US'  # Default fallback
        except Exception as e:
            logger.error(f"Error getting country: {e}")
            return 'US'  # Default fallback

    async def get_token(self) -> Optional[str]:
        """Get authentication token from ezconv API"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = self.headers.copy()
                headers.update({
                    'Origin': 'https://ezconv.com',
                    'Content-Length': '0'
                })

                async with session.post(
                    f"{self.base_url}/api/token",
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10)
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        token = data.get('token')
                        if token:
                            logger.debug("Token obtained successfully")
                            return token
                        else:
                            logger.error("No token in response")
                            return None
                    else:
                        logger.error(f"Token API returned status {response.status}")
                        return None
        except Exception as e:
            logger.error(f"Error getting token: {e}")
            return None

    async def convert_video(self, youtube_url: str, token: str) -> Optional[Dict[str, Any]]:
        """Convert YouTube video using ezconv API"""
        try:
            async with aiohttp.ClientSession() as session:
                headers = {
                    'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64; rv:143.0) Gecko/20100101 Firefox/143.0',
                    'Accept': '*/*',
                    'Accept-Language': 'en-US,en;q=0.5',
                    'Accept-Encoding': 'gzip, deflate, br',
                    'Content-Type': 'application/json',
                    'Origin': 'https://ezconv.com',
                    'Sec-Fetch-Dest': 'empty',
                    'Sec-Fetch-Mode': 'cors',
                    'Sec-Fetch-Site': 'cross-site',
                    'DNT': '1',
                    'Sec-GPC': '1',
                    'Priority': 'u=0',
                    'Connection': 'keep-alive'
                }

                payload = {
                    "url": youtube_url,
                    "quality": "320",  # 320kbps MP3
                    "trim": False,
                    "startT": 0,
                    "endT": 0,
                    "token": token
                }

                async with session.post(
                    f"{self.api_url}/api/convert",
                    headers=headers,
                    json=payload,
                    timeout=aiohttp.ClientTimeout(total=60)  # Longer timeout for conversion
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        if data.get('status') == 'done':
                            logger.info(f"Conversion successful: {data.get('title', 'Unknown')}")
                            return data
                        else:
                            logger.error(f"Conversion failed: {data}")
                            return None
                    else:
                        logger.error(f"Convert API returned status {response.status}")
                        response_text = await response.text()
                        logger.error(f"Response: {response_text}")
                        return None
        except Exception as e:
            logger.error(f"Error converting video: {e}")
            return None

    async def download_audio(self, download_url: str, output_path: Path) -> bool:
        """Download audio file from the provided URL with SSL fallback"""
        # First try with default SSL settings
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    download_url,
                    timeout=aiohttp.ClientTimeout(total=300)  # 5 minutes for download
                ) as response:
                    if response.status == 200:
                        output_path.parent.mkdir(parents=True, exist_ok=True)

                        with open(output_path, 'wb') as f:
                            async for chunk in response.content.iter_chunked(8192):
                                f.write(chunk)

                        if output_path.exists() and output_path.stat().st_size > 0:
                            logger.info(f"Successfully downloaded: {output_path}")
                            return True
                        else:
                            logger.error("Downloaded file is empty or not created")
                            return False
                    else:
                        logger.error(f"Download failed with status {response.status}")
                        return False
        except aiohttp.ClientSSLError as e:
            logger.warning(f"SSL error encountered: {e}")
            logger.info("Attempting download with SSL verification disabled...")

            # Fallback: Try with SSL verification disabled
            try:
                connector = aiohttp.TCPConnector(ssl=False)
                async with aiohttp.ClientSession(connector=connector) as session:
                    async with session.get(
                        download_url,
                        timeout=aiohttp.ClientTimeout(total=300)
                    ) as response:
                        if response.status == 200:
                            output_path.parent.mkdir(parents=True, exist_ok=True)

                            with open(output_path, 'wb') as f:
                                async for chunk in response.content.iter_chunked(8192):
                                    f.write(chunk)

                            if output_path.exists() and output_path.stat().st_size > 0:
                                logger.info(f"Successfully downloaded with SSL fallback: {output_path}")
                                return True
                            else:
                                logger.error("Downloaded file is empty or not created (SSL fallback)")
                                return False
                        else:
                            logger.error(f"Download failed with status {response.status} (SSL fallback)")
                            return False
            except Exception as fallback_e:
                logger.error(f"SSL fallback also failed: {fallback_e}")
                return False
        except Exception as e:
            logger.error(f"Error downloading audio: {e}")
            return False

    async def download_from_youtube(self, youtube_url: str, output_path: Path) -> Optional[Dict[str, Any]]:
        """
        Complete YouTube download process using ezconv.com

        Args:
            youtube_url: YouTube video URL
            output_path: Path where to save the MP3 file

        Returns:
            Dict with download info including title, or None if failed
        """
        logger.info(f"🎵 Starting YouTube download: {youtube_url}")

        try:
            # Step 1: Get country (optional but follows the flow)
            country = await self.get_country()

            # Step 2: Get authentication token
            token = await self.get_token()
            if not token:
                logger.error("Failed to get authentication token")
                return None

            # Step 3: Convert video and get download info
            convert_result = await self.convert_video(youtube_url, token)
            if not convert_result:
                logger.error("Failed to convert video")
                return None

            download_url = convert_result.get('url')
            title = convert_result.get('title', 'Unknown Title')

            if not download_url:
                logger.error("No download URL in conversion result")
                return None

            # Step 4: Download the audio file
            success = await self.download_audio(download_url, output_path)

            if success:
                logger.info(f"✅ YouTube download completed: {title}")
                return {
                    'success': True,
                    'title': title,
                    'file_path': str(output_path),
                    'url': youtube_url
                }
            else:
                logger.error(f"Failed to download audio file")
                return None

        except Exception as e:
            logger.error(f"Error in YouTube download process: {e}")
            return None

    def is_youtube_url(self, url: str) -> bool:
        """Check if URL is a valid YouTube URL"""
        youtube_domains = [
            'youtube.com', 'youtu.be', 'www.youtube.com', 'm.youtube.com'
        ]
        return any(domain in url.lower() for domain in youtube_domains)

    def sanitize_filename(self, title: str) -> str:
        """Sanitize title for use as filename"""
        # Remove or replace invalid characters
        title = re.sub(r'[<>:"/\\|?*]', '', title)
        title = re.sub(r'[^\w\s-]', '', title)
        title = re.sub(r'[-\s]+', '-', title)
        return title.strip('-')[:100]  # Limit length

# Convenience functions for integration with existing bot
async def download_youtube_with_ezconv(youtube_url: str, output_path: Path) -> Optional[Dict[str, Any]]:
    """
    Convenience function to download from YouTube using ezconv

    Args:
        youtube_url: YouTube video URL
        output_path: Path where to save the MP3 file

    Returns:
        Dict with download info or None if failed
    """
    downloader = EzconvDownloader()
    return await downloader.download_from_youtube(youtube_url, output_path)

def is_youtube_url(url: str) -> bool:
    """Check if URL is a YouTube URL"""
    downloader = EzconvDownloader()
    return downloader.is_youtube_url(url)

# Test function
async def test_ezconv_downloader():
    """Test the ezconv downloader"""
    test_url = "https://youtu.be/1bAxYmFpIiU"  # Example YouTube URL
    test_output = Path("/tmp/test_ezconv_output.mp3")

    print("🧪 Testing Ezconv Downloader...")

    downloader = EzconvDownloader()

    if not downloader.is_youtube_url(test_url):
        print("❌ Invalid YouTube URL")
        return False

    result = await downloader.download_from_youtube(test_url, test_output)

    if result and result.get('success'):
        print(f"✅ Download successful!")
        print(f"📁 Title: {result['title']}")
        print(f"📂 File: {result['file_path']}")

        if test_output.exists():
            size = test_output.stat().st_size
            print(f"📊 Size: {size / (1024*1024):.1f} MB")

            # Clean up test file
            test_output.unlink()

        return True
    else:
        print("❌ Download failed")
        return False

if __name__ == "__main__":
    # Test the downloader
    asyncio.run(test_ezconv_downloader())