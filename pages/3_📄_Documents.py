"""
📄 Documents — Document Upload, Knowledge Base, and Ollama Model Creation

Through this page you can:
- Upload PDF, DOCX, TXT, MD, PPTX documents
- Fetch content from web URLs
- Track RAG / Modelfile status of documents
- Generate Ollama Modelfiles from selected documents and push to Ollama
"""

import streamlit as st
import os
import json
import requests
from pathlib import Path
from datetime import datetime, timezone, timedelta
from config.settings import settings
from auth.authenticator import check_auth
from core.document_processor import SUPPORTED_EXTENSIONS
OLLAMA_MODELS = [
    "Qwen2.5:32B", "qwen3.5:27b", "qwen3.5:35b", "qwen3.5:397b-cloud",
    "command-r", "dolphin3", "llama3.1:8b", "ops-assistant",
]

# --- Page Settings ---
st.set_page_config(page_title="Documents — Infra Ops Center", page_icon="📄", layout="wide")

css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

if not check_auth():
    st.stop()


def _get_rag_engine():
    """RAG Engine singleton."""
    if "rag_engine" not in st.session_state:
        from core.rag_engine import RAGEngine
        st.session_state.rag_engine = RAGEngine()
    return st.session_state.rag_engine


def _format_size(size_bytes: int) -> str:
    if size_bytes < 1024:
        return f"{size_bytes} B"
    elif size_bytes < 1024 * 1024:
        return f"{size_bytes / 1024:.1f} KB"
    else:
        return f"{size_bytes / (1024 * 1024):.1f} MB"


def _status_badges(doc: dict) -> str:
    """Generate colored badge text based on document status."""
    badges = []

    # Active in RAG (always)
    badges.append("🟢 RAG")

    # Source
    if doc.get("source") == "url":
        badges.append("🔵 URL")
    else:
        badges.append("📁 File")

    # Is it new? (last 24 hours)
    try:
        indexed_dt = datetime.fromisoformat(doc["indexed_at"])
        # If naive datetime, assume UTC
        if indexed_dt.tzinfo is None:
            indexed_dt = indexed_dt.replace(tzinfo=timezone.utc)
        age = datetime.now(tz=timezone.utc) - indexed_dt
        if age < timedelta(hours=24):
            badges.append("📅 New")
    except Exception:
        pass

    # Used in Modelfile?
    if doc.get("modelfile_used"):
        badges.append("🤖 Modelfile")

    return "  ".join(badges)


# ══════════════════════════════════════════════════════════
# TITLE
# ══════════════════════════════════════════════════════════

st.title("📄 Document Management")
st.markdown(
    "Expand the AI knowledge base by uploading documents or adding web URLs. "
    "Content is automatically indexed, used as references in chat, and "
    "can be used to create Ollama models."
)

rag = _get_rag_engine()

# ══════════════════════════════════════════════════════════
# DOCUMENT UPLOAD
# ══════════════════════════════════════════════════════════

st.header("📤 Add Content")
tab_file, tab_url = st.tabs(["📁 Upload File", "🌐 Web URL"])

with tab_file:
    extensions = [ext.lstrip(".") for ext in SUPPORTED_EXTENSIONS]
    uploaded_files = st.file_uploader(
        "Select files (you can upload multiple files)",
        type=extensions,
        accept_multiple_files=True,
        key="doc_uploader",
        help=f"Supported formats: {', '.join(ext.upper() for ext in sorted(extensions))}"
    )

    if uploaded_files:
        kb_dir = Path(settings.KNOWLEDGE_BASE_DIR)
        kb_dir.mkdir(parents=True, exist_ok=True)

        progress = st.progress(0, text="Uploading documents...")
        results = []

        for i, uploaded_file in enumerate(uploaded_files):
            progress.progress((i) / len(uploaded_files), text=f"Processing: {uploaded_file.name}...")
            save_path = kb_dir / uploaded_file.name
            with open(save_path, "wb") as f:
                f.write(uploaded_file.getvalue())
            try:
                result = rag.index_document(str(save_path), source="file")
                results.append((uploaded_file.name, result))
            except Exception as e:
                results.append((uploaded_file.name, {"status": "error", "error": str(e)}))

        progress.progress(1.0, text="Completed!")

        for name, result in results:
            if result.get("status") == "ok":
                st.success(f"✅ **{name}** — Indexed as {result['chunks']} chunks")
            elif result.get("status") == "empty":
                st.warning(f"⚠️ **{name}** — Document is empty or could not be read")
            else:
                st.error(f"❌ **{name}** — Error: {result.get('error', 'Unknown error')}")

