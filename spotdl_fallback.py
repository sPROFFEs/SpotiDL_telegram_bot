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
            await result.communicate()
            return result.returncode == 0
        except:
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
                # Comando SpotDL optimizado
                cmd = [
                    'spotdl',
                    '--format', 'mp3',
                    '--bitrate', '320k',
                    '--threads', '1',
                    '--overwrite', 'skip',
                    '--simple-tui',
                    '--no-cache',
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
                        timeout=180  # 3 minutos máximo
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
                    logger.error("SpotDL timeout después de 3 minutos")
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
            return False

    async def _find_downloaded_file(self, search_dir: Path) -> Optional[Path]:
        """Encuentra el archivo descargado más reciente"""
        try:
            mp3_files = list(search_dir.glob('*.mp3'))
            if not mp3_files:
                return None

            # Devolver el archivo más reciente
            latest_file = max(mp3_files, key=lambda f: f.stat().st_mtime)
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

# Función de conveniencia para usar desde el bot principal
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

# Test independiente
async def test_spotdl_fallback():
    """Test del fallback de SpotDL"""
    test_url = "https://open.spotify.com/track/4iV5W9uYEdYUVa79Axb7Rh"
    test_output = Path("test_spotdl_output.mp3")

    print("🧪 Testing SpotDL Fallback...")

    fallback = SpotDLFallback()

    # Test disponibilidad
    available = await fallback.is_available()
    print(f"📊 SpotDL disponible: {available}")

    if available:
        # Test descarga
        success = await fallback.download_track(test_url, test_output)
        print(f"📊 Descarga exitosa: {success}")

        if success and test_output.exists():
            size = test_output.stat().st_size
            print(f"📊 Archivo creado: {test_output} ({size} bytes)")

            # Limpiar archivo de test
            test_output.unlink()

    return available

if __name__ == "__main__":
    # Test del fallback
    asyncio.run(test_spotdl_fallback())