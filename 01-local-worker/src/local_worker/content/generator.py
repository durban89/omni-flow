from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any

import httpx
from dotenv import load_dotenv

from local_worker.content.text import count_words, truncate
from local_worker.errors import LLMGenerationError
from local_worker.models import RawItem

load_dotenv()

logger = logging.getLogger("local_worker.generator")

AFFILIATE_SHORTCODE_RE = re.compile(
    r'\{\{<\s*affiliate\s+name=["\']([^"\']+)["\']\s*>\}\}'
)
MIN_LLM_WORDS = 200


class ContentGenerator:
    """Generates buying guides / niche articles via LLM or editorial templates."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model = str(self.config.get("model") or "gpt-4o-mini")
        self.temperature = float(self.config.get("temperature") or 0.3)
        self.max_words = int(self.config.get("max_words") or 1200)
        self.max_output_tokens = int(self.config.get("max_output_tokens") or 8000)
        self.timeout_seconds = float(self.config.get("timeout_seconds") or 30.0)
        # OpenRouter reasoning models can spend the whole output budget thinking,
        # leaving `content` empty with finish_reason=length. Cap reasoning tokens.
        self.reasoning_max_tokens = int(self.config.get("reasoning_max_tokens") or 0)
        self.base_url = str(
            self.config.get("base_url") or "https://api.openai.com/v1"
        ).rstrip("/")
        self.api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or ""
        )
        # Gemini is only a primary key when Gemini is actually the primary base.
        if "generativelanguage" in self.base_url:
            self.api_key = os.environ.get("GEMINI_API_KEY") or self.api_key
        # Optional free fallback used only when the primary provider fails.
        # Groq is OpenAI-compatible and has a no-card free tier.
        self.fallback_base_url = str(
            self.config.get("fallback_base_url")
            or "https://api.groq.com/openai/v1"
        ).rstrip("/")
        self.fallback_model = str(
            self.config.get("fallback_model") or "openai/gpt-oss-120b"
        )
        # An empty env name means the fallback needs no key (e.g. local Ollama).
        fallback_env = self.config.get("fallback_api_key_env", "GROQ_API_KEY")
        self.fallback_api_key_env = "" if fallback_env is None else str(fallback_env)
        self.fallback_api_key = (
            os.environ.get(self.fallback_api_key_env, "")
            if self.fallback_api_key_env
            else ""
        )
        self.fallback_enabled = bool(
            self.fallback_base_url and self.fallback_model
        ) and (not self.fallback_api_key_env or bool(self.fallback_api_key))
        if (
            self.fallback_base_url == self.base_url
            and self.fallback_model == self.model
        ):
            self.fallback_enabled = False
        self._use_health_mode = False
        self._master_db_path = Path("master-affiliate-db.json")
        self.valid_tokens: dict[str, dict[str, Any]] = {}
        self._tokens_loaded = False

    def set_master_db_path(self, path: str | Path) -> ContentGenerator:
        self._master_db_path = Path(path) if path else Path("master-affiliate-db.json")
        self._tokens_loaded = False
        self.valid_tokens = {}
        return self

    def set_health_mode(self, enabled: bool) -> ContentGenerator:
        self._use_health_mode = enabled
        return self

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.api_key.strip())

    def load_active_affiliate_tokens(self) -> dict[str, dict[str, Any]]:
        """Load active product tokens from master affiliate DB for shortcode allowlisting."""
        if not self._master_db_path.exists():
            logger.warning(
                "master affiliate db not found at %s; affiliate token allowlist is empty",
                self._master_db_path,
            )
            return {}

        try:
            master_db = json.loads(self._master_db_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as exc:
            logger.warning("failed to load master affiliate db %s: %s", self._master_db_path, exc)
            return {}

        if not isinstance(master_db, dict):
            logger.warning("master affiliate db must be a JSON object: %s", self._master_db_path)
            return {}

        active_tokens: dict[str, dict[str, Any]] = {}
        for token, entity in master_db.items():
            if not isinstance(entity, dict):
                continue
            metrics = entity.get("meta_metrics")
            metrics = metrics if isinstance(metrics, dict) else {}
            if not bool(metrics.get("is_active", True)):
                continue
            if not entity.get("masked_path"):
                continue
            active_tokens[str(token)] = {
                "product_name": entity.get("product_name"),
                "brand": entity.get("brand", ""),
            }
        return active_tokens

    def generate(self, raw: RawItem) -> tuple[str, str, str]:
        """Return (title, body_markdown, summary).

        Falls back to the editorial template on LLM failure, except for topic
        leads: those are LLM-only, so a failure raises LLMGenerationError and the
        item is left unwritten to be retried on the next run.
        """
        # One generator lives for a whole pipeline run, so read the master DB only once.
        if not self._tokens_loaded:
            self.valid_tokens = self.load_active_affiliate_tokens()
            self._tokens_loaded = True

        if self.has_llm_credentials:
            try:
                if self._use_health_mode:
                    return self._call_health_llm(raw)
                return self._call_llm(raw)
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
                if self._is_topic_lead(raw):
                    # The template skeleton is English and would claim this id,
                    # blocking any retry once the model quota recovers.
                    raise LLMGenerationError(
                        f"LLM generation failed for topic lead {raw.source_url}: {exc}"
                    ) from exc
                logger.warning("LLM generation failed, falling back to template: %s", exc)

        return self._template_generate(raw)

    def _chat_completion(self, system_prompt: str, user_prompt: str) -> str:
        try:
            return self._post_completion(
                base_url=self.base_url,
                model=self.model,
                api_key=self.api_key,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                allow_reasoning=True,
            )
        except (httpx.HTTPError, ValueError) as exc:
            if not self.fallback_enabled:
                raise
            logger.warning(
                "primary LLM %s failed (%s); falling back to %s",
                self.model,
                exc,
                self.fallback_model,
            )
        return self._post_completion(
            base_url=self.fallback_base_url,
            model=self.fallback_model,
            api_key=self.fallback_api_key,
            system_prompt=system_prompt,
            user_prompt=user_prompt,
            allow_reasoning=False,
        )

    def _post_completion(
        self,
        *,
        base_url: str,
        model: str,
        api_key: str,
        system_prompt: str,
        user_prompt: str,
        allow_reasoning: bool,
    ) -> str:
        headers = {"Content-Type": "application/json"}
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
        # Groq's free tier caps tokens per minute (8K), so a 16K completion cap
        # would be rejected; a 1200-word article fits comfortably in 6K.
        max_tokens = (
            self.max_output_tokens if allow_reasoning else min(self.max_output_tokens, 6000)
        )
        payload = {
            "model": model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            # This endpoint always spends part of the budget on reasoning and
            # rejects attempts to disable it, so leave generous headroom.
            "max_tokens": max_tokens,
        }
        if (
            allow_reasoning
            and self.reasoning_max_tokens
            and "openrouter.ai" in base_url
        ):
            payload["reasoning"] = {"max_tokens": self.reasoning_max_tokens}
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(
                f"{base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            # `is True` (not truthiness) keeps test doubles from taking this path.
            if resp.is_error is True:
                # Surface the provider's message; raise_for_status() alone hides
                # actionable 400/402 bodies behind a generic status line.
                logger.warning(
                    "LLM endpoint returned %s: %s",
                    resp.status_code,
                    resp.text[:500],
                )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM returned empty content")
        return content

    def _parse_llm_json(self, content: str) -> tuple[str, str, str]:
        text = content.strip()
        # Some providers wrap JSON in a markdown fence despite JSON mode.
        if text.startswith("```"):
            text = re.sub(r"^```[A-Za-z0-9_-]*\s*", "", text)
            text = re.sub(r"\s*```$", "", text)
        # Models sometimes emit raw newlines inside string values.
        parsed = json.loads(text, strict=False)
        if not isinstance(parsed, dict):
            raise TypeError("LLM JSON root must be an object")
        body = str(parsed.get("body_markdown") or "").strip()
        summary = str(parsed.get("summary") or "").strip() or truncate(body, 200)
        # Optional: models write a better headline than the raw lead title.
        title = " ".join(str(parsed.get("title") or "").split()).lstrip("# ").strip()
        if not body:
            raise ValueError("LLM JSON missing body_markdown")
        return title[:120], body, summary

    def _enforce_affiliate_tokens(self, body: str) -> str:
        """Ensure every affiliate shortcode name is in the active allowlist."""
        found = AFFILIATE_SHORTCODE_RE.findall(body)
        if not found:
            return body

        allowlist = set(self.valid_tokens)
        unknown = sorted({token for token in found if token not in allowlist})
        if unknown:
            raise ValueError(
                "LLM used unknown affiliate token(s): "
                + ", ".join(unknown)
                + (f"; allowlist={sorted(allowlist)}" if allowlist else "; allowlist is empty")
            )
        return body

    def _finalize_llm_body(
        self, title: str, body: str, summary: str
    ) -> tuple[str, str, str]:
        body = self._enforce_affiliate_tokens(body)
        if count_words(body) < MIN_LLM_WORDS:
            raise ValueError(
                f"LLM body too short ({count_words(body)} words, need >= {MIN_LLM_WORDS})"
            )
        return title, body, truncate(summary, 280)

    def _call_health_llm(self, raw: RawItem) -> tuple[str, str]:
        token_lines = [
            f"- Token: `{token}` (product: {meta.get('product_name') or token})"
            for token, meta in self.valid_tokens.items()
        ]

        if token_lines:
            affiliate_rules = (
                "2. When recommending a product solution, pick exactly ONE token from the "
                "allowlist below and insert it verbatim as a Hugo shortcode: "
                '{{< affiliate name="TOKEN" >}}.\n'
                "3. Never invent tokens that are not on the allowlist.\n"
                "4. Return JSON only with keys: title, summary, body_markdown.\n"
                "   - title: an original English headline (<= 65 characters, sentence case, "
                "contains the main search keyword, no site or publisher names).\n"
                "   - summary: 120-155 characters, plain English, no clickbait.\n\n"
                "Allowlisted product tokens:\n" + "\n".join(token_lines) + "\n\n"
                "body_markdown structure: # Title, ## The Issue, ## Biological Background, "
                "## Recommended Solution (embed the shortcode here). Section headings must be "
                "exactly these English headings."
            )
        else:
            affiliate_rules = (
                "2. Do not include any affiliate shortcode; no product tokens are available.\n"
                "3. Return JSON only with keys: title, summary, body_markdown.\n"
                "   - title: an original English headline (<= 65 characters, sentence case, "
                "contains the main search keyword, no site or publisher names).\n"
                "   - summary: 120-155 characters, plain English, no clickbait.\n\n"
                "body_markdown structure: # Title, ## The Issue, ## Biological Background, "
                "## Recommended Solution (general, non-commercial guidance). "
                "Section headings must be exactly these English headings."
            )

        system_prompt = (
            "You are a professional sexual-health writer for an English-language consumer "
            "health site read by audiences in the US, UK, Canada, Australia, Hong Kong and "
            "Taiwan. Write an educational, SEO-safe article answering the reader's concern.\n\n"
            "Hard rules:\n"
            "1. Use clinical, non-vulgar language only; this is health education, not erotica.\n"
            f"{affiliate_rules}"
            "5. Write in natural English with US spelling. Never output Chinese characters.\n"
            "6. At least 600 words. No individualized medical diagnosis; point readers to a "
            "clinician where it matters.\n"
            "7. Do not copy, quote, or closely paraphrase third-party text.\n"
        )
        user_prompt = (
            f"Reader question: {raw.title}\n"
            f"Context: {raw.body[:3000]}\n"
            "Write a complete educational article as JSON."
        )

        content = self._chat_completion(system_prompt, user_prompt)
        title, body, summary = self._parse_llm_json(content)
        return self._finalize_llm_body(title, body, summary)

    def _is_topic_lead(self, raw: RawItem) -> bool:
        """Topic leads carry search metadata only, so the article must be written from scratch."""
        return "topic" in raw.tags

    def _call_llm(self, raw: RawItem) -> tuple[str, str, str]:
        is_topic_lead = self._is_topic_lead(raw)
        system_prompt = (
            "You are an expert editorial reviewer for an authoritative English-language buying "
            "guide and CPS product matrix aimed at readers in the US, UK, Canada, Australia, "
            "Hong Kong and Taiwan. Write a structured, objective, and engaging buyer's guide "
            "from the provided source information. Requirements:\n"
            "- Write in clean Markdown, in English with US spelling.\n"
            "- Minimum 500 words.\n"
            "- Include sections: ## Overview & Key Features, ## In-Depth Analysis, "
            "## Pros & Cons, ## Buying Advice.\n"
            "- Maintain strict compliance: no false claims, no cloaking, clear editorial value.\n"
            "- Return JSON with keys 'title', 'summary' and 'body_markdown'. "
            "'title' is an original English headline (<= 65 characters, sentence case, "
            "contains the main search keyword, no site or publisher names). "
            "'summary' is 120-155 characters of plain English."
        )
        if is_topic_lead:
            system_prompt += (
                "\n- The input is a topic lead from search metadata only. Write an original article "
                "about the topic in your own words; do not copy, quote, or closely paraphrase any "
                "third-party text, and do not present the topic lead as a source."
            )
        body_label = "Topic Lead" if is_topic_lead else "Source Content"
        user_prompt = (
            f"Title: {raw.title}\n"
            f"Category: {raw.category}\n"
            f"Source URL: {raw.source_url}\n"
            f"Original Summary: {raw.summary}\n"
            f"{body_label}: {raw.body[:3000]}"
        )

        content = self._chat_completion(system_prompt, user_prompt)
        title, body, summary = self._parse_llm_json(content)
        return self._finalize_llm_body(title, body, summary)

    def _template_generate(self, raw: RawItem) -> tuple[str, str, str]:
        """Editorial structured synthesis ensuring clear sectioning and enough length."""
        title = raw.title.strip()
        category = raw.category.capitalize()
        summary = raw.summary.strip() or (
            f"A comprehensive buying guide and hands-on analysis of {title}."
        )

        # Topic leads are writing prompts, so their brief must not end up as article text.
        source_body = "" if self._is_topic_lead(raw) else raw.body.strip()

        body_parts = [
            f"# {title}",
            "",
            f"## Overview: {category} Buying Guide",
            "",
            (
                f"When evaluating options in the **{category}** category, selecting the right solution requires "
                "weighing multiple factors including design durability, performance efficiency, and total cost of ownership. "
                f"This guide synthesizes key specifications, market feedback, and practical usage considerations for **{title}**."
            ),
            "",
            "## Key Features & Evaluation Criteria",
            "",
            f"During our review and comparison of **{title}**, several defining characteristics stand out:",
            "",
            "- **Build Quality & Specification Alignment**: Materials and design choices reflect current industry benchmarks.",
            "- **Value-to-Performance Ratio**: Pricing structures should be evaluated against core features and long-term utility.",
            "- **Reliability & Support**: Manufacturer warranty terms, parts availability, and customer service responsiveness.",
            "",
            "## Detailed Breakdown",
            "",
            source_body,
            "",
            "## Pros and Cons",
            "",
            "### Advantages",
            f"- Well-documented specifications and verified compatibility for {category.lower()} workflows.",
            "- Strong initial feedback regarding durability and day-to-day usability.",
            "- Clear alignment with expected standards in its tier.",
            "",
            "### Considerations",
            "- May require configuration adjustments depending on your specific use case.",
            "- Verify warranty coverage and return policies prior to completing purchases.",
            "",
            "## Verdict & Recommendation",
            "",
            (
                f"Overall, **{title}** provides a compelling balance for shoppers seeking quality and dependable performance. "
                "We recommend verifying regional availability and checking current promotional pricing before finalizing your decision."
            ),
            "",
            "---",
            (
                "*Compliance Note: This article was prepared for independent editorial research. "
                "Links on this page may direct to verified partner offers subject to our standard disclosure policy.*"
            ),
        ]

        # Optionally embed one allowlisted shortcode for health-like niche drafts.
        if self._use_health_mode and self.valid_tokens:
            # Prefer the token this item is already linked to, then fall back to the first one.
            token = raw.affiliate_slug if raw.affiliate_slug in self.valid_tokens else None
            token = token or next(iter(self.valid_tokens))
            body_parts.extend(
                [
                    "",
                    "## Recommended Solution",
                    "",
                    f'{{{{< affiliate name="{token}" >}}}}',
                ]
            )

        full_body = "\n".join(body_parts)
        return raw.title.strip(), full_body, truncate(summary, 280)