with tab_url:
    st.markdown("Enter a web page URL. The page content will be fetched and added to the knowledge base.")
    url_input = st.text_input("Web URL", placeholder="https://docs.example.com/guide", key="url_input")

    if st.button("🌐 Fetch Content from URL", key="fetch_url", use_container_width=True):
        if url_input and url_input.startswith(("http://", "https://")):
            with st.spinner(f"Fetching content: {url_input}"):
                try:
                    from core.document_processor import extract_from_url
                    kb_dir = Path(settings.KNOWLEDGE_BASE_DIR)
                    kb_dir.mkdir(parents=True, exist_ok=True)
                    saved_path, extracted_text = extract_from_url(url_input, str(kb_dir))
                    preview = extracted_text[:500] + "..." if len(extracted_text) > 500 else extracted_text
                    st.text_area("📋 Extracted Content (Preview)", preview, height=150, disabled=True)
                    st.caption(f"Total: {len(extracted_text):,} characters")
                    result = rag.index_document(saved_path, source="url")
                    if result.get("status") == "ok":
                        st.success(
                            f"✅ URL successfully indexed!\n\n"
                            f"📁 Saved as: `{Path(saved_path).name}`\n\n"
                            f"🧩 {result['chunks']} chunks created"
                        )
                    elif result.get("status") == "empty":
                        st.warning("⚠️ Could not extract text from the page.")
                except Exception as e:
                    st.error(f"❌ Error processing URL: {str(e)}")
        else:
            st.warning("⚠️ Enter a valid URL (must start with http:// or https://)")

st.divider()

# ══════════════════════════════════════════════════════════
# DOCUMENT LIST (WITH STATUS BADGES)
# ══════════════════════════════════════════════════════════

st.header("📚 Uploaded Documents")

documents = rag.list_documents()

if not documents:
    st.info("📭 No documents uploaded yet.")
else:
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("📄 Total Documents", rag.total_documents)
    col2.metric("🧩 Total Chunks", rag.total_chunks)
    total_size = sum(d.get("size_bytes", 0) for d in documents)
    col3.metric("💾 Total Size", _format_size(total_size))
    modelfile_count = sum(1 for d in documents if d.get("modelfile_used"))
    col4.metric("🤖 In Modelfile", modelfile_count)

    st.markdown("---")

    ext_icons = {".PDF": "📕", ".DOCX": "📘", ".TXT": "📝", ".MD": "📋", ".PPTX": "📙", ".HTML": "🌐"}

    for doc in documents:
        col_info, col_badges, col_action = st.columns([4, 3, 1])
        ext = Path(doc["filename"]).suffix.upper()
        icon = ext_icons.get(ext, "📄")

        with col_info:
            st.markdown(
                f"**{icon} {doc['filename']}**  \n"
                f"🧩 {doc['chunks']} chunks · "
                f"💾 {_format_size(doc.get('size_bytes', 0))} · "
                f"🕐 {doc['indexed_at'][:16].replace('T', ' ')}"
            )
        with col_badges:
            st.markdown(_status_badges(doc))
        with col_action:
            if st.button("🗑️ Delete", key=f"del_{doc['doc_id']}", use_container_width=True):
                rag.delete_document_file(doc["doc_id"])
                st.success(f"✅ {doc['filename']} deleted!")
                st.rerun()

st.divider()

# ══════════════════════════════════════════════════════════
# OLLAMA MODELFILE GENERATOR
# ══════════════════════════════════════════════════════════

st.header("🤖 Create Ollama Model")
st.markdown(
    "Create an Ollama `Modelfile` from the content of selected documents and "
    "send it directly to the Ollama server. This is not full fine-tuning, but "
    "a **system prompt document embedding** (prompt injection) technique."
)

if not documents:
    st.info("📭 Please upload documents first.")
