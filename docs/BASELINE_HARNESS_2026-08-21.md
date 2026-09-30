# Harness Latency Baseline

**Date:** 2026-08-21T18:02:13.993515+00:00
**Model:** ollama-cloud:deepseek-v4-flash:0731
**Turns:** 40 (mixed plain + tool-using)
**Runs with waterfall lines:** 40

All values in milliseconds (lower is better).

| Stage | p50 | p95 | p99 | max |
|-------|-----|-----|-----|-----|
| context_assembly | 1 | 1 | 6 | 8 |
| persistence | 5 | 8 | 9 | 9 |
| provider_total | 1234 | 2593 | 4274 | 4824 |
| tool_exec | 1 | 118 | 231 | 259 |
| total (waterfall) | 3314 | 8715 | 14264 | 17044 |
| turn (client-side) | 3743 | 22187 | 43503 | 46385 |
