#!/usr/bin/env python3
"""
SpotDL Fallback - Fallback robusto para el bot de Telegram usando SpotDL
"""

import subprocess
import asyncio
import os
import sys
import json
import time
import logging
from pathlib import Path
from typing import Optional, Dict, Any

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('spotdl_fallback')

class SpotDLFallback:
    """
    Fallback robusto usando SpotDL para cuando la API principal falla
    """

    def __init__(self, output_dir: str = "downloads"):
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(exist_ok=True)

    async def is_available(self) -> bool:
        """Verifica si SpotDL está disponible"""
        try:
            result = await asyncio.create_subprocess_exec(
                'spotdl', '--version',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            if result.returncode == 0:
                version_output = stdout.decode('utf-8', errors='ignore')
                logger.info(f"SpotDL version detected: {version_output.strip()}")
                return True
            return False
        except Exception as e:
            logger.debug(f"SpotDL not available: {e}")
            return False

    async def get_spotdl_help(self) -> str:
        """Obtiene la ayuda de SpotDL para debugging"""
        try:
            result = await asyncio.create_subprocess_exec(
                'spotdl', '--help',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await result.communicate()
            if result.returncode == 0:
                return stdout.decode('utf-8', errors='ignore')
            return ""
        except:
            return ""

    async def download_from_youtube(self, youtube_url: str, output_path: Path, custom_title: str = None) -> bool:
        """
        Descarga directamente desde YouTube usando SpotDL/yt-dlp

        Args:
            youtube_url: URL del video de YouTube
            output_path: Path donde guardar el archivo
            custom_title: Título personalizado para el archivo

        Returns:
            True si la descarga fue exitosa, False en caso contrario
        """

        logger.info(f"🎵 SpotDL: Descargando desde YouTube {youtube_url}")

        try:
            # Verificar que SpotDL esté disponible
            if not await self.is_available():
                logger.error("SpotDL no está disponible")
                return False

            # Crear directorio temporal para SpotDL
            temp_dir = self.output_dir / "temp_youtube"
            temp_dir.mkdir(exist_ok=True)

            # Cambiar al directorio temporal
            original_cwd = os.getcwd()
            os.chdir(temp_dir)

            try:
                # Comando para YouTube corregido según v4.4.2
                cmd = [
                    'spotdl',
                    '--format', 'mp3',
                    '--bitrate', 'auto',
                    '--threads', '2',
                    '--overwrite', 'skip',
                    '--max-retries', '2'
                ]

                # Si se proporciona título personalizado, usarlo
                if custom_title:
                    sanitized_title = custom_title.replace('"', '').replace("'", "")
                    cmd.extend(['--output', f'"{sanitized_title}.%(ext)s"'])

                cmd.append(youtube_url)

                logger.info(f"🔧 Ejecutando SpotDL para YouTube: {' '.join(cmd)}")

                # Ejecutar con timeout extendido para YouTube
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=360  # 6 minutos para YouTube
                    )

                    if process.returncode == 0:
                        # Buscar el archivo descargado
                        downloaded_file = await self._find_downloaded_file(temp_dir)

                        if downloaded_file:
                            # Mover archivo a la ubicación final
                            try:
                                downloaded_file.rename(output_path)
                                logger.info(f"✅ YouTube descarga exitosa: {output_path}")
                                return True
                            except Exception as e:
                                logger.error(f"Error moviendo archivo de YouTube: {e}")
                                return False
                        else:
                            logger.error("No se encontró archivo descargado de YouTube")
                            return False
                    else:
                        stderr_str = stderr.decode('utf-8', errors='ignore')
                        logger.error(f"SpotDL falló para YouTube (código {process.returncode}): {stderr_str}")
                        return False

                except asyncio.TimeoutError:
                    process.kill()
                    logger.error("SpotDL timeout para YouTube después de 6 minutos")
                    return False

            finally:
                # Volver al directorio original
                os.chdir(original_cwd)

                # Limpiar directorio temporal
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                except:
                    pass

        except Exception as e:
            logger.error(f"Error inesperado descargando desde YouTube: {e}")
            return False

    async def download_track_basic(self, spotify_url: str, output_path: Path) -> bool:
        """
        Descarga un track usando SpotDL con configuración básica (fallback)
        """
        logger.info(f"🎵 SpotDL Basic: Descargando {spotify_url}")

        try:
            if not await self.is_available():
                logger.error("SpotDL no está disponible")
                return False

            temp_dir = self.output_dir / "temp_spotdl_basic"
            temp_dir.mkdir(exist_ok=True)

            original_cwd = os.getcwd()
            os.chdir(temp_dir)

            try:
                # Comando mínimo y básico de SpotDL
                cmd = [
                    'spotdl',
                    '--format', 'mp3',
                    spotify_url
                ]

                logger.info(f"🔧 Ejecutando SpotDL básico: {' '.join(cmd)}")

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=240  # 4 minutos para modo básico
                    )

                    if process.returncode == 0:
                        downloaded_file = await self._find_downloaded_file(temp_dir)
                        if downloaded_file:
                            try:
                                downloaded_file.rename(output_path)
                                logger.info(f"✅ SpotDL básico exitoso: {output_path}")
                                return True
                            except Exception as e:
                                logger.error(f"Error moviendo archivo básico: {e}")
                                return False
                        else:
                            logger.error("No se encontró archivo descargado (básico)")
                            return False
                    else:
                        stderr_str = stderr.decode('utf-8', errors='ignore')
                        logger.error(f"SpotDL básico falló: {stderr_str}")
                        return False

                except asyncio.TimeoutError:
                    process.kill()
                    logger.error("SpotDL básico timeout")
                    return False

            finally:
                os.chdir(original_cwd)
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                except:
                    pass

        except Exception as e:
            logger.error(f"Error inesperado en SpotDL básico: {e}")
            return False

    async def download_track_minimal(self, spotify_url: str, output_path: Path) -> bool:
        """
        Descarga un track usando SpotDL con comando ultra-básico (último recurso)
        """
        logger.info(f"🎵 SpotDL Minimal: Descargando {spotify_url}")

        try:
            if not await self.is_available():
                logger.error("SpotDL no está disponible")
                return False

            temp_dir = self.output_dir / "temp_spotdl_minimal"
            temp_dir.mkdir(exist_ok=True)

            original_cwd = os.getcwd()
            os.chdir(temp_dir)

            try:
                # Comando ultra-básico solo URL
                cmd = [
                    'spotdl',
                    spotify_url
                ]

                logger.info(f"🔧 Ejecutando SpotDL minimal: {' '.join(cmd)}")

                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=180  # 3 minutos para modo minimal
                    )

                    if process.returncode == 0:
                        downloaded_file = await self._find_downloaded_file(temp_dir)
                        if downloaded_file:
                            try:
                                downloaded_file.rename(output_path)
                                logger.info(f"✅ SpotDL minimal exitoso: {output_path}")
                                return True
                            except Exception as e:
                                logger.error(f"Error moviendo archivo minimal: {e}")
                                return False
                        else:
                            logger.error("No se encontró archivo descargado (minimal)")
                            return False
                    else:
                        stderr_str = stderr.decode('utf-8', errors='ignore')
                        logger.error(f"SpotDL minimal falló: {stderr_str}")
                        return False

                except asyncio.TimeoutError:
                    process.kill()
                    logger.error("SpotDL minimal timeout")
                    return False

            finally:
                os.chdir(original_cwd)
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                except:
                    pass

        except Exception as e:
            logger.error(f"Error inesperado en SpotDL minimal: {e}")
            return False

    async def download_track(self, spotify_url: str, output_path: Path) -> bool:
        """
        Descarga un track usando SpotDL

        Args:
            spotify_url: URL del track de Spotify
            output_path: Path donde guardar el archivo

        Returns:
            True si la descarga fue exitosa, False en caso contrario
        """

        logger.info(f"🎵 SpotDL Fallback: Descargando {spotify_url}")

        try:
            # Verificar que SpotDL esté disponible
            if not await self.is_available():
                logger.error("SpotDL no está disponible")
                return False

            # Crear directorio temporal para SpotDL
            temp_dir = self.output_dir / "temp_spotdl"
            temp_dir.mkdir(exist_ok=True)

            # Cambiar al directorio temporal
            original_cwd = os.getcwd()
            os.chdir(temp_dir)

            try:
                # Comando SpotDL corregido según v4.4.2
                cmd = [
                    'spotdl',
                    '--format', 'mp3',
                    '--bitrate', 'auto',
                    '--threads', '2',
                    '--overwrite', 'skip',
                    '--max-retries', '2',
                    spotify_url
                ]

                logger.info(f"🔧 Ejecutando SpotDL: {' '.join(cmd)}")

                # Ejecutar con timeout
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )

                try:
                    stdout, stderr = await asyncio.wait_for(
                        process.communicate(),
                        timeout=300  # 5 minutos máximo (aumentado de 3)
                    )

                    if process.returncode == 0:
                        # Buscar el archivo descargado
                        downloaded_file = await self._find_downloaded_file(temp_dir)

                        if downloaded_file:
                            # Mover archivo a la ubicación final
                            try:
                                downloaded_file.rename(output_path)
                                logger.info(f"✅ SpotDL descarga exitosa: {output_path}")
                                return True
                            except Exception as e:
                                logger.error(f"Error moviendo archivo: {e}")
                                return False
                        else:
                            logger.error("No se encontró archivo descargado")
                            return False
                    else:
                        stderr_str = stderr.decode('utf-8', errors='ignore')
                        logger.error(f"SpotDL falló (código {process.returncode}): {stderr_str}")
                        return False

                except asyncio.TimeoutError:
                    process.kill()
                    logger.error("SpotDL timeout después de 5 minutos")
                    return False

            finally:
                # Volver al directorio original
                os.chdir(original_cwd)

                # Limpiar directorio temporal
                try:
                    import shutil
                    shutil.rmtree(temp_dir)
                except:
                    pass

        except Exception as e:
            logger.error(f"Error inesperado en SpotDL fallback: {e}")

        # Si el método avanzado falla, intentar método básico
        logger.info(f"🔄 Intentando SpotDL con configuración básica para: {spotify_url}")
        basic_success = await self.download_track_basic(spotify_url, output_path)

        if not basic_success:
            # Último recurso: comando súper básico
            logger.info(f"🔄 Último intento con SpotDL ultra-básico para: {spotify_url}")
            return await self.download_track_minimal(spotify_url, output_path)

        return basic_success

    async def _find_downloaded_file(self, search_dir: Path) -> Optional[Path]:
        """Encuentra el archivo descargado más reciente"""
        try:
            # Buscar archivos de audio en general, no solo mp3
            audio_extensions = ['*.mp3', '*.flac', '*.ogg', '*.opus', '*.m4a', '*.wav']
            audio_files = []

            for pattern in audio_extensions:
                audio_files.extend(search_dir.glob(pattern))

            if not audio_files:
                logger.debug(f"No audio files found in {search_dir}")
                # List all files for debugging
                all_files = list(search_dir.iterdir())
                if all_files:
                    logger.debug(f"Files found: {[f.name for f in all_files]}")
                return None

            # Devolver el archivo más reciente
            latest_file = max(audio_files, key=lambda f: f.stat().st_mtime)
            logger.debug(f"Found downloaded file: {latest_file}")
            return latest_file

        except Exception as e:
            logger.error(f"Error buscando archivo descargado: {e}")
            return None

    async def get_track_info(self, spotify_url: str) -> Optional[Dict[str, Any]]:
        """
        Obtiene información de un track sin descargarlo

        Args:
            spotify_url: URL del track de Spotify

        Returns:
            Dict con información del track o None si falla
        """
        try:
            # Usar la API de Spotify directamente a través de spotipy
            # que es una dependencia de SpotDL
            import spotipy
            from spotipy.oauth2 import SpotifyClientCredentials

            # Extraer track ID de la URL
            track_id = self._extract_track_id(spotify_url)
            if not track_id:
                return None

            # Configurar cliente básico (sin autenticación)
            client_credentials_manager = SpotifyClientCredentials(
                client_id="your_client_id",  # Placeholder
                client_secret="your_client_secret"  # Placeholder
            )
            sp = spotipy.Spotify(client_credentials_manager=client_credentials_manager)

            # Obtener información del track
            track = sp.track(track_id)

            return {
                'title': track['name'],
                'artists': [artist['name'] for artist in track['artists']],
                'album': track['album']['name'],
                'duration_ms': track['duration_ms'],
                'popularity': track['popularity'],
                'explicit': track['explicit']
            }

        except Exception as e:
            logger.warning(f"No se pudo obtener info del track: {e}")
            return None

    def _extract_track_id(self, spotify_url: str) -> Optional[str]:
        """Extrae el track ID de una URL de Spotify"""
        try:
            import re
            match = re.search(r'track/([a-zA-Z0-9]+)', spotify_url)
            return match.group(1) if match else None
        except:
            return None

