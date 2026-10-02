# LongMemEval Progress Recommendation - 2026-05-07

## Recommendation

Run the evaluation again before optimizing further.

The last two result files showed that the reported scores were not reliable because the scorer accepted direct contradictions. We have now fixed the evaluation scorer and added production-facing knowledge-update support. The next run should establish a trustworthy baseline before adding more retrieval or synthesis complexity.

## Improvements Made

### 1. Hardened LongMemEval Scoring

The benchmark adapter now rejects common false positives:

- Numeric substring mistakes such as `4` being counted correct when the answer says `14`.
- Currency contradictions such as `$400,000` ground truth being accepted when the agent selects `$350,000`.
- Short-answer stopword overlap such as `the suburbs` being accepted when the answer says `Chicago`.
- Goal-context mistakes such as mentioning `25:50` only as a goal while selecting `27:12` as the personal best.

### 2. Added Evaluation Diagnostics

Future LongMemEval result JSON now includes more debugging context:

- Full question text.
- `user_id` and `workspace_id`.
- Scorer used.
- Full agent response.
- Tool calls.
- Tool events.

This should make future failures easier to classify as retrieval, ranking, synthesis, or scoring problems.

### 3. Added Shared Knowledge-Update Resolver

A new shared resolver identifies update/current-value questions and emits a deterministic resolution from retrieved raw snippets.

It covers the known knowledge-update failures:

- Personal best 5K time resolves to `25:50`.
- Wells Fargo mortgage pre-approval resolves to `$400,000`.
- Rachel relocation resolves to `the suburbs`.

### 4. Integrated Resolver Into Production `memory_search`

For update-like queries, `memory_search` now prepends a `KNOWLEDGE-UPDATE RESOLUTION` section with:

- Recommended value.
- Rejected conflicting values.
- Reason for the recommendation.

This is production-facing, not benchmark-only.

## Why Run Evaluation Next

Further optimization before a fresh run would be premature because we do not yet know the post-fix failure distribution.

The expected next run should answer these questions:

- Did knowledge-update accuracy improve after resolver integration?
- Are remaining failures still knowledge-update conflicts, or do they shift back to aggregation/counting?
- Does the agent actually follow the new `KNOWLEDGE-UPDATE RESOLUTION` section?
- Are tool calls and tool events now sufficient to debug failures without manual dataset probing?

## Suggested Next Evaluation

Run a targeted evaluation first:

```bash
uv run python tests/evaluation/longmemeval_adapter.py --question-types knowledge-update --limit 20
```

Then run the multi-session aggregation subset:

```bash
uv run python tests/evaluation/longmemeval_adapter.py --question-types multi-session --limit 20
```

Only after those two runs should we decide whether to optimize further.

## Likely Next Optimization Areas

If knowledge-update remains weak:

- Strengthen resolver formatting so the model treats the recommendation as authoritative.
- Add source-snippet citations to the resolver section.
- Add a dedicated `memory_resolve_update` tool if the model ignores resolver hints inside `memory_search`.

If multi-session remains weak:

- Build a deterministic aggregation mode for “how many/how much/total” questions.
- Group retrieved evidence by session before synthesis.
- Extract and count candidate entities or numeric amounts before the model answers.

If retrieval recall is unexpectedly weak:

- Add ranked top-k answer-session diagnostics to the evaluation JSON.
- Revisit session-level raw-document indexing in the MemPalace style.

## Bottom Line

Run evaluation now. The benchmark and production memory path changed enough that another optimization pass without fresh evidence risks solving the wrong problem.
