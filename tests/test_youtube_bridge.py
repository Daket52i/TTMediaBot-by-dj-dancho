import importlib.util
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

PROJECT_ROOT = pathlib.Path(__file__).resolve().parents[1]


def load_module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


bot_module = types.ModuleType("bot")
bot_module.__path__ = [str(PROJECT_ROOT / "bot")]
sys.modules["bot"] = bot_module
errors = load_module("bot.errors", PROJECT_ROOT / "bot" / "errors.py")
bot_module.errors = errors
services_module = types.ModuleType("bot.services")
services_module.__path__ = [str(PROJECT_ROOT / "bot" / "services")]
sys.modules["bot.services"] = services_module
bot_module.services = services_module
youtube_bridge = load_module(
    "bot.services.youtube_bridge",
    PROJECT_ROOT / "bot" / "services" / "youtube_bridge.py",
)
YouTubeBridge = youtube_bridge.YouTubeBridge


class YouTubeBridgeFallbackTests(unittest.TestCase):
    def setUp(self):
        self.bridge = YouTubeBridge(client="MWEB")

    def test_resolve_uses_bridge_result_when_available(self):
        expected = {"url": "https://stream.example/audio"}
        self.bridge._post = Mock(return_value=expected)
        self.bridge._resolve_with_ytdlp = Mock()

        result = self.bridge.resolve(video_id="dQw4w9WgXcQ")

        self.assertEqual(result, expected)
        self.bridge._resolve_with_ytdlp.assert_not_called()

    def test_resolve_falls_back_to_ytdlp_after_bridge_error(self):
        expected = {"url": "https://fallback.example/audio"}
        self.bridge._post = Mock(side_effect=errors.ServiceError("LOGIN_REQUIRED"))
        self.bridge._resolve_with_ytdlp = Mock(return_value=expected)

        result = self.bridge.resolve(video_id="48Lrud3Bxpc")

        self.assertEqual(result, expected)
        self.bridge._resolve_with_ytdlp.assert_called_once_with(
            "https://www.youtube.com/watch?v=48Lrud3Bxpc"
        )

    @patch("bot.services.youtube_bridge.YoutubeDL")
    def test_ytdlp_result_is_normalized_for_the_player(self, youtube_dl):
        youtube_dl.return_value.__enter__.return_value.extract_info.return_value = {
            "id": "48Lrud3Bxpc",
            "url": "https://fallback.example/audio",
            "title": "Track",
            "is_live": False,
        }

        result = self.bridge._resolve_with_ytdlp(
            "https://www.youtube.com/watch?v=48Lrud3Bxpc"
        )

        self.assertEqual(result["videoId"], "48Lrud3Bxpc")
        self.assertEqual(result["format"], "mp3")
        self.assertFalse(result["is_live"])


if __name__ == "__main__":
    unittest.main()
