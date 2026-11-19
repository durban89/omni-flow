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
        self.timeout_seconds = float(self.config.get("timeout_seconds") or 30.0)
        self.api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or ""
        )
        self.base_url = str(
            self.config.get("base_url") or "https://api.openai.com/v1"
        ).rstrip("/")
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
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            "response_format": {"type": "json_object"},
            # This endpoint always spends part of the budget on reasoning and
            # rejects attempts to disable it, so leave generous headroom.
            "max_tokens": 8000,
        }
        with httpx.Client(timeout=self.timeout_seconds) as client:
            resp = client.post(
                f"{self.base_url}/chat/completions",
                headers=headers,
                json=payload,
            )
            resp.raise_for_status()
            data = resp.json()
        content = data["choices"][0]["message"]["content"]
        if not isinstance(content, str) or not content.strip():
            raise ValueError("LLM returned empty content")
        return content

    def _parse_llm_json(self, content: str) -> tuple[str, str, str]:
        # Models sometimes emit raw newlines inside string values.
        parsed = json.loads(content, strict=False)
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
                "   - title: an original Chinese headline (Simplified Chinese, <= 35 characters, "
                "no site names or publisher names).\n\n"
                "Allowlisted product tokens:\n" + "\n".join(token_lines) + "\n\n"
                "body_markdown structure: # 标题, ## 现象, ## 生理背景, "
                "## 推荐解决方案 (embed the shortcode here). Section headings must be Simplified Chinese."
            )
        else:
            affiliate_rules = (
                "2. Do not include any affiliate shortcode; no product tokens are available.\n"
                "3. Return JSON only with keys: title, summary, body_markdown.\n"
                "   - title: an original Chinese headline (Simplified Chinese, <= 35 characters, "
                "no site names or publisher names).\n\n"
                "body_markdown structure: # 标题, ## 现象, ## 生理背景, "
                "## 推荐解决方案 (general, non-commercial guidance). "
                "Section headings must be Simplified Chinese."
            )

        system_prompt = (
            "You are a professional sexual-health education writer. "
            "Write an academic, SEO-safe educational article based on the user concern.\n\n"
            "Hard rules:\n"
            "1. Use clinical, non-vulgar language only.\n"
            f"{affiliate_rules}"
            "5. Write the article in Simplified Chinese (简体中文).\n"
        )
        user_prompt = (
            f"User question: {raw.title}\n"
            f"User description: {raw.body[:3000]}\n"
            "Write a complete educational review article as JSON."
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
            "You are an expert editorial reviewer for an authoritative buying guide and CPS product matrix. "
            "Write a structured, objective, and engaging product review or buyer's guide based on the provided source information. "
            "Requirements:\n"
            "- Write in clean Markdown.\n"
            "- Minimum 350 words.\n"
            "- Include sections: ## 概览与核心特性, ## 深度分析, ## 优缺点, ## 购买建议.\n"
            "- Maintain strict compliance: no false claims, no cloaking, clear editorial value.\n"
            "- Write in Simplified Chinese (简体中文).\n"
            "- Return JSON with keys 'title', 'summary' and 'body_markdown'. "
            "'title' is an original Chinese headline (<= 35 characters, no site names)."
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