# Funciones de conveniencia para usar desde el bot principal
async def try_spotdl_fallback(spotify_url: str, output_path: Path) -> bool:
    """
    Función de conveniencia para usar SpotDL como fallback

    Args:
        spotify_url: URL del track de Spotify
        output_path: Path donde guardar el archivo

    Returns:
        True si la descarga fue exitosa, False en caso contrario
    """
    fallback = SpotDLFallback()
    return await fallback.download_track(spotify_url, output_path)

async def try_spotdl_basic(spotify_url: str, output_path: Path) -> bool:
    """
    Función de conveniencia para usar SpotDL en modo básico

    Args:
        spotify_url: URL del track de Spotify
        output_path: Path donde guardar el archivo

    Returns:
        True si la descarga fue exitosa, False en caso contrario
    """
    fallback = SpotDLFallback()
    return await fallback.download_track_basic(spotify_url, output_path)

async def download_from_youtube_url(youtube_url: str, output_path: Path, custom_title: str = None) -> bool:
    """
    Función de conveniencia para descargar directamente desde YouTube

    Args:
        youtube_url: URL del video de YouTube
        output_path: Path donde guardar el archivo
        custom_title: Título personalizado para el archivo

    Returns:
        True si la descarga fue exitosa, False en caso contrario
    """
    fallback = SpotDLFallback()
    return await fallback.download_from_youtube(youtube_url, output_path, custom_title)

