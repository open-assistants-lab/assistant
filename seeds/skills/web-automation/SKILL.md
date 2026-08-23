---
name: web-automation
description: Browser automation for web tasks — navigating sites, filling forms, clicking buttons, taking screenshots, extracting data, logging into services, testing web apps. Use when the user needs to interact with any website, login to a service, fill a form, scrape data, take a screenshot, or automate any browser-based task.
allowed-tools: browser_open, browser_snapshot, browser_click, browser_fill, browser_eval, browser_screenshot, shell_execute
---

# Web Automation

Fast browser automation via the agent-browser CLI (accessibility-tree snapshots with `@eN` element refs).

## Core workflow (native tools)

1. **`browser_open(url)`** — Navigate to the page.
2. **`browser_snapshot()`** — Get interactive elements as `@eN` refs.
3. **`browser_click("@e3")`** / **`browser_fill("@e5", "text")`** — Act on refs from the snapshot.
4. **`browser_snapshot()`** — Always re-snapshot after any action. Refs go stale the moment the page changes.

## Beyond the core (CLI via shell_execute)

For page text/HTML/URL, tabs, navigation, scrolling, typing, waiting — run the CLI directly. Load the full usage guide from the CLI itself (always matches the installed version):

```bash
agent-browser skills get core
```

Quick reference:

```bash
agent-browser get title          # page title
agent-browser get text            # page text
agent-browser get html            # page HTML
agent-browser get url             # current URL
agent-browser tab new [<url>]     # new tab
agent-browser tab close           # close tab
agent-browser back                # history back
agent-browser forward             # history forward
agent-browser scroll [up|down]    # scroll
agent-browser type "<text>"       # type into focused element
agent-browser press <key>         # press key
agent-browser hover @<ref>        # hover element
agent-browser wait --text "<t>"   # wait for text
agent-browser session list        # list sessions
agent-browser close --all         # close all sessions
```

Not installed? `brew install agent-browser` (macOS) or `npm i -g agent-browser && agent-browser install`.
