"""Fetch a public web page and extract the parts that matter for analysis."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.config import settings
from app.utils.validators import validate_url

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36 LinkAnalysisBot/1.0"
)

MAX_REDIRECTS = 5
REMOVED_TAGS = ("script", "style", "noscript", "svg", "iframe", "template", "form", "canvas")


class WebsiteFetchError(RuntimeError):
    """Raised when a website cannot be downloaded or is not HTML."""


@dataclass
class ExtractedPage:
    url: str
    status_code: int = 200
    title: str = ""
    meta_description: str = ""
    headings: list[str] = field(default_factory=list)
    text: str = ""
    links: list[str] = field(default_factory=list)

    @property
    def content_length(self) -> int:
        return len(self.text)


def _clean(text: str) -> str:
    """Collapse whitespace and drop duplicate lines (menus/footers repeat a lot)."""
    lines: list[str] = []
    seen: set[str] = set()
    for raw_line in text.replace("\xa0", " ").splitlines():
        line = re.sub(r"\s+", " ", raw_line).strip()
        if not line:
            continue
        key = line.lower()
        if key in seen:
            continue
        seen.add(key)
        lines.append(line)
    return "\n".join(lines)


def parse_html(html: str, url: str, max_chars: int | None = None) -> ExtractedPage:
    """Turn raw HTML into a compact, LLM friendly representation."""
    max_chars = max_chars or settings.max_content_chars
    soup = BeautifulSoup(html, "html.parser")

    for tag in soup(REMOVED_TAGS):
        tag.decompose()

    title = soup.title.get_text(" ", strip=True) if soup.title else ""

    meta_description = ""
    meta_tag = soup.find("meta", attrs={"name": "description"}) or soup.find(
        "meta", attrs={"property": "og:description"}
    )
    if meta_tag and meta_tag.get("content"):
        meta_description = _clean(str(meta_tag["content"]))

    headings: list[str] = []
    for heading in soup.find_all(["h1", "h2", "h3"]):
        text = _clean(heading.get_text(" ", strip=True))
        if text and text not in headings:
            headings.append(text)
        if len(headings) >= 30:
            break

    root = soup.find("main") or soup.find("article") or soup.body or soup
    text = _clean(root.get_text("\n", strip=True))
    if len(text) > max_chars:
        text = text[:max_chars].rsplit("\n", 1)[0] + "\n[... content truncated ...]"

    host = urlparse(url).netloc
    links: list[str] = []
    for anchor in soup.find_all("a", href=True):
        absolute = urljoin(url, str(anchor["href"]))
        parsed = urlparse(absolute)
        if parsed.scheme not in ("http", "https") or not parsed.netloc:
            continue
        absolute = absolute.split("#", 1)[0]
        if parsed.netloc == host and absolute not in links:
            links.append(absolute)
        if len(links) >= 25:
            break

    return ExtractedPage(
        url=url,
        title=_clean(title),
        meta_description=meta_description,
        headings=headings,
        text=text,
        links=links,
    )


async def fetch_page(
    url: str,
    *,
    timeout: float | None = None,
    max_bytes: int | None = None,
    max_chars: int | None = None,
) -> ExtractedPage:
    """Download ``url`` (following redirects safely) and extract its content."""
    timeout = timeout or settings.request_timeout
    max_bytes = max_bytes or settings.max_page_bytes
    max_chars = max_chars or settings.max_content_chars

    current = url
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }

    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        for _ in range(MAX_REDIRECTS + 1):
            # Re-validate on every hop so a redirect cannot reach an internal host.
            validate_url(current)
            try:
                response = await client.get(current)
            except httpx.HTTPError as exc:
                raise WebsiteFetchError(f"Could not reach {current}: {exc}") from exc

            if response.is_redirect:
                location = response.headers.get("location")
                if not location:
                    raise WebsiteFetchError("Website returned a redirect without a location.")
                current = str(response.url.join(location))
                continue

            if response.status_code >= 400:
                raise WebsiteFetchError(
                    f"Website returned HTTP {response.status_code} for {current}."
                )

            content_type = response.headers.get("content-type", "").lower()
            if "html" not in content_type and "text/" not in content_type:
                raise WebsiteFetchError(
                    f"URL did not return an HTML page (content-type: {content_type or 'unknown'})."
                )

            if len(response.content) > max_bytes:
                raise WebsiteFetchError(
                    f"Website is larger than the configured limit ({max_bytes} bytes)."
                )

            page = parse_html(response.text, str(response.url), max_chars=max_chars)
            page.status_code = response.status_code
            if not page.text:
                raise WebsiteFetchError("No readable text content was found on the page.")
            return page

    raise WebsiteFetchError("Website redirected too many times.")
