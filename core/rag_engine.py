"""
RAG (Retrieval-Augmented Generation) Engine.
TF-IDF based vector search for document indexing and retrieval.
Chunks uploaded documents, indexes them with TF-IDF,
and returns the most relevant chunks for the user's query.

Uses scikit-learn TF-IDF instead of ChromaDB (pure Python, no extra C++ dependency).
"""

import os
import json
import pickle
from pathlib import Path
from datetime import datetime
from config.settings import settings
from core.document_processor import process_document, SUPPORTED_EXTENSIONS
from logging_config.logger import get_logger

logger = get_logger("rag_engine")

# Metadata and index files
_META_FILENAME = "doc_metadata.json"
_INDEX_FILENAME = "tfidf_index.pkl"

# Singleton cache — loads vectorizer once per process
_rag_singleton = None


def get_rag_engine():
    """
    Returns a cached RAGEngine singleton.
    The TF-IDF vectorizer is large (pickle) — loading it once saves ~500ms per query.
    """
    global _rag_singleton
    if _rag_singleton is None:
        _rag_singleton = RAGEngine()
    return _rag_singleton


def invalidate_rag_cache():
    """Force the next get_rag_engine() call to create a fresh instance (after re-indexing)."""
    global _rag_singleton
    _rag_singleton = None


