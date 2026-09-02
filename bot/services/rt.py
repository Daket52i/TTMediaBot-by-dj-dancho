from __future__ import annotations
import logging
import time
import threading
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Bot

from bot.config.models import RtModel
from bot.player.enums import TrackType
from bot.player.track import Track
from bot.services import Service as _Service
from bot import errors

import httpx


class RtService(_Service):
    def __init__(self, bot: Bot, config: RtModel):
        self.bot = bot
        self.config = config
        self.name = "rt"
        self.hostnames = ["rutube.ru", "www.rutube.ru", "mobile.rutube.ru"]
        self.is_enabled = self.config.enabled
        self.error_message = ""
        self.warning_message = ""
        self.help = ""
        self.hidden = False
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "application/json",
            "Referer": "https://rutube.ru/",
        }
        self._client: Optional[httpx.Client] = None

    def initialize(self):
        self._client = httpx.Client(
            headers=self._headers,
            follow_redirects=True,
            timeout=15.0,
        )
        logging.info("Rutube Service initialized")

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                headers=self._headers,
                follow_redirects=True,
                timeout=15.0,
            )
        return self._client

    def _extract_video_id(self, url: str) -> Optional[str]:
        if not url:
            return None
        url = url.strip()
        if len(url) == 32 and all(c in "0123456789abcdef" for c in url):
            return url
        if "/video/" in url:
            parts = url.split("/video/")
            if len(parts) > 1:
                vid = parts[1].split("/")[0].split("?")[0].split("#")[0]
                if len(vid) == 32:
                    return vid
        return None

    def search(self, query: str, limit: Optional[int] = None) -> List[Track]:
        if limit is None:
            limit = self.config.search_results
        start_time = time.perf_counter()

        client = self._get_client()
        try:
            resp = client.get(
                "https://rutube.ru/api/search/video/",
                params={"query": query, "page": 1, "limit": limit},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.error(f"Rutube search failed: {e}")
            raise errors.NothingFoundError(str(e))

        results = data.get("results", [])
        if not results:
            raise errors.NothingFoundError("")

        tracks: List[Track] = []
        for item in results:
            vid = item.get("id", "")
            title = item.get("title", "")
            author = item.get("author", {})
            author_name = author.get("name", "") if isinstance(author, dict) else ""
            full_title = f"{title} - {author_name}" if author_name else title

            track_url = f"https://rutube.ru/video/{vid}/"
            duration = item.get("duration", 0)

            track = Track(
                service=self.name,
                url=track_url,
                name=full_title,
                type=TrackType.Dynamic,
                extra_info={
                    "videoId": vid,
                    "title": title,
                    "author": author_name,
                    "duration": duration,
                    "thumbnail": item.get("thumbnail_url", ""),
                },
            )
            tracks.append(track)

        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Rutube Search finished in {elapsed:.2f}ms for query: {query}")
        return tracks

    def get(
        self,
        url: str,
        extra_info: Optional[Dict[str, Any]] = None,
        process: bool = False,
    ) -> List[Track]:
        start_time = time.perf_counter()

        info = dict(extra_info or {})
        video_id = info.get("videoId") or self._extract_video_id(url)

        if not video_id:
            raise errors.InvalidArgumentError("Cannot extract Rutube video ID")

        if process:
            stream_url = self._resolve_stream_url(video_id)
            if not stream_url:
                raise errors.ServiceError("Rutube returned no stream URL")

            title = info.get("title", "")
            author = info.get("author", "")
            full_title = f"{title} - {author}" if author else title

            elapsed = (time.perf_counter() - start_time) * 1000
            logging.info(f"Rutube Get (Process) finished in {elapsed:.2f}ms for {full_title}")

            return [
                Track(
                    service=self.name,
                    url=stream_url,
                    name=full_title or "Rutube Video",
                    format="mp4",
                    type=TrackType.Default,
                    extra_info=info,
                    extracted_at=time.perf_counter(),
                )
            ]

        if extra_info and not url:
            track_url = f"https://rutube.ru/video/{video_id}/"
            return [Track(
                service=self.name,
                url=track_url,
                name=info.get("title", ""),
                type=TrackType.Dynamic,
                extra_info=info,
            )]

        track_url = url or f"https://rutube.ru/video/{video_id}/"
        track_name = info.get("title", "")

        track = Track(
            service=self.name,
            url=track_url,
            name=track_name,
            type=TrackType.Dynamic,
            extra_info=info or {"videoId": video_id},
        )

        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Rutube Get (Dynamic) finished in {elapsed:.2f}ms for {track_url}")
        return [track]

    def _resolve_stream_url(self, video_id: str) -> Optional[str]:
        client = self._get_client()
        try:
            resp = client.get(
                f"https://rutube.ru/api/play/options/{video_id}/",
                params={"format": "json"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as e:
            logging.error(f"Rutube resolve failed for {video_id}: {e}")
            return None

        balancer = data.get("video_balancer", {})
        m3u8 = balancer.get("m3u8", "")

        if not m3u8:
            live = data.get("live_streams", {})
            m3u8 = live.get("m3u8", "")

        return m3u8 if m3u8 else None

    def download(self, track: Track, file_path: str, video: bool = False) -> None:
        info = track.extra_info or {}
        video_id = info.get("videoId") or self._extract_video_id(track.url)

        if video_id:
            stream_url = self._resolve_stream_url(video_id)
            if stream_url:
                import subprocess
                cmd = [
                    "ffmpeg", "-y", "-i", stream_url,
                    "-c", "copy", file_path,
                ]
                subprocess.run(cmd, capture_output=True, timeout=300)
                return

        downloader = __import__("downloader")
        downloader.download_file(track.url, file_path)
