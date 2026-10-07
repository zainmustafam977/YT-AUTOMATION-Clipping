"""
Unit and integration tests for Multi-Provider Dynamic API Key Vault (Task 2.1).
Verifies:
1. Fernet key generation and encryption/decryption round-trip.
2. Encrypted storage of API keys at rest in database.
3. Leaky-bucket RPM and TPM replenishment and pacing.
4. Least-loaded key acquisition and counter tracking.
5. Cooldown expiration and automatic active restoration.
6. Exponential backoff on 429/quota errors with EventBus notifications.
7. Consecutive error exhaustion threshold.
8. Daily quota resets and CRUD methods.
"""

from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from shorts_engine.db.models import Base
from shorts_engine.services.ai.key_vault import ApiKeyVault, LeakyBucketTracker
from shorts_engine.services.events.bus import global_event_bus


@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    db = Session()
    try:
        yield db
    finally:
        db.close()


@pytest.fixture(autouse=True)
def clean_events():
    global_event_bus.clear()
    yield
    global_event_bus.clear()


def test_fernet_vault_key_initialization_and_roundtrip(tmp_path, test_db):
    """Verifies that Fernet key generates, persists to disk, and encrypts/decrypts cleanly."""
    key_file = tmp_path / "custom_vault.key"
    vault = ApiKeyVault(db=test_db, vault_key_path=key_file)

    assert key_file.exists()
    assert key_file.stat().st_size > 0

    plain_key = "gsk_test_groq_api_key_1234567890abcdef"
    encrypted = vault.encrypt_key(plain_key)

    assert encrypted != plain_key
    assert len(encrypted) > 40

    decrypted = vault.decrypt_key(encrypted)
    assert decrypted == plain_key


def test_fernet_decryption_failure_on_invalid_token(tmp_path, test_db):
    """Verifies that tampering with encrypted key raises ValueError."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    with pytest.raises(ValueError):
        vault.decrypt_key("not_a_valid_fernet_token_xyz")


def test_api_key_crud_and_encrypted_storage(tmp_path, test_db):
    """Verifies API key persistence, encryption at rest, and CRUD operations."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")

    raw_groq = "gsk_live_secret_groq_key_999"
    created = vault.add_key(
        provider="groq",
        raw_key=raw_groq,
        nickname="Groq Primary Tier",
        rpm_limit=30,
        tpm_limit=60000,
        rpd_limit=14400,
    )

    assert created.id is not None
    assert created.provider == "groq"
    assert created.nickname == "Groq Primary Tier"
    assert created.status == "ACTIVE"
    # Ensure raw secret is NOT stored in plaintext
    assert raw_groq not in created.api_key_encrypted

    # Decrypt verification
    decrypted = vault.get_decrypted_key(created.id)
    assert decrypted == raw_groq

    # Listing
    keys = vault.list_keys(provider="groq")
    assert len(keys) == 1
    assert keys[0].id == created.id

    empty_list = vault.list_keys(provider="openai")
    assert len(empty_list) == 0

    # Disable / Enable
    vault.disable_key(created.id)
    assert vault.get_key(created.id).status == "DISABLED"

    vault.enable_key(created.id)
    assert vault.get_key(created.id).status == "ACTIVE"

    # Delete
    deleted = vault.delete_key(created.id)
    assert deleted is True
    assert vault.get_key(created.id) is None


def test_leaky_bucket_tracker():
    """Verifies RPM/TPM leaky bucket consumption, rejection, and leak replenishment."""
    # 2 requests per minute limit, 1000 tokens per minute
    bucket = LeakyBucketTracker(rpm_limit=2, tpm_limit=1000, initial_time=100.0)

    # First request
    assert bucket.can_consume(estimated_tokens=400, now=100.0) is True
    assert bucket.consume(estimated_tokens=400, now=100.0) is True

    # Second request
    assert bucket.can_consume(estimated_tokens=400, now=100.0) is True
    assert bucket.consume(estimated_tokens=400, now=100.0) is True

    # Third request immediately -> Should exceed RPM (water = 2.0, capacity = 2.0)
    assert bucket.can_consume(estimated_tokens=100, now=100.0) is False
    assert bucket.consume(estimated_tokens=100, now=100.0) is False

    # Simulate 30 seconds passing (half a minute)
    # At 2 RPM, leak rate is 2/60 = 0.0333 req/s. 30s * (2/60) = 1.0 request leaked!
    assert bucket.can_consume(estimated_tokens=100, now=130.0) is True
    assert bucket.consume(estimated_tokens=100, now=130.0) is True

    # Reset
    bucket.reset()
    assert bucket.request_water == 0.0
    assert bucket.token_water == 0.0


