import json
from unittest.mock import MagicMock, patch

import httpx

from local_worker.content.generator import ContentGenerator
from local_worker.models import RawItem, utc_now


@patch("httpx.Client.post")
@patch("local_worker.content.generator.count_words")
def test_content_generator_health_llm_flow(mock_count_words: MagicMock, mock_post: MagicMock) -> None:
    # 1. Force word count check to bypass the >= 200 validation threshold
    mock_count_words.return_value = 250

    # 2. Prepare high-sensitivity test container
    raw = RawItem(
        source_id="health-test",
        source_url="https://example.com",
        title="Sensitive Health Query Topic",
        summary="Initial health query summary.",
        body="Detailed context regarding a sensitive medical or health concern.",
        body_format="markdown",
        retrieved_at=utc_now(),
        category="health",
    )

    # 3. Simulate model returning clean compliance JSON payload
    mock_response = MagicMock(spec=httpx.Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [
            {
                "message": {
                    "content": (
                            "## Professional Health Overview\n\n"
                            "Addressing tactile sensitivity requires precise barrier optimization. "
                            "Using premium, highly specialized thin profile materials ensures "
                            "both safety and comfort, satisfying comprehensive clinical standards. "
                            "This approach focuses strictly on standard professional medical vocabularies "
                            "to maintain structural integrity and scientific alignment."
                        )
                }
            }
        ]
    }
    mock_post.return_value = mock_response

    # 4. Initialize and target setup
    config = {
        "model": "gpt-4o-mini",
        "base_url": "https://openai.com"
    }
    
    with patch.dict("os.environ", {"OPENAI_API_KEY": "mock-api-key"}):
        generator = ContentGenerator(config)
        
        # Activating runtime switch hook
        generator.set_health_mode(True)
        body, summary = generator.generate(raw)

    # 5. Asset parsed results are successfully mapped to LLM content
    assert "Professional Health Overview" in body
    assert "tactile sensitivity" in body
    assert "barrier optimization" in body
    assert "Professional Health Overview" in summary

