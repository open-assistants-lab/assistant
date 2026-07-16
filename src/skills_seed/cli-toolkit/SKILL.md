---
name: cli-toolkit
description: Discover, install, learn, execute, and register CLI tools as custom tools for the Assistant. Use when the user needs to interact with a command-line tool — whether for file conversion, data processing, API calls, or any shell-accessible utility. Covers end-to-end workflow from finding the right tool to registering it as a permanent custom tool.
---

# CLI Toolkit

This skill guides you through the full lifecycle of using a CLI tool in the Assistant: discover → install → learn → execute → register.

## Workflow

### Phase 1 — Discover

**Always check the OS first:** Run `uname -s` and `uname -m` to know the platform — this determines which commands and package managers to use.

Identify the right CLI tool. Use these strategies in order:

1. **LLM knowledge** — You already know the most common tools: `ffmpeg` (media), `pandoc` (documents), `jq` (JSON), `imagemagick` (images), `pdftotext` (PDF), `yt-dlp` (video download), etc.
2. **Web search** — "cli tool to convert markdown to pdf" → top result is usually correct
3. **Package manager search** — `brew search pdf`, `pip search pdf`, `npm search pdf`
4. **Convention mapping** — User mentions Python → prefer `pip install`. Node → `npx`/`npm`. macOS → `brew`

Propose 2-3 candidates if uncertain, with brief pros/cons.

### Phase 2 — Install

1. Check availability: `which <tool>`
2. If not found, install via the right package manager based on OS (detected in Phase 1):
   - macOS: `brew install <tool>` (check `which brew` first)
   - Linux: `apt-get install <tool>` or `dnf install <tool>`
   - Python tools: `uv add <tool>` or `pip install <tool>`
   - Node tools: `npm install -g <tool>` or `npx <tool>`
3. Verify: `which <tool>` and `<tool> --version`

### Phase 3 — Learn

Run `<tool> --help` (or `<tool> <subcommand> --help`) to understand flags, subcommands, and expected arguments. Identify the exact command structure needed for the task at hand.

### Phase 4 — Execute

1. Construct the full command with the flags/subcommands identified in Phase 3
2. Run via `shell_execute`
3. Check exit code (non-zero = error)
4. Validate output (file exists, correct format, expected content)
5. On failure: adjust flags and retry (up to 3 attempts)
6. If all 3 attempts fail, surface the error and try an alternative tool

### Phase 5 — Register (for reusable tools)

If the tool is generally useful (not a one-off task), register it as a custom tool:

1. Choose a descriptive name: `pdf_extract_text` not `pdf-tool`, `video_to_gif` not `convert`
2. Choose a keyword-rich description that would match a `tool_search` query
3. Write a `TOOL.md` file:

```
files_write(path="{ea_root}/Tools/{name}/TOOL.md", content="""---
name: pdf_extract_text
description: Extract text from PDF files using ocrmypdf + pdftotext. Use when the user needs text content from a PDF document.
command: ocrmypdf "{{input}}" /tmp/_ocr_output.pdf && pdftotext /tmp/_ocr_output.pdf "{{output}}"
parameters:
  type: object
  properties:
    input:
      type: string
      description: Path to the PDF file
    output:
      type: string
      description: Path for the extracted text file
  required:
    - input
    - output
---
""")
```

The `{{param}}` placeholders in `command` become the tool parameters. The description should include keywords that match queries like "extract text from pdf" or "ocr pdf files".

4. Call `tool_reload()` to make it immediately available in the search index:

5. Call `tool_search("extract text from pdf")` to verify it appears

6. The tool is now available by name for the rest of the conversation and future sessions.

**Important:** Always include the OS detection result (`uname -s`, `uname -m`) in the TOOL.md as `os` and if applicable `python_version` fields. This makes the tool portable and helps debug platform-specific issues:

```
files_write(path="{ea_root}/Tools/{name}/TOOL.md", content="""---
name: pdf_extract_text
description: Extract text from PDF files using ocrmypdf + pdftotext
command: ocrmypdf "{{input}}" /tmp/_ocr_output.pdf && pdftotext /tmp/_ocr_output.pdf "{{output}}"
os: Darwin arm64
python_version: "3.13"
---
""")
```

## Notes

- `tool_search` searches the HybridDB index of all tools (native + custom) by name and description
- After `tool_reload`, any new `TOOL.md` files are immediately searchable
- Deleted `TOOL.md` files are removed from the index on next reload
- If called directly by name, a discovered tool stays loaded for the rest of the conversation (recency tracking)

## Examples

### Common CLI tools
| Task | CLI | Package |
|------|-----|---------|
| Extract PDF text | `pdftotext` | `poppler` (brew) |
| Extract PDF text with OCR | `ocrmypdf` | `ocrmypdf` (pip) |
| Convert images | `convert` | `imagemagick` (brew) |
| Process video/audio | `ffmpeg` | `ffmpeg` (brew) |
| Parse JSON | `jq` | `jq` (brew) |
| Convert documents | `pandoc` | `pandoc` (brew) |
| Download video | `yt-dlp` | `yt-dlp` (brew) |
| Image compression | `mozjpeg` | `mozjpeg` (brew) |