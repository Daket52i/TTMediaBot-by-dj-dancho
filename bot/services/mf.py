from __future__ import annotations
import logging
import time
import re
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Bot

from bot.config.models import MfModel
from bot.player.enums import TrackType
from bot.player.track import Track
from bot.services import Service as _Service
from bot import errors

import httpx
from bs4 import BeautifulSoup


class MfService(_Service):
    def __init__(self, bot: Bot, config: MfModel):
        self.bot = bot
        self.config = config
        self.name = "mf"
        self.hostnames = ["muzofond.fm", "www.muzofond.fm"]
        self.is_enabled = self.config.enabled
        self.error_message = ""
        self.warning_message = ""
        self.help = ""
        self.hidden = False
        self._headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            "Referer": "https://muzofond.fm/",
        }
        self._client: Optional[httpx.Client] = None

    def initialize(self):
        self._client = httpx.Client(
            headers=self._headers,
            follow_redirects=True,
            timeout=15.0,
        )
        logging.info("Muzofond Service initialized")

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                headers=self._headers,
                follow_redirects=True,
                timeout=15.0,
            )
        return self._client

    def _parse_search_page(self, html: str, limit: int) -> List[Dict[str, Any]]:
        soup = BeautifulSoup(html, "html.parser")
        tracks: List[Dict[str, Any]] = []

        items = soup.select(".musicItem, .playlist-item, .track-item, [class*=item]")

        if not items:
            items = soup.select("div[data-id], li[data-id], .search-item")

        if not items:
            audio_tags = soup.select("audio")
            for audio in audio_tags[:limit]:
                src = audio.get("src", "") or audio.get("data-src", "")
                if not src:
                    source = audio.select_one("source")
                    if source:
                        src = source.get("src", "")
                if src:
                    parent = audio.parent
                    title = ""
                    if parent:
                        title_el = parent.select_one(".title, .name, .track-name, h3, h4, a")
                        if title_el:
                            title = title_el.get_text(strip=True)
                    tracks.append({
                        "title": title or "Unknown",
                        "url": src,
                        "artist": "",
                    })

        if not tracks:
            all_links = soup.select("a[href*='/pesnya/'], a[href*='/track/']")
            for link in all_links[:limit]:
                href = link.get("href", "")
                title = link.get_text(strip=True)
                if title and href:
                    tracks.append({
                        "title": title,
                        "url": f"https://muzofond.fm{href}" if href.startswith("/") else href,
                        "artist": "",
                        "is_page": True,
                    })

        if not tracks:
            for tag in soup.select("[data-url], [data-audio], [data-mp3]"):
                url = tag.get("data-url") or tag.get("data-audio") or tag.get("data-mp3")
                title = tag.get_text(strip=True)
                if url:
                    tracks.append({
                        "title": title or "Unknown",
                        "url": url,
                        "artist": "",
                    })

        return tracks[:limit]

    def _extract_audio_url(self, page_url: str) -> Optional[str]:
        client = self._get_client()
        try:
            resp = client.get(page_url)
            resp.raise_for_status()
            html = resp.text
        except Exception as e:
            logging.error(f"Muzofond page fetch failed: {e}")
            return None

        patterns = [
            r'(?:src|url|file)\s*[:=]\s*["\']?(https?://[^"\'>\s]+\.mp3[^"\'>\s]*)',
            r'(?:src|url|file)\s*[:=]\s*["\']?(https?://[^"\'>\s]+\.m4a[^"\'>\s]*)',
            r'(?:src|url|file)\s*[:=]\s*["\']?(https?://[^"\'>\s]+audio[^"\'>\s]*)',
            r'audio[^>]*src\s*=\s*["\']?(https?://[^"\'>\s]+)',
        ]
        for pattern in patterns:
            match = re.search(pattern, html, re.IGNORECASE)
            if match:
                return match.group(1).rstrip("'\"")

        soup = BeautifulSoup(html, "html.parser")
        audio = soup.select_one("audio")
        if audio:
            src = audio.get("src", "")
            if not src:
                source = audio.select_one("source")
                if source:
                    src = source.get("src", "")
            if src:
                return src

        return None

    def search(self, query: str, limit: Optional[int] = None) -> List[Track]:
        if limit is None:
            limit = self.config.search_results
        start_time = time.perf_counter()

        client = self._get_client()
        try:
            resp = client.get(f"https://muzofond.fm/search/{query}")
            resp.raise_for_status()
        except Exception as e:
            logging.error(f"Muzofond search failed: {e}")
            raise errors.NothingFoundError(str(e))

        raw_tracks = self._parse_search_page(resp.text, limit)
        if not raw_tracks:
            raise errors.NothingFoundError("")

        tracks: List[Track] = []
        for item in raw_tracks:
            url = item.get("url", "")
            title = item.get("title", "")
            artist = item.get("artist", "")
            full_title = f"{artist} - {title}" if artist and artist not in title else title

            if item.get("is_page"):
                track = Track(
                    service=self.name,
                    url=url,
                    name=full_title,
                    type=TrackType.Dynamic,
                    extra_info={"page_url": url, "title": title, "artist": artist},
                )
            else:
                track = Track(
                    service=self.name,
                    url=url,
                    name=full_title,
                    format="mp3",
                    type=TrackType.Default,
                    extra_info={"stream_url": url, "title": title, "artist": artist},
                    extracted_at=time.perf_counter(),
                )
                track._is_fetched = True

            tracks.append(track)

        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Muzofond Search finished in {elapsed:.2f}ms for query: {query}")
        return tracks

    def get(
        self,
        url: str,
        extra_info: Optional[Dict[str, Any]] = None,
        process: bool = False,
    ) -> List[Track]:
        start_time = time.perf_counter()

        info = dict(extra_info or {})
        page_url = info.get("page_url") or url

        if process:
            audio_url = self._extract_audio_url(page_url)
            if not audio_url:
                stream = info.get("stream_url", "")
                if stream:
                    audio_url = stream
                else:
                    raise errors.ServiceError("Muzofond: Could not extract audio URL")

            title = info.get("title", "")
            artist = info.get("artist", "")
            full_title = f"{artist} - {title}" if artist and artist not in title else title

            elapsed = (time.perf_counter() - start_time) * 1000
            logging.info(f"Muzofond Get (Process) finished in {elapsed:.2f}ms for {full_title}")

            return [
                Track(
                    service=self.name,
                    url=audio_url,
                    name=full_title or "Muzofond Track",
                    format="mp3",
                    type=TrackType.Default,
                    extra_info=info,
                    extracted_at=time.perf_counter(),
                )
            ]

        stream_url = info.get("stream_url", "")
        if stream_url:
            return [Track(
                service=self.name,
                url=stream_url,
                name=info.get("title", ""),
                format="mp3",
                type=TrackType.Default,
                extra_info=info,
                extracted_at=time.perf_counter(),
            )]

        track = Track(
            service=self.name,
            url=page_url,
            name=info.get("title", ""),
            type=TrackType.Dynamic,
            extra_info=info or {"page_url": page_url},
        )

        elapsed = (time.perf_counter() - start_time) * 1000
        logging.info(f"Muzofond Get (Dynamic) finished in {elapsed:.2f}ms for {page_url}")
        return [track]

    def download(self, track: Track, file_path: str, video: bool = False) -> None:
        info = track.extra_info or {}
        audio_url = info.get("stream_url", "")

        if not audio_url:
            page_url = info.get("page_url") or track.url
            audio_url = self._extract_audio_url(page_url)

        if audio_url:
            downloader = __import__("downloader")
            downloader.download_file(audio_url, file_path)
        else:
            downloader = __import__("downloader")
            downloader.download_file(track.url, file_path)