def is_youtube_url(url: str) -> bool:
    """
    Verifica si una URL es de YouTube

    Args:
        url: URL a verificar

    Returns:
        True si es una URL de YouTube, False en caso contrario
    """
    youtube_domains = [
        'youtube.com', 'youtu.be', 'www.youtube.com', 'm.youtube.com'
    ]
    return any(domain in url.lower() for domain in youtube_domains)

# Test independiente
async def test_spotdl_fallback():
    """Test del fallback de SpotDL"""
    test_url = "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"
    test_output = Path("/tmp/test_spotdl_output.mp3")

    print("🧪 Testing SpotDL Fallback...")

    fallback = SpotDLFallback("/tmp")

    # Test disponibilidad
    available = await fallback.is_available()
    print(f"📊 SpotDL disponible: {available}")

    if available:
        # Mostrar ayuda para debugging
        help_output = await fallback.get_spotdl_help()
        if help_output:
            print("📋 SpotDL Help (primeras 10 líneas):")
            for i, line in enumerate(help_output.split('\n')[:10]):
                print(f"   {line}")
            print("   ...")

        # Test descarga
        success = await fallback.download_track(test_url, test_output)
        print(f"📊 Descarga exitosa: {success}")

        if success and test_output.exists():
            size = test_output.stat().st_size
            print(f"📊 Archivo creado: {test_output} ({size} bytes)")

            # Limpiar archivo de test
            test_output.unlink()

    return available

async def debug_spotdl_command():
    """Debug SpotDL command structure"""
    fallback = SpotDLFallback()
    available = await fallback.is_available()

    if available:
        help_output = await fallback.get_spotdl_help()
        print("🔍 SpotDL Command Structure Analysis:")

        # Look for download command
        if 'download' in help_output:
            print("✅ 'download' command found")
        else:
            print("❌ 'download' command not found")

        # Check for common parameters
        params_to_check = ['--format', '--bitrate', '--threads', '--overwrite', '--simple-tui', '--max-retries', '--print-errors']
        for param in params_to_check:
            if param in help_output:
                print(f"✅ {param} available")
            else:
                print(f"❌ {param} not available")
    else:
        print("❌ SpotDL not available for debugging")

if __name__ == "__main__":
    # Test del fallback
    asyncio.run(test_spotdl_fallback())