else:
    col_left, col_right = st.columns([3, 2])

    with col_left:
        st.subheader("1️⃣ Select Documents")
        selected_doc_ids = []
        for doc in documents:
            ext = Path(doc["filename"]).suffix.upper()
            icon = ext_icons.get(ext, "📄")
            modelfile_tag = " 🤖" if doc.get("modelfile_used") else ""
            checked = st.checkbox(
                f"{icon} {doc['filename']}  ({doc['chunks']} chunks){modelfile_tag}",
                key=f"mf_sel_{doc['doc_id']}"
            )
            if checked:
                selected_doc_ids.append(doc["doc_id"])

    with col_right:
        st.subheader("2️⃣ Settings")
        model_name = st.text_input(
            "Model name to create",
            value="ops-assistant",
            help="A new model will be created with this name in Ollama"
        )
        # Default to qwen3.5:27b (or first model if not available)
        _default_base = "qwen3.5:27b" if "qwen3.5:27b" in OLLAMA_MODELS else OLLAMA_MODELS[0]
        base_model = st.selectbox(
            "Base model",
            OLLAMA_MODELS,
            index=OLLAMA_MODELS.index(_default_base),
            help="The model to use in the Modelfile FROM line"
        )
        max_chars_per_doc = st.slider(
            "Max characters per document",
            min_value=1000,
            max_value=20000,
            value=6000,
            step=1000,
            help="Limits the total system prompt size"
        )
        num_ctx = st.select_slider(
            "Context window (num_ctx)",
            options=[4096, 8192, 16384, 32768, 65536],
            value=16384,
            help="Model memory window — select the max value supported by the model"
        )

    if selected_doc_ids:
        st.subheader("3️⃣ Modelfile Preview")

        # Collect content
        doc_sections = []
        for doc_id in selected_doc_ids:
            text = rag.get_document_text(doc_id, max_chars=max_chars_per_doc)
            meta = next((d for d in documents if d["doc_id"] == doc_id), {})
            fname = meta.get("filename", doc_id)
            doc_sections.append(f"=== {fname} ===\n{text}")

        combined_content = "\n\n".join(doc_sections)

        # Clean control characters from PDFs (to avoid breaking Modelfile / JSON)
        import re
        combined_content = re.sub(r'[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]', '', combined_content)

        system_prompt = (
            "Sen kıdemli bir IT Ops ve Sistem Yönetimi uzmanısın. "
            "Aşağıdaki belgelerden edindiğin bilgileri kullanarak kullanıcıların sorularını yanıtla. "
            "Yanıtlarını Türkçe ver ve teknik konularda doğrudan, net ol.\n\n"
            "=== BİLGİ TABANI ===\n"
            f"{combined_content}\n"
            "=== BİLGİ TABANI SONU ==="
        )

        # New Ollama API payload (0.5+): structured JSON format
        api_payload = {
            "model": model_name,
            "from": base_model,
            "system": system_prompt,
            "parameters": {
                "num_ctx": num_ctx,
                "temperature": 0.7,
                "top_p": 0.9,
            },
        }

        # Old API compatibility: also generate Modelfile string (for download only)
        # Escape triple-quotes in Modelfile to avoid syntax errors
        safe_system = system_prompt.replace('"""', '\\"\\"\\"')
        modelfile_content = (
            f"FROM {base_model}\n\n"
            f'SYSTEM """\n{safe_system}\n"""\n\n'
            f"PARAMETER num_ctx {num_ctx}\n"
            f"PARAMETER temperature 0.7\n"
            f"PARAMETER top_p 0.9\n"
        )

        total_chars = len(modelfile_content)
        approx_tokens = total_chars // 4

        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Selected Documents", len(selected_doc_ids))
        col_b.metric("Modelfile Size", f"{total_chars:,} characters")
        col_c.metric("Estimated Tokens", f"~{approx_tokens:,}")

        if approx_tokens > num_ctx * 0.8:
            st.warning(
                f"⚠️ System prompt (~{approx_tokens:,} tokens) exceeds 80% of the context window "
                f"({int(num_ctx * 0.8):,}). "
                "Select fewer documents or reduce the max character count."
            )

        with st.expander("📋 Modelfile Content (Preview)", expanded=False):
            st.code(modelfile_content[:3000] + ("\n...(truncated)" if len(modelfile_content) > 3000 else ""), language="dockerfile")

        st.subheader("4️⃣ Send to Ollama")
        ollama_url = st.text_input(
            "Ollama URL",
            value=settings.OLLAMA_URL,
            key="ollama_url_mf"
        )

        col_btn1, col_btn2 = st.columns(2)

        with col_btn1:
            if st.button("🚀 Send to Ollama", type="primary", use_container_width=True):
                with st.spinner(f"🤖 Creating '{model_name}'... (may take minutes for large models)"):
                    try:
                        def _send_to_ollama(payload: dict) -> requests.Response:
                            return requests.post(
                                f"{ollama_url}/api/create",
                                json=payload,
                                timeout=300,
                                stream=True,
                            )

                        # Try new API format first (Ollama 0.5+)
                        resp = _send_to_ollama(api_payload)

                        # If 400, fall back to old Modelfile format (older Ollama versions)
                        if resp.status_code == 400:
                            err_preview = resp.text[:200]
                            st.warning(f"⚠️ New API format not accepted, trying old format... ({err_preview[:80]})")
                            old_payload = {"name": model_name, "modelfile": modelfile_content}
                            resp = _send_to_ollama(old_payload)

                        # If HTTP error, read body before stream starts
                        if resp.status_code != 200:
                            err_text = resp.text
                            try:
                                err_body = json.loads(err_text)
                                err_msg = err_body.get("error", err_text[:500])
                            except Exception:
                                err_msg = err_text[:500] or f"HTTP {resp.status_code}"
                            st.error(f"❌ Ollama API error (HTTP {resp.status_code}): {err_msg}")
                            with st.expander("📋 Error Details"):
                                st.code(err_text[:2000], language="json")
                        else:
                            # 200 OK → track progress via streaming
                            progress_area = st.empty()
                            last_status = ""
                            success = False
                            collected_lines = []

                            for line in resp.iter_lines():
                                if line:
                                    try:
                                        data = json.loads(line.decode("utf-8"))
                                        collected_lines.append(data)
                                        status = data.get("status", "")
                                        if status != last_status:
                                            progress_area.info(f"⏳ {status}")
                                            last_status = status
                                        if data.get("status") == "success":
                                            success = True
                                        if data.get("error"):
                                            st.error(f"❌ Ollama error: {data['error']}")
                                            success = False
                                            break
                                    except Exception:
                                        pass

                            if success:
                                rag.mark_modelfile_used(selected_doc_ids)
                                progress_area.empty()
                                st.success(
                                    f"✅ **'{model_name}'** model created successfully!\n\n"
                                    f"To use it, select **🟣 Ollama (Local)** from the sidebar "
                                    f"and add **`{model_name}`** to the model list."
                                )
                                st.balloons()
                            elif not any(d.get("error") for d in collected_lines):
                                st.warning("⚠️ Ollama process completed but no 'success' signal received.")

                    except requests.ConnectionError:
                        st.error(f"❌ Could not connect to Ollama: `{ollama_url}`")
                    except Exception as e:
                        st.error(f"❌ Error: {str(e)}")

        with col_btn2:
            mf_bytes = modelfile_content.encode("utf-8")
            st.download_button(
                "💾 Download Modelfile",
                data=mf_bytes,
                file_name=f"Modelfile_{model_name}",
                mime="text/plain",
                use_container_width=True,
            )
    else:
        st.info("☝️ Select at least one document above to create a Modelfile.")

