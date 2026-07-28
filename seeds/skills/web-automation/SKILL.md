---
name: web-automation
description: Browser automation for web tasks — navigating sites, filling forms, clicking buttons, taking screenshots, extracting data, logging into services, testing web apps. Use when the user needs to interact with any website, login to a service, fill a form, scrape data, take a screenshot, or automate any browser-based task. Also use for automating Electron desktop apps (VS Code, Slack, Discord, Figma, Notion, Spotify).
allowed-tools: browser_open, browser_snapshot, browser_click, browser_type, browser_press, browser_screenshot, browser_back, browser_forward, browser_wait_text
hidden: false
---

# Web Automation

Fast browser automation via accessibility-tree snapshots with `@eN` element refs.

## Workflow

1. **`browser_open(url)`** — Navigate to the page. Returns a snapshot of interactive elements.
2. **`browser_snapshot()`** — Get current page state. Returns elements like `@e3: link "Sign In"`.
3. **`browser_click("@e3")`** — Click using the `@eN` ref from the snapshot.
4. **`browser_type("@e5", "my text")`** — Fill a text field using its `@eN` ref.
5. **`browser_press("Enter")`** — Submit forms, dismiss dialogs.

Always call `browser_snapshot` after any action to get fresh element refs. Always use `@eN` refs from the *most recent* snapshot — they change when the page changes.
