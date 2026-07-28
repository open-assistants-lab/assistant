---
name: canvas-painting
description: >-
  Generate HTML for the Canvas tab. Use when asked to create interactive
  visualizations, forms, dashboards, reports, config panels, skill editors,
  subagent editors, or any rendered surface in the Canvas. Always emit full
  HTML via a single fenced code block per surface — the Canvas renders it
  directly.
---

# Canvas Painting

Generate self-contained HTML rendered inside the Canvas tab. Every output
is a single fenced code block using a surface-specific fence tag.

## Output Format

Use **exactly** these fence tags. The colon modifier after `html` is
**required** — without it, the HTML renders as a code block in chat
instead of routing to the Canvas tab.

**To route to Canvas** (colon modifier):
    ```html:skill-form
    <html>...</html>
    ```
    ```html:subagent-form
    <html>...</html>
    ```
    ```html:editor
    <file path and markdown content>
    ```
    ```html:canvas
    <html>...</html>
    ```

**To show a code example in chat** (no modifier):
    ```html
    <div>example</div>
    ```

## Surface Types

### `html:canvas` — Free-form interactive pages
No required structure. Use for dashboards, charts, reports, visualizations,
config panels, or any interactive widget.

### `html:skill-form` — Create/edit a skill
Must contain `<form>` fields with these `name` attributes:

| name          | type   | required |
|---------------|--------|----------|
| `name`        | text   | yes      |
| `description` | text   | yes      |
| `content`     | textarea | yes    |

### `html:subagent-form` — Create/edit a subagent
Must contain `<form>` fields with these `name` attributes:

| name             | type   | required |
|------------------|--------|----------|
| `name`           | text   | yes      |
| `description`    | text   | yes      |
| `model`          | select | no       |
| `system_prompt`  | textarea | yes    |

### `html:editor` — Inline rich text editor
Lets the user create or edit a file interactively. The editor renders
a WYSIWYG toolbar (bold, italic, heading, lists, code blocks) inside
the Canvas tab. User edits content and clicks Save/Cancel.

**Format:**
````
```html:editor
filePath: /absolute/path/to/file.md
---

Initial markdown content here.
```
````

**Required fields in the opening line:**
| field      | description |
|------------|-------------|
| `filePath` | Absolute path to the file being created/edited |

Everything after `---` is initial markdown content pre-loaded into
the editor.

**User actions:**
| action   | agent receives |
|----------|---------------|
| `save`   | `[Canvas submit] editor: {filePath}... {markdown content}` — use `files_write` to persist |
| `cancel` | `[Canvas] User cancelled the editor.` — no action needed |

## Theming with CSS Custom Properties

The Canvas host injects these custom properties. Use them instead of
hardcoded colors so surfaces adapt to light/dark mode:

| property         | usage                          |
|------------------|--------------------------------|
| `var(--primary)` | accent color (buttons, links)  |
| `var(--bg)`      | background                     |
| `var(--text)`    | foreground text                |
| `var(--border)`  | borders, dividers              |

```css
body {
  background: var(--bg);
  color: var(--text);
  font-family: system-ui, sans-serif;
  margin: 0; padding: 16px;
}
button {
  background: var(--primary);
  color: #fff; border: none;
  padding: 8px 16px; border-radius: 6px;
  cursor: pointer;
}
```

## `postMessage` Bridge

Use the injected `postMessage(data)` function to send user actions back
to the agent. It is available globally — no import needed.

```html
<button onclick="postMessage({action:'save',form:'skill-form',fields:{name:document.getElementById('name').value}})">Save</button>
<button onclick="postMessage({action:'cancel'})">Cancel</button>
```

The `postMessage` function calls `canvasBridge.postMessage(JSON.stringify(data))`
internally. Always pass a plain object with at least an `action` field.

| action      | fields    | agent receives |
|-------------|-----------|---------------|
| `save`      | `form`, `fields` | `[Canvas submit] {form}: {fields}... Create this.` |
| `cancel`    | —         | `[Canvas] User cancelled the form.` |
| any other   | free-form | `[Canvas] {json-serialised data}` |

## Constraints

- Output exactly one fenced block per response.
- Do NOT include `<script src="...">` to external origins (CSP blocked).
- Do NOT include `<link href="...">` to external stylesheets.
- Use `input`, `textarea`, `select`, `button` — standard form elements.
- For charts/visuals, either draw with a `<canvas>` element or use inline
  SVG. Small utility libraries are fine if embedded in a `<script>` block.
