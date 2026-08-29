from __future__ import annotations

import logging
import os
import subprocess
from typing import Any, Dict, Optional

import requests

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
        return self._post("/resolve", url=url or None, video_id=video_id or None)

    def info(self, url: str = "", video_id: str = "") -> Dict[str, Any]:
        return self._post("/info", url=url or None, video_id=video_id or None)

    def playlist(self, url: str) -> Dict[str, Any]:
        return self._post("/playlist", url=url)

    def download(self, url: str, file_path: str, video: bool = False) -> None:
        plan = self._post("/download-plan", url=url, video=video)
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
