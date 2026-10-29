import pytest

from local_worker.errors import ScrapeError
from local_worker.models import utc_now
from local_worker.scraper.rss import parse_feed

RSS_FEED = b"""<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0">
  <channel>
    <title>Example Feed</title>
    <copyright>CC-BY-4.0</copyright>
    <item>
      <title>Example Post</title>
      <link>https://example.com/post</link>
      <description><![CDATA[<p>Short summary.</p>]]></description>
      <pubDate>Tue, 02 Jan 2026 09:00:00 GMT</pubDate>
      <category>news</category>
    </item>
  </channel>
</rss>
"""

ATOM_FEED = b"""<?xml version="1.0" encoding="utf-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <title>Example Atom</title>
  <entry>
    <title>Atom Post</title>
    <link rel="alternate" href="https://example.com/atom-post"/>
    <summary>Atom summary.</summary>
    <content type="html">&lt;p&gt;Atom body.&lt;/p&gt;</content>
    <updated>2026-01-02T09:00:00Z</updated>
    <category term="updates"/>
    <author><name>Example Author</name></author>
  </entry>
</feed>
"""


def test_parse_rss_feed() -> None:
    items = parse_feed(
        RSS_FEED,
        "feed",
        {"default_category": "news", "default_tags": ["rss"]},
        utc_now(),
    )

    assert len(items) == 1
    assert items[0].source_url == "https://example.com/post"
    assert items[0].tags == ("rss", "news")
    assert items[0].license == "CC-BY-4.0"
    assert items[0].body_format == "html"


def test_parse_atom_feed() -> None:
    items = parse_feed(
        ATOM_FEED,
        "atom",
        {"default_category": "updates"},
        utc_now(),
    )

    assert len(items) == 1
    assert items[0].title == "Atom Post"
    assert items[0].source_url == "https://example.com/atom-post"
    assert items[0].publisher == "Example Author"
    assert items[0].body_format == "html"


def test_parse_feed_rejects_unknown_format() -> None:
    with pytest.raises(ScrapeError):
        parse_feed(b"<root></root>", "unknown", {}, utc_now())
