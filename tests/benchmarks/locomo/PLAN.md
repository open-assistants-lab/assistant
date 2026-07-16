# LoCoMo Benchmark Integration Plan

## Overview

**LoCoMo** (Long Conversation Memory) is a benchmark from Snap Research evaluating very long-term conversational memory of LLM agents. It contains ~90 long-conversation sessions spanning simulated weeks/months with three task types: question answering, event summarization, and dialogue generation.

**Paper:** [2402.17753](https://arxiv.org/abs/2402.17753) | **Repo:** [snap-research/locomo](https://github.com/snap-research/locomo) | **Dataset:** `snap-research/locomo` on HuggingFace

---

## Why LoCoMo

| Dimension | LongMemEval | LoCoMo |
|-----------|-------------|--------|
| Sessions per instance | 40–500 (haystack) | ~90 long dialogues |
| Task types | QA only | QA + summarization + dialogue generation |
| Time span | Individual sessions, sparse dates | Continuous weeks/months, dense timeline |
| Abstention | Explicit abstention questions | No abstention; confidence-based |
| What it stress-tests | Retrieval precision in noise | Long-range temporal reasoning, event tracking, summarization fidelity |

LoCoMo's dense multi-session timeline directly tests our **SummarizationMiddleware** (when the conversation grows past 50K tokens across many turns) and validates the **MemoryMiddleware**'s extraction pipeline over extended conversation spans.

---

## File Structure

```
tests/benchmarks/locomo/
├── __init__.py
├── PLAN.md              # This file
├── dataset.py           # LoCoMoDataset — download/load/parse from HuggingFace
├── adapter.py           # LoCoMoAdapter — inject conversations into MessageStore
├── runner.py            # LoCoMoRunner — run evaluations via AgentLoop
├── judge.py             # Judge — GPT-4o scoring for QA + summarization + dialogue
├── eval.py              # Main evaluation entry point (full benchmark run)
└── cli.py               # Click CLI for individual commands

data/benchmarks/locomo/
├── locomo_raw/          # Raw dataset files from HuggingFace
└── results/             # Benchmark result JSON files
```

---

## Phase 1: Dataset (`dataset.py`)

### Data Format
LoCoMo on HuggingFace provides conversations as JSONL with fields:
```json
{
  "conversation_id": "conv_001",
  "turns": [
    {"speaker": "speaker1", "text": "...", "timestamp": "2023-05-01 10:00"},
    {"speaker": "speaker2", "text": "...", "timestamp": "2023-05-01 10:05"}
  ]
}
```

### Three Task Splits
The benchmark provides separate splits for each evaluation task:

1. **QA** — Questions about facts/events across the conversation timeline (closest to LongMemEval)
2. **Event Summarization** — Summarize a date range or topic across sessions
3. **Multi-Session Dialogue Generation** — Generate a new conversation turn grounded in prior history

### Implementation

```python
# dataset.py
@dataclass
class LoCoMoInstance:
    instance_id: str
    task_type: Literal["qa", "summarization", "dialogue"]
    question: str                          # The prompt/task description
    expected_answer: str                   # Ground truth
    conversation_ids: list[str]            # Which conversations form the context
    evidence_turns: list[dict] | None      # Specific turns containing the answer
    metrics: list[str]                     # Expected metrics (e.g., ["ROUGE-L", "BERTScore"])

class LoCoMoDataset:
    HF_REPO = "snap-research/locomo"
    
    def download(self) -> Path:
        """Download all splits from HuggingFace."""
    
    def load(self, task: Literal["qa", "summarization", "dialogue"]) -> list[LoCoMoInstance]:
        """Load instances for a specific task."""
    
    def get_stats(self) -> dict:
        """Statistics: instance counts per task, avg conversation length, etc."""
```

### Dependencies
- `datasets` (HuggingFace datasets library) — `uv add --dev datasets`
- If unavailable, fall back to direct JSON download from HuggingFace raw URLs (same pattern as LongMemEval)

---

## Phase 2: Adapter (`adapter.py`)

### Purpose
Injects LoCoMo conversation data into our `MessageStore` so the agent can use `memory_search`, `memory_get_history`, and `memory_search_all` to retrieve evidence.

### Key Differences from LongMemEval Adapter
- LoCoMo has **dense multi-turn dialogues** (not sparse sessions), requiring the SummarizationMiddleware to kick in
- Conversations span **continuous time periods**; timestamps must be normalized to the system's date format
- Need to handle **multi-speaker** turns (speaker1/speaker2 → user/assistant roles)

### Implementation

```python
# adapter.py
class LoCoMoAdapter:
    def __init__(self, user_id: str = "benchmark_locomo", base_dir: Path | None = None):
        self.user_id = user_id
        self.store = MessageStore(user_id=user_id, base_dir=base_dir)
    
    def inject_conversations(
        self,
        conversations: list[dict],  # Raw LoCoMo conversation data
    ) -> int:
        """Inject LoCoMo conversations into MessageStore.
        
        Maps speaker1 → user, speaker2 → assistant.
        Uses batch embedding for speed.
        Returns count of injected messages.
        """
    
    def inject_for_instance(
        self,
        instance: LoCoMoInstance,
        conversations: list[dict],  # All available conversations
    ) -> None:
        """Inject only the conversations relevant to this instance.
        Clears previous data first to avoid cross-contamination.
        """
    
    def cleanup(self) -> None:
        """Remove benchmark data directory."""
    
    def verify_injection(self) -> dict:
        """Return stats about injected data."""
```

### Design Decision: Multi-User vs Single-User
Unlike LongMemEval which uses per-instance user IDs (`lme_qa_001`), LoCoMo's conversations are long enough that we should test **how our agent handles a realistic single-user scenario** with 90+ long sessions. Use a single user ID with all conversations injected, rather than per-task isolation.

---

## Phase 3: Runner (`runner.py`)

### Purpose
Runs the evaluation loop: for each instance, ensure the right context is injected, then ask the agent the question/task, collect the response.

### Three Evaluation Modes

```python
# runner.py
@dataclass
class LoCoMoEvalResult:
    instance_id: str
    task_type: str
    question: str
    expected_answer: str
    agent_answer: str
    automated_score: dict | None = None    # ROUGE-L, BERTScore, etc.
    gpt4o_score: dict | None = None        # GPT-4o judgment
    latency_ms: float = 0.0
    error: str | None = None
    tool_calls: list[str] | None = None

class LoCoMoRunner:
    def __init__(self, user_id: str = "benchmark_locomo"):
        self.adapter = LoCoMoAdapter(user_id=user_id)
        self.user_id = user_id
    
    async def evaluate_qa(
        self, instances: list[LoCoMoInstance], conversations: list[dict]
    ) -> list[LoCoMoEvalResult]:
        """QA evaluation — closest to LongMemEval.
        
        Inject all conversations, ask each question,
        collect agent answers.
        """
    
    async def evaluate_summarization(
        self, instances: list[LoCoMoInstance], conversations: list[dict]
    ) -> list[LoCoMoEvalResult]:
        """Summarization evaluation — generate summary of date range/topic.
        
        Tests the agent's ability to synthesize information across
        many turns and produce coherent summaries.
        """
    
    async def evaluate_dialogue(
        self, instances: list[LoCoMoInstance], conversations: list[dict]
    ) -> list[LoCoMoEvalResult]:
        """Dialogue generation evaluation — generate next turn.
        
        Tests grounding in conversation history.
        """
    
    def _build_prompt(self, instance: LoCoMoInstance, mode: str) -> str:
        """Build agent prompt with instructions to use memory tools."""
```

### Prompt Engineering
Following the LongMemEval runner's pattern:
```python
prompt = f"""Search your conversation history using memory_search or memory_get_history BEFORE answering.
User ID: {self.user_id}

Task: {instance.question}

Provide a comprehensive answer based on your conversation history."""
```

### Rate Limiting & Retry
- Rate limit: 30 req/min (summarization and dialogue need more model time)
- Retry on error: up to 3 attempts with exponential backoff
- Same retry logic as `evaluate_qa_direct` in LongMemEval

---

## Phase 4: Judging (`judge.py`)

### Scoring Methods
LoCoMo uses multiple scoring approaches depending on task:

| Task | Primary Score | Automated | GPT-4o |
|------|--------------|-----------|--------|
| QA | GPT-4o semantic equivalence | Exact match | Yes |
| Summarization | ROUGE-L + BERTScore | Yes | Optional |
| Dialogue generation | BLEU + BERTScore | Yes | Optional |

### Implementation

```python
# judge.py
class LoCoMoJudge:
    def __init__(self, use_gpt4o: bool = True):
        self.gpt4o_judge = Judge(model="gpt-4o-mini")  # Reuse LongMemEval Judge
    
    async def judge_qa(self, result: LoCoMoEvalResult) -> None:
        """Grade QA answer using GPT-4o semantic equivalence."""
        # Reuses exact same Judge class from longmemeval/judge.py
    
    def judge_summarization(self, result: LoCoMoEvalResult) -> dict:
        """Grade summarization using ROUGE-L + BERTScore."""
        # Requires: pip install rouge-score bert-score
    
    def judge_dialogue(self, result: LoCoMoEvalResult) -> dict:
        """Grade dialogue generation using BLEU + BERTScore."""
        # Requires: pip install nltk sacrebleu
    
    async def evaluate_batch(
        self, results: list[LoCoMoEvalResult], task_type: str
    ) -> list[LoCoMoEvalResult]:
        """Judge a batch of results, dispatching by task type."""
```

### Dependencies for Scoring
```
uv add --dev rouge-score bert-score sacrebleu nltk
```

---

## Phase 5: Main Runner (`eval.py`)

### Full Benchmark Entry Point

```python
# eval.py
@dataclass
class LoCoMoBenchmarkResults:
    variant: str
    model: str
    timestamp: str
    qa_results: list[LoCoMoEvalResult] = []
    summarization_results: list[LoCoMoEvalResult] = []
    dialogue_results: list[LoCoMoEvalResult] = []
    
    def qa_accuracy(self) -> float:
        """QA accuracy (semantic equivalence %)."""
    
    def summarization_rouge_l(self) -> float:
        """Average ROUGE-L across summarization tasks."""
    
    def dialogue_bleu(self) -> float:
        """Average BLEU across dialogue tasks."""
    
    def to_dict(self) -> dict:
        """Serializable dict for JSON output."""


async def run_full_benchmark(
    tasks: list[str] = ["qa", "summarization", "dialogue"],
    use_gpt4o: bool = True,
    output_path: Path | None = None,
) -> LoCoMoBenchmarkResults:
    """Run the full LoCoMo benchmark.
    
    1. Download dataset
    2. Load conversations
    3. For each task type, run evaluation
    4. Judge results
    5. Print summary + save to file
    """
```

### CLI (`cli.py`)

```python
@click.group()
def cli():
    """LoCoMo benchmark runner."""

@cli.command()
@click.option("--task", type=click.Choice(["qa", "summarization", "dialogue", "all"]))
@click.option("--output", type=click.Path(path_type=Path))
@click.option("--no-gpt4o-judge", is_flag=True)
def run(task: str, output: Path | None, no_gpt4o_judge: bool):
    """Run LoCoMo benchmark."""

@cli.command()
def download():
    """Download LoCoMo dataset from HuggingFace."""

@cli.command()
def inspect():
    """Inspect dataset structure and statistics."""
```

---

## Phase 6: What It Tests of Our System

| Our Component | What LoCoMo Validates |
|---------------|----------------------|
| **SummarizationMiddleware** | Handles 90+ long conversations, triggers summarization at 50K tokens, preserves critical facts across truncation |
| **MemoryMiddleware.before_agent** | Progressive disclosure context stays relevant with many memories (not just a few sessions) |
| **MemoryMiddleware.after_agent** | Extraction quality over 90+ densely-packed conversations, facts are updated as new info comes in |
| **MemoryStore (confidence decay)** | Events from "early" sessions still retrievable; recent events prioritized |
| **HybridDB** (SQLite + FTS5 + ChromaDB) | Hybrid search performance at scale (thousands of messages) |
| **memory_search / memory_get_history** | Agent successfully uses memory tools to navigate a large corpus |
| **Summarization scoring** | Tests the agent's ability to synthesize multi-turn information — a key assistant skill |

---

## Implementation Order

1. **`dataset.py`** — Download, parse, validate LoCoMo data (Phase 1)
2. **`adapter.py`** — Inject conversations into MessageStore (Phase 2)
3. **`runner.py`** — QA evaluation first (Phase 3, QA mode)
4. **`judge.py`** — QA judging (reuses existing Judge) + automated scoring (Phase 4)
5. **`eval.py` + `cli.py`** — Full benchmark runner (Phase 5)
6. **Summarization + Dialogue modes** — Extend runner/judge for remaining tasks
7. **Documentation** — Add comparison table to EVAL.md with LoCoMo vs LongMemEval results
8. **CI integration** — Optional: add to CI pipeline for regression testing

---

## Risks & Mitigations

| Risk | Mitigation |
|------|-----------|
| LoCoMo dataset format changes on HF | Pin to specific commit hash for downloads |
| 90+ conversations cause OOM in summarization | Cap at `max_context_chars=80000`, chunk summarization if needed |
| GPT-4o cost for judging all tasks | Only GPT-4o for QA; automated metrics for summarization/dialogue |
| Rate limiting too slow for full benchmark | Parallelize where possible; use `evaluate_batch` pattern |
| `datasets` library adds heavy dependency | Implement fallback that downloads raw JSON files directly |

---

## Success Criteria

- [ ] QA accuracy measured and compared to published LoCoMo baselines
- [ ] SummarizationMiddleware correctly triggers and produces useful summaries
- [ ] Memory extraction pipeline correctly captures facts across 90+ sessions
- [ ] No memory leaks between evaluation instances
- [ ] Results reproducible with a single command: `uv run python -m tests.benchmarks.locomo.eval`
- [ ] All automated tests pass for the new module
