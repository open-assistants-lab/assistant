"""Browser automation via agent-browser CLI — replaces Playwright browser.py.

agent-browser is a fast Rust CLI for browser automation via CDP (Chrome DevTools
Protocol). It uses accessibility-tree snapshots with @eN element references,
making it more reliable than CSS-selector-based approaches.

Install: npm i -g agent-browser && agent-browser install
"""

import json
import os
from pathlib import Path
from typing import Any

from src.app_logging import get_logger
from src.sdk.tools import ToolAnnotations, ToolResult, tool

logger = get_logger()


def _find_agent_browser() -> str:
    """Find agent-browser binary on PATH or bundled with EA."""
    try:
        from src.sdk.sandbox import get_sandbox_backend

        result = get_sandbox_backend().run(
            ["which", "agent-browser"], Path.cwd(), None
        )
        if result.exit_code == 0:
            return result.stdout.strip()
    except Exception:
        pass
    bundled = Path(__file__).parent.parent.parent.parent / "bin" / "agent-browser"
    if bundled.exists():
        return str(bundled)
    return "agent-browser"


def _ab(*args: str, json_output: bool = False, timeout: int = 60) -> dict[str, Any]:
    """Run agent-browser command and return parsed output."""
    cmd = [_find_agent_browser(), *args]
    if json_output:
        cmd.append("--json")
    try:
        from src.sdk.sandbox import SandboxLimits, get_sandbox_backend

        result = get_sandbox_backend().run(
            cmd, Path.cwd(), SandboxLimits(timeout_seconds=float(timeout))
        )
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip()[:200]
            if "not installed" in error.lower() or "not found" in error.lower():
                return {
                    "success": False,
                    "error": (
                        "agent-browser not installed. Install: npm i -g agent-browser"
                        " && agent-browser install"
                    ),
                }
            return {"success": False, "error": error[:500]}
        if json_output:
            try:
                data: dict[str, Any] = json.loads(result.stdout)
                data["success"] = True
                return data
            except json.JSONDecodeError:
                return {"success": True, "text": result.stdout.strip()}
        return {"success": True, "text": result.stdout.strip()}
    except FileNotFoundError:
        return {
            "success": False,
            "error": "agent-browser not found on PATH.",
        }


def _format_snapshot(data: dict[str, Any]) -> str:
    """Format snapshot output for LLM consumption."""
    if not data.get("success"):
        return f"Error: {data.get('error', 'snapshot failed')}"
    tree = data.get("tree") or data.get("snapshot") or data.get("text", "")
    if isinstance(tree, list):
        lines = []
        for item in tree:
            ref = item.get("ref", "")
            text = item.get("label", "") or item.get("text", "") or item.get("name", "")
            role = item.get("role", "")
            if ref and text:
                lines.append(f"{ref}: {role} \"{text}\"")
            elif ref:
                lines.append(f"{ref}: {role}")
        tree = "\n".join(lines[:200])
    return str(tree)


def _install_instructions() -> ToolResult:
    """Return a ToolResult telling the agent to ask the user and install agent-browser."""
    return ToolResult(
        content=(
            "Agent-browser (browser automation CLI) is not installed.\n\n"
            "Action required:\n"
            "1. Tell the user: \"I need to install agent-browser to control a browser.\"\n"
            "2. Ask the user: \"May I run 'npm i -g agent-browser && agent-browser install'?\"\n"
            "3. If the user agrees, call:\n"
            '   shell_execute(command="npm i -g agent-browser && agent-browser install")\n'
            "4. After installation succeeds, retry this browser operation."
        ),
        audience=["assistant"],
    )


def _ensure_browser() -> ToolResult | None:
    """Ensure agent-browser is installed and has a browser. Returns ToolResult or None."""
    try:
        from src.sdk.sandbox import get_sandbox_backend

        result = get_sandbox_backend().run(
            ["which", "agent-browser"], Path.cwd(), None
        )
        if result.exit_code != 0:
            return _install_instructions()
    except Exception:
        return _install_instructions()
    bundled = Path(__file__).parent.parent.parent.parent / "bin" / "agent-browser"
    if not bundled.exists():
        # agent-browser is on PATH but bundled doesn't exist — that's OK
        pass
    version = _ab("--version", timeout=10).get("text", "").strip()
    check = _ab("open", "about:blank", json_output=True, timeout=15)
    if not check.get("success"):
        err = check.get("error", "")
        if "install" in err.lower() and "browser" in err.lower():
            return ToolResult(
                content=(
                    "Browser runtime not installed. Run 'agent-browser install'.\n\n"
                    "Action required:\n"
                    "1. Tell the user: \"Agent-browser is installed but the browser runtime needs to be set up.\"\n"
                    "2. Ask: \"May I run 'agent-browser install' to set up the browser?\"\n"
                    "3. If the user agrees, call:\n"
                    '   shell_execute(command="agent-browser install")\n'
                    "4. After installation succeeds, retry this browser operation."
                ),
                audience=["assistant"],
            )
        return ToolResult(content=f"Error: {err}", is_error=True)
    logger.info("agent_browser.ready", {"version": version})
    return None


