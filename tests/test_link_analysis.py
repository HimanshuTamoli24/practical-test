"""Unit and API tests for the link analysis prototype (Groq calls are mocked)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from app.main import app
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
from app.services.analysis_service import _to_response
from app.services.llm_service import LLMError, LLMResult
from app.services.website_extractor import ExtractedPage, WebsiteFetchError, parse_html
from app.utils.validators import InvalidURLError, normalize_url, validate_url

client = TestClient(app)


# --------------------------------------------------------------------------- #
# URL validation
# --------------------------------------------------------------------------- #
def test_normalize_url_adds_scheme_and_drops_fragment() -> None:
    assert normalize_url("example.com/path#section") == "https://example.com/path"


def test_normalize_url_keeps_http() -> None:
    assert normalize_url("http://example.com") == "http://example.com"


@pytest.mark.parametrize("url", ["ftp://example.com", "javascript:alert(1)", "https://"])
def test_normalize_url_rejects_bad_schemes(url: str) -> None:
    with pytest.raises(InvalidURLError):
        normalize_url(url)


@pytest.mark.parametrize(
    "url",
    ["http://127.0.0.1", "http://localhost:4000", "http://192.168.1.10", "http://[::1]"],
)
def test_validate_url_blocks_internal_hosts(url: str) -> None:
    with pytest.raises(InvalidURLError):
        validate_url(url)


# --------------------------------------------------------------------------- #
# HTML extraction
# --------------------------------------------------------------------------- #
def test_parse_html_extracts_relevant_content() -> None:
    html = """
    <html>
      <head>
        <title>Acme Consulting</title>
        <meta name="description" content="Business growth consulting for SaaS teams." />
        <script>console.log('ignore me')</script>
        <style>.hidden { display: none; }</style>
      </head>
      <body>
        <nav><a href="/">Home</a><a href="/about">About</a></nav>
        <h1>Grow faster with Acme</h1>
        <h2>Our services</h2>
        <p>We help SaaS companies scale revenue.</p>
        <p>We help SaaS companies scale revenue.</p>
        <a href="/services">Services</a>
      </body>
    </html>
    """
    page = parse_html(html, "https://acme.example")

    assert page.title == "Acme Consulting"
    assert page.meta_description == "Business growth consulting for SaaS teams."
    assert page.headings[:2] == ["Grow faster with Acme", "Our services"]
    assert "scale revenue" in page.text
    assert "ignore me" not in page.text
    assert page.text.count("We help SaaS companies scale revenue.") == 1
    assert "https://acme.example/services" in page.links


# --------------------------------------------------------------------------- #
# API behaviour
# --------------------------------------------------------------------------- #
def test_link_analysis_rejects_private_url() -> None:
    response = client.post("/link-analysis", json={"url": "http://127.0.0.1/admin"})

    assert response.status_code == 400
    assert "private" in response.json()["detail"].lower()


def test_link_analysis_rejects_invalid_body() -> None:
    response = client.post("/link-analysis", json={"website": "https://example.com"})

    assert response.status_code == 422
    assert response.json()["success"] is False


def test_link_analysis_returns_503_when_key_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, **_: object) -> ExtractedPage:
        return ExtractedPage(url=url, title="Acme", text="Acme sells consulting.")

    async def fake_analyze(_: ExtractedPage) -> LinkAnalysisResponse:
        raise LLMError("GROQ_API_KEY is not configured. Add your key to .env.")

    monkeypatch.setattr("app.main.fetch_page", fake_fetch)
    monkeypatch.setattr("app.main.analyze_page", fake_analyze)

    response = client.post("/link-analysis", json={"url": "https://example.com"})

    assert response.status_code == 503
    assert "GROQ_API_KEY" in response.json()["detail"]


def test_link_analysis_returns_502_when_site_unreachable(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, **_: object) -> ExtractedPage:
        raise WebsiteFetchError("Website returned HTTP 404 for https://example.com/missing.")

    monkeypatch.setattr("app.main.fetch_page", fake_fetch)

    response = client.post("/link-analysis", json={"url": "https://example.com/missing"})

    assert response.status_code == 502
    assert "HTTP 404" in response.json()["detail"]


def test_link_analysis_success_shape(monkeypatch: pytest.MonkeyPatch) -> None:
    async def fake_fetch(url: str, **_: object) -> ExtractedPage:
        return ExtractedPage(
            url=url,
            title="Acme Consulting",
            text="Acme Consulting helps SaaS teams grow revenue.",
        )

    async def fake_analyze(page: ExtractedPage) -> LinkAnalysisResponse:
        return LinkAnalysisResponse(
            website=page.url,
            brand=BrandInfo(
                name="Acme Consulting",
                category="Business Consulting",
                summary="Acme helps SaaS teams grow revenue.",
            ),
            analysis=AnalysisSections(
                competitor_analysis=CompetitorAnalysis(
                    brand_overview="Acme helps SaaS teams grow revenue.",
                    main_category="Business Consulting",
                    products_and_services=["Growth strategy"],
                    competitors=[
                        CompetitorProfile(
                            name="Big Consult",
                            main_service="Enterprise consulting",
                            target_market="Enterprises",
                            key_strength="Brand recognition",
                            weakness_or_gap="Slow delivery",
                            opportunity="Win on speed and pricing",
                        )
                    ],
                    competitive_advantages=["Focused on SaaS"],
                    strengths=["Specialist positioning"],
                    opportunities=["Publish case studies"],
                ),
                target_search_queries=[
                    SearchQuery(
                        query="saas growth consulting",
                        platforms=["Google", "LinkedIn"],
                        intent="Service search",
                        business_relevance="High-intent buyers",
                        target_audience="SaaS founders",
                    )
                ],
                seo_improvements=SeoImprovements(
                    primary_keyword="SaaS growth consulting",
                    secondary_keywords=["revenue consulting"],
                    long_tail_keywords=["saas revenue growth consultant"],
                    business_keywords=["b2b consulting"],
                    recommended_title="SaaS Growth Consulting for B2B Teams",
                    meta_description="Acme helps SaaS teams grow revenue with practical consulting.",
                    supporting_content=["Customer case studies"],
                ),
                top_services=[
                    ServiceItem(
                        name="Growth strategy",
                        description="Revenue planning",
                        target_industry="SaaS",
                        target_customer="SaaS founders",
                        main_benefit="Faster growth",
                        source="website content",
                    )
                ],
                target_audience=TargetAudience(
                    primary_audience=["SaaS founders"],
                    secondary_audience=["Sales leaders"],
                    industries=["B2B SaaS"],
                    business_types=["Startups", "SMBs"],
                    customer_profiles=["Founder-led sales teams"],
                    location_focus=None,
                    customer_needs=["Predictable pipeline"],
                    customer_interests=["Growth tactics"],
                    recommended_segments=["Seed to Series A SaaS"],
                ),
            ),
            meta=LlmMeta(
                model="openai/gpt-oss-120b",
                input_tokens=2500,
                output_tokens=900,
                response_ms=1800,
                extracted_characters=page.content_length,
            ),
        )

    monkeypatch.setattr("app.main.fetch_page", fake_fetch)
    monkeypatch.setattr("app.main.analyze_page", fake_analyze)

    response = client.post("/link-analysis", json={"url": "example.com"})

    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["website"] == "https://example.com"
    assert body["brand"]["name"] == "Acme Consulting"
    assert len(body["analysis"]["target_search_queries"]) == 1
    assert body["meta"]["provider"] == "Groq"
    assert body["meta"]["model"] == "openai/gpt-oss-120b"


def test_health_endpoint() -> None:
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ui_is_served_at_root() -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "LinkLens" in response.text
    assert "/link-analysis" in response.text



# --------------------------------------------------------------------------- #
# LLM output normalization
# --------------------------------------------------------------------------- #
def test_llm_output_is_normalized_leniently() -> None:
    page = ExtractedPage(url="https://acme.example", title="Acme", text="Acme sells consulting.")
    result = LLMResult(
        data={
            "brand": {"name": "Acme", "category": "Consulting"},
            "analysis": {
                "competitor_analysis": {
                    "brand_overview": "Acme is a consultancy.",
                    "products_and_services": "Growth strategy",
                    "competitors": [
                        {"name": "Big Consult", "weakness": "Slow", "opportunity": "Speed"}
                    ],
                },
                "target_search_queries": [
                    {"query": "growth consulting", "platform": "google search", "intent": "Buy"}
                ],
                "seo_improvements": {
                    "primary_keyword": "growth consulting",
                    "supporting_content": [{"idea": "Case studies"}],
                },
                "top_services": [
                    {"service_name": "Growth strategy", "benefit": "Revenue lift"},
                    {"description": "no name, dropped"},
                ],
                "target_audience": {"primary_audience": "SaaS founders"},
            },
        },
        model="openai/gpt-oss-120b",
        input_tokens=100,
        output_tokens=50,
        response_ms=1200,
    )

    response = _to_response(page, result)

    assert response.brand.name == "Acme"
    assert response.analysis.competitor_analysis.products_and_services == ["Growth strategy"]
    assert response.analysis.competitor_analysis.competitors[0].name == "Big Consult"
    assert response.analysis.competitor_analysis.competitors[0].basis.startswith("AI-generated")
    assert response.analysis.target_search_queries[0].platforms == ["Google"]
    assert response.analysis.seo_improvements.supporting_content == ["Case studies"]
    assert [s.name for s in response.analysis.top_services] == ["Growth strategy"]
    assert response.analysis.target_audience.primary_audience == ["SaaS founders"]
    assert response.meta.extracted_characters == page.content_length

