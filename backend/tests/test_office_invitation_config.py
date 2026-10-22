from virtual_office.invitation_config import InvitationConfig, load_invitation_config_from_environment


def test_invitation_config_has_no_implicit_lifetime(monkeypatch):
    monkeypatch.delenv("OFFICE_INVITATION_LIFETIME_SECONDS", raising=False)
    assert load_invitation_config_from_environment() is None


def test_invitation_config_requires_canonical_bounded_integer(monkeypatch):
    for raw in ("0", "-1", "01", "604801", "one", " 60"):
        monkeypatch.setenv("OFFICE_INVITATION_LIFETIME_SECONDS", raw)
        assert load_invitation_config_from_environment() is None
    monkeypatch.setenv("OFFICE_INVITATION_LIFETIME_SECONDS", "60")
    assert load_invitation_config_from_environment() == InvitationConfig(lifetime_seconds=60)