@tool
def browser_open(
    url: str = "",
    user_id: str = "",
) -> str | ToolResult:
    """Open a URL in the browser and return the page snapshot.

    Use this FIRST whenever the user asks to visit a website, check a page,
    log into a service, or interact with anything on the web. Opens the URL,
    takes an accessibility-tree snapshot, and returns interactive elements
    with @eN refs you can use with browser_click and browser_type.

    Args:
        url: Full URL to open (e.g., https://example.com)
        user_id: User ID (REQUIRED)

    Returns:
        Page snapshot with interactive elements
    """
    if not url:
        return "Error: url is required."
    install = _ensure_browser()
    if install:
        return install
    result = _ab("open", url, json_output=True, timeout=30)
    if not result.get("success"):
        return f"Error: {result.get('error', 'failed to open page')}"
    snap = _ab("snapshot", "--compact", "--urls", "--interactive", json_output=True, timeout=15)
    return _format_snapshot(snap)


browser_open.annotations = ToolAnnotations(title="Open URL in Browser", open_world=True)


@tool
def browser_snapshot(
    user_id: str = "",
) -> str | ToolResult:
    """Get the current page snapshot (accessibility tree with element refs).

    Returns interactive elements with @eN refs. Use this after any browser
    action to see the updated page state. Always call this before clicking
    or typing so you have valid element references.

    Args:
        user_id: User ID (REQUIRED)

    Returns:
        Page snapshot with interactive elements
    """
    install = _ensure_browser()
    if install:
        return install
    snap = _ab("snapshot", "--compact", "--urls", "--interactive", json_output=True, timeout=15)
    if not snap.get("success"):
        return f"Error: {snap.get('error', 'snapshot failed')}"
    return _format_snapshot(snap)


browser_snapshot.annotations = ToolAnnotations(
    title="Get Browser Snapshot", read_only=True, idempotent=True
)


@tool
def browser_click(
    ref: str = "",
    user_id: str = "",
) -> str | ToolResult:
    """Click an element identified by @eN ref.

    Use the @eN ref from browser_snapshot output. After clicking, a new
    snapshot is automatically taken so you can see the result.

    Args:
        ref: Element reference (e.g., @e3)
        user_id: User ID (REQUIRED)

    Returns:
        Updated page snapshot after click
    """
    if not ref:
        return "Error: ref is required (e.g., @e3)."
    install = _ensure_browser()
    if install:
        return install
    result = _ab("click", ref, json_output=True, timeout=30)
    if not result.get("success"):
        return f"Error clicking {ref}: {result.get('error', 'click failed')}"
    snap = _ab("snapshot", "--compact", "--urls", "--interactive", json_output=True, timeout=15)
    return _format_snapshot(snap)


browser_click.annotations = ToolAnnotations(title="Click Element", idempotent=True)


@tool
def browser_type(
    ref: str = "",
    text: str = "",
    user_id: str = "",
) -> str | ToolResult:
    """Type text into an element identified by @eN ref.

    Use the @eN ref from browser_snapshot output. This clear-and-fills
    the element (replaces existing content). After typing, a new snapshot
    is taken automatically.

    Args:
        ref: Element reference (e.g., @e5)
        text: Text to type into the element
        user_id: User ID (REQUIRED)

    Returns:
        Updated page snapshot after typing
    """
    if not ref:
        return "Error: ref is required (e.g., @e5)."
    if not text:
        return "Error: text is required."
    install = _ensure_browser()
    if install:
        return install
    result = _ab("fill", ref, text, json_output=True, timeout=30)
    if not result.get("success"):
        return f"Error typing into {ref}: {result.get('error', 'type failed')}"
    snap = _ab("snapshot", "--compact", "--urls", "--interactive", json_output=True, timeout=15)
    return _format_snapshot(snap)


browser_type.annotations = ToolAnnotations(title="Type into Element", idempotent=True)


@tool
def browser_press(
    key: str = "",
    user_id: str = "",
) -> str | ToolResult:
    """Press a keyboard key: Enter, Tab, Escape, ArrowDown, ArrowUp, etc.

    Use after browser_type to submit forms (Enter) or move between fields (Tab).
    After pressing, a new snapshot is taken automatically.

    Args:
        key: Key to press (e.g., Enter, Tab, Escape)
        user_id: User ID (REQUIRED)

    Returns:
        Updated page snapshot after key press
    """
    if not key:
        return "Error: key is required (e.g., Enter, Tab)."
    install = _ensure_browser()
    if install:
        return install
    result = _ab("press", key, json_output=True, timeout=15)
    if not result.get("success"):
        return f"Error pressing {key}: {result.get('error', 'key press failed')}"
    snap = _ab("snapshot", "--compact", "--urls", "--interactive", json_output=True, timeout=15)
    return _format_snapshot(snap)


