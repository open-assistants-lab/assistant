---
name: subagent-creation
description: Create, modify, and test subagents for the Assistant. MUST be loaded when the user asks to create, build, make, write, or edit a subagent. Use files_write to write PROFILE.md files to the subagents directory. Do NOT create skills for subagent requests — create AgentProfile subagents instead.
---

# Subagent Creator

> **IMPORTANT**: The user wants a subagent (PROFILE.md file), NOT a skill. Use `files_write` to create the PROFILE.md file at the subagents directory path shown in your system prompt. Do NOT call `skills_load` or `skills_reload` when asked to create a subagent. You have `files_write` available — use it.

## Subagent Directory

The subagents directory is dynamically injected into your system prompt (see "Subagents Directory" section). Use `files_write` to write the PROFILE.md file there.

## Workflow

1. Decide what the subagent should do
2. Design the subagent (model, tools, skills, system prompt)
3. Use `files_write` to create the PROFILE.md file
4. Test with `subagent_delegate`
5. Iterate based on results
6. Repeat until satisfied

---

## Creating a subagent

### Capture Intent

Start by understanding the user's intent. The current conversation might already contain a workflow the user wants to capture as a subagent (e.g., they say "turn this into a reusable agent"). If so, extract answers from the conversation history first.

1. **What should this subagent do?** — Define the single responsibility clearly
2. **When should it be used?** — What tasks would this subagent handle better than the main agent?
3. **What's the output format?** — Structured data? Text report? File creation?
4. **What model does it need?** — Simple tasks can use cheaper/faster models; complex reasoning needs stronger models

### Single Responsibility Principle

Each subagent should do ONE thing well. Signs you're overloading a subagent:

- The system prompt has "if X do Y, otherwise do Z"
- It needs many diverse tools
- The description is vague or covers multiple domains

If the user asks for something broad, help them break it into separate focused subagents.

### Design the Subagent

#### Model Selection

Choose based on the subagent's task complexity:

| Task type | Recommended model |
|-----------|------------------|
| Simple data extraction, formatting | `ollama:llama3.2` or `gemini:gemini-2.0-flash` |
| Code generation, analysis | `anthropic:claude-sonnet-4-20250514` |
| Deep research, complex reasoning | `anthropic:claude-sonnet-4-20250514` or `gemini:gemini-2.5-pro` |
| High-throughput batch work | `openai:gpt-4o-mini` |

Default to the model powering the current session unless there's a clear reason to use something different.

#### Tool Selection

Choose tools that match the subagent's responsibility. Guidelines:

- **Research/read subagents**: `files_read`, `files_glob_search`, `files_grep_search`, `web_search`, `web_scrape`, `memory_search`
- **Write/create subagents**: `files_write`, `files_edit`, `files_delete`, `files_mkdir`
- **Shell/task subagents**: `shell_execute`
- **Email subagents**: `email_list`, `email_get`, `email_search`, `email_send`
- **Todo subagents**: `todos_list`, `todos_add`, `todos_update`, `todos_delete`

Subagent tools (`subagent_*`) and dangerous memory tools are automatically blocked. Memory search tools and `skills_load` are available as needed.

Every subagent automatically gets `message_search` for looking up conversation history.

#### Skills

If the subagent needs specialized knowledge, include relevant skill names. The skill content will be loaded automatically via `skills_load` before the subagent runs.

#### Output Schema

If the subagent's output will be consumed programmatically (by another tool or script), define a JSON output schema. The subagent will be instructed to structure its output to match this schema.

#### Provider Options

For advanced use cases, provider-specific options can be set:
- **Anthropic**: `thinking` blocks (`{"thinking": {"type": "enabled", "budget_tokens": 10000}}`)
- **Gemini**: `thinkingConfig` (`{"thinkingConfig": {"thinkingType": "enabled"}}`)
- **OpenAI**: `logprobs` etc.

### Write the PROFILE.md

Based on the user interview, use `files_write` to write the PROFILE.md file at the subagents directory path shown in your system prompt.

The PROFILE.md format uses YAML frontmatter delimited by `---` followed by a Markdown body (the system prompt):

```markdown
---
name: my-subagent
description: What this subagent does — used for routing decisions
model: anthropic:claude-sonnet-4-20250514
tools:
  - files_read
  - files_write
  - web_search
skills:
  - my-skill
max_llm_calls: 50
cost_limit_usd: 1.0
timeout_seconds: 300
handoff_instructions: Return the result as structured JSON.
output_schema:
  type: object
  properties:
    result:
      type: string
provider_options:
  anthropic:
    thinking:
      type: enabled
      budget_tokens: 10000
---

You are my-subagent, a specialized subagent for ...

Your job is to:
- Task description
- Output format requirements

When done, present the result according to the handoff instructions.
```

The file must be written to `{subagents_directory}/{name}/PROFILE.md`. The subagents directory is shown in your system prompt.

### Updating a subagent

To update, read the existing PROFILE.md, edit the frontmatter fields or body, then write it back using `files_write` to the same path.

---

## Testing and Iterating

### Step 1: Quick smoke test

Run the subagent on a simple test task to verify it works:

```
subagent_delegate("my-subagent", "<simple test task>", user_id="<user_id>")
```

### Step 2: Realistic test cases

After the smoke test passes, run 2-3 realistic tasks. Use `subagent_delegate` (blocking, waits for result) for quick iterations:

```
result1 = subagent_delegate("my-subagent", "<test task 1>", user_id="<user_id>")
result2 = subagent_delegate("my-subagent", "<test task 2>", user_id="<user_id>")
```

For long-running tasks (>2 min), use `subagent_start` + `subagent_check` so the user can continue chatting.

### Step 3: Review and iterate

- Show results to the user
- Ask what works and what doesn't
- Use `files_write` to update the PROFILE.md
- Rerun tests

### Step 4: Benchmark (optional)

For production subagents, test multiple prompts and compare. Aggregate results into a simple table and present to the user.

---

## EA-Specific Instructions

You are in the Assistant system. Adapt accordingly:

- **No `claude` CLI** — use `subagent_delegate` or `subagent_start` for testing
- **Subagents directory** — shown in your system prompt
- **Tools available** — `files_write`, `files_read`, `files_edit`, `files_delete`, `subagent_delegate`, `subagent_start`, `subagent_check`, `subagent_list`, `subagent_instruct`, `subagent_cancel`
- **Memory tools** are automatically blocked for subagents
- **Subagent tools** are automatically blocked for subagents (no recursion)
- **Skills** can be used from subagents — include them in the `frontmatter` list

### Simplified Testing

When you can't do full benchmark runs:
1. Create the subagent with `files_write`
2. Run 2-3 test tasks with `subagent_delegate`
3. Show results to the user
4. Iterate by editing the PROFILE.md and writing it back

---

Please add steps to your TodoList to make sure you don't forget key stages: intent capture, design, creation, testing, iteration.

Good luck!