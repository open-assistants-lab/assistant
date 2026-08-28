"""Design-system extractor (P1-T6, keystone demo).

Fetches a site, extracts look-and-feel tokens (palette, typography,
spacing, radii) and drafts a design-system SKILL.md into the skill review
queue. The draft NEVER joins the live skills dir until a human approves it
via SkillRegistry.approve_skill_draft().
"""

from __future__ import annotations

import colorsys
import re
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlparse

import httpx

from src.app_logging import get_logger
from src.sdk.tools import tool
from src.sdk.tools_core.web import TIMEOUT, USER_AGENT

logger = get_logger()

MAX_PAGES = 3
MAX_STYLESHEETS = 5

_HEX_RE = re.compile(r"#[0-9a-fA-F]{6}\b|#[0-9a-fA-F]{3}\b")
_FONT_RE = re.compile(r"font-family\s*:\s*([^;}}]+)")
_SIZE_RE = re.compile(r"font-size\s*:\s*([^;}}]+)")
_SPACE_RE = re.compile(r"(?:margin|padding|gap|row-gap|column-gap)\s*:\s*([^;}}]+)")
_PX_RE = re.compile(r"(\d+(?:\.\d+)?)px")
_RADIUS_RE = re.compile(r"border-radius\s*:\s*([^;}}]+)")
_LINK_RE = re.compile(
    r"<link\b[^>]*?rel=[\"']stylesheet[\"'][^>]*?>", re.IGNORECASE
)
_HREF_RE = re.compile(r"href=[\"']([^\"']+)[\"']", re.IGNORECASE)


def _expand_hex(color: str) -> str:
    h = color.lstrip("#")
    if len(h) == 3:
        h = "".join(c * 2 for c in h)
    return "#" + h.lower()


def _lightness(hex_color: str) -> float:
    """Perceived lightness 0..1 for hex (#rgb or #rrggbb)."""
    h = hex_color.lstrip("#")
    r, g, b = (int(h[i : i + 2], 16) / 255.0 for i in (0, 2, 4))
    _, lig, _ = colorsys.rgb_to_hls(r, g, b)
    return lig


def _extract_css(css: str) -> dict[str, list[str]]:
    colors = [c for c in _HEX_RE.findall(css)]
    fonts = [m.strip() for m in _FONT_RE.findall(css) if m.strip()]
    sizes = [m.strip() for m in _SIZE_RE.findall(css) if m.strip()]
    space_vals = [m for m in _SPACE_RE.findall(css)]
    spacings = sorted(
        {round(float(p), 1) for v in space_vals for p in _PX_RE.findall(v)}
    )
    radii = [p for m in _RADIUS_RE.findall(css) for p in _PX_RE.findall(m)]
    return {
        "colors": colors,
        "fonts": fonts,
        "sizes": sizes,
        "spacings": [f"{v:g}px" for v in spacings],
        "radii": radii,
    }


def _name_colors(colors: list[str]) -> list[tuple[str, str]]:
    """Deterministic semantic names for the palette (hex -> token, value)."""
    freq = Counter(_expand_hex(c) for c in colors)
    ordered = [c for c, _ in freq.most_common()]
    if not ordered:
        return []
    by_lig = sorted(ordered, key=_lightness)
    named: list[tuple[str, str]] = []

    def take(candidate: str) -> str | None:
        if candidate in ordered:
            ordered.remove(candidate)
            return candidate
        return None

    ink = by_lig[0] if _lightness(by_lig[0]) < 0.35 else None
    surface = by_lig[-1] if _lightness(by_lig[-1]) > 0.82 else None
    if ink:
        named.append(("--color-ink", ink))
        take(ink)
    if surface and surface in ordered:
        named.append(("--color-surface", surface))
        take(surface)
    # Remaining colors, most frequent first: primary, accent, muted, extra-N.
    semantic = ["--color-primary", "--color-accent", "--color-muted"]
    idx = 0
    for c in ordered[:6]:
        if any(c == v for _, v in named):
            continue
        label = semantic[idx] if idx < len(semantic) else f"--color-extra-{idx}"
        named.append((label, c))
        idx += 1
    return named


def _slugify_host(url: str) -> str:
    host = urlparse(url).hostname or "site"
    host = re.sub(r"[^a-z0-9-]", "-", host.lower())
    host = re.sub(r"-+", "-", host).strip("-")
    return host or "site"


def _fetch(url: str) -> httpx.Response:
    return httpx.get(
        url,
        headers={"User-Agent": USER_AGENT},
        timeout=TIMEOUT,
        follow_redirects=True,
    )


