"""
Data Filtering module.
Masks sensitive data before sending to the Claude API.
Cleans sensitive information from SSH outputs.
"""

import re
from logging_config.logger import get_logger, audit_log, AuditEvent

logger = get_logger("proxy")

# ============================================================
# 1. OUTGOING FILTER — Masking before sending to Claude
# ============================================================

# Sensitive data patterns to detect (regex)
# Format: (label, pattern, replacement) or (label, pattern, replacement, flags)
SENSITIVE_PATTERNS = [
    # ── API Keys ──
    ("Anthropic API Key", r"sk-ant-api\d{2}-[A-Za-z0-9_\-]{40,}", "***API_KEY_MASKED***"),
    ("OpenAI API Key", r"sk-proj-[A-Za-z0-9_\-]{20,}", "***API_KEY_MASKED***"),
    ("Generic API Key", r"sk-[A-Za-z0-9_\-]{20,}", "***API_KEY_MASKED***"),
    ("API Key Assignment", r"api[_-]?key\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{20,})['\"]?", "api_key=***MASKED***"),
    ("Google API Key", r"AIza[0-9A-Za-z_\-]{35}", "***GOOGLE_API_KEY_MASKED***"),
    ("GitHub Token", r"(ghp|gho|ghu|ghs|ghr)_[A-Za-z0-9]{36}", "***GITHUB_TOKEN_MASKED***"),
    ("Slack Token", r"xox[baprs]-[A-Za-z0-9\-]{10,}", "***SLACK_TOKEN_MASKED***"),
    ("Stripe Key", r"(sk|pk)_(test|live)_[A-Za-z0-9]{24,}", "***STRIPE_KEY_MASKED***"),

    # ── JWT Tokens ──
    ("JWT Token", r"eyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+", "***JWT_MASKED***"),

    # ── Bearer / Authorization tokens ──
    ("Bearer Token", r"(Bearer\s+)[A-Za-z0-9_\-\.]{20,}", r"\1***TOKEN_MASKED***"),
    ("Authorization Header", r"(Authorization:\s*)[^\s\n]{20,}", r"\1***AUTH_MASKED***"),
    ("Basic Auth", r"Basic\s+[A-Za-z0-9+/]{20,}={0,2}", "Basic ***BASIC_AUTH_MASKED***"),

    # ── Passwords (various formats) ──
    ("Password", r"(password|passwd|pwd|şifre)\s*[=:]\s*['\"]?([^\s'\"\n]{3,})['\"]?",
     r"\1=***PASSWORD_MASKED***", re.IGNORECASE),
    ("Password Property", r"(\w*\.password)\s*[=:]\s*['\"]?([^\s'\"\n]{3,})['\"]?",
     r"\1=***PASSWORD_MASKED***", re.IGNORECASE),

    # ── Connection strings ──
    ("Connection String", r"(mysql|postgres|postgresql|mongodb|redis|ssh|ftp|sftp|amqp|amqps)://[^\s@]+:([^\s@]+)@",
     r"\1://***USER***:***PASS***@"),
    ("JDBC URL", r"(jdbc:\w+://[^\s?]+)\?([^\s]*(?:password|pwd)=)([^\s&]+)",
     r"\1?\2***MASKED***", re.IGNORECASE),

    # ── Private keys (all types) ──
    ("Private Key",
     r"-----BEGIN\s+(RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----[\s\S]*?-----END\s+(RSA |EC |DSA |OPENSSH |ENCRYPTED )?PRIVATE KEY-----",
     "***PRIVATE_KEY_MASKED***"),
    ("PGP Private Key",
     r"-----BEGIN\s+PGP\s+PRIVATE\s+KEY\s+BLOCK-----[\s\S]*?-----END\s+PGP\s+PRIVATE\s+KEY\s+BLOCK-----",
     "***PGP_PRIVATE_KEY_MASKED***"),
    ("SSH Private Key Body",
     r"ssh-rsa\s+[A-Za-z0-9+/=]{100,}",
     "ssh-rsa ***SSH_KEY_MASKED***"),

    # ── Cloud credentials ──
    ("AWS Access Key", r"AKIA[0-9A-Z]{16}", "***AWS_KEY_MASKED***"),
    ("AWS Secret Key", r"aws_secret_access_key\s*[=:]\s*['\"]?([A-Za-z0-9/+=]{40})['\"]?",
     "aws_secret_access_key=***AWS_SECRET_MASKED***", re.IGNORECASE),
    ("Azure Storage Key", r"DefaultEndpointsProtocol=https;AccountName=[^;]+;AccountKey=[^;]+",
     "***AZURE_STORAGE_KEY_MASKED***"),
    ("GCP Service Account", r"\"private_key\"\s*:\s*\"-----BEGIN[^\"]+\"",
     "\"private_key\": \"***GCP_KEY_MASKED***\""),

    # ── Kubernetes secrets (base64) ──
    ("K8s Secret Token",
     r"(token|password|ca\.crt|tls\.key|\.dockerconfigjson)\s*:\s*([A-Za-z0-9+/]{40,}={0,2})",
     r"\1: ***K8S_SECRET_MASKED***", re.IGNORECASE),

    # ── Docker credentials ──
    ("Docker Auth", r"\"auth\"\s*:\s*\"[A-Za-z0-9+/=]{20,}\"",
     "\"auth\": \"***DOCKER_AUTH_MASKED***\""),

    # ── Generic base64 secrets (long strings likely to be secrets) ──
    # ── .env file secret assignments (general) ──
    ("Env Secret",
     r"(ANTHROPIC_API_KEY|OPENAI_API_KEY|GEMINI_API_KEY|SECRET_KEY|ENCRYPTION_KEY|DEVICE_ENCRYPTION_KEY|DB_PASSWORD|APP_PASSWORD_HASH|PROXY_API_KEY|GITHUB_TOKEN|GITLAB_TOKEN|DOCKER_PASSWORD)\s*=\s*([^\s\n]+)",
     r"\1=***MASKED***"),

    # ── Credit card numbers (PAN) ──
    ("Credit Card", r"\b(?:4\d{3}|5[1-5]\d{2}|3[47]\d{2}|6011)[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}\b",
     "***CREDIT_CARD_MASKED***"),
]

