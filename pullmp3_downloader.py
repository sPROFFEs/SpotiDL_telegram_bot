#!/usr/bin/env python3
"""
PullMP3Downloader - YouTube downloader using pullmp3.com API
Alternative downloader option for the bot
"""

import asyncio
import json
import logging
import time
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
import re
from urllib.parse import parse_qs, urlparse
from playwright.async_api import async_playwright, Browser, Page, Response

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
        self.last_check = 0
        self.check_interval = 300  # 5 minutes
        self.browser: Optional[Browser] = None
        self.context = None
        self.page: Optional[Page] = None
        self._initialized = False
        
    async def initialize(self):
        """Inicializa el navegador y la página"""
        if self._initialized:
            return
            
        try:
            playwright = await async_playwright().start()
            self.browser = await playwright.chromium.launch(
                headless=True,
                args=['--no-sandbox']
            )
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36'
            )
            self.page = await self.context.new_page()
            self._initialized = True
            logger.info("Browser initialized successfully")
        except Exception as e:
            logger.error(f"Failed to initialize browser: {e}")
            await self.cleanup()
            raise

    async def cleanup(self):
        """Limpia los recursos del navegador"""
        try:
            if self.page:
                await self.page.close()
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
        except Exception as e:
            logger.error(f"Error during cleanup: {e}")
        finally:
            self._initialized = False
            self.page = None
            self.context = None
            self.browser = None
            
    async def get_session_data(self) -> Tuple[dict, str]:
        """Obtiene las cookies y el nonce de la página"""
        if not self._initialized:
            await self.initialize()
            
        try:
            # Configurar headers específicos para la petición inicial
            await self.context.add_init_script('''
                Object.defineProperty(navigator, 'languages', {
                    get: function() { return ['en-US', 'en']; }
                });
            ''')
            
            # Navegar a la página principal con los headers correctos
            await self.page.set_extra_http_headers({
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7',
                'Accept-Language': 'en-US,en;q=0.9',
                'Cache-Control': 'max-age=0',
                'Sec-Ch-Ua': '"Not)A;Brand";v="8", "Chromium";v="138"',
                'Sec-Ch-Ua-Mobile': '?0',
                'Sec-Ch-Ua-Platform': '"Linux"',
                'Sec-Fetch-Dest': 'document',
                'Sec-Fetch-Mode': 'navigate',
                'Sec-Fetch-Site': 'none',
                'Sec-Fetch-User': '?1',
                'Upgrade-Insecure-Requests': '1'
            })
            
            # Navegar a la página principal y esperar a que cargue
            response = await self.page.goto(self.base_url, wait_until="networkidle")
            await self.page.wait_for_load_state("domcontentloaded")
            
            # Extraer las cookies después de la navegación
            cookies = await self.context.cookies()
            cookie_dict = {cookie["name"]: cookie["value"] for cookie in cookies}
            
            # Buscar el nonce en el script específico
            nonce = await self.page.evaluate('''() => {
                const script = document.querySelector('script#pullmp3-js-js-extra');
                if (script) {
                    const match = script.textContent.match(/var PULLMP3 = .*"nonce":"([^"]+)"/);
                    if (match) return match[1];
                }
                return null;
            }''')
            
            if not nonce:
                # Si no encontramos el nonce en el script específico, buscamos en la variable global
                nonce = await self.page.evaluate('''() => {
                    if (typeof PULLMP3 !== 'undefined' && PULLMP3.nonce) {
                        return PULLMP3.nonce;
                    }
                    return null;
                }''')
            
            if not nonce:
                raise ValueError("Could not find nonce in page")
            
            logger.info(f"Found nonce: {nonce}")
            return cookie_dict, nonce
            
        except Exception as e:
            logger.error(f"Error getting session data: {e}")
            raise

    async def check_service(self) -> bool:
        """Verifica si el servicio está activo y responde correctamente usando Playwright"""
        if time.time() - self.last_check < self.check_interval:
            return True

        try:
            if not self._initialized:
                await self.initialize()
            
            response = await self.page.goto(self.base_url)
            self.last_check = time.time()
            
            if response and response.status == 200:
                logger.info("Service check: OK")
                return True
            else:
                logger.error(f"Service check failed: Status {response.status if response else 'No response'}")
                return False
                
        except Exception as e:
            logger.error(f"Service check failed: {e}")
            return False

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
            # Obtener cookies y nonce
            try:
                cookies, nonce = await self.get_session_data()
            except Exception as e:
                logger.error(f"Failed to get session data: {e}")
                return None

            # Extract video ID from URL
            video_id = self.extract_video_id(youtube_url)
            if not video_id:
                logger.error(f"Could not extract video ID from URL: {youtube_url}")
                return None

            logger.info(f"Converting video ID: {video_id} with quality: {quality}kbps")

            api_url = f"{self.base_url}{self.api_endpoint}"

            # Construir los datos del formulario
            form_data = {
                'action': 'convert_youtube',
                'video_id': video_id,
                'quality': quality,
                '_nonce': nonce
            }
            
            # Realizar la petición POST usando Playwright
            response = await self.page.evaluate("""
                async (params) => {
                    const { url, data } = params;
                    const formData = new URLSearchParams();
                    for (const [key, value] of Object.entries(data)) {
                        formData.append(key, value);
                    }
                    
                    const response = await fetch(url, {
                        method: 'POST',
                        headers: {
                            'Content-Type': 'application/x-www-form-urlencoded',
                            'Accept': '*/*',
                        },
                        body: formData,
                        credentials: 'include'
                    });
                    
                    return await response.text();
                }
            """, {"url": api_url, "data": form_data})
            
            try:
                data = json.loads(response)
                logger.info(f"API Response: {response}")

                if data.get('status') == 'ok':
                    logger.info(f"Conversion successful: {data.get('title', 'Unknown')}")
                    return {
                        'status': 'done',
                        'title': data.get('title', 'Unknown Title'),
                        'url': data.get('link', '').replace('\\/', '/'),  # Fix URL escaping
                        'filesize': 0,  # No proporcionado por la API
                        'duration': 0,  # No proporcionado por la API
                        'video_id': video_id,
                        'quality': quality,
                        'checked_at': data.get('checked_at', 0)
                    }
                
                # Si hay un mensaje de error en la respuesta, lo registramos
                if 'data' in data and 'message' in data['data']:
                    logger.error(f"API Error Message: {data['data']['message']}")
                
                logger.error(f"Conversion failed: {data}")
                return None
            
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse API response: {e}")
                logger.error(f"Raw response: {response}")
                return None

        except Exception as e:
            logger.error(f"Error converting video: {e}")
            return None

    async def download_audio(self, download_url: str, output_path: Path) -> bool:
        """Download audio file from the provided URL using Playwright (handles real file downloads)"""
        try:
            output_path.parent.mkdir(parents=True, exist_ok=True)

            await self.page.set_extra_http_headers({
                'Accept': 'audio/*;q=0.9,application/ogg;q=0.7,*/*;q=0.5',
                'User-Agent': 'Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/138.0.0.0 Safari/537.36',
                'Referer': 'https://pullmp3.com/',
            })

            logger.info(f"Starting download from: {download_url}")

            # Esperar la descarga
            async with self.page.expect_download() as download_info:
                # Iniciar la descarga cambiando la ubicación del navegador
                await self.page.evaluate(f"window.location.href = '{download_url}';")

            # Obtener el objeto de descarga
            download = await download_info.value

            # Guardar el archivo descargado
            await download.save_as(str(output_path))

            if output_path.exists() and output_path.stat().st_size > 0:
                logger.info(f"✅ Successfully downloaded: {output_path} ({output_path.stat().st_size} bytes)")
                return True
            else:
                logger.error("Downloaded file is empty or not created")
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

if __name__ == "__main__":
    # Test the downloader
    async def test():
        test_url = "https://youtu.be/989-7xsRLR4"
        test_output = Path("/tmp/test_pullmp3_output.mp3")
        result = await download_youtube_with_pullmp3(test_url, test_output)
        print("Success!" if result else "Failed!")

    asyncio.run(test())