browser_press.annotations = ToolAnnotations(title="Press Key", idempotent=True)


@tool
def browser_back(
    user_id: str = "",
) -> str | ToolResult:
    """Go back to the previous page.

    Args:
        user_id: User ID (REQUIRED)

    Returns:
        Updated page snapshot
    """
    install = _ensure_browser()
    if install:
        return install
    result = _ab("back", json_output=True, timeout=15)
    if not result.get("success"):
        return f"Error: {result.get('error', 'back failed')}"
    snap = _ab("snapshot", "--compact", "--urls", "--interactive", json_output=True, timeout=15)
    return _format_snapshot(snap)


browser_back.annotations = ToolAnnotations(title="Go Back", idempotent=True)


@tool
def browser_forward(
    user_id: str = "",
) -> str | ToolResult:
    """Go forward to the next page.

    Args:
        user_id: User ID (REQUIRED)

    Returns:
        Updated page snapshot
    """
    install = _ensure_browser()
    if install:
        return install
    result = _ab("forward", json_output=True, timeout=15)
    if not result.get("success"):
        return f"Error: {result.get('error', 'forward failed')}"
    snap = _ab("snapshot", "--compact", "--urls", "--interactive", json_output=True, timeout=15)
    return _format_snapshot(snap)


browser_forward.annotations = ToolAnnotations(title="Go Forward", idempotent=True)


@tool
def browser_screenshot(
    user_id: str = "",
) -> str | ToolResult:
    """Take a screenshot of the current page.

    Saves as PNG to a temp file and returns the path. Useful for visual
    verification after complex actions.

    Args:
        user_id: User ID (REQUIRED)

    Returns:
        Path to screenshot file
    """
    import tempfile

    install = _ensure_browser()
    if install:
        return install
    path = os.path.join(tempfile.gettempdir(), "ea_screenshot.png")
    result = _ab("screenshot", path, timeout=30)
    if not result.get("success"):
        return f"Error: {result.get('error', 'screenshot failed')}"
    return f"Screenshot saved: {path}"


browser_screenshot.annotations = ToolAnnotations(
    title="Take Screenshot", read_only=True, idempotent=True
)


@tool
def browser_wait_text(
    text: str = "",
    timeout: int = 10,
    user_id: str = "",
) -> str | ToolResult:
    """Wait for text to appear on the page.

    Takes snapshots in a loop until the text appears or timeout expires.
    Use when navigating pages that have loading spinners or async content.

    Args:
        text: Text to wait for
        timeout: Max seconds to wait (default: 10)
        user_id: User ID (REQUIRED)

    Returns:
        Page snapshot when text is found, or timeout message
    """
    if not text:
        return "Error: text is required."
    install = _ensure_browser()
    if install:
        return install
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        snap = _ab("snapshot", "--compact", json_output=True, timeout=15)
        if snap.get("success"):
            output = _format_snapshot(snap)
            if text.lower() in output.lower():
                return output
        time.sleep(1)
    return f"Timed out waiting for '{text}' ({timeout}s).\n" + _format_snapshot(snap) if snap.get("success") else f"Timed out waiting for '{text}'."


browser_wait_text.annotations = ToolAnnotations(
    title="Wait for Text", read_only=True, idempotent=True
)


@tool
def browser_sessions(
    user_id: str = "",
) -> str | ToolResult:
    """List all active browser sessions.

    Args:
        user_id: User ID (REQUIRED)

    Returns:
        List of active sessions
    """
    install = _ensure_browser()
    if install:
        return install
    result = _ab("sessions", json_output=True, timeout=10)
    if not result.get("success"):
        return "No active sessions." if "not found" in result.get("error", "") else f"Error: {result.get('error', 'unknown')}"
    sessions = result.get("sessions", result.get("text", "(no output)"))
    return f"Active sessions:\n{sessions}"


browser_sessions.annotations = ToolAnnotations(
    title="List Browser Sessions", read_only=True, idempotent=True
)


@tool
def browser_close_all(
    user_id: str = "",
) -> str | ToolResult:
    """Close all browser sessions.

    Args:
        user_id: User ID (REQUIRED)

    Returns:
        Confirmation
    """
    install = _ensure_browser()
    if install:
        return install
    result = _ab("close", json_output=True, timeout=10)
    if not result.get("success"):
        return f"Error: {result.get('error', 'close failed')}"
    return "All browser sessions closed."


browser_close_all.annotations = ToolAnnotations(title="Close All Sessions", destructive=True)
