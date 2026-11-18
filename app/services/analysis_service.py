"""Prompt construction, Groq call and normalization into the response schema."""

from __future__ import annotations

from typing import Any

from app.config import settings
from app.schemas import (
    AnalysisSections,
    BrandInfo,
    CompetitorAnalysis,
    CompetitorProfile,
    LinkAnalysisResponse,
    LlmMeta,
    SearchQuery,
    SeoImprovements,
    ServiceItem,
    TargetAudience,
)
from app.services.llm_service import GroqClient, LLMError, LLMResult
from app.services.website_extractor import ExtractedPage

SYSTEM_PROMPT = """You are a senior business, SEO and competitive-intelligence analyst.
You receive the extracted text of one public website. Analyse ONLY that content.

Return a single JSON object with exactly this shape:
{
  "brand": {
    "name": "brand name",
    "category": "main business category",
    "summary": "2-3 sentence factual overview of the business"
  },
  "analysis": {
    "competitor_analysis": {
      "brand_overview": "overview of the submitted brand",
      "main_category": "category",
      "products_and_services": ["..."],
      "competitors": [
        {
          "name": "competitor name",
          "main_service": "what they sell",
          "target_market": "who they serve",
          "key_strength": "their strength",
          "weakness_or_gap": "their weakness or gap",
          "opportunity": "how the submitted brand can position against them",
          "basis": "AI-generated suggestion"
        }
      ],
      "competitive_advantages": ["..."],
      "strengths": ["..."],
      "opportunities": ["..."]
    },
    "target_search_queries": [
      {
        "query": "realistic search phrase",
        "platforms": ["Google"],
        "intent": "search intent",
        "business_relevance": "why it matters to this business",
        "target_audience": "who would search this"
      }
    ],
    "seo_improvements": {
      "primary_keyword": "...",
      "secondary_keywords": ["..."],
      "long_tail_keywords": ["..."],
      "business_keywords": ["..."],
      "recommended_title": "SEO title under 60 characters",
      "meta_description": "meta description, 140-160 characters",
      "supporting_content": ["specific content/page idea"]
    },
    "top_services": [
      {
        "name": "service name",
        "description": "short explanation",
        "target_industry": "industry",
        "target_customer": "customer type",
        "main_benefit": "why customers need it",
        "source": "website content" or "inferred"
      }
    ],
    "target_audience": {
      "primary_audience": ["..."],
      "secondary_audience": ["..."],
      "industries": ["..."],
      "business_types": ["..."],
      "customer_profiles": ["..."],
      "location_focus": "city/country/region or null",
      "customer_needs": ["..."],
      "customer_interests": ["..."],
      "recommended_segments": ["..."]
    }
  }
}

Rules:
- Use 5 target_search_queries, 5 top_services and up to 5 competitors.
- Platforms must be chosen from: Google, Facebook, Instagram, LinkedIn, X.
- Never invent search volumes, ranking positions or traffic numbers.
- Mark services as "inferred" when the page does not state them explicitly.
- Competitor entries are AI-generated suggestions: keep basis honest.
- Write specific, actionable insights grounded in the page text. No placeholders.
- Output JSON only. No markdown, no commentary."""


def build_user_prompt(page: ExtractedPage) -> str:
    headings = "\n".join(f"- {h}" for h in page.headings[:20]) or "- (none found)"
    links = "\n".join(page.links[:15]) or "- (none found)"
    return f"""WEBSITE: {page.url}
PAGE TITLE: {page.title or "(none)"}
META DESCRIPTION: {page.meta_description or "(none)"}

HEADINGS:
{headings}

INTERNAL LINKS:
{links}

PAGE CONTENT:
{page.text}
"""


def _as_text(value: Any, default: str = "") -> str:
    if isinstance(value, str):
        cleaned = value.strip()
        return cleaned or default
    if isinstance(value, (int, float)):
        return str(value)
    return default


def _as_text_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    items: list[str] = []
    for item in value:
        if isinstance(item, str) and item.strip():
            items.append(item.strip())
        elif isinstance(item, dict):
            for key in ("name", "text", "value", "title", "idea", "description"):
                text = _as_text(item.get(key))
                if text:
                    items.append(text)
                    break
    return items


