# Autoresearch Program

This is the structured experiment workflow. Follow these steps for each optimization cycle.

## Setup

1. Pick a **target** (prompt, skill, or subagent config)
2. Define an **eval task** — a clear question or task the target should handle well
3. Set a **baseline** — run eval on the current target, record the metric

## Experiment Cycle

### Step 1: Hypothesize
State what you will change and why. Example:
> "Adding step-by-step instructions will reduce hallucinations."

### Step 2: Run Experiment
```bash
research_start \
  target_type=prompt \
  target_ref=path/to/prompt.txt \
  change_description="Add step-by-step reasoning instruction"
```

### Step 3: Review Result
The tool returns: keep (metric improved) or discard (metric regressed).

- **Keep**: Commit the change. The target is now better.
- **Discard**: The change is auto-rolled back. Log what you learned.

### Step 4: Log
Check the full history:
```bash
research_list
```

### Step 5: Iterate
Go back to Step 1 with a new hypothesis. Aim for 10-20 cycles per session.

## Success Criteria

Stop optimizing when:
- Three consecutive experiments all discard
- The metric exceeds your target threshold
- You've exhausted your hypothesis list

## Advanced

### Branching
For risky experiments, create a git branch first:
```bash
git checkout -b experiment/short-description
```

If the experiment succeeds, merge it. If not, delete the branch.

### Batching
Run multiple independent experiments in parallel if the target supports it.

## Safe Abort

If any experiment causes errors or unexpected behavior:
1. The tool auto-rolls back on regression
2. You can manually restore from git if needed
3. Results are always logged — no data loss
