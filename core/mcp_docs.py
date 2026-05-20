"""
Per-MCP document indexing and endpoint search.

Lets every MCP tool answer "find the endpoint that does X" by searching
documentation indexed for that specific MCP. The agent doesn't need to
carry the API spec in its context — it asks for what it needs when it
needs it.

Public functions:
    index_doc_for_mcp(file_path, mcp_name, source)
    search_mcp_docs(mcp_name, query, top_k=5) -> list[dict]
    list_indexed_mcps() -> list[dict]
    format_search_results(results) -> str    # for agent-consumable output
"""

from typing import Optional

from logging_config.logger import get_logger

logger = get_logger("mcp_docs")


def index_doc_for_mcp(file_path: str, mcp_name: str,
                      *, source: str = "api_doc",
                      doc_id: Optional[str] = None) -> dict:
    """
    Index a document and tag it as belonging to `mcp_name`.

    Returns the RAG engine's result dict ({doc_id, chunks, status}).
    """
    if not mcp_name:
        return {"status": "error", "error": "mcp_name is required"}
    try:
        from core.rag_engine import RAGEngine
        result = RAGEngine().index_document(
            file_path=file_path,
            doc_id=doc_id,
            source=source,
            mcp=mcp_name,
        )
        logger.info(
            f"Indexed doc for MCP '{mcp_name}': {file_path} → {result}"
        )
        return result
    except Exception as e:
        logger.error(f"Failed to index doc for MCP '{mcp_name}': {e}")
        return {"status": "error", "error": f"{type(e).__name__}: {e}"}


def search_mcp_docs(mcp_name: str, query: str,
                    top_k: int = 5) -> list[dict]:
    """
    Search this MCP's indexed documents for chunks matching `query`.

    Returns a list of {text, filename, doc_id, chunk_index, score}.
    Empty list if nothing indexed for this MCP or no matches.
    """
    if not mcp_name or not query:
        return []
    try:
        from core.rag_engine import get_rag_engine
        return get_rag_engine().search(
            query=query, top_k=top_k, mcp=mcp_name,
        )
    except Exception as e:
        logger.warning(f"search_mcp_docs failed for '{mcp_name}': {e}")
        return []


def list_indexed_mcps() -> list[dict]:
    """
    Return one entry per MCP that has at least one indexed document.
    Used by UI / the cross-MCP discovery action.
    """
    try:
        from core.rag_engine import get_rag_engine
        docs = get_rag_engine().list_documents()
    except Exception as e:
        logger.warning(f"list_indexed_mcps failed: {e}")
        return []

    out: dict[str, dict] = {}
    for d in docs:
        mcp = d.get("mcp") or ""
        if not mcp:
            continue
        row = out.setdefault(mcp, {
            "mcp": mcp, "docs": 0, "chunks": 0, "files": [],
        })
        row["docs"] += 1
        row["chunks"] += d.get("chunks", 0)
        if d.get("filename"):
            row["files"].append(d["filename"])
    return sorted(out.values(), key=lambda r: r["mcp"])


def format_search_results(matches: list[dict],
                          *, max_chars_per_chunk: int = 600) -> str:
    """
    Render search results as a single text block the agent can read.

    Truncates each chunk; keeps the filename and score for traceability.
    """
    if not matches:
        return (
            "No matching endpoints in indexed API documentation.\n"
            "Tip: ask the operator to upload the API doc on the "
            "📄 Documents page and tag it with this MCP."
        )
    lines = [f"Top {len(matches)} matching passages from indexed docs:"]
    for i, m in enumerate(matches, 1):
        snippet = (m.get("text") or "").strip().replace("\r", "")
        if len(snippet) > max_chars_per_chunk:
            snippet = snippet[:max_chars_per_chunk] + "\n... (truncated)"
        lines.append(
            f"\n--- #{i} · score={m.get('score', 0)} · "
            f"file={m.get('filename', '?')} · chunk={m.get('chunk_index', '?')} ---\n"
            f"{snippet}"
        )
    return "\n".join(lines)


# ─── Shared dispatch helper ─────────────────────────────────────────

def handle_search_api_action(mcp_name: str, tool_input: dict) -> str:
    """
    Generic handler for `action: search_api` — same behaviour across every
    MCP. Tools just delegate here when their action is "search_api".

    Recognised input fields:
        query: str        — the natural-language question
        top_k: int        — max results (default 5)
    """
    query = (tool_input.get("query")
             or tool_input.get("q")
             or tool_input.get("command")
             or "").strip()
    if not query:
        return "❌ search_api needs a 'query' field describing what to look for."

    top_k = int(tool_input.get("top_k", 5))
    matches = search_mcp_docs(mcp_name, query, top_k=top_k)
    return format_search_results(matches)
