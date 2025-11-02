import logging
import time
import yt_dlp

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def get_video_info(youtube_url):
    """
    Gets video information from a YouTube URL using yt-dlp.

    Args:
        youtube_url (str): The URL of the YouTube video.

    Returns:
        dict: A dictionary containing video information, or None if an error occurs.
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'skip_download': True,
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(youtube_url, download=False)
            return info
    except Exception as e:
        logger.error(f"Error getting video info: {e}")
        return None

def download_audio(youtube_url, output_path, quality='192', retries=3, proxy=None):
    """
    Downloads audio from a YouTube URL using yt-dlp with retries and proxy support.

    Args:
        youtube_url (str): The URL of the YouTube video.
        output_path (str): The path to save the downloaded audio file (without extension).
        quality (str): The desired audio quality in kbps (e.g., '192').
        retries (int): The number of times to retry the download.
        proxy (str, optional): The proxy to use for the download.

    Returns:
        bool: True if download is successful, False otherwise.
    """
    ydl_opts = {
        'format': 'bestaudio/best',
        'outtmpl': f'{output_path}.%(ext)s',
        'postprocessors': [{
            'key': 'FFmpegExtractAudio',
            'preferredcodec': 'mp3',
            'preferredquality': quality,
        }],
        'logger': logger,
        'progress_hooks': [lambda d: None],  # Suppress progress output
        'quiet': True,
        'no_warnings': True,
    }

    if proxy:
        ydl_opts['proxy'] = proxy

    for attempt in range(retries):
        try:
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                ydl.download([youtube_url])
            return True
        except yt_dlp.utils.DownloadError as e:
            logger.warning(f"Attempt {attempt + 1} of {retries} failed for {youtube_url}: {e}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)  # Exponential backoff
            else:
                logger.error(f"All {retries} attempts failed for {youtube_url}.")
        except Exception as e:
            logger.error(f"An unexpected error occurred: {e}")
            break  # Don't retry on unexpected errors

    return False

def get_playlist_info(playlist_url):
    """
    Gets information about a YouTube playlist using yt-dlp.

    Args:
        playlist_url (str): The URL of the YouTube playlist.

    Returns:
        dict: A dictionary containing playlist information, or None if an error occurs.
    """
    ydl_opts = {
        'quiet': True,
        'no_warnings': True,
        'extract_flat': True,  # Extract only video entries, not full info
        'dump_single_json': True, # Dump playlist info as a single JSON
    }

    try:
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(playlist_url, download=False)
            return info
    except Exception as e:
        logger.error(f"Error getting playlist info: {e}")
        return None

def is_youtube_playlist_url(url):
    """
    Checks if a URL is a YouTube playlist URL.

    Args:
        url (str): The URL to check.

    Returns:
        bool: True if the URL is a YouTube playlist URL, False otherwise.
    """
    return "list=" in url
