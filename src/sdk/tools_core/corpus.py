"""Corpus tools — index/search reference knowledge (P1-T1).

`index_corpus` grounds stored documents into the per-user, per-workspace
reference corpus (client records, case history, price tables — facts).
`search_corpus` queries it read-only. Facts live here; methodology
lives in skills — never auto-convert corpus content into skills.
"""

from __future__ import annotations

from src.sdk.tools import ToolAnnotations, tool
from src.storage.corpus import CorpusStore
from src.storage.paths import DEFAULT_USER_ID


@tool
def index_corpus(
    text: str,
    source: str,
    user_id: str = DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Index reference text into the user's knowledge corpus.

    Grounds facts (client records, case history, price tables) so they can
    be searched later. Idempotent per source: re-indexing the same source
    replaces its previous content. Nothing is auto-committed into skills.

    Args:
        text: Reference text to index
        source: Source identifier, e.g. "acme/retainer" or "pricing/2024"
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        JSON string: {"indexed": <chunks>, "source": "..."}
    """
    store = CorpusStore(user_id=user_id, workspace_id=workspace_id)
    n = store.index(text=text, source=source)
    import json

    return json.dumps({"indexed": n, "source": source})


@tool
def search_corpus(
    query: str,
    k: int = 5,
    user_id: str = DEFAULT_USER_ID,
    workspace_id: str = "personal",
) -> str:
    """Search the user's reference knowledge corpus (read-only).

    Keyword search over indexed reference documents (client records, case
    history, price tables). Returns the top-k ranked snippets with source
    attribution. Use before claiming a fact is unknown.

    Args:
        query: Keyword query
        k: Maximum number of results (default 5)
        user_id: User identifier
        workspace_id: Workspace ID (defaults to current workspace)

    Returns:
        JSON string: {"results": [{source, snippet, chunk_idx, confidence}], "query": ...}
    """
    store = CorpusStore(user_id=user_id, workspace_id=workspace_id)
    import json

    try:
        hits = store.search(query, k=max(1, k))
    except ValueError:
        # Empty/garbage query: honest empty result, not an error for the agent.
        return json.dumps({"query": query, "results": []})

    return json.dumps({
        "query": query,
        "results": [
            {
                "source": h["source"],
                "snippet": h["snippet"],
                "chunk_idx": h["chunk_idx"],
                "confidence": h["confidence"],
            }
            for h in hits
        ],
    })


index_corpus.annotations = ToolAnnotations(
    title="Index Corpus", destructive=False, read_only=False
)
search_corpus.annotations = ToolAnnotations(
    title="Search Corpus", destructive=False, read_only=True
)
