---
name: autoresearch
description: >-
  Iteratively optimize prompts, skills, and subagent configs using
  the research_start and research_list tools. Run controlled
  experiments: snapshot → apply change → evaluate → keep or rollback.
  Uses the program.md workflow for structured, multi-cycle optimization.
allowed-tools: research_start, research_list, skills_load, skills_list,
  skill_create, files_read, files_write, shell_execute
---

# Autoresearch

> Run controlled experiments to improve prompts, skills, and agents.

## When to Use This Skill

- A prompt needs systematic optimization
- An agent or skill needs its instructions improved
- You want to compare two approaches objectively
- You need to find the best system prompt for a task

## How It Works

1. **Define the target** — a prompt file, skill directory, or subagent config
2. **Apply a change** — modify the target, eg "be more concise" or "add step-by-step"
3. **Evaluate** — run the target against a fixed eval task, measure the metric
4. **Keep or discard** — if the metric improves, keep the change; otherwise roll back
5. **Log** — every experiment is recorded in a results.tsv file

## Tools

### research_start
Start a new experiment. Parameters:
- `target_type`: "prompt", "skill", or "subagent"
- `target_ref`: path to target (eg `/path/to/prompt.txt` or skill directory)
- `change_description`: what to change and why

### research_list
List past experiments from results.tsv files.

## Experiment Workflow

Follow `program.md` for the structured multi-cycle experiment process.

## Best Practices

- **One change at a time** — test a single variable per experiment
- **Write a clear change_description** — include the hypothesis
- **Keep experiments small** — fast feedback, iterate many times
- **Review results.tsv** — identify patterns across experiments
- **Commit improvements** — when a change sticks, commit it