# Compiled regexes (compiled once for performance)
# Tuple: (label, compiled_regex, replacement)
_compiled_patterns = []
for pattern_tuple in SENSITIVE_PATTERNS:
    if len(pattern_tuple) == 4:
        label, pattern, replacement, flags = pattern_tuple
        _compiled_patterns.append((label, re.compile(pattern, flags), replacement))
    else:
        label, pattern, replacement = pattern_tuple
        _compiled_patterns.append((label, re.compile(pattern), replacement))


def sanitize_outgoing(text: str) -> tuple:
    """
    Masks sensitive data from text to be sent to Claude/AI.
    Returns: (masked_text, list_of_matched_labels)
    """
    if not text:
        return text, []

    result = text
    matched_labels = []

    for label, compiled_re, replacement in _compiled_patterns:
        new_result = compiled_re.sub(replacement, result)
        if new_result != result:
            matched_labels.append(label)
        result = new_result

    if matched_labels:
        labels_str = ", ".join(matched_labels)
        logger.info(f"FILTER | {len(matched_labels)} sensitive data items masked in outgoing message: [{labels_str}]")
        audit_log(AuditEvent.DATA_FILTERED, detail=f"Outgoing: {labels_str}", extra={"direction": "outgoing", "count": len(matched_labels), "types": matched_labels})

    return result, matched_labels


# ============================================================
# 3. SSH OUTPUT FILTER — Cleaning tool results
# ============================================================

# Additional patterns to clean from SSH outputs
SSH_OUTPUT_PATTERNS = [
    # /etc/shadow lines (hashed passwords)
    ("Shadow Hash", re.compile(r"^([a-zA-Z0-9_-]+):(\$\d\$[^\s:]+):", re.MULTILINE),
     r"\1:***HASH_MASKED***:"),

    # MySQL/PostgreSQL connection logs
    ("DB Access Denied", re.compile(r"(Access denied for user\s+'[^']+')@'[^']+'\s+\(using password:\s+\w+\)"),
     r"\1@'***HOST***' (using password: ***)"),

    # IP:Port credential combinations
    ("IP:Port Credential", re.compile(r"(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+)\s+(\S+)\s+(\S{8,})"),
     r"\1 \2 ***MASKED***"),

    # Token/session IDs (long hex strings)
    ("Session/Token ID", re.compile(r"(token|session[_-]?id|csrf|nonce)\s*[=:]\s*([a-fA-F0-9]{32,})", re.IGNORECASE),
     r"\1=***TOKEN_MASKED***"),

    # .env or config file cat outputs
    ("Config Secret", re.compile(r"^([\w_]+_KEY|[\w_]+_SECRET|[\w_]+_PASSWORD|[\w_]+_TOKEN)\s*=\s*(.+)$", re.MULTILINE),
     r"\1=***MASKED***"),
]


def sanitize_ssh_output(output: str) -> tuple:
    """
    Cleans sensitive data from SSH command output.
    Returns: (cleaned_output, list_of_matched_labels)
    """
    if not output:
        return output, []

    result = output
    matched_labels = []

    for label, compiled_re, replacement in _compiled_patterns:
        new_result = compiled_re.sub(replacement, result)
        if new_result != result:
            matched_labels.append(label)
        result = new_result

    for label, compiled_re, replacement in SSH_OUTPUT_PATTERNS:
        new_result = compiled_re.sub(replacement, result)
        if new_result != result:
            matched_labels.append(label)
        result = new_result

    if matched_labels:
        labels_str = ", ".join(matched_labels)
        logger.info(f"FILTER | {len(matched_labels)} sensitive data items cleaned from SSH output: [{labels_str}]")
        audit_log(AuditEvent.DATA_FILTERED, detail=f"SSH: {labels_str}", extra={"direction": "ssh_output", "count": len(matched_labels), "types": matched_labels})

    return result, matched_labels


def sanitize_messages(messages: list) -> tuple:
    """
    Filters all text in the message list.
    Returns: (filtered_list, total_masked_count)
    """
    sanitized = []
    total_masked = 0

    for msg in messages:
        new_msg = {"role": msg["role"]}

        if isinstance(msg["content"], str):
            filtered, matched = sanitize_outgoing(msg["content"])
            new_msg["content"] = filtered
            total_masked += len(matched)
        elif isinstance(msg["content"], list):
            new_content = []
            for block in msg["content"]:
                if isinstance(block, dict):
                    new_block = block.copy()
                    if block.get("type") == "text" and "text" in block:
                        filtered, matched = sanitize_outgoing(block["text"])
                        new_block["text"] = filtered
                        total_masked += len(matched)
                    elif block.get("type") == "tool_result" and "content" in block:
                        if isinstance(block["content"], str):
                            filtered, matched = sanitize_ssh_output(block["content"])
                            new_block["content"] = filtered
                            total_masked += len(matched)
                    new_content.append(new_block)
                else:
                    new_content.append(block)
            new_msg["content"] = new_content
        else:
            new_msg["content"] = msg["content"]

        sanitized.append(new_msg)

    return sanitized, total_masked