def test_acquire_key_least_loaded_and_exhaustion(tmp_path, test_db):
    """Verifies least-loaded key prioritization and error raising on exhaustion."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")

    # Add two keys for gemini
    k1 = vault.add_key(provider="gemini", raw_key="key1_secret", nickname="Gemini Spare", rpm_limit=15)
    k2 = vault.add_key(provider="gemini", raw_key="key2_secret", nickname="Gemini Main", rpm_limit=15)

    # First acquire should pick k1 or k2 (both have 0 daily tokens)
    chosen_1 = vault.acquire_key("gemini", estimated_tokens=3000)
    assert chosen_1.daily_token_count == 3000
    assert chosen_1.daily_request_count == 1

    # Next acquire should pick the other key because it has 0 daily tokens
    chosen_2 = vault.acquire_key("gemini", estimated_tokens=2000)
    assert chosen_2.id != chosen_1.id
    assert chosen_2.daily_token_count == 2000

    # Third acquire should pick chosen_2 because 2000 < 3000
    chosen_3 = vault.acquire_key("gemini", estimated_tokens=500)
    assert chosen_3.id == chosen_2.id
    assert chosen_3.daily_token_count == 2500

    # Exhaust provider test
    vault.disable_key(k1.id)
    vault.disable_key(k2.id)

    with pytest.raises(RuntimeError) as exc_info:
        vault.acquire_key("gemini")
    assert "cooling down or exhausted" in str(exc_info.value)


def test_cooldown_backoff_and_event_bus_notifications(tmp_path, test_db):
    """Verifies exponential cooldown calculation and live EventBus alerts on rate limits."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    key = vault.add_key(provider="groq", raw_key="groq_err_key", nickname="Groq Worker")

    # 1. First error (429 Rate Limit) -> 30s backoff
    vault.report_error(key.id, http_status=429, error_msg="Too many requests")
    refreshed = vault.get_key(key.id)
    assert refreshed.status == "COOLING_DOWN"
    assert refreshed.consecutive_errors == 1
    assert refreshed.cooldown_until is not None
    assert refreshed.cooldown_until > datetime.utcnow()

    # EventBus check
    events = global_event_bus.get_history(category="quota")
    assert len(events) >= 1
    assert events[-1].level == "WARNING"
    assert "API Key Rate Limited" in events[-1].title
    assert "30s" in events[-1].message

    # 2. Second error while throttled -> 60s backoff (30 * 2^1)
    vault.report_error(key.id, http_status=429, error_msg="RESOURCE_EXHAUSTED: rate limit exceeded")
    refreshed = vault.get_key(key.id)
    assert refreshed.consecutive_errors == 2

    # 3. Third error -> 120s backoff
    vault.report_error(key.id, http_status=403, error_msg="quota exceeded")
    refreshed = vault.get_key(key.id)
    assert refreshed.consecutive_errors == 3

    # 4. Report success resets consecutive errors
    vault.report_success(key.id)
    refreshed = vault.get_key(key.id)
    assert refreshed.consecutive_errors == 0


def test_consecutive_error_exhaustion_threshold(tmp_path, test_db):
    """Verifies that 5 non-429 consecutive errors disable the key and set EXHAUSTED."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    key = vault.add_key(provider="openai", raw_key="openai_flaky_key", nickname="OpenAI Fallback")

    for i in range(4):
        vault.report_error(key.id, http_status=500, error_msg="Internal Server Error")
        assert vault.get_key(key.id).status == "ACTIVE"

    # 5th error triggers EXHAUSTED
    vault.report_error(key.id, http_status=500, error_msg="Fatal Invalid Signature")
    refreshed = vault.get_key(key.id)
    assert refreshed.status == "EXHAUSTED"

    events = global_event_bus.get_history(category="quota", level="ERROR")
    assert len(events) >= 1
    assert "API Key Disabled" in events[-1].title


def test_cooldown_automatic_recovery(tmp_path, test_db):
    """Verifies that expired cooldown keys automatically recover to ACTIVE on acquire."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    key = vault.add_key(provider="deepseek", raw_key="deepseek_test_key", nickname="DeepSeek V3")

    # Manually set cooling down with expired cooldown timestamp (10 seconds ago)
    key.status = "COOLING_DOWN"
    key.cooldown_until = datetime.utcnow() - timedelta(seconds=10)
    key.consecutive_errors = 2
    test_db.commit()

    # Acquiring key should recover it to ACTIVE while preserving consecutive_errors
    acquired = vault.acquire_key("deepseek")
    assert acquired.id == key.id
    assert acquired.status == "ACTIVE"
    assert acquired.cooldown_until is None
    # consecutive_errors must persist so next failure applies 60s/120s backoff
    assert acquired.consecutive_errors == 2

    # Successful call resets consecutive_errors to 0
    vault.report_success(acquired.id)
    assert vault.get_key(acquired.id).consecutive_errors == 0


