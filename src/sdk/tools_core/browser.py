"""Browser tools — Agent-Browser CLI implementation (core set).

Uses Agent-Browser (https://agent-browser.dev) by Vercel Labs.
Pure Rust CLI with daemon architecture, ~50ms command latency.
Ref-based element selection for deterministic AI interaction.

Key difference from Browser-Use CLI:
- Uses refs (@e1, @e2) instead of indices — deterministic across snapshots
- Compact text output (~200-400 tokens vs ~3000-5000 for full DOM)
- Pure Rust binary — no Python/Node.js runtime needed
- 50+ commands including network, clipboard, diff, device emulation

Core tools kept native (structured args, session state, interactive loop):
  browser_open        → agent-browser open <url>
  browser_snapshot    → agent-browser snapshot -i
  browser_click       → agent-browser click @<ref>
  browser_fill        → agent-browser fill @<ref> "<text>"
  browser_screenshot  → agent-browser screenshot [path]
  browser_eval        → agent-browser eval "<js>"

Long-tail commands (get title/text/html/url, tab new/close, back, forward,
scroll, type, press, hover, wait --text, session list, close --all) are
covered by the web-automation skill via shell_execute + the agent-browser
CLI — see seeds/skills/web-automation/SKILL.md.
"""

from __future__ import annotations

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, tool
from src.sdk.tools_core.cli_adapter import CLIToolAdapter

logger = get_logger()


class AgentBrowserCLI(CLIToolAdapter):
    cli_name = "agent-browser"
    install_hint = "brew install agent-browser  OR  npm install -g agent-browser"


_ab = AgentBrowserCLI()

_DEFAULT_SESSION = "default_session"
_DEFAULT_TIMEOUT = 60


def _session_flag(session: str | None) -> list[str]:
    if session:
        return ["--session", session]
    return []


def _check_available() -> str | None:
    err = _ab.require()
    if err:
        return err
    return None


@tool
def browser_open(url: str, session: str | None = None) -> str:
    """INTERACTIVE: Open a page in a visual browser (not for simple lookups).

    For INFORMATION retrieval (weather, news, facts, docs), use web_search instead.
    This tool requires the agent-browser CLI to be installed.
    Navigates the browser to the specified URL. Creates or reuses a session.
    After opening, use browser_snapshot to see the page elements.

    Args:
        url: The URL to open (e.g., 'https://example.com')
        session: Optional session name to reuse (default: 'default_session')

    Returns:
        Confirmation of navigation or error message
    """
    err = _check_available()
    if err:
        return f"Error: {err}"

    sess = session or _DEFAULT_SESSION
    args = ["open", url, "--session", sess]

    logger.info("browser.open", {"url": url, "session": sess}, channel="agent")

    rc, output = _ab.run(args, timeout=_DEFAULT_TIMEOUT)
    return output.strip() if rc == 0 else f"Error opening {url}: {output}"


browser_open.annotations = ToolAnnotations(title="Open URL in Browser", open_world=True)


@tool
def browser_snapshot(session: str | None = None) -> str:
    """Get the current page snapshot — interactive elements with refs.

    Returns a compact accessibility tree with element refs (@e1, @e2, etc.)
    that you can use with browser_click, browser_fill, etc.

    This is more token-efficient than getting full HTML. Typically 200-400 tokens
    vs 3000-5000 for full DOM.

    Args:
        session: Optional session name (default: 'default_session')

    Returns:
        Accessibility tree with refs for each interactive element
    """
    err = _check_available()
    if err:
        return f"Error: {err}"

    sess = session or _DEFAULT_SESSION
    args = ["snapshot", "-i", "--session", sess]

    logger.info("browser.snapshot", {"session": sess}, channel="agent")

    rc, output = _ab.run(args, timeout=30)
    if rc == 0:
        if len(output) > 8000:
            output = output[:8000] + "\n\n... [truncated]"
        return output
    return f"Error getting snapshot: {output}"


browser_snapshot.annotations = ToolAnnotations(
    title="Get Browser Snapshot", read_only=True, idempotent=True
)


@tool
def browser_click(ref: str, session: str | None = None) -> str:
    """Click on an element by its ref from the browser snapshot.

    Use browser_snapshot first to get element refs, then click by ref.
    Refs look like @e1, @e2, etc.

    Args:
        ref: Element ref from browser_snapshot (e.g., '@e5')
        session: Optional session name (default: 'default_session')

    Returns:
        Result of the click action
    """
    err = _check_available()
    if err:
        return f"Error: {err}"

    sess = session or _DEFAULT_SESSION
    args = ["click", ref, "--session", sess]

    logger.info("browser.click", {"ref": ref, "session": sess}, channel="agent")

    rc, output = _ab.run(args, timeout=_DEFAULT_TIMEOUT)
    return output.strip() if rc == 0 else f"Error clicking {ref}: {output}"


browser_click.annotations = ToolAnnotations(title="Click Browser Element")


@tool
def browser_fill(ref: str, text: str, session: str | None = None) -> str:
    """Fill text into a form field identified by its ref.

    Clears the field first, then types the text.

    Args:
        ref: Element ref from browser_snapshot (e.g., '@e3')
        text: Text to fill into the field
        session: Optional session name (default: 'default_session')

    Returns:
        Confirmation or error message
    """
    err = _check_available()
    if err:
        return f"Error: {err}"

    sess = session or _DEFAULT_SESSION
    args = ["fill", ref, text, "--session", sess]

    logger.info("browser.fill", {"ref": ref, "text": text[:50], "session": sess}, channel="agent")

    rc, output = _ab.run(args, timeout=_DEFAULT_TIMEOUT)
    return output.strip() if rc == 0 else f"Error filling {ref}: {output}"


browser_fill.annotations = ToolAnnotations(title="Fill Browser Field")


@tool
def browser_screenshot(path: str | None = None, session: str | None = None) -> str:
    """Take a screenshot of the current browser page.

    Args:
        path: Optional file path to save screenshot (e.g., 'page.png')
        session: Optional session name (default: 'default_session')

    Returns:
        Path to saved screenshot or confirmation message
    """
    err = _check_available()
    if err:
        return f"Error: {err}"

    sess = session or _DEFAULT_SESSION
    args = ["screenshot", "--session", sess]
    if path:
        args.append(path)

    logger.info("browser.screenshot", {"session": sess}, channel="agent")

    rc, output = _ab.run(args, timeout=15)
    return output.strip() if rc == 0 else f"Error taking screenshot: {output}"


browser_screenshot.annotations = ToolAnnotations(title="Take Browser Screenshot", read_only=True)


@tool
def browser_eval(script: str, session: str | None = None) -> str:
    """Execute JavaScript in the current browser page.

    Args:
        script: JavaScript code to execute (e.g., 'document.title')
        session: Optional session name (default: 'default_session')

    Returns:
        JavaScript execution result
    """
    err = _check_available()
    if err:
        return f"Error: {err}"

    sess = session or _DEFAULT_SESSION
    args = ["eval", script, "--session", sess]

    logger.info("browser.eval", {"session": sess}, channel="agent")

    rc, output = _ab.run(args, timeout=15)
    return output.strip() if rc == 0 else f"Error executing JavaScript: {output}"


browser_eval.annotations = ToolAnnotations(title="Execute Browser JavaScript", open_world=True)
