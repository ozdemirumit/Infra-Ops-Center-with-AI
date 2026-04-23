"""
Unit tests for proxy/data_filter.py — ensures sensitive data is masked.
"""

import sys
from pathlib import Path

# Add project root to path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from proxy.data_filter import sanitize_outgoing, sanitize_ssh_output


class TestAPIKeys:
    def test_anthropic_key(self):
        text = "My key is sk-ant-api03-abcdefghijklmnopqrstuvwxyz1234567890_-ABCDE"
        masked, labels = sanitize_outgoing(text)
        assert "sk-ant-api" not in masked
        assert "API_KEY_MASKED" in masked
        assert "Anthropic API Key" in labels

    def test_openai_key(self):
        text = "Using sk-proj-abcdefghijklmnopqrstuvwxyz1234567890ABCD"
        masked, labels = sanitize_outgoing(text)
        assert "sk-proj-abcd" not in masked
        assert "API_KEY_MASKED" in masked

    def test_google_api_key(self):
        text = "GOOGLE_KEY = AIzaSyC-abc123def456ghi789jkl012mno345pqr678"
        masked, labels = sanitize_outgoing(text)
        assert "AIzaSy" not in masked
        assert "Google API Key" in labels

    def test_github_token(self):
        text = "export GITHUB=ghp_abcdef1234567890abcdef1234567890abcd"
        masked, labels = sanitize_outgoing(text)
        assert "ghp_abcdef" not in masked
        assert "GitHub Token" in labels


class TestJWT:
    def test_jwt_detected(self):
        jwt = (
            "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJzdWIiOiIxMjM0NTY3ODkwIn0."
            "TJVA95OrM7E2cBab30RMHrHDcEfxjoYZgeFONFh7HgQ"
        )
        masked, labels = sanitize_outgoing(jwt)
        assert "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9" not in masked
        assert "JWT_MASKED" in masked
        assert "JWT Token" in labels


class TestPasswords:
    def test_password_equals(self):
        text = "password=MySecretPass123"
        masked, labels = sanitize_outgoing(text)
        assert "MySecretPass123" not in masked

    def test_password_colon(self):
        text = "password: TopSecret!"
        masked, labels = sanitize_outgoing(text)
        assert "TopSecret!" not in masked

    def test_connection_string(self):
        text = "mysql://admin:MyDbPass@localhost:3306/db"
        masked, labels = sanitize_outgoing(text)
        assert "MyDbPass" not in masked
        assert "Connection String" in labels


class TestPrivateKeys:
    def test_rsa_private_key(self):
        text = """-----BEGIN RSA PRIVATE KEY-----
MIIEpAIBAAKCAQEA1234567890abcdefghijklmnop
-----END RSA PRIVATE KEY-----"""
        masked, labels = sanitize_outgoing(text)
        assert "MIIEpAIBAAKC" not in masked
        assert "PRIVATE_KEY_MASKED" in masked

    def test_encrypted_private_key(self):
        text = """-----BEGIN ENCRYPTED PRIVATE KEY-----
abc123def456
-----END ENCRYPTED PRIVATE KEY-----"""
        masked, labels = sanitize_outgoing(text)
        assert "abc123def456" not in masked

    def test_pgp_private_key(self):
        text = """-----BEGIN PGP PRIVATE KEY BLOCK-----
Version: GnuPG v1
abc123
-----END PGP PRIVATE KEY BLOCK-----"""
        masked, labels = sanitize_outgoing(text)
        assert "abc123" not in masked
        assert "PGP Private Key" in labels


class TestCloudCredentials:
    def test_aws_access_key(self):
        text = "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE"
        masked, labels = sanitize_outgoing(text)
        assert "AKIAIOSFODNN7EXAMPLE" not in masked
        assert "AWS Access Key" in labels


class TestK8sSecrets:
    def test_k8s_token(self):
        text = "token: dGhpc2lzYWxvbmdzZWNyZXR0b2tlbnRoYXRzaG91bGRiZW1hc2tlZGZvcnN1cmU="
        masked, labels = sanitize_outgoing(text)
        assert "dGhpc2lzYWxvbmdz" not in masked


class TestSSHOutputFilter:
    def test_shadow_hash(self):
        text = "admin:$6$abcdefg$HashGoesHere123:19000:0:99999:7:::"
        cleaned, labels = sanitize_ssh_output(text)
        assert "HashGoesHere123" not in cleaned
        assert "Shadow Hash" in labels

    def test_env_file_content(self):
        text = "DB_PASSWORD=MySecretDbPass\nAPI_KEY=abc123"
        cleaned, labels = sanitize_ssh_output(text)
        assert "MySecretDbPass" not in cleaned


class TestNoFalsePositives:
    def test_normal_text_unchanged(self):
        text = "This is a normal message without any secrets."
        masked, labels = sanitize_outgoing(text)
        assert masked == text
        assert labels == []

    def test_ip_address_unchanged(self):
        text = "Server IP: 192.168.1.10 is responding."
        masked, labels = sanitize_outgoing(text)
        # IP should not be masked
        assert "192.168.1.10" in masked


class TestEnvSecrets:
    def test_anthropic_key_env(self):
        text = "ANTHROPIC_API_KEY=sk-ant-api03-realkey1234"
        masked, labels = sanitize_outgoing(text)
        assert "sk-ant-api03-realkey1234" not in masked

    def test_device_encryption_key(self):
        text = "DEVICE_ENCRYPTION_KEY=gAAAAABabc123"
        masked, labels = sanitize_outgoing(text)
        assert "gAAAAABabc123" not in masked


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
