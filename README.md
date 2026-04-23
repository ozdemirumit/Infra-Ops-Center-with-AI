# 🛡️ Infra Ops Center with AI

AI-powered autonomous infrastructure management center. Give natural language commands — AI connects to your servers via SSH/HTTP/REST and completes the tasks.

![Python](https://img.shields.io/badge/Python-3.10+-blue?logo=python)
![Streamlit](https://img.shields.io/badge/Streamlit-1.30+-red?logo=streamlit)
![License](https://img.shields.io/badge/License-MIT-green)

## ✨ Key Features

### 🤖 AI Agentic Loop
- **Multi-model support** — Anthropic Claude, OpenAI GPT, Google Gemini, Ollama (local/self-hosted)
- **Autonomous operation** — AI analyzes commands, calls tools, interprets results, retries on failure
- **ReAct planning** — For risky tasks, AI generates a step-by-step plan and waits for approval
- **Inline command approval** — Change commands (apt upgrade, reboot, etc.) require in-chat confirmation

### 🔧 Dynamic MCP Tool System
- **7 built-in tools** — Linux SSH, Windows SSH, VMware ESXi, Router, Switch, Deco Mesh, Commvault Backup
- **Dynamic tool registry** — Add/remove/enable/disable tools from the UI at runtime
- **Custom tool backends** — SSH command templates, HTTP request templates, Python script uploads
- **📄 Document-to-Tool Generation** — Upload REST API docs, CLI references, or any technical documentation and AI automatically generates MCP tool definitions with proper schemas and backend configurations
- **Tool ↔ Device mapping** — New tools automatically create corresponding device types

### 📚 RAG Knowledge Base & Ollama Model Training
- **TF-IDF knowledge base** — Upload documents (PDF, DOCX, TXT, MD, PPTX), AI uses them as context during conversations
- **Ollama Modelfile generation** — Select uploaded documents to create custom Ollama models trained on your infrastructure documentation
- **Session memory** — AI learns from completed tasks and applies knowledge to future operations
- **Runbook generation** — Successful operations are automatically saved as reusable runbooks
- **Web URL ingestion** — Fetch and index content directly from web pages

### 📊 Autonomous Monitoring
- **Per-metric independent scheduling** — Each metric (disk, RAM, CPU) runs on its own configurable interval
- **Custom metric support** — Define custom metrics with any SSH command
- **Automatic AI incidents** — When thresholds are exceeded, AI automatically investigates and runs remediation
- **Manual AI trigger** — Click "🤖 AI Investigate" to start an AI investigation on demand

### 🔒 Security
- **Bcrypt authentication** — Role-based access control (Admin / Viewer)
- **Fernet (AES-128) encryption** — Device passwords stored encrypted on disk
- **Data filtering** — API keys, passwords, tokens, private keys automatically masked before sending to AI
- **Audit logging** — All operations logged in Syslog RFC 5424 format

## 🏗️ Architecture

```
User -> [Auth] -> [Streamlit UI] -> [Agent Loop] -> [AI Provider] -> Claude / GPT / Gemini / Ollama
                       |                |
                [Device Storage]   [Tool Registry]
                (Fernet + JSON)    (Dynamic MCP)
                                   /  /   |   \  \
                                SSH Win Switch Deco CV  + Custom Tools
```

### AI Provider Options

| Mode | Description |
|------|-------------|
| **Direct API** | Connect directly to Anthropic/OpenAI/Gemini/Ollama APIs with your own keys |
| **[onPrem LLM Sentinel](https://github.com/ozdemirumit/LLM-Sentinel)** *(optional)* | Enterprise proxy with rate limiting, key rotation, circuit breaking, cost tracking, content filtering |

## 🚀 Quick Start

### Requirements
- Python 3.10+
- At least one AI provider API key (Anthropic, OpenAI, Gemini) or a local Ollama instance

### Installation

```bash
# 1. Clone
git clone https://github.com/ozdemirumit/Infra-Ops-Center-with-AI.git
cd Infra-Ops-Center-with-AI

# 2. Virtual environment
python -m venv venv
venv\Scripts\activate        # Windows
source venv/bin/activate     # Linux/Mac

# 3. Dependencies
pip install -r requirements.txt

# 4. Initial setup (creates .env with admin account, encryption keys, API config)
python setup_env.py

# 5. Run
python -m streamlit run Home.py
```

The `setup_env.py` script will interactively:
- Create an admin account with a bcrypt-hashed password
- Generate a Fernet encryption key for device storage
- Configure your AI provider (Direct API or onPrem LLM Sentinel proxy)
- Write all settings to `.env`

### Manual Configuration

If you prefer manual setup, copy `.env.example` to `.env` and fill in the values:

```bash
cp .env.example .env
```

## ⚙️ Configuration (.env)

| Variable | Default | Description |
|----------|---------|-------------|
| `ANTHROPIC_API_KEY` | — | Anthropic API key (direct mode) |
| `OPENAI_API_KEY` | — | OpenAI API key (direct mode) |
| `DEFAULT_PROVIDER` | `anthropic` | AI provider: anthropic / openai / gemini / ollama |
| `DEFAULT_MODEL` | `claude-sonnet-4-5` | Default model name |
| `PROXY_ENABLED` | `false` | Use onPrem LLM Sentinel proxy |
| `PROXY_HOST` | `localhost` | Proxy host |
| `PROXY_PORT` | `8765` | Proxy port |
| `PROXY_API_KEY` | — | Proxy Bearer token |
| `SSH_TIMEOUT` | `20` | SSH connection timeout (sec) |
| `SSH_EXEC_TIMEOUT` | `1800` | Command execution timeout (sec) |
| `MAX_AGENT_STEPS` | `25` | Max agentic loop iterations |
| `APP_USERNAME` | `admin` | Login username |
| `APP_PASSWORD_HASH` | — | Bcrypt hash (generated by setup_env.py) |
| `DEVICE_ENCRYPTION_KEY` | — | Fernet key (generated by setup_env.py) |
| `RAG_ENABLED` | `true` | Enable knowledge base |

## 🖥️ Pages

| Page | Description |
|------|-------------|
| **Home** | AI chat interface, task management, inline command approval |
| **Device Management** | Add/edit/delete servers and devices (passwords encrypted) |
| **MCP Tools** | Dynamic tool registry, custom tool creation, document-to-tool generation |
| **Documents** | RAG knowledge base, document upload, URL fetching, Ollama model training |
| **Proxy Settings** | AI Proxy / direct API connection settings |
| **Server Inventory** | Filterable device inventory view with hardware details |
| **Monitoring** | Per-metric dashboard, AI investigation, custom metrics |

## 📄 Document-to-Tool Generation

One of the most powerful features — upload any API documentation and AI creates MCP tools automatically:

1. Go to **MCP Tools** → **Generate from Document** tab
2. Upload a PDF/DOCX/TXT/MD file, paste text, or fetch from a URL
3. Choose generation mode:
   - **Single Tool** — One tool that uses `action` parameter to select the right endpoint
   - **Multi Tool** — Separate MCP tools for each API endpoint
4. AI analyzes the documentation and generates tool definitions with proper schemas
5. Review, edit, and save the generated tools

## 📚 Ollama Model Training

Create custom Ollama models trained on your infrastructure documentation:

1. Go to **Documents** page
2. Upload your infrastructure docs, runbooks, or knowledge articles
3. Select documents to include in the training set
4. Choose a base Ollama model (Qwen, LLaMA, Command-R, etc.)
5. Generate and push the Modelfile to your Ollama instance

The resulting model will have deep knowledge of your specific infrastructure, naming conventions, and operational procedures.

## 🔧 Built-in MCP Tools

| Tool | Protocol | Description |
|------|----------|-------------|
| `linux_ops` | SSH | Bash commands on Linux servers |
| `windows_ops` | SSH | PowerShell on Windows Server |
| `esxi_ops` | SSH | VMware ESXi management |
| `router_ops` | SSH | Router network commands |
| `switch_ops` | HTTP | Switch web API (ports, VLANs, PoE) |
| `deco_ops` | HTTP | Deco Mesh Wi-Fi management |
| `commvault_ops` | REST | Commvault backup management |

Custom tools can be added via the MCP Tools page with SSH, HTTP, or Python script backends.

## 📁 Project Structure

```
Home.py                        # Main entry point (Streamlit)
setup_env.py                   # Initial setup script (generates .env)
config/settings.py             # Central settings
auth/authenticator.py          # Login, session, roles
proxy/
  ai_proxy.py                  # AI Proxy / Direct API client
  data_filter.py               # Sensitive data masking
tools/
  registry.py                  # Dynamic Tool Registry
  ssh_tool.py                  # Linux/ESXi/Router SSH
  windows_tool.py              # Windows PowerShell SSH
  switch_tool.py               # Switch HTTP API
  deco_tool.py                 # Deco Mesh HTTP API
  commvault_tool.py            # Commvault REST API
  custom/                      # User-uploaded Python scripts
core/
  agent_loop.py                # Agentic loop + inline approval
  planner.py                   # ReAct plan generator
  monitor.py                   # Autonomous monitoring (APScheduler)
  incident_manager.py          # Auto incident + AI remediation
  headless_loop.py             # UI-less agent loop
  rag_engine.py                # TF-IDF RAG engine
  session_learner.py           # Cross-session learning
  document_processor.py        # PDF/DOCX/TXT/PPTX reader
  runbook_saver.py             # Runbook generator
sessions/storage.py            # Task session management
devices/storage.py             # Encrypted device storage
ui/
  sidebar.py                   # Sidebar control panel
  chat.py                      # Chat message renderer
  style.css                    # Futuristic cyberpunk CSS theme
pages/                         # Streamlit multi-page app
knowledge_base/                # RAG document storage
logging_config/logger.py       # Syslog RFC 5424 logging
```

## 🐳 Docker Deployment

```bash
# Build and run with docker-compose
docker-compose up -d

# Or build manually
docker build -t infra-ops-center .
docker run -p 8501:8501 --env-file .env infra-ops-center
```

## 🧪 Testing

```bash
pip install pytest
python -m pytest tests/ -v
```

24 unit tests cover the data filter (JWT, K8s secrets, API keys, private keys) and atomic JSON I/O.

## 🛡️ Production-Readiness Features

- **Atomic JSON writes** — race-condition-proof storage via `os.replace()` + file locks
- **SSH connection pooling** — reuses authenticated sessions for 5 min (eliminates handshake overhead)
- **RAG singleton cache** — TF-IDF vectorizer loaded once per process
- **Actionable error messages** — SSH errors map to specific remediations
- **Password scrubbing** — SSH passwords cleared from memory after auth
- **Input validation** — device names, IPs, hostnames, usernames, commands
- **Startup validation** — warns if encryption key or auth hash is missing
- **Destructive action confirmations** — delete buttons require double-click
- **Log rotation** — automatic cleanup of logs older than 30 days
- **Data filter** — masks JWT, K8s secrets, PGP keys, AWS/GCP/Azure credentials

## 📝 License

MIT License