def _collect_styles(html: str, base_url: str) -> list[str]:
    """Inline <style> blocks + linked stylesheets (capped)."""
    styles: list[str] = []
    for m in re.finditer(r"<style\b[^>]*>(.*?)</style>", html, re.IGNORECASE | re.DOTALL):
        styles.append(m.group(1))
    for link in _LINK_RE.findall(html)[:MAX_STYLESHEETS]:
        href_match = _HREF_RE.search(link)
        if not href_match:
            continue
        css_url = urljoin(base_url, href_match.group(1))
        try:
            styles.append(_fetch(css_url).text)
        except httpx.HTTPError as e:
            logger.warning(
                "design_extract.css_fetch_failed",
                {"css_url": css_url, "error": str(e)},
            )
    return styles


def _draft_body(tokens: dict[str, list[str]], url: str) -> str:
    color_tokens = _name_colors(tokens["colors"])

    font_faces: list[str] = []
    for stack in tokens["fonts"][:3]:
        primary = stack.split(",")[0].strip().strip("'\"")
        if primary and primary.lower() not in ("inherit", "initial"):
            font_faces.append(primary)

    sizes = tokens["sizes"][:4]
    spacing = tokens["spacings"][:6]
    radii = [f"{r.split()[0]}" for r in tokens["radii"][:2]]
    body_lines: list[str] = []

    def add(line: str) -> None:
        body_lines.append(line)

    add("")
    add("## Design tokens")
    add("")
    add("```css")
    add(":root {")
    for label, value in color_tokens:
        add(f"  {label}: {value};")
    for i, size in enumerate(sizes):
        add(f"  --font-size-{i + 1}: {size};")
    for i, s in enumerate(spacing, start=1):
        add(f"  --space-{i}: {s};")
    if radii:
        for i, r in enumerate(radii, start=1):
            add(f"  --radius-{i}: {r};")
    add("}")
    add("```")
    add("")
    add("## Typography")
    add("")
    for face in font_faces:
        add(f"- Font family: `{face}`")
    for size in sizes:
        add(f"- Font size: `{size}`")
    add("")
    add("## Usage notes")
    add("")
    add(
        f"Source: {url}. Tokens are extracted from the site's shipped CSS — "
        "verify against the live site for animation/motion tastes, imagery "
        "style, and voice before applying them to new work."
    )
    return "\n".join(body_lines) + "\n"


@tool(name="design_extract")  # type: ignore[untyped-decorator]
def design_extract(url: str, registry: Any = None) -> Any:
    """Fetch a site and draft a design-system SKILL.md into the review queue.

    Extracts palette, typography, spacing scale, and radii from the page's
    HTML/CSS. The draft is written to the skill review queue and is NOT
    available to the agent (or get_loaded_skills()) until a human approves
    it via SkillRegistry.approve_skill_draft().

    Args:
        url: Site URL to analyze (e.g. 'https://example.com').
        registry: SkillRegistry (defaults to the current user's registry).

    Returns:
        ToolResult with draft path and token inventory.
    """
    from src.sdk.tools import ToolResult
    from src.skills.registry import get_skill_registry

    reg = registry if registry is not None else get_skill_registry()

    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    try:
        resp = _fetch(url)
        resp.raise_for_status()
    except httpx.HTTPError as e:
        logger.warning("design_extract.fetch_failed", {"url": url, "error": str(e)})
        return ToolResult(
            content=f"Site unreachable, cannot extract design tokens: {url} ({e})",
            structured_content={"url": url, "error": str(e)},
            is_error=True,
        )

    html = resp.text
    css_blob = "\n".join(_collect_styles(html, str(resp.url)))
    if "<style" in html.lower():
        css_blob += "\n" + "\n".join(
            m.group(1) for m in re.finditer(r"<style\b[^>]*>(.*?)</style>", html, re.IGNORECASE | re.DOTALL)
        )

    tokens = _extract_css(css_blob)
    host = _slugify_host(url)
    skill_name = f"{host}-design"
    skill_name = skill_name[:64].rstrip("-")

    description = (
        f'Design system for {urlparse(url).hostname} - palette, typography, '
        "spacing scale, and radii extracted from the site's CSS."
    )
    frontmatter = (
        f"---\nname: {skill_name}\ndescription: {description}\n---\n\n"
    )
    content = frontmatter + _draft_body(tokens, str(resp.url))

    draft_path = reg.put_skill_draft(skill_name, content, source=str(resp.url))
    logger.info(
        "design_extract.drafted",
        {"url": str(resp.url), "skill": skill_name},
    )
    return ToolResult(
        content=(
            f"Design-system skill drafted: `{skill_name}` (pending review at "
            f"{draft_path}). {len(tokens['colors'])} colors, "
            f"{len(tokens['fonts'])} font stacks, {len(tokens['spacings'])} "
            "spacing steps extracted. Approve via the review queue to load it."
        ),
        structured_content={
            "draft_name": skill_name,
            "draft_path": str(draft_path),
            "colors": len(set(_expand_hex(c) for c in tokens["colors"])),
            "fonts": len(tokens["fonts"]),
            "spacings": len(tokens["spacings"]),
        },
    )
