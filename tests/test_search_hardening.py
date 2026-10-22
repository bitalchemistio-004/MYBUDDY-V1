import sys
import os
import unittest
from unittest.mock import MagicMock, patch

# Ensure project root is in path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.search_provider import DdgsSearchProvider

class TestSearchHardening(unittest.TestCase):
    
    @patch.dict(os.environ, {"MBUDDY_SEARCH_FETCH_PAGES": "0"})  # no real page fetches in a unit test
    @patch("ddgs.DDGS")
    def test_ddgs_backend_rotation(self, mock_ddgs_class):
        """A failing backend falls through to the next, fastest first (api, lite, auto)."""
        provider = DdgsSearchProvider()
        self.assertEqual(provider.BACKENDS, ("api", "lite", "auto"))

        mock_instance = MagicMock()
        mock_instance.text.side_effect = [
            Exception("Simulated Failure"),  # api
            Exception("Simulated Failure"),  # lite
            [{"title": "Messi 2026", "href": "http://test.com", "body": "Snippet"}],  # auto
        ]
        mock_ddgs_class.return_value.__enter__.return_value = mock_instance

        result = provider.search("Messi 2026")

        self.assertIn("Provider: DuckDuckGo (auto)", result)
        self.assertIn("Messi 2026", result)
        self.assertEqual(mock_instance.text.call_count, 3)


if __name__ == "__main__":
    unittest.main()
