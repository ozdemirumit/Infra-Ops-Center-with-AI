"""
📡 MCP Tools — Dynamic Management Page

Enable/disable built-in tools, add/edit/delete custom tools.
3 different execute backends: SSH Command, HTTP Template, Python Script, Schema Only.
"""

import json
import os
import streamlit as st
from auth.authenticator import check_auth
from ui.sidebar import render_sidebar
from devices.storage import DeviceStorage, DEVICE_TYPES
from tools.registry import (
    get_all_tools_with_status, get_active_tools,
    set_builtin_enabled, add_custom_tool, update_custom_tool,
    delete_custom_tool, set_custom_enabled, get_custom_tool,
    generate_tools_from_doc,
    delete_builtin_tool, restore_builtin_tool, restore_all_builtin_tools,
    get_deleted_builtin_tools,
)

st.set_page_config(page_title="MCP Tools", page_icon="📡", layout="wide")

# Modern UI CSS
css_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "ui", "style.css")
if os.path.exists(css_path):
    with open(css_path, "r", encoding="utf-8") as f:
        st.markdown(f"<style>{f.read()}</style>", unsafe_allow_html=True)

if not check_auth():
    st.stop()


render_sidebar()
is_admin = st.session_state.get("role") == "admin"

st.title("📡 MCP Tools (Model Context Protocol)")

# ═══════════════════════════════════════════════════════════════════
# TABS
# ═══════════════════════════════════════════════════════════════════

tab_manage, tab_generate, tab_details, tab_arch = st.tabs([
    "🔧 Tool Management",
    "📄 Generate from Document",
    "📋 Tool Details",
    "🏗️ Architecture",
])

# ═══════════════════════════════════════════════════════════════════
# TAB 1: TOOL MANAGEMENT
# ═══════════════════════════════════════════════════════════════════