st.divider()

# ══════════════════════════════════════════════════════════
# RAG TEST SEARCH
# ══════════════════════════════════════════════════════════

st.header("🔍 RAG Test Search")
st.caption("Test that RAG is working by searching across uploaded documents.")

search_query = st.text_input(
    "Search query",
    placeholder="e.g. What are the security policies?",
    key="rag_search"
)

if search_query:
    with st.spinner("Searching..."):
        results = rag.search(search_query)

    if results:
        st.success(f"✅ {len(results)} result(s) found")
        for i, r in enumerate(results):
            with st.expander(
                f"📄 {r['filename']} — Chunk {r['chunk_index'] + 1} "
                f"(Similarity: {r['score']:.1%})",
                expanded=(i == 0)
            ):
                st.markdown(r["text"])
    else:
        st.warning("No results found. Try a different search or upload documents.")

# ══════════════════════════════════════════════════════════
# RAG SETTINGS (SIDEBAR)
# ══════════════════════════════════════════════════════════

with st.sidebar:
    st.header("⚙️ RAG Settings")
    st.caption(f"Chunk size: **{settings.RAG_CHUNK_SIZE}** characters")
    st.caption(f"Overlap: **{settings.RAG_CHUNK_OVERLAP}** characters")
    st.caption(f"Top-K: **{settings.RAG_TOP_K}** results")
    st.caption(f"Knowledge base: `{settings.KNOWLEDGE_BASE_DIR}`")
    rag_status = "✅ Active" if settings.RAG_ENABLED else "❌ Disabled"
    st.caption(f"RAG Status: {rag_status}")

    st.divider()
    st.header("🤖 Ollama")
    st.caption(f"URL: `{settings.OLLAMA_URL}`")
    st.caption("Select documents for the Modelfile and click 'Send to Ollama'.")
