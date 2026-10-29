from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx

from local_worker.content.text import count_words, truncate
from local_worker.models import RawItem

logger = logging.getLogger("local_worker.generator")


class ContentGenerator:
    """Generates comprehensive, compliance-ready buying guides and reviews using LLM or structured templates."""

    def __init__(self, config: dict[str, Any] | None = None) -> None:
        self.config = config or {}
        self.model = str(self.config.get("model") or "gpt-4o-mini")
        self.temperature = float(self.config.get("temperature") or 0.3)
        self.max_words = int(self.config.get("max_words") or 1200)
        self.api_key = (
            os.environ.get("OPENAI_API_KEY")
            or os.environ.get("GEMINI_API_KEY")
            or os.environ.get("LLM_API_KEY")
            or ""
        )

        self.base_url = str(self.config.get("base_url") or "https://api.openai.com/v1").rstrip("/")

        # 默认不开启这个模式
        self._use_health_mode: bool = False


    def set_health_mode(self, enabled: bool) -> ContentGenerator:
        """Dynamically enables or disables the health-specific LLM generator for the next operation."""
        
        self._use_health_mode = enabled
        return self

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.api_key.strip())

    def generate(self, raw: RawItem) -> tuple[str, str]:
        """Generates (body_markdown, summary). Tries LLM if configured, otherwise falls back to editorial template."""
        if self.has_llm_credentials:
            try:
                if self._use_health_mode:
                    return self._call_health_llm(raw)
                
                return self._call_llm(raw)
            except (httpx.HTTPError, json.JSONDecodeError, KeyError, ValueError) as exc:
                logger.warning("LLM generation failed, falling back to template: %s", exc)

        return self._template_generate(raw)


    def _call_health_llm(self, raw: RawItem) -> tuple[str, str]:
        prompt = f"""
        Translate the following high-sensitivity user concern into a professional, educational, search-engine-friendly article.
        User Concern: {raw.body[:3000]}
        Requirements:
        - Use formal health vocabulary (e.g., 'tactile sensitivity', 'barrier optimization').
        - Do not use explicit adult-network slang.
        - Seamlessly integrate context for premium product selections (e.g., thin profiles, specialized materials).
        """

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "user", "content": prompt},
            ],
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            body = str(parsed.get("body_markdown") or "")
            summary = str(parsed.get("summary") or truncate(body, 200))
            if count_words(body) >= 200:
                return body, summary

        return self._template_generate(raw)

    def _call_llm(self, raw: RawItem) -> tuple[str, str]:
        system_prompt = (
            "You are an expert editorial reviewer for an authoritative buying guide and CPS product matrix. "
            "Write a structured, objective, and engaging product review or buyer's guide based on the provided source information. "
            "Requirements:\n"
            "- Write in clean Markdown.\n"
            "- Minimum 350 words.\n"
            "- Include sections: ## Overview & Key Features, ## In-Depth Analysis, ## Pros & Cons, ## Buying Recommendation.\n"
            "- Maintain strict compliance: no false claims, no cloaking, clear editorial value.\n"
            "- Return JSON with keys 'summary' and 'body_markdown'."
        )
        user_prompt = (
            f"Title: {raw.title}\n"
            f"Category: {raw.category}\n"
            f"Source URL: {raw.source_url}\n"
            f"Original Summary: {raw.summary}\n"
            f"Source Content: {raw.body[:3000]}"
        )

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
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            parsed = json.loads(content)
            body = str(parsed.get("body_markdown") or "")
            summary = str(parsed.get("summary") or truncate(body, 200))
            if count_words(body) >= 200:
                return body, summary

        return self._template_generate(raw)

    def _template_generate(self, raw: RawItem) -> tuple[str, str]:
        """Editorial structured synthesis ensuring >= 300 words and clear sectioning."""
        title = raw.title.strip()
        category = raw.category.capitalize()
        summary = raw.summary.strip() or f"A comprehensive buying guide and hands-on analysis of {title}."

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
            raw.body.strip(),
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

        full_body = "\n".join(body_parts)
        return full_body, truncate(summary, 280)
