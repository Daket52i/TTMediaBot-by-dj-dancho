from __future__ import annotations
import logging
import time
import threading
import os
import json
import http.cookiejar
import requests
import httpx

class HTTP2Session(requests.Session):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        limits = httpx.Limits(max_keepalive_connections=5, keepalive_expiry=30.0)
        self.httpx_client = httpx.Client(http2=True, limits=limits, timeout=30.0)

    def request(self, method, url, **kwargs):
        hk = {}
        for k in ['headers', 'params', 'data', 'json', 'cookies', 'auth', 'verify', 'cert']:
            if k in kwargs:
                hk[k] = kwargs[k]
        if 'timeout' in kwargs:
            hk['timeout'] = kwargs['timeout']
        if 'allow_redirects' in kwargs:
            hk['follow_redirects'] = kwargs['allow_redirects']
        try:
            r = self.httpx_client.request(method, url, **hk)
            resp = requests.Response()
            resp.status_code = r.status_code
            resp._content = r.content
            resp.headers.update(r.headers)
            resp.url = str(r.url)
            return resp
        except Exception as e:
            return super().request(method, url, **kwargs)
from typing import Any, Dict, List, Optional, TYPE_CHECKING

if TYPE_CHECKING:
    from bot import Bot

from ytmusicapi import YTMusic

from bot.config.models import YtmModel
from bot.player.enums import TrackType
from bot.player.track import Track
from bot.services import Service as _Service
from bot.services.youtube_bridge import YouTubeBridge
from bot import errors