with tab_manage:
    all_tools = get_all_tools_with_status()
    active_count = sum(1 for t in all_tools if t.get("enabled"))
    total_count = len(all_tools)

    st.metric("Active Tools", f"{active_count} / {total_count}")
    st.divider()

    # ── Built-in Tools ──
    st.subheader("🏗️ Built-in Tools")
    builtin_tools = [t for t in all_tools if t.get("is_builtin")]

    # First row: 4 tools
    row1 = builtin_tools[:4]
    row2 = builtin_tools[4:]

    for row_tools in [row1, row2]:
        if not row_tools:
            continue
        cols = st.columns(len(row_tools))
        for i, tool in enumerate(row_tools):
            with cols[i]:
                name = tool["name"]
                icon = tool.get("icon", "🛠️")
                devices = DeviceStorage.get_by_type(
                    {"linux_ops": "linux", "esxi_ops": "esxi", "router_ops": "router",
                     "switch_ops": "switch", "deco_ops": "deco", "commvault_ops": "commvault",
                     "windows_ops": "windows"}.get(name, "")
                )

                st.markdown(f"### {icon} {name}")

                if devices:
                    st.caption(f"✅ {len(devices)} device(s) registered")
                else:
                    st.caption("⚠️ No devices")

                if is_admin:
                    enabled = st.toggle(
                        "Active",
                        value=tool.get("enabled", True),
                        key=f"toggle_builtin_{name}",
                    )
                    if enabled != tool.get("enabled", True):
                        set_builtin_enabled(name, enabled)
                        st.rerun()

                    if st.button("🗑️ Remove", key=f"del_builtin_{name}", use_container_width=True,
                                 help="Remove from system (can be restored later)"):
                        delete_builtin_tool(name)
                        st.rerun()
                else:
                    if tool.get("enabled"):
                        st.success("Active", icon="✅")
                    else:
                        st.error("Inactive", icon="⛔")

    # ── Removed built-in tools (restore section) ──
    if is_admin:
        deleted = get_deleted_builtin_tools()
        if deleted:
            with st.expander(f"♻️ Restore Removed Tools ({len(deleted)})", expanded=False):
                col_all, _ = st.columns([1, 3])
                with col_all:
                    if st.button("♻️ Restore All Defaults", type="primary", use_container_width=True):
                        n = restore_all_builtin_tools()
                        st.success(f"Restored {n} built-in tools.")
                        st.rerun()

                for d in deleted:
                    c1, c2, c3 = st.columns([1, 4, 1])
                    c1.markdown(f"### {d['icon']}")
                    c2.markdown(f"**{d['name']}**\n\n{d['description']}...")
                    if c3.button("Restore", key=f"restore_{d['name']}", use_container_width=True):
                        restore_builtin_tool(d["name"])
                        st.rerun()

    st.divider()

    # ── Custom Tools ──
    st.subheader("🔧 Custom Tools")
    custom_tools = [t for t in all_tools if not t.get("is_builtin")]

    if not custom_tools:
        st.info("No custom tools added yet.")

    for tool in custom_tools:
        tool_id = tool["id"]
        icon = tool.get("icon", "🔧")
        backend_labels = {
            "ssh_command": "SSH Command",
            "http_template": "HTTP Template",
            "python_script": "Python Script",
            "schema_only": "Schema Only",
        }
        backend_label = backend_labels.get(tool.get("execute_backend", ""), tool.get("execute_backend", ""))

        with st.expander(f"{icon} **{tool['name']}** — {backend_label}", expanded=False):
            col_info, col_actions = st.columns([3, 1])

            with col_info:
                st.markdown(f"**Description:** {tool.get('description', '-')}")
                st.markdown(f"**Backend:** `{tool.get('execute_backend', '-')}`")
                st.markdown(f"**Created:** {tool.get('created_at', '-')[:16]}")

                if tool.get("input_schema"):
                    st.json(tool["input_schema"])

                if tool.get("backend_config"):
                    st.markdown("**Backend Configuration:**")
                    st.json(tool["backend_config"])

            with col_actions:
                if is_admin:
                    enabled = st.toggle(
                        "Active", value=tool.get("enabled", True),
                        key=f"toggle_custom_{tool_id}",
                    )
                    if enabled != tool.get("enabled", True):
                        set_custom_enabled(tool_id, enabled)
                        st.rerun()

                    if st.button("🗑️ Delete", key=f"del_{tool_id}", use_container_width=True):
                        delete_custom_tool(tool_id)
                        st.success(f"'{tool['name']}' deleted.")
                        st.rerun()

    st.divider()

    # ── Add New Tool Form ──
    if is_admin:
        st.subheader("➕ Add New Custom Tool")

        with st.form("add_custom_tool", clear_on_submit=True):
            col_name, col_icon = st.columns([3, 1])
            with col_name:
                new_name = st.text_input(
                    "Tool Name (snake_case)",
                    placeholder="example_ops",
                    help="Use lowercase letters, digits, and underscores. e.g. netbox_ops, zabbix_query"
                )
            with col_icon:
                new_icon = st.text_input("Icon", value="🔧", max_chars=2)

            new_desc = st.text_area(
                "Description",
                placeholder="What does this tool do? The AI selects the tool based on this description.",
                height=80,
            )

            new_schema_str = st.text_area(
                "Input Schema (JSON)",
                value='{\n  "type": "object",\n  "properties": {\n    "action": {\n      "type": "string",\n      "description": "The operation to perform"\n    }\n  },\n  "required": ["action"]\n}',
                height=150,
                help="In JSON Schema format. Defines the parameters the AI will use when calling the tool.",
            )

            new_backend = st.selectbox(
                "Execute Backend",
                options=["schema_only", "ssh_command", "http_template", "python_script"],
                format_func=lambda x: {
                    "schema_only": "📝 Schema Only (no backend)",
                    "ssh_command": "🖥️ SSH Command",
                    "http_template": "🌐 HTTP Template",
                    "python_script": "🐍 Python Script",
                }[x],
            )

            # Backend-specific fields
            backend_config = {}

            if new_backend == "ssh_command":
                st.markdown("**SSH Backend Configuration:**")
                ssh_device = st.selectbox(
                    "Device Type",
                    options=list(DEVICE_TYPES.keys()),
                    format_func=lambda x: DEVICE_TYPES[x].get("label", x),
                )
                ssh_template = st.text_input(
                    "Command Template",
                    value="{{action}}",
                    help="{{action}} will be replaced with the command sent by the user.",
                )
                backend_config = {"device_type": ssh_device, "command_template": ssh_template}

            elif new_backend == "http_template":
                st.markdown("**HTTP Backend Configuration:**")
                http_method = st.selectbox("HTTP Method", ["GET", "POST", "PUT", "PATCH", "DELETE"])
                http_url = st.text_input(
                    "URL Template",
                    placeholder="https://api.example.com/v1/{{action}}",
                    help="{{action}} and other input parameters are automatically substituted.",
                )
                http_headers_str = st.text_area(
                    "Headers (JSON, optional)",
                    value='{}',
                    height=60,
                )
                http_body_str = st.text_area(
                    "Body Template (JSON, for POST/PUT)",
                    value='',
                    height=60,
                )
                http_timeout = st.number_input("Timeout (sec)", value=30, min_value=5, max_value=300)
                try:
                    http_headers = json.loads(http_headers_str) if http_headers_str.strip() else {}
                except json.JSONDecodeError:
                    http_headers = {}
                try:
                    http_body = json.loads(http_body_str) if http_body_str.strip() else None
                except json.JSONDecodeError:
                    http_body = http_body_str if http_body_str.strip() else None

                backend_config = {
                    "method": http_method,
                    "url_template": http_url,
                    "headers": http_headers,
                    "body_template": http_body,
                    "timeout": http_timeout,
                }

            elif new_backend == "python_script":
                st.markdown("**Python Script Backend:**")
                uploaded_file = st.file_uploader(
                    "Upload Python Script (.py)",
                    type=["py"],
                    help="The script must contain an `execute(tool_input: dict, connections: dict) -> str` function.",
                )
                if uploaded_file:
                    backend_config = {"script_filename": uploaded_file.name}

            elif new_backend == "schema_only":
                st.info("ℹ️ Only an AI tool definition will be created; an execute backend can be added later.")

            submitted = st.form_submit_button("🚀 Add Tool", type="primary", use_container_width=True)

        if submitted and new_name and new_desc:
            try:
                new_schema = json.loads(new_schema_str)
            except json.JSONDecodeError:
                st.error("❌ Input Schema is not valid JSON!")
                st.stop()

            try:
                # Upload Python script
                if new_backend == "python_script" and uploaded_file:
                    import pathlib
                    custom_dir = pathlib.Path(__file__).parent.parent / "tools" / "custom"
                    custom_dir.mkdir(exist_ok=True)
                    script_path = custom_dir / uploaded_file.name
                    script_path.write_bytes(uploaded_file.getvalue())
                    backend_config = {"script_filename": uploaded_file.name}

                tool_id = add_custom_tool(
                    name=new_name,
                    description=new_desc,
                    input_schema=new_schema,
                    execute_backend=new_backend,
                    backend_config=backend_config,
                    icon=new_icon,
                )
                st.success(f"✅ Tool '{new_name}' added! (ID: {tool_id})")
                st.rerun()
            except ValueError as e:
                st.error(f"❌ {str(e)}")


