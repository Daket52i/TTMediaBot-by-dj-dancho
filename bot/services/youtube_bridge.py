from __future__ import annotations

import logging
import os
import shutil
import subprocess
import tempfile
from contextlib import contextmanager
from typing import Any, Dict, Optional

import requests
from yt_dlp import YoutubeDL
from yt_dlp.utils import DownloadError

from bot import errors


class YouTubeBridge:
    def __init__(self, cookie_file: str = "", client: str = "WEB") -> None:
        self.base_url = os.getenv("YOUTUBE_BRIDGE_URL", "http://127.0.0.1:4417")
        self.cookie_file = cookie_file
        self.client = client
        self.timeout = (5, 30)

    def _post(self, endpoint: str, **payload: Any) -> Dict[str, Any]:
        payload.setdefault("cookie_file", self.cookie_file or None)
        payload.setdefault("client", self.client)
        try:
            response = requests.post(
                f"{self.base_url}{endpoint}",
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except requests.RequestException as exc:
            detail = ""
            if getattr(exc, "response", None) is not None:
                try:
                    detail = exc.response.json().get("error", "")
                except Exception:
                    detail = exc.response.text[:500]
            message = detail or str(exc)
            raise errors.ServiceError(f"YouTube.js bridge error: {message}") from exc
        except ValueError as exc:
            raise errors.ServiceError("YouTube.js bridge returned invalid JSON") from exc

        if data.get("error"):
            raise errors.ServiceError(data["error"])
        return data

    def health(self) -> bool:
        try:
            response = requests.get(f"{self.base_url}/health", timeout=(2, 5))
            return response.ok
        except requests.RequestException:
            return False

    def resolve(self, url: str = "", video_id: str = "") -> Dict[str, Any]:
        source_url = url or f"https://www.youtube.com/watch?v={video_id}"
        try:
            return self._post("/resolve", url=url or None, video_id=video_id or None)
        except errors.ServiceError as exc:
            logging.warning("YouTube.js resolve failed; trying yt-dlp fallback: %s", exc)
            return self._resolve_with_ytdlp(source_url)

    def info(self, url: str = "", video_id: str = "") -> Dict[str, Any]:
        return self._post("/info", url=url or None, video_id=video_id or None)

    def playlist(self, url: str) -> Dict[str, Any]:
        return self._post("/playlist", url=url)

    def download(self, url: str, file_path: str, video: bool = False) -> None:
        try:
            plan = self._post("/download-plan", url=url, video=video)
        except errors.ServiceError as exc:
            logging.warning("YouTube.js download plan failed; trying yt-dlp fallback: %s", exc)
            self._download_with_ytdlp(url, file_path, video)
            return
        headers = plan.get("http_headers") or {}
        user_agent = headers.get("User-Agent", "")
        base_cmd = ["ffmpeg", "-y", "-loglevel", "error"]
        if user_agent:
            base_cmd += ["-user_agent", user_agent]

        if not video:
            audio_url = (plan.get("audio") or {}).get("url")
            if not audio_url:
                raise errors.ServiceError("YouTube.js did not return an audio stream")
            cmd = base_cmd + ["-i", audio_url, "-vn", "-codec:a", "libmp3lame", "-b:a", "320k", file_path]
        else:
            video_url = (plan.get("video") or {}).get("url")
            audio_url = (plan.get("audio") or {}).get("url")
            if not video_url or not audio_url:
                raise errors.ServiceError("YouTube.js did not return video/audio streams")
            cmd = base_cmd + [
                "-i", video_url,
                "-i", audio_url,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-c:v", "copy",
                "-c:a", "aac",
                "-movflags", "+faststart",
                file_path,
            ]

        logging.info("YouTube.js download: starting ffmpeg")
        try:
            subprocess.run(cmd, check=True)
        except subprocess.CalledProcessError as exc:
            raise errors.ServiceError(f"FFmpeg failed with exit code {exc.returncode}") from exc

    @contextmanager
    def _cookie_copy(self):
        if not self.cookie_file or not os.path.isfile(self.cookie_file):
            yield None
            return

        suffix = os.path.splitext(self.cookie_file)[1] or ".txt"
        handle, cookie_path = tempfile.mkstemp(prefix="ttmediabot-cookies-", suffix=suffix)
        os.close(handle)
        try:
            shutil.copy2(self.cookie_file, cookie_path)
            yield cookie_path
        finally:
            try:
                os.remove(cookie_path)
            except FileNotFoundError:
                pass

    def _ydl_options(self, cookie_file: Optional[str]) -> Dict[str, Any]:
        options: Dict[str, Any] = {
            "format": "ba[ext=m4a]/ba[ext=webm]/ba/bestaudio/best",
            "format_sort": ["codec:opus", "codec:m4a", "codec:mp3"],
            "noplaylist": True,
            "quiet": True,
            "no_warnings": True,
            "socket_timeout": 15,
            "cachedir": False,
            "js_runtimes": {"node": {}},
            "extractor_args": {
                "youtube": {
                    "player_client": ["mweb", "web", "android", "ios"],
                    "player_skip": ["webpage", "configs"],
                }
            },
        }
        if cookie_file:
            options["cookiefile"] = cookie_file
        return options

    def _resolve_with_ytdlp(self, url: str) -> Dict[str, Any]:
        try:
            with self._cookie_copy() as cookie_file:
                with YoutubeDL(self._ydl_options(cookie_file)) as ydl:
                    info = ydl.extract_info(url, download=False)
        except DownloadError as exc:
            raise errors.ServiceError(f"yt-dlp fallback error: {exc}") from exc

        stream_url = info.get("url")
        if not stream_url:
            raise errors.ServiceError("yt-dlp fallback returned no stream URL")
        return {
            **info,
            "url": stream_url,
            "videoId": info.get("id"),
            "is_live": bool(info.get("is_live")),
            "format": "mp3",
        }

    def _download_with_ytdlp(self, url: str, file_path: str, video: bool) -> None:
        with self._cookie_copy() as cookie_file:
            options = self._ydl_options(cookie_file)
            options["outtmpl"] = file_path.rsplit(".", 1)[0] + ".%(ext)s"
            if video:
                options["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
                options["merge_output_format"] = "mp4"
            else:
                options["postprocessors"] = [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "320",
                }]
            try:
                with YoutubeDL(options) as ydl:
                    ydl.download([url])
            except DownloadError as exc:
                raise errors.ServiceError(f"yt-dlp fallback error: {exc}") from exc