def _as_dicts(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        value = [value]
    if not isinstance(value, (list, tuple)):
        return []
    return [item for item in value if isinstance(item, dict)]


_PLATFORM_ALIASES = {
    "google": "Google",
    "google search": "Google",
    "facebook": "Facebook",
    "fb": "Facebook",
    "instagram": "Instagram",
    "ig": "Instagram",
    "linkedin": "LinkedIn",
    "x": "X",
    "twitter": "X",
    "x (twitter)": "X",
}


def _platforms(value: Any) -> list[str]:
    raw = _as_text_list(value)
    platforms: list[str] = []
    for item in raw:
        canonical = _PLATFORM_ALIASES.get(item.lower(), item)
        if canonical not in platforms:
            platforms.append(canonical)
    return platforms or ["Google"]


def _competitors(raw: Any) -> list[CompetitorProfile]:
    competitors: list[CompetitorProfile] = []
    for item in _as_dicts(raw):
        name = _as_text(item.get("name") or item.get("brand") or item.get("competitor"))
        if not name:
            continue
        competitors.append(
            CompetitorProfile(
                name=name,
                main_service=_as_text(item.get("main_service") or item.get("service")) or None,
                target_market=_as_text(item.get("target_market")) or None,
                key_strength=_as_text(item.get("key_strength")) or None,
                weakness_or_gap=_as_text(
                    item.get("weakness_or_gap") or item.get("weakness") or item.get("gap")
                )
                or None,
                opportunity=_as_text(item.get("opportunity")) or None,
                basis=_as_text(item.get("basis"), "AI-generated suggestion (not externally verified)"),
            )
        )
    return competitors[:5]


def _queries(raw: Any) -> list[SearchQuery]:
    queries: list[SearchQuery] = []
    for item in _as_dicts(raw):
        query = _as_text(item.get("query") or item.get("search_query"))
        if not query:
            continue
        queries.append(
            SearchQuery(
                query=query,
                platforms=_platforms(item.get("platforms") or item.get("platform")),
                intent=_as_text(item.get("intent")),
                business_relevance=_as_text(
                    item.get("business_relevance") or item.get("relevance")
                ),
                target_audience=_as_text(item.get("target_audience") or item.get("audience")),
            )
        )
    return queries


def _services(raw: Any) -> list[ServiceItem]:
    services: list[ServiceItem] = []
    for item in _as_dicts(raw):
        name = _as_text(item.get("name") or item.get("service_name"))
        if not name:
            continue
        services.append(
            ServiceItem(
                name=name,
                description=_as_text(item.get("description")),
                target_industry=_as_text(item.get("target_industry") or item.get("industry")),
                target_customer=_as_text(item.get("target_customer") or item.get("customer")),
                main_benefit=_as_text(item.get("main_benefit") or item.get("benefit")),
                source=_as_text(item.get("source"), "inferred"),
            )
        )
    return services[:5]


def _seo(raw: dict[str, Any]) -> SeoImprovements:
    return SeoImprovements(
        primary_keyword=_as_text(raw.get("primary_keyword")),
        secondary_keywords=_as_text_list(raw.get("secondary_keywords")),
        long_tail_keywords=_as_text_list(raw.get("long_tail_keywords")),
        business_keywords=_as_text_list(raw.get("business_keywords")),
        recommended_title=_as_text(raw.get("recommended_title") or raw.get("seo_title")),
        meta_description=_as_text(raw.get("meta_description")),
        supporting_content=_as_text_list(
            raw.get("supporting_content") or raw.get("additional_content")
        ),
    )


def _audience(raw: dict[str, Any]) -> TargetAudience:
    return TargetAudience(
        primary_audience=_as_text_list(raw.get("primary_audience")),
        secondary_audience=_as_text_list(raw.get("secondary_audience")),
        industries=_as_text_list(raw.get("industries")),
        business_types=_as_text_list(raw.get("business_types")),
        customer_profiles=_as_text_list(raw.get("customer_profiles") or raw.get("customers")),
        location_focus=_as_text(raw.get("location_focus") or raw.get("location")) or None,
        customer_needs=_as_text_list(raw.get("customer_needs")),
        customer_interests=_as_text_list(raw.get("customer_interests")),
        recommended_segments=_as_text_list(
            raw.get("recommended_segments") or raw.get("marketing_segments")
        ),
    )


def _to_response(page: ExtractedPage, result: LLMResult) -> LinkAnalysisResponse:
    data = result.data
    analysis_raw = data.get("analysis")
    if not isinstance(analysis_raw, dict):
        analysis_raw = data

    brand_raw = data.get("brand")
    if not isinstance(brand_raw, dict):
        brand_raw = {}

    competitor_raw = analysis_raw.get("competitor_analysis")
    if not isinstance(competitor_raw, dict):
        competitor_raw = {}

    seo_raw = analysis_raw.get("seo_improvements")
    if not isinstance(seo_raw, dict):
        seo_raw = {}

    audience_raw = analysis_raw.get("target_audience")
    if not isinstance(audience_raw, dict):
        audience_raw = {}

    brand = BrandInfo(
        name=_as_text(brand_raw.get("name"), page.title or page.url),
        category=_as_text(
            brand_raw.get("category"), _as_text(competitor_raw.get("main_category"), "Unknown")
        ),
        summary=_as_text(brand_raw.get("summary") or competitor_raw.get("brand_overview")),
    )

    analysis = AnalysisSections(
        competitor_analysis=CompetitorAnalysis(
            brand_overview=_as_text(competitor_raw.get("brand_overview"), brand.summary),
            main_category=_as_text(competitor_raw.get("main_category"), brand.category),
            products_and_services=_as_text_list(competitor_raw.get("products_and_services")),
            competitors=_competitors(competitor_raw.get("competitors")),
            competitive_advantages=_as_text_list(competitor_raw.get("competitive_advantages")),
            strengths=_as_text_list(competitor_raw.get("strengths")),
            opportunities=_as_text_list(competitor_raw.get("opportunities")),
        ),
        target_search_queries=_queries(analysis_raw.get("target_search_queries")),
        seo_improvements=_seo(seo_raw),
        top_services=_services(analysis_raw.get("top_services")),
        target_audience=_audience(audience_raw),
    )

    return LinkAnalysisResponse(
        website=page.url,
        brand=brand,
        analysis=analysis,
        meta=LlmMeta(
            model=result.model,
            input_tokens=result.input_tokens,
            output_tokens=result.output_tokens,
            response_ms=result.response_ms,
            extracted_characters=page.content_length,
        ),
    )


async def analyze_page(page: ExtractedPage) -> LinkAnalysisResponse:
    """Run the Groq analysis for an already extracted page."""
    if not settings.groq_configured:
        raise LLMError(
            "GROQ_API_KEY is not configured. Copy .env.example to .env and add your key."
        )

    client = GroqClient(
        api_key=settings.groq_api_key,
        model=settings.groq_model,
        fallback_model=settings.groq_fallback_model,
        base_url=settings.groq_base_url,
        timeout=settings.llm_timeout,
    )
    result = await client.complete_json(SYSTEM_PROMPT, build_user_prompt(page))
    return _to_response(page, result)