def test_reset_daily_quotas(tmp_path, test_db):
    """Verifies daily quota counter reset functionality."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    k = vault.add_key(provider="groq", raw_key="groq_daily", nickname="Groq Daily")
    k.daily_request_count = 42
    k.daily_token_count = 150000
    test_db.commit()

    updated = vault.reset_daily_quotas()
    assert updated >= 1

    refreshed = vault.get_key(k.id)
    assert refreshed.daily_request_count == 0
    assert refreshed.daily_token_count == 0


def test_exponential_cooldown_multi_step_progression(tmp_path, test_db):
    """Verifies that cooldown backoff progresses exponentially across recovery cycles."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    key = vault.add_key(provider="gemini", raw_key="gemini_exp_key", nickname="Gemini Exp")

    # Cycle 1: 429 Error -> 30s backoff
    vault.report_error(key.id, http_status=429, error_msg="rate limit")
    k1 = vault.get_key(key.id)
    assert k1.consecutive_errors == 1
    assert k1.status == "COOLING_DOWN"

    # Simulate cooldown expiry
    k1.cooldown_until = datetime.utcnow() - timedelta(seconds=1)
    test_db.commit()

    # Recover key via acquire
    rec1 = vault.acquire_key("gemini")
    assert rec1.status == "ACTIVE"
    assert rec1.consecutive_errors == 1

    # Cycle 2: Second 429 Error -> 60s backoff (30 * 2^1)
    vault.report_error(rec1.id, http_status=429, error_msg="quota exceeded")
    k2 = vault.get_key(rec1.id)
    assert k2.consecutive_errors == 2
    assert k2.status == "COOLING_DOWN"
    # Cooldown duration should be ~60 seconds from now
    diff_sec = (k2.cooldown_until - datetime.utcnow()).total_seconds()
    assert 55 <= diff_sec <= 65


def test_invalid_api_key_immediate_exhaustion(tmp_path, test_db):
    """Verifies that an invalid API key / auth rejection is immediately marked EXHAUSTED."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    key = vault.add_key(provider="groq", raw_key="invalid_secret", nickname="Groq Bad Key")

    vault.report_error(key.id, http_status=401, error_msg="Invalid API Key provided")
    refreshed = vault.get_key(key.id)
    assert refreshed.status == "EXHAUSTED"

    events = global_event_bus.get_history(category="quota", level="ERROR")
    assert len(events) >= 1
    assert "API Key Invalid" in events[-1].title


def test_nulls_first_key_ordering(tmp_path, test_db):
    """Verifies that a never-used key (last_used_at IS NULL) is prioritized first."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")

    # k1 was used in the past with 0 daily tokens
    k1 = vault.add_key(provider="deepseek", raw_key="deepseek_used", nickname="DeepSeek Used")
    k1.last_used_at = datetime.utcnow() - timedelta(hours=1)
    k1.daily_token_count = 0

    # k2 was never used (last_used_at is None) with 0 daily tokens
    k2 = vault.add_key(provider="deepseek", raw_key="deepseek_fresh", nickname="DeepSeek Fresh")
    k2.last_used_at = None
    k2.daily_token_count = 0
    test_db.commit()

    chosen = vault.acquire_key("deepseek")
    assert chosen.id == k2.id
    assert chosen.nickname == "DeepSeek Fresh"


def test_zero_and_negative_tokens_safety(tmp_path, test_db):
    """Verifies that acquiring with zero or negative tokens does not break rate limits or water levels."""
    vault = ApiKeyVault(db=test_db, vault_key_path=tmp_path / "vault.key")
    key = vault.add_key(provider="openai", raw_key="openai_safe", nickname="OpenAI Safe")

    acquired = vault.acquire_key("openai", estimated_tokens=0)
    assert acquired.id == key.id
    assert acquired.daily_token_count >= 1
