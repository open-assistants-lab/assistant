# Harness Latency Baseline

**Date:** 2026-08-22T23:51:13.233571+00:00
**Model:** ollama-cloud:deepseek-v4-flash:0731
**Verify mode:** auto
**Turns:** 40 (mixed plain + tool-using)
**Runs with waterfall lines:** 40

All values in milliseconds (lower is better).

| Stage | p50 | p95 | p99 | max |
|-------|-----|-----|-----|-----|
| context_assembly | 1 | 1 | 1 | 1 |
| persistence | 4 | 6 | 10 | 10 |
| provider_total | 1092 | 1955 | 4387 | 4653 |
| tool_exec | 0 | 25 | 41 | 45 |
| total (waterfall) | 3068 | 6602 | 14745 | 15457 |
| turn (client-side) | 3279 | 37849 | 56720 | 65544 |
