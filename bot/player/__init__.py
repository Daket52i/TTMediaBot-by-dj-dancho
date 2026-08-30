from __future__ import annotations
import html
import logging
import time
import threading
from typing import Any, Dict, Callable, List, Optional, TYPE_CHECKING
import random

import mpv

from bot import errors
from bot.player.enums import Mode, State, TrackType
from bot.player.track import Track
from bot.player.queue_manager import QueueManager
from bot.sound_devices import SoundDevice, SoundDeviceType


if TYPE_CHECKING:
    from bot import Bot


class Player:
    def __init__(self, bot: Bot):
        self.config = bot.config.player
        self.cache = bot.cache
        self.cache_manager = bot.cache_manager
        mpv_options = {
            "demuxer_lavf_o": "http_persistent=false",
            "demuxer_max_back_bytes": 524288,
            "demuxer_max_bytes": 262144,
            "demuxer_readahead_secs": 2,
            "video": False,
            "user_agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36",
            "ytdl": False,
        }
        mpv_options.update(self.config.player_options)
        try:
            self._player = mpv.MPV(**mpv_options, log_handler=self.log_handler)
        except AttributeError:
            del mpv_options["demuxer_max_back_bytes"]
            self._player = mpv.MPV(**mpv_options, log_handler=self.log_handler)
        self._log_level = 5
        self.track_list: List[Track] = []
        self.track: Track = Track()
        self.track_index: int = -1
        self.state = State.Stopped
        self.mode = Mode.TrackList
        self.volume = self.config.default_volume
        self.is_playlist: bool = False
        self._navigation_lock = threading.RLock()

        self.queue: QueueManager = QueueManager()

    def initialize(self) -> None:
        logging.debug("Initializing player")
        logging.debug("Player initialized")

    def run(self) -> None:
        logging.debug("Registering player callbacks")
        self.register_event_callback("end-file", self.on_end_file)
        self._player.observe_property("metadata", self.on_metadata_update)
        self._player.observe_property("media-title", self.on_metadata_update)
        logging.debug("Player callbacks registered")

    def close(self) -> None:
        logging.debug("Closing player")
        if self.state != State.Stopped:
            self.stop()
        self._player.terminate()
        logging.debug("Player closed")

    def play(
        self,
        tracks: Optional[List[Track]] = None,
        start_track_index: Optional[int] = None,
        is_playlist: Optional[bool] = None,
    ) -> None:
        if tracks != None:
            self.track_list = tracks
            if is_playlist is not None:
                self.is_playlist = is_playlist
            else:
                self.is_playlist = len(tracks) > 1
            if not start_track_index and self.mode == Mode.Random:
                self.shuffle(True)
                self.track_index = self._index_list[0]
                self.track = self.track_list[self.track_index]
            else:
                self.track_index = start_track_index if start_track_index else 0
                self.track = tracks[self.track_index]
            self._play(self.track.url)
        else:
            self._player.pause = False
        self._player.volume = self.volume
        self.state = State.Playing

    def pause(self) -> None:
        self.state = State.Paused
        self._player.pause = True

    def stop(self) -> None:
        self.state = State.Stopped
        self._player.stop()
        self.track_list = []
        self.track = Track()
        self.track_index = -1
        self.is_playlist = False

    def _play(self, arg: str, save_to_recents: bool = True) -> None:
        if save_to_recents:
            try:
                if self.cache.recents[-1] != self.track_list[self.track_index]:
                    self.cache.recents.append(
                        self.track_list[self.track_index].get_raw()
                    )
            except:
                self.cache.recents.append(self.track_list[self.track_index].get_raw())
            self.cache_manager.save()
            
        # Apply headers dynamically if available in extra_info to prevent User-Agent/domain mismatches
        extra_info = getattr(self.track, "extra_info", None) or {}
        headers = extra_info.get("http_headers", {})
        if headers:
            try:
                header_fields = [f"{k}: {v}" for k, v in headers.items() if k.lower() != "user-agent"]
                self._player.user_agent = headers.get("User-Agent")
                self._player.http_header_fields = header_fields
                logging.debug(f"[Player] Dynamic headers applied to MPV")
            except Exception as e:
                logging.debug(f"[Player] Failed to apply dynamic headers to MPV: {e}")
                
        self._player.pause = False
        self._player.play(arg)
        threading.Timer(1.0, self._prefetch_next_track).start()

    def _sync_index_list(self) -> None:
        if self.mode == Mode.Random:
            if not hasattr(self, "_index_list") or not self._index_list:
                self.shuffle(True, preserve_current=True)
            elif len(self._index_list) < len(self.track_list):
                existing = set(self._index_list)
                missing = [i for i in range(len(self.track_list)) if i not in existing]
                random.shuffle(missing)
                self._index_list.extend(missing)

    def _prefetch_next_track(self) -> None:
        try:
            # Se há faixa na fila, ela será a próxima — prefetch dela
            next_from_queue = self.queue.peek_next()
            if next_from_queue is not None:
                if not next_from_queue._is_fetched:
                    logging.info(f"Prefetching next track from queue: {next_from_queue.name}")
                    _ = next_from_queue.url
                    logging.info(f"Prefetch from queue completed: {next_from_queue.name}")
                return

            if not self.track_list:
                return

            next_index = -1
            if self.mode == Mode.Random:
                self._sync_index_list()
                try:
                    current_pos = self._index_list.index(self.track_index)
                    if current_pos + 1 < len(self._index_list):
                        next_index = self._index_list[current_pos + 1]
                    elif len(self._index_list) > 0:
                        next_index = self._index_list[0]
                except (ValueError, IndexError, AttributeError):
                    pass
            elif self.mode == Mode.RepeatTrack:
                next_index = self.track_index
            else:
                if self.track_index + 1 < len(self.track_list):
                    next_index = self.track_index + 1
                elif self.mode == Mode.RepeatTrackList and len(self.track_list) > 0:
                    next_index = 0

            if next_index != -1 and next_index < len(self.track_list):
                next_track = self.track_list[next_index]
                if not next_track._is_fetched:
                    logging.info(f"Prefetching next track: {next_track.name}")
                    _ = next_track.url
                    logging.info(f"Prefetch completed for: {next_track.name}")
        except Exception as e:
            logging.warning(f"Prefetch failed: {e}")

    def play_from_queue(self) -> bool:
        next_track = self.queue.pop_next()
        if next_track is None:
            return False

        logging.info(f"Playing from queue: {next_track.name}")
        self.track_list = [next_track]
        self.track_index = 0
        self.track = next_track
        self._play(next_track.url)
        self.state = State.Playing
        return True

    def _get_track_video_id(self, track: Optional[Track]) -> Optional[str]:
        if not track:
            return None
        info = getattr(track, "extra_info", None) or {}
        vid = info.get("videoId") or info.get("id") or info.get("contentId")
        if vid:
            return str(vid)
        url = getattr(track, "_url", "")
        if url:
            if "v=" in url:
                return url.split("v=")[1].split("&")[0].split("?")[0]
            elif "youtu.be" in url:
                return url.split("/")[-1].split("?")[0]
        return None

    def _check_and_trigger_autoplay(self) -> None:
        try:
            if not self.track_list or self.mode == Mode.SingleTrack or self.is_playlist:
                return
            remaining = len(self.track_list) - 1 - self.track_index
            if remaining <= 4:
                candidates = []
                if self.track:
                    candidates.append(self.track)
                for t in reversed(self.track_list):
                    if t not in candidates:
                        candidates.append(t)

                for cand in candidates:
                    target_id = self._get_track_video_id(cand)
                    if target_id:
                        service_name = getattr(cand, "service", None) or self.bot.service_manager.service.name
                        service = self.bot.service_manager.get_service_by_name(service_name)
                        if hasattr(service, "_fetch_autoplay_async"):
                            service._fetch_autoplay_async(target_id)
                            break
        except Exception as e:
            logging.debug(f"[Player] Autoplay check trigger error: {e}")

    def _replenish_autoplay_sync(self) -> bool:
        try:
            if not self.track_list or self.mode == Mode.SingleTrack or self.is_playlist:
                return False
            candidates = []
            if self.track:
                candidates.append(self.track)
            for t in reversed(self.track_list):
                if t not in candidates:
                    candidates.append(t)

            for cand in candidates:
                target_id = self._get_track_video_id(cand)
                if target_id:
                    service_name = getattr(cand, "service", None) or self.bot.service_manager.service.name
                    service = self.bot.service_manager.get_service_by_name(service_name)
                    if hasattr(service, "_fetch_autoplay_sync"):
                        if service._fetch_autoplay_sync(target_id):
                            return True
        except Exception as e:
            logging.warning(f"[Player] Sync autoplay replenishment error: {e}")
        return False

    def next(self) -> None:
        with self._navigation_lock:
            self._next_locked()

    def _next_locked(self) -> None:
        if not self.queue.is_empty:
            if self.play_from_queue():
                return

        track_index = self.track_index
        if len(self.track_list) > 0:
            if self.mode == Mode.Random:
                self._sync_index_list()
                try:
                    current_position = self._index_list.index(self.track_index)
                    if current_position + 1 < len(self._index_list):
                        track_index = self._index_list[current_position + 1]
                    else:
                        if self.is_playlist and self.mode != Mode.RepeatTrackList:
                            raise errors.NoNextTrackError()
                        self.shuffle(True)
                        track_index = self._index_list[0] if self._index_list else 0
                except (IndexError, ValueError, AttributeError):
                    if self.is_playlist and self.mode != Mode.RepeatTrackList:
                        raise errors.NoNextTrackError()
                    self.shuffle(True)
                    track_index = self._index_list[0] if self._index_list else 0
            else:
                track_index += 1
        else:
            track_index = 0

        if track_index >= len(self.track_list):
            if self.mode == Mode.RepeatTrackList:
                self.play_by_index(0)
                return
            if not self.is_playlist:
                self._replenish_autoplay_sync()
            if track_index >= len(self.track_list):
                raise errors.NoNextTrackError()

        try:
            self.play_by_index(track_index)
        except errors.IncorrectTrackIndexError:
            if self.mode == Mode.RepeatTrackList:
                self.play_by_index(0)
            elif self.mode == Mode.Random and not self.is_playlist:
                self.shuffle(True)
                self.play_by_index(self._index_list[0] if self._index_list else 0)
            else:
                raise errors.NoNextTrackError()

    def previous(self) -> None:
        track_index = self.track_index
        if len(self.track_list) > 0:
            if self.mode == Mode.Random:
                self._sync_index_list()
                try:
                    current_position = self._index_list.index(self.track_index)
                    if current_position > 0:
                        track_index = self._index_list[current_position - 1]
                    else:
                        track_index = self._index_list[-1]
                except (IndexError, ValueError, AttributeError):
                    track_index = self.track_index
            else:
                if track_index == 0 and self.mode != Mode.RepeatTrackList:
                    raise errors.NoPreviousTrackError
                else:
                    track_index -= 1
        else:
            track_index = 0
        try:
            self.play_by_index(track_index)
        except errors.IncorrectTrackIndexError:
            if self.mode == Mode.RepeatTrackList:
                self.play_by_index(len(self.track_list) - 1)
            else:
                raise errors.NoPreviousTrackError

    def play_by_index(self, index: int) -> None:
        if index < len(self.track_list) and index >= (0 - len(self.track_list)):
            self.track = self.track_list[index]
            self.track_index = self.track_list.index(self.track)
            try:
                self._play(self.track.url)
                self.state = State.Playing
                self._check_and_trigger_autoplay()
            except errors.ServiceError as e:
                logging.warning(f"[Player] Track '{self.track.name}' is unplayable ({e}). Auto-skipping to next track...")
                if self.mode != Mode.SingleTrack and len(self.track_list) > index + 1:
                    self.next()
                else:
                    raise
        else:
            raise errors.IncorrectTrackIndexError()

    def set_volume(self, volume: int) -> None:
        volume = volume if volume <= self.config.max_volume else self.config.max_volume
        self.volume = volume
        if self.config.volume_fading:
            n = 1 if self._player.volume < volume else -1
            for i in range(int(self._player.volume), volume, n):
                self._player.volume = i
                time.sleep(self.config.volume_fading_interval)
        else:
            self._player.volume = volume

    def get_speed(self) -> float:
        return self._player.speed

    def set_speed(self, arg: float) -> None:
        if arg < 0.25 or arg > 4:
            raise ValueError()
        self._player.speed = arg

    def seek_back(self, step: Optional[float] = None) -> None:
        step = step if step else self.config.seek_step
        if step <= 0:
            raise ValueError()
        try:
            self._player.seek(-step, reference="relative")
        except SystemError:
            self.stop()

    def seek_forward(self, step: Optional[float] = None) -> None:
        step = step if step else self.config.seek_step
        if step <= 0:
            raise ValueError()
        try:
            self._player.seek(step, reference="relative")
        except SystemError:
            self.stop()

    def get_duration(self) -> float:
        return self._player.duration

    """def get_position(self) -> float:
        return self._player.time_pos

    def set_position(self, arg: float) -> None:
        if arg < 0:
            raise errors.IncorrectPositionError()
        self._player.seek(arg, reference="absolute")"""

    def get_output_devices(self) -> List[SoundDevice]:
        devices: List[SoundDevice] = []
        for device in self._player.audio_device_list:
            devices.append(
                SoundDevice(
                    device["description"], device["name"], SoundDeviceType.Output
                )
            )
        return devices

    def set_output_device(self, id: str) -> None:
        self._player.audio_device = id

    def shuffle(self, enable: bool, preserve_current: bool = False) -> None:
        if enable:
            if not self.track_list:
                self._index_list = []
                return
            indices = list(range(len(self.track_list)))
            if preserve_current and self.track_index in indices:
                indices.remove(self.track_index)
                random.shuffle(indices)
                self._index_list = [self.track_index] + indices
            else:
                random.shuffle(indices)
                self._index_list = indices
            logging.info(f"[Player] Shuffled playlist of {len(self.track_list)} tracks 100% randomly (preserve_current={preserve_current}). First picked track index: {self._index_list[0] if self._index_list else None}")
        else:
            if hasattr(self, "_index_list"):
                del self._index_list

    def register_event_callback(
        self, callback_name: str, callback_func: Callable[[mpv.MpvEvent], None]
    ) -> None:
        self._player.event_callback(callback_name)(callback_func)

    def log_handler(self, level: str, component: str, message: str) -> None:
        logging.log(self._log_level, "{}: {}: {}".format(level, component, message))

    def _parse_metadata(self, metadata: Dict[str, Any]) -> str:
        stream_names = ["icy-name"]
        stream_name = None
        title = None
        artist = None
        for i in metadata:
            if i in stream_names:
                stream_name = html.unescape(metadata[i])
            if "title" in i:
                title = html.unescape(metadata[i])
            if "artist" in i:
                artist = html.unescape(metadata[i])
        chunks: List[str] = []
        chunks.append(artist) if artist else ...
        chunks.append(title) if title else ...
        chunks.append(stream_name) if stream_name else ...
        return " - ".join(chunks)

    def on_end_file(self, event: mpv.MpvEvent) -> None:
        if self.state == State.Playing and self._player.idle_active:
            if self.mode == Mode.SingleTrack or self.track.type == TrackType.Direct:
                # Mesmo em SingleTrack/Direct, a fila tem prioridade
                if not self.queue.is_empty:
                    self.play_from_queue()
                else:
                    self.stop()
            elif self.mode == Mode.RepeatTrack:
                # RepeatTrack repete a atual — fila NÃO interrompe automaticamente
                # O usuário pode usar 'qs' para pular para a fila manualmente
                self.play_by_index(self.track_index)
            else:
                # Para todos os outros modos, a fila tem prioridade
                if not self.queue.is_empty:
                    self.play_from_queue()
                else:
                    try:
                        self.next()
                    except errors.NoNextTrackError:
                        self.stop()

    def on_metadata_update(self, name: str, value: Any) -> None:
        if self.state == State.Playing and (
            self.track.type == TrackType.Direct or self.track.type == TrackType.Local
        ):
            metadata = self._player.metadata
            try:
                new_name = self._parse_metadata(metadata)
                if not new_name:
                    new_name = html.unescape(self._player.media_title)
            except TypeError:
                new_name = html.unescape(self._player.media_title)
            if self.track.name != new_name and new_name:
                self.track.name = new_name
