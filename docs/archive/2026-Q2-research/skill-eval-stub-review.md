# SkillTarget Evaluation Stub — Peer Review

**File:** `src/sdk/research.py`

## The Problem

`SkillTarget.evaluate()` always returns `0.5` — a neutral metric that tells `ResearchLoop` neither to keep nor discard the experiment. This means autoresearch on skills can apply changes and roll them back, but cannot determine whether a modified `SKILL.md` is *actually better*.

```python
class SkillTarget(ResearchTarget):
    async def evaluate(self) -> float:
        return 0.5  # STUB — no real measurement
```

## What It Should Do

For a proper skill self-improvement loop, `evaluate()` must measure **trigger rate**: how often does the revised `SKILL.md` activate for a set of benchmark queries compared to the baseline?

### Proposed approach

1. **Define a benchmark set** — N user queries where the skill should (or should not) trigger
2. **Run the agent against each query**, capturing which skills activate
3. **Measure trigger rate** — `correct_triggers / total_queries`
4. **Return the metric** — higher trigger rate = better skill

### Implementation considerations

| Concern | Options |
|---|---|
| **Benchmark source** | User provides queries at experiment start, or LLM generates synthetic ones, or a fixed curated set |
| **Eval cost** | Each benchmark run costs N × average conversation tokens. Must stay under `cost_limit_usd` |
| **Model** | Same model as the running agent, or a cheaper model for eval-only |
| **Speed** | N runs sequentially could be slow. Option: parallel eval via `asyncio.gather()` |
| **Metric** | Simple accuracy: did the skill trigger correctly? Or weighted: penalize false triggers more than missed triggers |

## Dependencies

- `SkillTarget` depends on the agent's skill activation middleware (SkillMiddleware) to report which skills matched
- Currently there's no API to query "did skill X trigger for message Y?" — this would need to be added

## Question for Reviewers

1. Is a stub acceptable for V1 (research_start works for prompts and subagents, skill eval is placeholder)?
2. If implementing real eval, should benchmark queries be user-provided, LLM-generated, or a mix?
3. Should eval use a cheaper/faster model than the main agent? (Cost concern: N × tokens × model_pricing)