class RAGEngine:
    """
    TF-IDF based RAG engine.

    - Chunks documents and stores them as a TF-IDF matrix
    - Finds the most relevant chunks for a query using cosine similarity
    - Generates context text for the system prompt
    """

    def __init__(self):
        self._kb_dir = Path(settings.KNOWLEDGE_BASE_DIR)
        self._kb_dir.mkdir(parents=True, exist_ok=True)

        self._meta_path = self._kb_dir / _META_FILENAME
        self._index_path = self._kb_dir / _INDEX_FILENAME

        self._metadata = self._load_metadata()
        self._chunks: list[dict] = []       # All chunks
        self._vectorizer = None              # TfidfVectorizer
        self._tfidf_matrix = None            # TF-IDF sparse matrix

        self._load_index()

    # ══════════════════════════════════════════════════════════
    # METADATA MANAGEMENT
    # ══════════════════════════════════════════════════════════

    def _load_metadata(self) -> dict:
        """Loads the metadata file."""
        if self._meta_path.exists():
            try:
                return json.loads(self._meta_path.read_text(encoding="utf-8"))
            except Exception:
                return {}
        return {}

    def _save_metadata(self):
        """Saves the metadata file."""
        self._meta_path.write_text(
            json.dumps(self._metadata, ensure_ascii=False, indent=2),
            encoding="utf-8"
        )

    # ══════════════════════════════════════════════════════════
    # TF-IDF INDEX MANAGEMENT
    # ══════════════════════════════════════════════════════════

    def _load_index(self):
        """Loads the saved TF-IDF index."""
        if self._index_path.exists():
            try:
                with open(self._index_path, "rb") as f:
                    data = pickle.load(f)
                self._chunks = data.get("chunks", [])
                self._vectorizer = data.get("vectorizer")
                self._tfidf_matrix = data.get("tfidf_matrix")
            except Exception as e:
                logger.warning(f"Failed to load index, resetting: {e}")
                self._chunks = []
                self._vectorizer = None
                self._tfidf_matrix = None

    def _save_index(self):
        """Saves the TF-IDF index to disk."""
        try:
            with open(self._index_path, "wb") as f:
                pickle.dump({
                    "chunks": self._chunks,
                    "vectorizer": self._vectorizer,
                    "tfidf_matrix": self._tfidf_matrix,
                }, f)
        except Exception as e:
            logger.error(f"Failed to save index: {e}")

    def _rebuild_tfidf(self):
        """Rebuilds the TF-IDF matrix for all chunks."""
        if not self._chunks:
            self._vectorizer = None
            self._tfidf_matrix = None
            self._save_index()
            return

        from sklearn.feature_extraction.text import TfidfVectorizer

        texts = [c["text"] for c in self._chunks]
        self._vectorizer = TfidfVectorizer(
            max_features=10000,
            ngram_range=(1, 2),
            sublinear_tf=True,
        )
        self._tfidf_matrix = self._vectorizer.fit_transform(texts)
        self._save_index()

    # ══════════════════════════════════════════════════════════
    # DOCUMENT INDEXING
    # ══════════════════════════════════════════════════════════

    def index_document(self, file_path: str, doc_id: str = None, source: str = "file") -> dict:
        """
        Chunks the document and adds it to the TF-IDF index.

        Args:
            file_path: Document file path
            doc_id: Optional unique ID (generated from filename if not provided)
            source: "file" | "url" — document source

        Returns:
            {"doc_id": "...", "chunks": 5, "status": "ok"}
        """
        file_path = str(file_path)
        filename = Path(file_path).name

        if doc_id is None:
            doc_id = filename.replace(" ", "_").lower()

        # If the document already exists and is being updated, remove it first
        if doc_id in self._metadata:
            self.remove_document(doc_id)

        # Chunk the document
        chunks = process_document(
            file_path,
            chunk_size=settings.RAG_CHUNK_SIZE,
            overlap=settings.RAG_CHUNK_OVERLAP,
        )

        if not chunks:
            logger.warning(f"Document is empty, could not index: {filename}")
            return {"doc_id": doc_id, "chunks": 0, "status": "empty"}

        # Add chunks to the list
        for c in chunks:
            self._chunks.append({
                "text": c["text"],
                "doc_id": doc_id,
                "filename": filename,
                "chunk_index": c["index"],
            })

        # Rebuild TF-IDF
        self._rebuild_tfidf()

        # Save metadata
        self._metadata[doc_id] = {
            "filename": filename,
            "file_path": file_path,
            "chunks": len(chunks),
            "indexed_at": datetime.now().isoformat(),
            "size_bytes": os.path.getsize(file_path),
            "source": source,          # "file" | "url"
            "modelfile_used": False,   # Whether used in Ollama Modelfile
        }
        self._save_metadata()

        logger.info(f"Document indexed: {filename} -> {len(chunks)} chunks")
        return {"doc_id": doc_id, "chunks": len(chunks), "status": "ok"}

    # ══════════════════════════════════════════════════════════
    # DOCUMENT REMOVAL
    # ══════════════════════════════════════════════════════════

    def remove_document(self, doc_id: str) -> bool:
        """Removes the document from the index and metadata."""
        if doc_id not in self._metadata:
            return False

        # Clean up chunks
        self._chunks = [c for c in self._chunks if c["doc_id"] != doc_id]

        # Rebuild TF-IDF
        self._rebuild_tfidf()

        # Remove from metadata
        del self._metadata[doc_id]
        self._save_metadata()

        logger.info(f"Document removed: {doc_id}")
        return True

    def delete_document_file(self, doc_id: str) -> bool:
        """Removes the document from both the index and the physical file."""
        meta = self._metadata.get(doc_id)
        if not meta:
            return False

        file_path = meta.get("file_path", "")

        # First remove from index
        self.remove_document(doc_id)

        # Delete physical file
        if file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
                logger.info(f"File deleted: {file_path}")
            except Exception as e:
                logger.error(f"File deletion error: {e}")
                return False

        return True

    # ══════════════════════════════════════════════════════════
    # SEARCH
    # ══════════════════════════════════════════════════════════

    def search(self, query: str, top_k: int = None) -> list[dict]:
        """
        Returns the most relevant chunks for the query using cosine similarity.

        Args:
            query: Search query
            top_k: Maximum number of results to return

        Returns:
            [{"text": "...", "filename": "...", "score": 0.85, "chunk_index": 0}, ...]
        """
        if top_k is None:
            top_k = settings.RAG_TOP_K

        if not self._chunks or self._vectorizer is None or self._tfidf_matrix is None:
            return []

        from sklearn.metrics.pairwise import cosine_similarity

        # Convert query to TF-IDF vector
        query_vec = self._vectorizer.transform([query])

        # Calculate cosine similarity
        similarities = cosine_similarity(query_vec, self._tfidf_matrix).flatten()

        # Get top scores
        top_indices = similarities.argsort()[::-1][:top_k]

        matches = []
        for idx in top_indices:
            score = float(similarities[idx])
            if score <= 0:
                continue

            chunk = self._chunks[idx]
            matches.append({
                "text": chunk["text"],
                "filename": chunk["filename"],
                "doc_id": chunk["doc_id"],
                "chunk_index": chunk["chunk_index"],
                "score": round(score, 3),
            })

        return matches

    # ══════════════════════════════════════════════════════════
    # SYSTEM PROMPT CONTEXT
    # ══════════════════════════════════════════════════════════

    def get_context_for_prompt(self, query: str, top_k: int = None) -> str:
        """
        Returns the most relevant document chunks formatted
        for inclusion in the system prompt.
        """
        if not settings.RAG_ENABLED:
            return ""

        matches = self.search(query, top_k)

        if not matches:
            return ""

        context_parts = []
        for m in matches:
            context_parts.append(
                f"📄 {m['filename']} (Chunk {m['chunk_index'] + 1}, Similarity: {m['score']}):\n"
                f"{m['text']}"
            )

        context = "\n\n---\n\n".join(context_parts)
        return (
            "\n\n══════ DOCUMENT CONTEXT (RAG) ══════\n"
            "Below is information found in uploaded documents related to the user's question. "
            "Use this information as a reference in your responses.\n\n"
            f"{context}\n"
            "══════════════════════════════════\n"
        )

    # ══════════════════════════════════════════════════════════
    # DOCUMENT LIST
    # ══════════════════════════════════════════════════════════

    def list_documents(self) -> list[dict]:
        """Returns a list of indexed documents."""
        docs = []
        for doc_id, meta in self._metadata.items():
            docs.append({
                "doc_id": doc_id,
                "filename": meta["filename"],
                "chunks": meta["chunks"],
                "indexed_at": meta["indexed_at"],
                "size_bytes": meta.get("size_bytes", 0),
                "source": meta.get("source", "file"),
                "modelfile_used": meta.get("modelfile_used", False),
            })
        return sorted(docs, key=lambda d: d["indexed_at"], reverse=True)

    def mark_modelfile_used(self, doc_ids: list[str]):
        """Marks the specified documents as used in Modelfile."""
        for doc_id in doc_ids:
            if doc_id in self._metadata:
                self._metadata[doc_id]["modelfile_used"] = True
        self._save_metadata()
        logger.info(f"Modelfile usage marked: {doc_ids}")

    def get_document_text(self, doc_id: str, max_chars: int = 8000) -> str:
        """Combines chunk texts for the specified document (for Modelfile)."""
        chunks = [c["text"] for c in self._chunks if c["doc_id"] == doc_id]
        combined = "\n\n".join(chunks)
        return combined[:max_chars] if len(combined) > max_chars else combined

    @property
    def total_chunks(self) -> int:
        """Total number of chunks."""
        return len(self._chunks)

    @property
    def total_documents(self) -> int:
        """Total number of documents."""
        return len(self._metadata)
