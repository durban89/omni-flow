from __future__ import annotations

import json
import logging
import os
from typing import Any

import httpx
from dotenv import load_dotenv

from local_worker.content.text import count_words, truncate
from local_worker.models import RawItem

load_dotenv()

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

        self._master_db_path = "master-affiliate-db.json" # 主配方表

    def set_master_db_path(self, path: str):
        self._master_db_path = path

        return self

    def set_health_mode(self, enabled: bool) -> ContentGenerator:
        """Dynamically enables or disables the health-specific LLM generator for the next operation."""
        
        self._use_health_mode = enabled

        return self

    @property
    def has_llm_credentials(self) -> bool:
        return bool(self.api_key.strip())

    def load_active_affiliate_tokens(self):
        """
        核心防御机制：从本地主数据库中，动态动态提取出所有当前合法、激活的商品 Token 及其描述
        """
        if not os.path.exists(self._master_db_path):
            print(f"❌ 严重错误: 找不到主数据库 [{self._master_db_path}]，AI 引擎无法确保 Token 安全映射！")
            return {}
            
        with open(self._master_db_path, "r", encoding="utf-8") as f:
            master_db = json.load(f)
            
        active_tokens = {}
        for token, entity in master_db.items():
            # 只提取 active = true 且配置了跳转路径的商品
            if entity.get("meta_metrics", {}).get("is_active", True) and entity.get("masked_path"):
                active_tokens[token] = {
                    "product_name": entity.get("product_name"),
                    "brand": entity.get("brand", "")
                }
        return active_tokens

    def generate(self, raw: RawItem) -> tuple[str, str]:
        self.valid_tokens = self.load_active_affiliate_tokens()
            
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
        # 将合法的 Token 字典格式化为一段非常清晰的 Prompt 上下文
        token_context_str = ""
        for token, meta in self.valid_tokens.items():
            token_context_str += f"- Token: `{token}` (对应商品: {meta['product_name']})\n"

        question = raw.title
        symptom = raw.body[:3000]

        # 在 System Prompt 中动态注入 valid_tokens
        system_prompt = (
            "你是一位专业的两性健康与性教育科普科普专家。你的任务是根据用户的真实身体困惑撰写科普评测文章。\n\n"
            "【核心强制规范】：\n"
            "1. 语言必须高度学术、健康、符合搜索引擎（SEO）收录合规逻辑，禁止使用任何低俗或擦边敏感词汇。\n"
            "2. 当你在文章后半部分提到针对该痛点的最佳解决方案时，【必须且只能】从以下给出的【合法的商品Token列表中】选择一个最贴切的Token，"
            "并原封不动地以 Hugo 短代码占位符的形式插入文章中。语法格式为：`{{< affiliate name=\"你的选择\" >}}`。\n"
            "⚠️警告：绝对禁止自己生造、拼写错误或发明任何不在此列表中的 Token，否则系统会崩溃！\n\n"
            "【当前合法的商品Token列表】：\n"
            f"{token_context_str}\n"
            "【输出结构】：文章必须包含：# 标题, ## 现象剖析, ## 生理学成因, ## 解决方案推荐（在此处埋入短代码）。"
        )

        user_prompt = f"用户真实提问：{question}\n用户具体描述的身体不适症状：{symptom}\n请围绕此问题完成一篇完整的科普评测文章。"

        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        
        payload = {
            "model": self.model,
            "temperature": self.temperature,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt}
            ],
            "response_format": {"type": "json_object"},
        }

        with httpx.Client(timeout=30.0) as client:
            resp = client.post(f"{self.base_url}/chat/completions", headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

            logger.warning("LLM generation json : %s", data)

            content = data["choices"][0]["message"]["content"]

            body = content
            summary = str(truncate(body, 200))

            # parsed = json.loads(content)
            # body = str(parsed.get("body_markdown") or "")
            # summary = str(parsed.get("summary") or truncate(body, 200))

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
