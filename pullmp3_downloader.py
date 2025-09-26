#!/usr/bin/env python3
"""
PullMP3Downloader - YouTube downloader using pullmp3.com API
Alternative downloader option for the bot
"""

import asyncio
import aiohttp
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any
import re
from urllib.parse import parse_qs, urlparse

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('pullmp3_downloader')

class PullMP3Downloader:
    """
    YouTube downloader using pullmp3.com API
    Simple API that accepts YouTube video ID and quality
    """

    def __init__(self):
        self.base_url = "https://pullmp3.com"
        self.api_endpoint = "/wp-admin/admin-ajax.php"
        self.headers = {
            'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/140.0.0.0 Safari/537.36',
            'Sec-Ch-Ua-Platform': '"Linux"',
            'Sec-Ch-Ua': '"Not=A?Brand";v="24", "Chromium";v="140"',
            'Sec-Ch-Ua-Mobile': '?0',
            'Accept': '*/*',
            'Sec-Fetch-Site': 'same-origin',
            'Sec-Fetch-Mode': 'cors',
            'Sec-Fetch-Dest': 'empty',
            'Referer': 'https://pullmp3.com/',
            'Accept-Encoding': 'gzip, deflate, br',
            'Accept-Language': 'en-US,en;q=0.9',
            'Priority': 'u=1, i'
        }

    def extract_video_id(self, youtube_url: str) -> Optional[str]:
        """Extract YouTube video ID from URL"""
        youtube_regex = r'(?:youtube\.com\/(?:[^\/]+\/.+\/|(?:v|e(?:mbed)?)\/|.*[?&]v=)|youtu\.be\/)([^"&?\/\s]{11})'
        match = re.search(youtube_regex, youtube_url)
        return match.group(1) if match else None

    async def convert_video(self, youtube_url: str, quality: str = "320") -> Optional[Dict[str, Any]]:
        """
        Convert YouTube video using pullmp3.com API

        Args:
            youtube_url: YouTube video URL
            quality: Audio quality (320, 256, 192, 128, 64)

        Returns:
            Dict with conversion result or None if failed
        """
        try:
            # Extract video ID from URL
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                logger.error(f"Could not extract video ID from URL: {youtube_url}")
                return None

            logger.info(f"Converting video ID: {video_id} with quality: {quality}kbps")

            async with aiohttp.ClientSession() as session:
                # Build the API URL with parameters
                api_url = f"{self.base_url}{self.api_endpoint}"
                params = {
                    'action': 'convert_youtube',
                    'video_id': video_id,
                    'quality': quality
                }

                async with session.get(
                    api_url,
                    params=params,
                    headers=self.headers,
                    timeout=aiohttp.ClientTimeout(total=60)
                ) as response:
                    if response.status == 200:
                        data = await response.json()

                        if data.get('status') == 'ok':
                            logger.info(f"Conversion successful: {data.get('title', 'Unknown')}")
                            return {
                                'status': 'done',
                                'title': data.get('title', 'Unknown Title'),
                                'url': data.get('link'),
                                'filesize': data.get('filesize', 0),
                                'duration': data.get('duration', 0),
                                'video_id': video_id,
                                'quality': quality
                            }
                        else:
                            logger.error(f"Conversion failed: {data}")
                            return None
                    else:
                        logger.error(f"API returned status {response.status}")
                        response_text = await response.text()
                        logger.error(f"Response: {response_text}")
                        return None

        except Exception as e:
            logger.error(f"Error converting video: {e}")
            return None

    async def download_audio(self, download_url: str, output_path: Path) -> bool:
        """Download audio file from the provided URL"""
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

        except Exception as e:
            logger.error(f"Error downloading audio: {e}")
            return False

    async def download_from_youtube(self, youtube_url: str, output_path: Path, quality: str = "320") -> Optional[Dict[str, Any]]:
        """
        Complete YouTube download process using pullmp3.com

        Args:
            youtube_url: YouTube video URL
            output_path: Path where to save the MP3 file
            quality: Audio quality (320, 256, 192, 128, 64)

        Returns:
            Dict with download info including title, or None if failed
        """
        logger.info(f"🎵 Starting YouTube download via PullMP3: {youtube_url}")

        try:
            # Step 1: Convert video and get download info
            convert_result = await self.convert_video(youtube_url, quality)
            if not convert_result:
                logger.error("Failed to convert video")
                return None

            download_url = convert_result.get('url')
            title = convert_result.get('title', 'Unknown Title')

            if not download_url:
                logger.error("No download URL in conversion result")
                return None

            # Step 2: Download the audio file
            success = await self.download_audio(download_url, output_path)

            if success:
                logger.info(f"✅ YouTube download completed via PullMP3: {title}")
                return {
                    'success': True,
                    'title': title,
                    'file_path': str(output_path),
                    'url': youtube_url,
                    'filesize': convert_result.get('filesize', 0),
                    'duration': convert_result.get('duration', 0),
                    'quality': quality
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
async def download_youtube_with_pullmp3(youtube_url: str, output_path: Path, quality: str = "320") -> Optional[Dict[str, Any]]:
    """
    Convenience function to download from YouTube using pullmp3

    Args:
        youtube_url: YouTube video URL
        output_path: Path where to save the MP3 file
        quality: Audio quality (320, 256, 192, 128, 64)

    Returns:
        Dict with download info or None if failed
    """
    downloader = PullMP3Downloader()
    return await downloader.download_from_youtube(youtube_url, output_path, quality)

def is_youtube_url_pullmp3(url: str) -> bool:
    """Check if URL is a YouTube URL"""
    downloader = PullMP3Downloader()
    return downloader.is_youtube_url(url)

# Test function
async def test_pullmp3_downloader():
    """Test the pullmp3 downloader"""
    test_url = "https://youtu.be/989-7xsRLR4"  # Example YouTube URL (Vitas - The 7th Element)
    test_output = Path("/tmp/test_pullmp3_output.mp3")

    print("🧪 Testing PullMP3 Downloader...")

    downloader = PullMP3Downloader()

    if not downloader.is_youtube_url(test_url):
        print("❌ Invalid YouTube URL")
        return False

    result = await downloader.download_from_youtube(test_url, test_output, "320")

    if result and result.get('success'):
        print(f"✅ Download successful!")
        print(f"📁 Title: {result['title']}")
        print(f"📂 File: {result['file_path']}")
        print(f"🎵 Quality: {result['quality']}kbps")

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
    asyncio.run(test_pullmp3_downloader())