"""
Central configuration module.
All settings are loaded from the .env file.
"""

import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    """Application-wide settings."""

    MAX_AGENT_STEPS: int = int(os.getenv("MAX_AGENT_STEPS", "25"))

    # --- AI Proxy (optional — if not set, direct API is used) ---
    PROXY_HOST: str = os.getenv("PROXY_HOST", "localhost")
    PROXY_PORT: int = int(os.getenv("PROXY_PORT", "8765"))
    PROXY_API_KEY: str = os.getenv("PROXY_API_KEY", "")
    PROXY_ENABLED: bool = os.getenv("PROXY_ENABLED", "true").lower() == "true"

    # --- Direct API keys (used when proxy is disabled) ---
    ANTHROPIC_API_KEY: str = os.getenv("ANTHROPIC_API_KEY", "")
    OPENAI_API_KEY: str = os.getenv("OPENAI_API_KEY", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    DEFAULT_MODEL: str = os.getenv("DEFAULT_MODEL", "claude-sonnet-4-5")
    DEFAULT_PROVIDER: str = os.getenv("DEFAULT_PROVIDER", "anthropic")

    # --- Ollama (local model server) ---
    OLLAMA_URL: str = os.getenv("OLLAMA_URL", "http://localhost:11434")
    OLLAMA_MODEL: str = os.getenv("OLLAMA_MODEL", "llama3.1")

    # --- SSH ---
    SSH_TIMEOUT: int = int(os.getenv("SSH_TIMEOUT", "20"))
    SSH_BANNER_TIMEOUT: int = int(os.getenv("SSH_BANNER_TIMEOUT", "20"))
    SSH_EXEC_TIMEOUT: int = int(os.getenv("SSH_EXEC_TIMEOUT", "1800"))
    SSH_OUTPUT_LIMIT: int = int(os.getenv("SSH_OUTPUT_LIMIT", "5000"))

    # --- Authentication ---
    APP_USERNAME: str = os.getenv("APP_USERNAME", "admin")
    APP_PASSWORD_HASH: str = os.getenv("APP_PASSWORD_HASH", "")
    ADMIN_USERS: list = os.getenv("ADMIN_USERS", "admin").split(",")

    # --- Device Storage ---
    DEVICE_ENCRYPTION_KEY: str = os.getenv("DEVICE_ENCRYPTION_KEY", "")

    # --- Commvault ---
    COMMVAULT_TIMEOUT: int = int(os.getenv("COMMVAULT_TIMEOUT", "30"))

    # --- RAG / Knowledge Base ---
    KNOWLEDGE_BASE_DIR: str = os.getenv(
        "KNOWLEDGE_BASE_DIR",
        str(Path(__file__).resolve().parent.parent / "knowledge_base")
    )
    RAG_CHUNK_SIZE: int = int(os.getenv("RAG_CHUNK_SIZE", "1000"))
    RAG_CHUNK_OVERLAP: int = int(os.getenv("RAG_CHUNK_OVERLAP", "200"))
    RAG_TOP_K: int = int(os.getenv("RAG_TOP_K", "5"))
    RAG_ENABLED: bool = os.getenv("RAG_ENABLED", "true").lower() == "true"

    # --- System Prompt (dynamic — built from active tools) ---
    _SYSTEM_PROMPT_ENV: str = os.getenv("SYSTEM_PROMPT", "")

    _SYSTEM_PROMPT_BASE: str = (
        "You are a senior Systems and Network Engineer. "
        "Use the available MCP tools to complete tasks:\n\n"
    )

    _SYSTEM_PROMPT_DIRECTIVES: str = (
        "\n## Tool Usage Directives\n"
        "1. Before diagnosing, COLLECT data using tools — do not guess.\n"
        "2. If multiple steps are needed, call tools sequentially; analyze each result before proceeding.\n"
        "3. Choose the right tool for the target: Linux → linux_ops, Windows → windows_ops, ESXi → esxi_ops.\n"
        "4. Calling linux_ops/windows_ops WITHOUT target_host runs on ALL servers — be careful.\n"
        "5. For apt upgrade or package installs, ALWAYS use:\n"
        "   sudo DEBIAN_FRONTEND=noninteractive apt-get -y "
        "-o Dpkg::Options::='--force-confdef' -o Dpkg::Options::='--force-confold' upgrade\n"
        "6. When tasks are complete, provide a clear and concise summary.\n"
        "7. If you encounter an error, analyze the cause and retry with an alternative command."
    )

    @property
    def SYSTEM_PROMPT(self) -> str:
        """Dynamic system prompt — updates based on active tools."""
        if self._SYSTEM_PROMPT_ENV:
            return self._SYSTEM_PROMPT_ENV
        from tools.registry import get_dynamic_system_prompt_section
        return (
            self._SYSTEM_PROMPT_BASE
            + get_dynamic_system_prompt_section()
            + self._SYSTEM_PROMPT_DIRECTIVES
        )

    @property
    def TOOLS(self) -> list:
        """Active tool definitions from the registry."""
        from tools.registry import get_active_tools
        return get_active_tools()


settings = Settings()
