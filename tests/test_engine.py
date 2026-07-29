from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from content_engine import (  # noqa: E402
    FeedItem,
    deterministic_article,
    parse_feed,
    quality_check,
    safe_slug,
    score_items,
    select_balanced,
)


SETTINGS = {
    "brand": "ToolSignal Daily",
    "keywords": ["ai", "automation", "api"],
    "excluded_topics": ["stock prediction"],
    "lookback_hours": 200000,
    "minimum_sources": 3,
    "minimum_unique_domains": 2,
    "minimum_words": 50,
    "call_to_action": "Follow.",
}


class EngineTests(unittest.TestCase):
    def test_parse_rss(self) -> None:
        payload = b"""<rss><channel><item><title>AI API update</title><link>https://example.com/a</link><description>Useful update.</description><pubDate>Tue, 28 Jul 2026 10:00:00 GMT</pubDate></item></channel></rss>"""
        items = parse_feed(payload, {"name": "Example", "weight": 1})
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].title, "AI API update")

    def test_balanced_sources(self) -> None:
        items = [
            FeedItem("A", 1, "AI one", "https://a.com/1", "AI", "2026-07-29T00:00:00+00:00", 5),
            FeedItem("A", 1, "AI two", "https://a.com/2", "AI", "2026-07-29T00:00:00+00:00", 4),
            FeedItem("A", 1, "AI three", "https://a.com/3", "AI", "2026-07-29T00:00:00+00:00", 3),
            FeedItem("B", 1, "AI four", "https://b.com/1", "AI", "2026-07-29T00:00:00+00:00", 2),
        ]
        selected = select_balanced(items, 4)
        self.assertEqual(len(selected), 3)
        self.assertEqual(sum(item.source == "A" for item in selected), 2)

    def test_quality_requires_editor_for_rules(self) -> None:
        items = [
            FeedItem("A", 1, "AI one", "https://a.com/1", "AI", "2026-07-29T00:00:00+00:00"),
            FeedItem("B", 1, "AI two", "https://b.com/2", "AI", "2026-07-29T00:00:00+00:00"),
            FeedItem("C", 1, "AI three", "https://c.com/3", "AI", "2026-07-29T00:00:00+00:00"),
        ]
        article = deterministic_article(items, SETTINGS, "2026-07-29")
        quality = quality_check(article, items, SETTINGS)
        self.assertIn("rules_mode_requires_editorial_review", quality["reasons"])

    def test_safe_slug(self) -> None:
        self.assertEqual(safe_slug("Hello, AI World!"), "hello-ai-world")


if __name__ == "__main__":
    unittest.main()