class YtmService(_Service):
    def __init__(self, bot: Bot, config: YtmModel):
        self.bot = bot
        self.config = config
        self.name = "ytm"
        self.hostnames = []
        self.is_enabled = self.config.enabled
        self.error_message = ""
        self.warning_message = ""
        self.help = ""
        self.hidden = False
        self.ytmusic = None
        self.yt_config = bot.config.services.yt
        self._cookie_lock = threading.Lock()
        self._warm_lock = threading.Lock()
        self._is_warmed = False
        self._max_retries = 2
        
    def _fetch_and_queue_autoplay(self, video_id: str, original_url: str):
        """Background task to fetch Watch Playlist and add to queue."""
        try:
            logging.info(f"[YTM] Starting background Autoplay fetch for video_id={video_id}")
            start_time = time.perf_counter()
            
            # radio=False ensures we get the "Up Next" / Autoplay queue
            watch_playlist = (self.ytmusic or self.ytmusic_public).get_watch_playlist(videoId=video_id, limit=50, radio=False)
            tracks_data = watch_playlist.get("tracks", [])
            
            new_tracks: List[Track] = []
            # Skip the first track usually as it is the current one, BUT get_watch_playlist 
            # might return the current one as first item.
            # We want to add RECOMMENDATIONS to the queue.
            # If the first item is the same video_id, skip it.
            
            for item in tracks_data:
                t_video_id = item.get("videoId")
                if t_video_id == video_id:
                    continue
                    
                t_title = item.get("title")
                t_artist = ""
                if "artists" in item:
                     t_artist = ", ".join([a["name"] for a in item["artists"]])
                
                full_title = f"{t_title} - {t_artist}" if t_artist else t_title
                # Optimization: Use www.youtube.com for faster extraction later
                t_url = f"https://www.youtube.com/watch?v={t_video_id}"
                
                new_tracks.append(
                     Track(service=self.name, url=t_url, name=full_title, type=TrackType.Dynamic, extra_info=item)
                )
            
            if new_tracks:
                # Add to bot queue safely
                self.bot.player.track_list.extend(new_tracks)
                
                duration = (time.perf_counter() - start_time) * 1000
                logging.info(f"[YTM] Background Autoplay fetch added {len(new_tracks)} tracks in {duration:.2f}ms")
            else:
                logging.info("[YTM] Background Autoplay fetch found no new tracks.")
                
        except Exception as e:
            logging.error(f"[YTM] Background Autoplay fetch failed: {e}")

    def initialize(self):
        # Validate cookie file at startup
        cookie_path = None
        if self.yt_config and self.yt_config.cookiefile_path:
            cookie_path = self.yt_config.cookiefile_path
            if os.path.isfile(cookie_path):
                logging.info(f"YTM Service: Cookie file found at {cookie_path}")
            else:
                logging.warning(
                    f"YTM Service: Cookie file NOT FOUND at '{cookie_path}'. "
                    "YouTube may block requests."
                )

        self.cookiejar = None
        auth = None
        if cookie_path and os.path.isfile(cookie_path):
             try:
                 # Parse Netscape cookies to build a Cookie header
                 self.cookiejar = http.cookiejar.MozillaCookieJar(cookie_path)
                 self.cookiejar.load(ignore_discard=True, ignore_expires=True)
                 
                 cookie_header_parts = []
                 sapisid = ""
                 for cookie in self.cookiejar:
                     if "youtube" in cookie.domain or "google" in cookie.domain:
                         cookie_header_parts.append(f"{cookie.name}={cookie.value}")
                     if cookie.name == "SAPISID":
                         sapisid = cookie.value
                 
                 if cookie_header_parts:
                     # 1. Extract cookies to string
                     cookie_string = "; ".join(cookie_header_parts)
                     
                     # 3. Construct headers dict
                     headers = {
                         "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
                         "accept-language": "en-US",
                         "content-type": "application/json",
                         "cookie": cookie_string,
                         "accept": "*/*",
                         "x-goog-authuser": "0",
                         "x-origin": "https://music.youtube.com"
                     }
                     
                     # 4. Generate Authorization header if SAPISID is available
                     if sapisid:
                         try:
                             from ytmusicapi.helpers import get_authorization
                             auth_header = get_authorization(sapisid + " " + "https://music.youtube.com")
                             headers["authorization"] = auth_header
                         except ImportError:
                             import hashlib
                             timestamp = str(int(time.time()))
                             payload = f"{timestamp} {sapisid} https://music.youtube.com"
                             sha = hashlib.sha1(payload.encode("utf-8")).hexdigest()
                             headers["authorization"] = f"SAPISIDHASH {timestamp}_{sha}"
                     
                     auth = headers
             except Exception as e:
                 logging.error(f"Failed to parse cookies for YTM: {e}")

        # Instantiate HTTP2Session
        self.http2_session = HTTP2Session()

        if auth and isinstance(auth, dict) and "authorization" in auth:
             self.ytmusic = YTMusic(auth=auth, requests_session=self.http2_session)
        else:
             # Fallback to public instance if auth generation failed
             self.ytmusic = YTMusic(requests_session=self.http2_session)
        
        # Explicit public instance for search/metadata (User Request: No cookies for search)
        self.ytmusic_public = YTMusic(requests_session=self.http2_session)

        # Connection Keep-Alive to prevent TCP/SSL handshake lag
        threading.Thread(target=self._connection_keeper, daemon=True).start()

        self._bridge = YouTubeBridge(self.yt_config.cookiefile_path, client="YTMUSIC")

        # Run pre-warming in a background thread so the bot connects to TeamTalk immediately
        threading.Thread(target=self._pre_warm, daemon=True, name="YTM_PreWarm").start()

    def _pre_warm(self):
        if self._is_warmed:
            return
        with self._warm_lock:
            if self._is_warmed:
                return
            self._bridge.wait_ready(timeout=5.0)
            for attempt in range(1, 4):
                try:
                    logging.info(f"YTM Service pre-warming (attempt {attempt}/3)...")
                    self.ytmusic_public.search("music", filter="songs", limit=1)
                    self._is_warmed = True
                    logging.info("YTM Service pre-warming finished successfully.")
                    return
                except Exception as e:
                    if attempt < 3:
                        logging.warning(f"YTM Pre-warming attempt {attempt} failed: {e}. Retrying in 0.5 seconds...")
                        time.sleep(0.5)
                    else:
                        logging.error(f"YTM Pre-warming failed after 3 attempts: {e}")

    def download(self, track: Track, file_path: str, video: bool = False) -> None:
        start_time = time.perf_counter()
        info = track.extra_info or {}
        video_id = info.get("videoId") or info.get("id")
        source_url = f"https://www.youtube.com/watch?v={video_id}" if video_id else track.url
        self._bridge.download(source_url, file_path, video=video)
        duration = (time.perf_counter() - start_time) * 1000
        logging.info(f"YTM Download finished in {duration:.2f}ms for {track.name}")

    def get(
        self,
        url: str,
        extra_info: Optional[Dict[str, Any]] = None,
        process: bool = False,
    ) -> List[Track]:
        start_time = time.perf_counter()
        if not (url or extra_info):
            raise errors.InvalidArgumentError()

        # Stream resolution is handled by the persistent YouTube.js bridge.
        if process:
            info = dict(extra_info or {})
            video_id = info.get("videoId") or info.get("id")
            resolved = self._bridge.resolve(url=url if not video_id else "", video_id=video_id or "")
            stream = {**info, **resolved}
            stream_url = resolved.get("url")
            if not stream_url:
                raise errors.ServiceError("YouTube.js returned no stream URL")

            title = resolved.get("title") or info.get("title") or self.bot.translator.translate("Unknown")
            uploader = resolved.get("uploader")
            if uploader:
                title += f" - {uploader}"

            current_video_id = resolved.get("id") or video_id
            if current_video_id:
                try:
                    remaining = len(self.bot.player.track_list) - 1 - self.bot.player.track_index
                    if remaining <= 4:
                        self._fetch_autoplay_async(current_video_id)
                except Exception as e:
                    logging.debug(f"[YTM] Trace bot player state error: {e}")

            duration = (time.perf_counter() - start_time) * 1000
            logging.info(f"YTM Get (Process/YouTube.js) finished in {duration:.2f}ms for {title}")
            return [
                Track(
                    service=self.name,
                    name=title,
                    url=stream_url,
                    type=TrackType.Live if resolved.get("is_live") else TrackType.Default,
                    format="mp3",
                    extra_info=stream,
                    extracted_at=time.perf_counter(),
                )
            ]

        # If process=False, we are adding to queue (The "Radio" logic)
        if extra_info and not url:
             t_title = extra_info.get("title", "")
             t_vid = extra_info.get("videoId") or extra_info.get("id")
             t_url = f"https://www.youtube.com/watch?v={t_vid}" if t_vid else ""
             return [Track(service=self.name, url=t_url, name=t_title, type=TrackType.Dynamic, extra_info=extra_info)]

        video_id = None
        if extra_info and "videoId" in extra_info:
             video_id = extra_info["videoId"]
        elif url:
             if "v=" in url:
                  video_id = url.split("v=")[1].split("&")[0]
             elif "youtu.be" in url:
                  video_id = url.split("/")[-1]
        
        if not video_id:
             return [Track(service=self.name, url=url, type=TrackType.Dynamic)]

        # 2. Get Watch Playlist (Autoplay)
        try:
             watch_playlist = (self.ytmusic or self.ytmusic_public).get_watch_playlist(videoId=video_id, limit=20, radio=False)
             tracks_data = watch_playlist.get("tracks", [])
             
             new_tracks: List[Track] = []
             for item in tracks_data:
                  t_title = item.get("title")
                  t_artist = ""
                  if "artists" in item:
                       t_artist = ", ".join([a["name"] for a in item["artists"]])
                  
                  full_title = f"{t_title} - {t_artist}" if t_artist else t_title
                  t_video_id = item.get("videoId")
                  t_url = f"https://www.youtube.com/watch?v={t_video_id}"
                  
                  new_tracks.append(
                       Track(service=self.name, url=t_url, name=full_title, type=TrackType.Dynamic, extra_info=item)
                  )
             
             duration = (time.perf_counter() - start_time) * 1000
             logging.info(f"YTM Get (Watch Playlist) finished in {duration:.2f}ms for video_id {video_id}")
             return new_tracks

        except Exception as e:
             logging.error(f"YTM Watch Playlist failed: {e}")
             duration = (time.perf_counter() - start_time) * 1000
             logging.info(f"YTM Get (Fallback) finished in {duration:.2f}ms for {url}")
             return [Track(service=self.name, url=url, type=TrackType.Dynamic)]

    def _fetch_autoplay_async(self, video_id: str) -> None:
         threading.Thread(target=self._fetch_autoplay_sync, args=(video_id,), daemon=True, name=f"Autoplay_{video_id}").start()

    def _fetch_autoplay_sync(self, video_id: str) -> bool:
         try:
              logging.info(f"[YTM] Fetching continuous recommendations for {video_id}")
              watch_playlist = (self.ytmusic or self.ytmusic_public).get_watch_playlist(videoId=video_id, limit=50, radio=True)
              tracks_data = watch_playlist.get("tracks", [])
              if not tracks_data:
                  watch_playlist = (self.ytmusic or self.ytmusic_public).get_watch_playlist(videoId=video_id, limit=50, radio=False)
                  tracks_data = watch_playlist.get("tracks", [])
              
              if tracks_data:
                   current_idx = self.bot.player.track_index
                   recent_tracks = self.bot.player.track_list[max(0, current_idx - 15):]
                   existing_ids = set()
                   for t in recent_tracks:
                        t_info = getattr(t, "extra_info", None) or {}
                        vid = t_info.get("videoId") or t_info.get("id") or t_info.get("contentId")
                        if vid:
                             existing_ids.add(vid)
                   existing_ids.add(video_id)

                   new_tracks = []
                   for t_info in tracks_data:
                        v_id = t_info.get('videoId')
                        if not v_id or v_id in existing_ids:
                             continue
                        existing_ids.add(v_id)
                        title = t_info.get('title', '')
                        if 'artists' in t_info and isinstance(t_info['artists'], list):
                             artists = ", ".join([a.get('name', '') for a in t_info['artists'] if isinstance(a, dict) and a.get('name')])
                             if artists:
                                  title += f" - {artists}"
                        track = Track(
                             service=self.name,
                             name=title or "Unknown",
                             url=f"https://www.youtube.com/watch?v={v_id}",
                             type=TrackType.Dynamic,
                             extra_info=t_info
                        )
                        new_tracks.append(track)
                        if len(new_tracks) >= 15:
                             break
                   
                   if new_tracks:
                        logging.info(f"[YTM] Adding {len(new_tracks)} continuous recommendations to track list (total: {len(self.bot.player.track_list) + len(new_tracks)})")
                        self.bot.player.track_list.extend(new_tracks)
                        return True
                   else:
                        logging.info(f"[YTM] No new unique recommendations found for video_id {video_id}")
         except Exception as e:
              logging.error(f"[YTM] Autoplay fetch failed: {e}")
         return False

    def search(self, query: str, limit: Optional[int] = None) -> List[Track]:
        if limit is None:
            limit = self.config.search_results
        start_time = time.perf_counter()
        results = self.ytmusic_public.search(query, filter="songs", limit=limit)
        if not results:
             raise errors.NothingFoundError("")
        
        results = results[:limit]
        
        duration = (time.perf_counter() - start_time) * 1000
        logging.info(f"YTM Search (Fast) finished in {duration:.2f}ms for query: {query}")
        
        return self._create_tracks_from_results(results)

    def _create_tracks_from_results(self, results: List[Dict[str, Any]]) -> List[Track]:
        tracks: List[Track] = []
        for item in results:
             t_title = item.get("title")
             t_artist = ""
             if "artists" in item:
                  t_artist = ", ".join([a["name"] for a in item["artists"]])
             
             full_title = f"{t_title} - {t_artist}" if t_artist else t_title
             t_video_id = item.get("videoId")
             t_url = f"https://www.youtube.com/watch?v={t_video_id}"
             
             tracks.append(
                  Track(service=self.name, url=t_url, name=full_title, type=TrackType.Dynamic, extra_info=item)
             )
        return tracks

    def _connection_keeper(self):
        while True:
            time.sleep(4)
            try:
                if self.ytmusic_public and hasattr(self.ytmusic_public, "_session"):
                     self.ytmusic_public._session.get("https://music.youtube.com/generate_204", timeout=5)
            except Exception:
                pass
