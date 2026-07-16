"""Built-in web tools — fetch and search, zero configuration required.

Uses httpx for HTTP requests and DuckDuckGo HTML search (no API key).
Works without any external service or account.
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

import html2text
import httpx

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool

logger = get_logger()

USER_AGENT = "Mozilla/5.0 (compatible; Assistant/1.0; +https://github.com/ea)"
TIMEOUT = 30.0
MAX_CONTENT_LENGTH = 10000

_h2t = html2text.HTML2Text()
_h2t.ignore_links = False
_h2t.ignore_images = True
_h2t.ignore_emphasis = False
_h2t.body_width = 0


@tool
def web_fetch(url: str) -> str:
    """Fetch a URL and return its content as markdown.

    Fetches any HTTP/HTTPS URL and converts HTML to clean markdown.
    Works with no API key or external service required.

    Args:
        url: The URL to fetch (e.g., 'https://example.com')

    Returns:
        Page content as markdown text
    """
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    logger.info("web.fetch", {"url": url}, channel="agent")

    try:
        response = httpx.get(
            url,
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()

        content_type = response.headers.get("content-type", "").lower()
        if "text/html" in content_type:
            text = _h2t.handle(response.text)
        elif "text/" in content_type:
            text = response.text
        else:
            return f"Unsupported content type: {content_type}"

        if len(text) > MAX_CONTENT_LENGTH:
            text = text[:MAX_CONTENT_LENGTH] + "\n\n... [truncated]"

        return text

    except httpx.TimeoutException:
        return f"Error: Request timed out after {TIMEOUT}s for {url}"
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} for {url}"
    except httpx.RequestError as e:
        return f"Error fetching {url}: {e}"
    except Exception as e:
        logger.warning("web.fetch_error", {"url": url, "error": str(e)})
        return f"Error fetching {url}: {e}"


web_fetch.annotations = ToolAnnotations(
    title="Web Fetch", read_only=True, idempotent=True, open_world=True
)


_DDG_URL_RE = re.compile(
    r'uddg=([^&\'"]+)',
)


def _extract_real_url(ddg_url: str) -> str:
    """Extract the real URL from a DuckDuckGo redirect URL."""
    m = _DDG_URL_RE.search(ddg_url)
    if m:
        from urllib.parse import unquote

        return unquote(m.group(1))
    return ddg_url


def _parse_ddg_results(html: str, limit: int = 10) -> list[dict[str, Any]]:
    """Parse DuckDuckGo HTML search results into structured items."""
    results: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    title_link_re = re.compile(
        r'<a[^>]*rel="nofollow"[^>]*class="result__a"[^>]*href="([^"]*)"[^>]*>(.*?)</a>',
        re.DOTALL,
    )

    snippet_re = re.compile(
        r'<a[^>]*class="result__snippet"[^>]*>\s*(.*?)\s*</a>',
        re.DOTALL,
    )

    titles = list(title_link_re.finditer(html))
    snippets = list(snippet_re.finditer(html))

    for match in titles:
        raw_url = match.group(1)
        real_url = _extract_real_url(raw_url)
        title = _html.unescape(re.sub(r"<[^>]+>", "", match.group(2)).strip())

        if real_url in seen_urls:
            continue
        seen_urls.add(real_url)

        results.append({"title": title, "url": real_url, "snippet": ""})
        if len(results) >= limit:
            break

    for i, match in enumerate(snippets):
        if i < len(results):
            snippet = _html.unescape(re.sub(r"<[^>]+>", "", match.group(1)).strip())
            results[i]["snippet"] = snippet

    return results


@tool
def web_search(query: str, limit: int = 10) -> str:
    """Search the web and return results.

    Uses DuckDuckGo search. No API key required.
    Returns titles, URLs, and snippets for each result.

    Args:
        query: Search query (e.g., 'latest AI news')
        limit: Number of results to return (default: 10, max: 20)

    Returns:
        Search results formatted as markdown
    """
    limit = min(limit, 20)

    logger.info("web.search", {"query": query, "limit": limit}, channel="agent")

    try:
        response = httpx.get(
            "https://html.duckduckgo.com/html/",
            params={"q": query},
            headers={"User-Agent": USER_AGENT},
            timeout=TIMEOUT,
            follow_redirects=True,
        )
        response.raise_for_status()

        results = _parse_ddg_results(response.text, limit)

        if not results:
            return f"No results found for: {query}"

        output = f"## Search Results for: {query}\n\n"
        for i, r in enumerate(results, 1):
            output += f"### {i}. {r['title']}\n"
            output += f"   URL: {r['url']}\n"
            if r["snippet"]:
                output += f"   {r['snippet']}\n"
            output += "\n"

        return output

    except httpx.TimeoutException:
        return f"Error: Search request timed out after {TIMEOUT}s"
    except httpx.HTTPStatusError as e:
        return f"Error: HTTP {e.response.status_code} from search"
    except httpx.RequestError as e:
        return f"Error searching: {e}"
    except Exception as e:
        logger.warning("web.search_error", {"query": query, "error": str(e)})
        return f"Error searching for '{query}': {e}"


web_search.annotations = ToolAnnotations(
    title="Web Search", read_only=True, open_world=True
)
