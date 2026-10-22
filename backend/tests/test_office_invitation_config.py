from virtual_office.invitation_config import InvitationConfig, load_invitation_config_from_environment


def test_invitation_config_has_no_implicit_lifetime(monkeypatch):
    monkeypatch.delenv("OFFICE_INVITATION_LIFETIME_SECONDS", raising=False)
    monkeypatch.delenv("OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS", raising=False)
    monkeypatch.delenv("OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", raising=False)
    monkeypatch.delenv("OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT", raising=False)
    monkeypatch.delenv("OFFICE_INVITATION_PUBLIC_BASE_URL", raising=False)
    assert load_invitation_config_from_environment() is None


def test_invitation_config_requires_canonical_bounded_integer(monkeypatch):
    for raw in ("0", "-1", "01", "604801", "one", " 60"):
        monkeypatch.setenv("OFFICE_INVITATION_LIFETIME_SECONDS", raw)
        monkeypatch.setenv("OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS", "60")
        monkeypatch.setenv("OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", "50")
        monkeypatch.setenv("OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT", "50")
        monkeypatch.setenv("OFFICE_INVITATION_PUBLIC_BASE_URL", "https://virtualoffice.example.test")
        assert load_invitation_config_from_environment() is None
    monkeypatch.setenv("OFFICE_INVITATION_LIFETIME_SECONDS", "60")
    monkeypatch.setenv("OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS", "60")
    monkeypatch.setenv("OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", "50")
    monkeypatch.setenv("OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT", "50")
    monkeypatch.setenv("OFFICE_INVITATION_PUBLIC_BASE_URL", "https://virtualoffice.example.test")
    assert load_invitation_config_from_environment() == InvitationConfig(
        lifetime_seconds=60, claim_timeout_seconds=60, stale_claim_batch_limit=50,
        expiry_sweep_batch_limit=50, public_base_url="https://virtualoffice.example.test")
    # A process-wide mail locale cannot stand in for a recipient preference.
    monkeypatch.setenv("OFFICE_INVITATION_EMAIL_LOCALE", "en")
    assert load_invitation_config_from_environment() == InvitationConfig(
        lifetime_seconds=60, claim_timeout_seconds=60, stale_claim_batch_limit=50,
        expiry_sweep_batch_limit=50, public_base_url="https://virtualoffice.example.test")


def test_invitation_config_requires_https_origin(monkeypatch):
    for url in ("http://virtualoffice.example.test",
                "https://virtualoffice.example.test/office",
                "https://user@virtualoffice.example.test"):
        monkeypatch.setenv("OFFICE_INVITATION_LIFETIME_SECONDS", "60")
        monkeypatch.setenv("OFFICE_INVITATION_CLAIM_TIMEOUT_SECONDS", "60")
        monkeypatch.setenv("OFFICE_INVITATION_STALE_CLAIM_BATCH_LIMIT", "50")
        monkeypatch.setenv("OFFICE_INVITATION_EXPIRY_SWEEP_BATCH_LIMIT", "50")
        monkeypatch.setenv("OFFICE_INVITATION_PUBLIC_BASE_URL", url)
        assert load_invitation_config_from_environment() is None
