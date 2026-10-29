from local_worker.content.text import (
    body_to_markdown,
    clean_summary,
    count_words,
    html_to_markdown,
    html_to_text,
    truncate,
)


def test_html_to_markdown_basic_elements() -> None:
    html_input = """
    <h2>Buying Guide</h2>
    <p>This is <strong>important</strong> and <em>notable</em> advice.</p>
    <p>Check out <a href="https://example.com/product">our top pick</a>.</p>
    <ul>
      <li>Feature A</li>
      <li>Feature B</li>
    </ul>
    <blockquote>Note: Always verify warranty.</blockquote>
    """
    md = html_to_markdown(html_input)
    assert "## Buying Guide" in md
    assert "**important**" in md
    assert "*notable*" in md
    assert "[our top pick](https://example.com/product)" in md
    assert "- Feature A" in md
    assert "- Feature B" in md
    assert "> Note: Always verify warranty." in md


def test_html_to_markdown_ordered_list_and_code() -> None:
    html_input = """
    <ol>
      <li>Step One</li>
      <li>Step Two</li>
    </ol>
    <pre><code>config: true</code></pre>
    """
    md = html_to_markdown(html_input)
    assert "1. Step One" in md
    assert "2. Step Two" in md
    assert "```" in md
    assert "config: true" in md


def test_html_to_text_strips_tags() -> None:
    html_input = "<p>Hello <b>World</b> with <a href='https://x.com'>link</a></p>"
    text = html_to_text(html_input)
    assert text == "Hello World with link"


def test_body_to_markdown() -> None:
    assert body_to_markdown("plain text", "markdown") == "plain text"
    html_content = "<p>Some <b>bold</b> text</p>"
    assert "**bold**" in body_to_markdown(html_content, "html")
    assert "**bold**" in body_to_markdown(html_content, "text")


def test_clean_summary_and_truncate() -> None:
    long_text = "word " * 50
    truncated = truncate(long_text, 50)
    assert len(truncated) <= 53
    assert truncated.endswith("...")
    assert count_words(truncated) < 50

    cleaned = clean_summary("<p>Brief <i>highlight</i></p>", "fallback")
    assert cleaned == "Brief highlight"
