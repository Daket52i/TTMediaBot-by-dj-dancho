from unittest import TestCase
from unittest.mock import patch

from bot.services.youtube_bridge import YouTubeBridge


class YouTubeBridgeContractTests(TestCase):
    def setUp(self):
        self.bridge = YouTubeBridge(client="YTMUSIC")

    def test_music_search_sets_explicit_mode(self):
        with patch.object(self.bridge, "_post", return_value={"entries": []}) as post:
            self.bridge.search("ela vem", 1, mode="music")

        post.assert_called_once_with(
            "/search", query="ela vem", limit=1, mode="music"
        )

    def test_recommendations_send_video_id_and_limit(self):
        with patch.object(self.bridge, "_post", return_value={"entries": []}) as post:
            self.bridge.recommendations("48Lrud3Bxpc", 15)

        post.assert_called_once_with(
            "/recommendations", video_id="48Lrud3Bxpc", limit=15
        )