# ═══════════════════════════════════════════════════════════════════
# TAB 2: GENERATE FROM DOCUMENT
# ═══════════════════════════════════════════════════════════════════

with tab_generate:
    st.markdown("""
    Upload an **API document or CLI reference** — the AI will analyze it and automatically generate MCP tool definitions.
    REST API endpoints, CLI commands, webhook definitions, etc. are supported.
    """)

    if not is_admin:
        st.warning("This feature is only available for admin users.")
        st.stop()

    st.divider()

    # Input method
    input_method = st.radio(
        "Document Input Method",
        ["📄 Upload File", "📝 Paste Text", "🌐 Fetch from URL"],
        horizontal=True,
        key="doc_input_method",
    )

    # Persist doc_text across reruns
    if "doc_text" not in st.session_state:
        st.session_state["doc_text"] = ""

    if input_method == "📄 Upload File":
        # Limits — AI prompt has a 30K char cap, so no point reading more
        MAX_CHARS = 30000
        MAX_PAGES_PDF = 200          # safety cap for huge PDFs
        BIG_FILE_THRESHOLD = 10_000_000   # 10 MB warning

        uploaded_doc = st.file_uploader(
            "Upload Document",
            type=["pdf", "docx", "txt", "md", "html", "pptx"],
            help="REST API documentation, CLI reference, Swagger/OpenAPI spec, etc.",
            key="doc_uploader",
        )
        if uploaded_doc is not None:
            cur_name = st.session_state.get("doc_uploaded_name")
            file_size = len(uploaded_doc.getvalue()) if hasattr(uploaded_doc, "getvalue") else 0

            if cur_name != uploaded_doc.name:
                # Show big file warning
                if file_size > BIG_FILE_THRESHOLD:
                    st.warning(
                        f"⚠️ Large file ({file_size/1024/1024:.1f} MB) — "
                        f"only the first {MAX_PAGES_PDF} pages or {MAX_CHARS:,} characters will be processed. "
                        "Tip: For best results upload only the relevant API reference pages."
                    )

                import tempfile, os as _os, time as _time
                from core.document_processor import extract_text

                # Status widget with progress
                with st.status(f"📄 Processing **{uploaded_doc.name}** ({file_size/1024/1024:.1f} MB)…", expanded=True) as status:
                    progress_placeholder = st.empty()
                    progress_bar = None
                    suffix = "." + uploaded_doc.name.rsplit(".", 1)[-1].lower()

                    with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
                        tmp.write(uploaded_doc.getvalue())
                        tmp_path = tmp.name

                    def _on_progress(current, total):
                        nonlocal progress_bar
                        if progress_bar is None:
                            progress_bar = progress_placeholder.progress(0, text=f"Reading pages…")
                        pct = min(int(current / total * 100), 100) if total else 0
                        progress_bar.progress(pct, text=f"Reading page {current} / {total}")

                    try:
                        start = _time.time()
                        text = extract_text(
                            tmp_path,
                            max_pages=MAX_PAGES_PDF,
                            max_chars=MAX_CHARS,
                            progress_cb=_on_progress,
                        )
                        elapsed = _time.time() - start
                        st.session_state["doc_text"] = text
                        st.session_state["doc_uploaded_name"] = uploaded_doc.name
                        progress_placeholder.empty()
                        status.update(
                            label=f"✅ **{uploaded_doc.name}** — {len(text):,} chars extracted in {elapsed:.1f}s",
                            state="complete",
                            expanded=False,
                        )
                    except Exception as e:
                        progress_placeholder.empty()
                        status.update(label=f"❌ Failed to read document", state="error", expanded=True)
                        st.error(f"❌ Could not read document: {type(e).__name__}: {e}")
                    finally:
                        try:
                            _os.unlink(tmp_path)
                        except Exception:
                            pass
            else:
                st.caption(f"✓ Loaded: {uploaded_doc.name} ({len(st.session_state['doc_text']):,} chars)")

    elif input_method == "📝 Paste Text":
        pasted = st.text_area(
            "API/CLI Document Text",
            value=st.session_state.get("doc_text", ""),
            height=300,
            placeholder="Paste REST API endpoints, CLI commands, or document text here...",
            key="doc_text_paste",
        )
        st.session_state["doc_text"] = pasted

    elif input_method == "🌐 Fetch from URL":
        doc_url = st.text_input(
            "Document URL",
            placeholder="https://docs.example.com/api-reference",
            key="doc_url_input",
        )
        if doc_url and st.button("🔗 Fetch from URL", key="fetch_url_btn"):
            with st.spinner(f"Fetching {doc_url}…"):
                try:
                    from core.document_processor import extract_from_url
                    import tempfile
                    tmp_dir = tempfile.mkdtemp()
                    _, fetched = extract_from_url(doc_url, tmp_dir)
                    st.session_state["doc_text"] = fetched
                    st.success(f"✅ URL read — {len(fetched)} characters")
                except Exception as e:
                    st.error(f"❌ Could not read URL: {e}")

    doc_text = st.session_state.get("doc_text", "")

    # Clear button
    if doc_text:
        col_clear, _ = st.columns([1, 4])
        with col_clear:
            if st.button("🗑️ Clear Document", key="clear_doc"):
                st.session_state.pop("doc_text", None)
                st.session_state.pop("doc_uploaded_name", None)
                st.session_state.pop("generated_tools", None)
                st.rerun()

    if doc_text:
        with st.expander(f"📖 Document Preview ({len(doc_text)} chars)", expanded=False):
            st.text(doc_text[:3000] + ("..." if len(doc_text) > 3000 else ""))

        st.divider()

        # Generation settings
        col_mode, col_generate = st.columns([2, 1])
        with col_mode:
            gen_mode = st.radio(
                "Generation Mode",
                ["single", "multi"],
                format_func=lambda x: {
                    "single": "🎯 Single Tool — AI selects endpoint via action parameter",
                    "multi": "📦 Multiple Tools — Separate MCP tool for each endpoint/command",
                }[x],
                key="gen_mode_radio",
            )

        with col_generate:
            st.write("")
            generate_btn = st.button("🤖 Generate with AI", type="primary", use_container_width=True, key="gen_ai_btn")

        if generate_btn:
            if not doc_text.strip():
                st.error("❌ Document text is empty.")
            else:
                with st.spinner("🤖 AI is analyzing the document and generating tool definitions…"):
                    try:
                        generated = generate_tools_from_doc(doc_text, mode=gen_mode)
                        if not generated:
                            st.warning("⚠️ AI returned no tools. Try a different document or generation mode.")
                        else:
                            st.session_state["generated_tools"] = generated
                            st.success(f"✅ Generated {len(generated)} tool(s)!")
                            st.rerun()
                    except Exception as e:
                        st.error(f"❌ Generation failed: {type(e).__name__}: {e}")
                        with st.expander("Show troubleshooting tips"):
                            st.markdown(
                                "- Check that an AI provider is configured "
                                "(Proxy Settings → 📊 Status tab)\n"
                                "- Try a smaller document (max 30K characters)\n"
                                "- If using Ollama, ensure the model supports JSON output\n"
                                "- Try a stronger model (e.g. claude-opus, gpt-4o)"
                            )

    # Show generated tools
    generated = st.session_state.get("generated_tools", [])
    if generated:
        st.divider()
        st.subheader(f"🎉 {len(generated)} Tool(s) Generated")
        st.info("Review and approve. You can add each tool individually or save all at once.")

        # Save All button
        if st.button("✅ Save All", type="primary"):
            added = 0
            for t in generated:
                try:
                    add_custom_tool(
                        name=t["name"],
                        description=t.get("description", ""),
                        input_schema=t.get("input_schema", {}),
                        execute_backend=t.get("execute_backend", "schema_only"),
                        backend_config=t.get("backend_config", {}),
                        icon="🔧",
                    )
                    added += 1
                except ValueError as e:
                    st.warning(f"⚠️ Could not add '{t['name']}': {e}")
            if added:
                st.success(f"✅ {added} tool(s) saved!")
                st.session_state.pop("generated_tools", None)
                st.rerun()

        # Show each tool
        for i, t in enumerate(generated):
            with st.expander(f"🔧 **{t.get('name', '?')}** — {t.get('execute_backend', '?')}", expanded=True):
                col_info, col_action = st.columns([4, 1])

                with col_info:
                    st.markdown(f"**Description:** {t.get('description', '-')}")
                    st.markdown(f"**Backend:** `{t.get('execute_backend', '-')}`")

                    if t.get("input_schema"):
                        st.markdown("**Input Schema:**")
                        st.json(t["input_schema"])

                    if t.get("backend_config"):
                        st.markdown("**Backend Config:**")
                        st.json(t["backend_config"])

                with col_action:
                    if st.button("✅ Add", key=f"add_gen_{i}", use_container_width=True):
                        try:
                            add_custom_tool(
                                name=t["name"],
                                description=t.get("description", ""),
                                input_schema=t.get("input_schema", {}),
                                execute_backend=t.get("execute_backend", "schema_only"),
                                backend_config=t.get("backend_config", {}),
                                icon="🔧",
                            )
                            st.success(f"✅ '{t['name']}' added!")
                            st.rerun()
                        except ValueError as e:
                            st.error(f"❌ {e}")

                    if st.button("🗑️ Skip", key=f"skip_gen_{i}", use_container_width=True):
                        generated.pop(i)
                        st.session_state["generated_tools"] = generated
                        st.rerun()


# ═══════════════════════════════════════════════════════════════════
# TAB 3: TOOL DETAILS
# ═══════════════════════════════════════════════════════════════════

with tab_details:
    st.markdown("""
    **MCP (Model Context Protocol)** provides tool definitions that allow the AI model to interact with the outside world.
    Each MCP tool informs the AI model about which operations can be performed and which parameters are required.
    """)

    st.divider()

    TOOL_DETAILS = {
        "linux_ops": {
            "icon": "🐧", "name": "Linux Server", "protocol": "SSH (TCP/22)",
            "library": "Paramiko", "auth": "Username + Password", "device_type": "linux",
            "capabilities": [
                "Run bash commands", "Query system information",
                "Package management (apt)", "Service management (systemctl)",
                "Log analysis", "File management",
            ]
        },
        "esxi_ops": {
            "icon": "☁️", "name": "VMware ESXi", "protocol": "SSH (TCP/22)",
            "library": "Paramiko", "auth": "Username + Password", "device_type": "esxi",
            "capabilities": [
                "esxcli commands", "VM listing and management",
                "Datastore information", "Network configuration", "Host status monitoring",
            ]
        },
        "router_ops": {
            "icon": "🌐", "name": "TP-Link ER605 Router", "protocol": "SSH (TCP/22)",
            "library": "Paramiko", "auth": "Username + Password", "device_type": "router",
            "capabilities": [
                "Network analysis (ping, traceroute)", "Routing table",
                "NAT/Firewall rules", "WAN status", "DHCP information",
            ]
        },
        "switch_ops": {
            "icon": "🔌", "name": "TP-Link TL-SG1016PE Switch", "protocol": "HTTP (TCP/80)",
            "library": "Requests", "auth": "Web Session (Cookie)", "device_type": "switch",
            "capabilities": [
                "Port status and statistics", "PoE power management",
                "VLAN configuration", "Port mirroring", "Trunk/LAG settings",
            ]
        },
        "deco_ops": {
            "icon": "📶", "name": "TP-Link Deco Mesh Wi-Fi", "protocol": "HTTP (TCP/80)",
            "library": "Requests", "auth": "Web Session (Token)", "device_type": "deco",
            "capabilities": [
                "Connected device list", "Mesh node status",
                "Guest network management", "Wi-Fi settings", "Firmware information",
            ]
        },
        "commvault_ops": {
            "icon": "💾", "name": "Commvault Backup", "protocol": "HTTPS REST API",
            "library": "Requests", "auth": "AuthToken (Login API)", "device_type": "commvault",
            "capabilities": [
                "Client listing and details", "Job monitoring", "Job cancellation",
                "Alert listing", "Storage pool information", "Backup initiation",
            ]
        },
        "windows_ops": {
            "icon": "🪟", "name": "Windows Server", "protocol": "SSH (TCP/22)",
            "library": "OpenSSH + Paramiko", "auth": "SSH Key / Password", "device_type": "windows",
            "capabilities": [
                "PowerShell commands", "Service management",
                "Event log analysis", "Disk and memory monitoring",
                "Windows Update management", "Active Directory queries",
            ]
        },
    }

    active_tools = get_active_tools()

    for tool_name, detail in TOOL_DETAILS.items():
        with st.expander(f"{detail['icon']} **{detail['name']}** — `{tool_name}`", expanded=False):
            col1, col2 = st.columns(2)

            with col1:
                st.subheader("Capabilities")
                for cap in detail["capabilities"]:
                    st.markdown(f"- {cap}")

            with col2:
                mcp_schema = next((t for t in active_tools if t["name"] == tool_name), None)
                if mcp_schema:
                    st.subheader("MCP Schema")
                    st.json(mcp_schema)
                else:
                    st.warning("This tool is currently disabled.")

            devices = DeviceStorage.get_by_type(detail["device_type"])
            if devices:
                st.subheader("Registered Devices")
                for d in devices:
                    st.markdown(f"- **{d['name']}** (`{d['ip']}`)")

    # Custom tool details
    custom_tools = [t for t in get_all_tools_with_status() if not t.get("is_builtin")]
    if custom_tools:
        st.divider()
        st.subheader("🔧 Custom Tool Details")
        for tool in custom_tools:
            with st.expander(f"{tool.get('icon', '🔧')} **{tool['name']}**", expanded=False):
                st.markdown(f"**Description:** {tool.get('description', '-')}")
                if tool.get("input_schema"):
                    st.json(tool["input_schema"])


# ═══════════════════════════════════════════════════════════════════
# TAB 4: ARCHITECTURE
# ═══════════════════════════════════════════════════════════════════

with tab_arch:
    st.header("🏗️ MCP Architecture")
    st.markdown("""
```
                          ┌──────────────────┐
                          │   Claude AI       │
                          │   (Anthropic)     │
                          └────────┬─────────┘
                                   │ API
                          ┌────────▼─────────┐
                          │    AI Proxy       │
                          │  (Rate Limit +    │
                          │   Data Filter)    │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │   Agent Loop      │
                          │  (Tool Dispatch)  │
                          └────────┬─────────┘
                                   │
                          ┌────────▼─────────┐
                          │  Tool Registry    │
                          │  (Dynamic)        │
                          └────────┬─────────┘
                                   │
        ┌──────────┬──────────┼──────────┬──────────┬──────────┐
        ▼          ▼          ▼          ▼          ▼          ▼
  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌───────────┐
  │linux_ops │ │esxi_ops  │ │router_ops│ │switch_ops│ │deco_ops  │ │windows_ops│
  │ SSH/22   │ │ SSH/22   │ │ SSH/22   │ │ HTTP/80  │ │ HTTP/80  │ │ SSH/22    │
  └──────────┘ └──────────┘ └──────────┘ └──────────┘ └──────────┘ └───────────┘
                                   │
                          ┌────────▼─────────┐
                          │  Custom Tools     │
                          │  SSH / HTTP /     │
                          │  Python Script    │
                          └──────────────────┘
```
    """)

    st.divider()

    st.header("🔒 Data Filtering")
    st.markdown("""
    The AI Proxy automatically masks sensitive data before sending to the Claude API and in SSH output.
    """)

    col1, col2 = st.columns(2)
    with col1:
        st.subheader("📤 Outgoing Message Filters")
        filters = [
            ("API Key", "sk-ant-api..., sk-...", "***API_KEY_MASKED***"),
            ("Bearer Token", "Bearer ...", "***TOKEN_MASKED***"),
            ("Password", "password=..., pwd=...", "***PASSWORD_MASKED***"),
            ("Private Key", "-----BEGIN PRIVATE KEY-----", "***PRIVATE_KEY_MASKED***"),
            ("AWS Key", "AKIA...", "***AWS_KEY_MASKED***"),
            ("Connection String", "mysql://user:pass@", "***USER***:***PASS***@"),
        ]
        for label, pattern, mask in filters:
            st.markdown(f"- **{label}** (`{pattern}`) → `{mask}`")

    with col2:
        st.subheader("📥 SSH Output Filters")
        ssh_filters = [
            ("Shadow Hash", "/etc/shadow lines", "***HASH_MASKED***"),
            ("DB Access", "MySQL/PostgreSQL access denied", "Host is masked"),
            ("Session ID", "Long hex token/session ID", "***TOKEN_MASKED***"),
            ("Config Secret", "*_KEY=, *_PASSWORD=", "***MASKED***"),
        ]
        for label, desc, mask in ssh_filters:
            st.markdown(f"- **{label}** ({desc}) → `{mask}`")

    st.info("💡 Filtering results are logged in `logs/proxy.log` and `logs/audit.log`.")
