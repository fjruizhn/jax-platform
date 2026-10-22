"""Real MariaDB behaviour for the explicit Office bootstrap ceremony."""
from __future__ import annotations

import asyncio
import argparse
import pyotp
import time

from auth.models import AuthUser
from db.transaccion import AISLAMIENTO_ADMIN, transaccion
from tests.identidades import crear_usuario, sql
from virtual_office.bootstrap import _locked_current_target, bootstrap_initial_office_root
from virtual_office.bootstrap import credential_self_control_in_cursor
from virtual_office.mfa import (
    MFAConfig, confirm_enrollment_in_cursor, issue_login_challenge_after_password_in_cursor,
    start_first_enrollment_after_password_in_cursor, verify_and_consume_totp_in_cursor,
)
from virtual_office.migrations import apply_office_migrations

CONFIG = MFAConfig(120, 3, 300, 300, 3)


def test_bootstrap_credential_chain_requires_a_self_actor_and_resets_after_admin_control(client):
    async def scenario():
        subject, _ = await crear_usuario()
        administrator, _ = await crear_usuario()
        await sql(
            "INSERT INTO user_admin_audit (ts,actor_user_id,target_user_id,action) VALUES "
            "(UTC_TIMESTAMP(6),%s,%s,'create'),(UTC_TIMESTAMP(6),%s,%s,'password_reset_completed')",
            (administrator, subject, administrator, subject),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await credential_self_control_in_cursor(cur, user_id=subject) is None
        await sql(
            "INSERT INTO user_admin_audit (ts,actor_user_id,target_user_id,action) VALUES "
            "(UTC_TIMESTAMP(6),%s,%s,'password_changed_self')",
            (subject, subject),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            self_audit_id = await credential_self_control_in_cursor(cur, user_id=subject)
            assert self_audit_id is not None
        await sql(
            "INSERT INTO user_admin_audit (ts,actor_user_id,target_user_id,action) VALUES "
            "(UTC_TIMESTAMP(6),%s,%s,'password_set_by_admin')",
            (administrator, subject),
        )
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await credential_self_control_in_cursor(cur, user_id=subject) is None
    client.portal.call(scenario)


def test_bootstrap_cli_checks_runtime_contract_before_reading_or_mutating(monkeypatch):
    import virtual_office.bootstrap_cli as cli
    from db.runtime_contract import RuntimeDbContractError

    async def rejected(_pool):
        raise RuntimeDbContractError("contract denied")

    async def must_not_run(*_args, **_kwargs):
        raise AssertionError("bootstrap action ran after failed runtime contract")

    monkeypatch.setattr(cli, "assert_runtime_db_contract", rejected)
    monkeypatch.setattr(cli, "bootstrap_initial_office_root", must_not_run)
    monkeypatch.setattr("sys.stdin.readline", lambda: "untrusted-token\n")
    args = argparse.Namespace(target_user_id=1, expected_email="root@example.invalid", basis_ref="test", access_token_stdin=True)
    assert asyncio.run(cli._run(args)) == 1


def test_bootstrap_target_guard_rejects_inactive_tenant_user_and_external_class(client):
    async def scenario():
        user_id, email = await crear_usuario()
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await _locked_current_target(cur, user_id, email) is not None
        await sql("UPDATE jax_users SET status='inactive' WHERE user_id=%s", (user_id,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await _locked_current_target(cur, user_id, email) is None
        await sql("UPDATE jax_users SET status='active',account_class='EXTERNAL_CLIENT' WHERE user_id=%s", (user_id,))
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            assert await _locked_current_target(cur, user_id, email) is None
        await sql("UPDATE jax_users SET account_class='INTERNAL' WHERE user_id=%s", (user_id,))
        await sql("UPDATE jax_tenants SET status='inactive' WHERE tenant_id=1")
        try:
            async with transaccion(AISLAMIENTO_ADMIN) as cur:
                assert await _locked_current_target(cur, user_id, email) is None
        finally:
            await sql("UPDATE jax_tenants SET status='active' WHERE tenant_id=1")
    client.portal.call(scenario)


async def _login_proof(user_id: int, secret: str, correlation: bytes, offset: int):
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        challenge = await issue_login_challenge_after_password_in_cursor(
            cur, preauthenticated_user_id=user_id, config=CONFIG,
        )
    async with transaccion(AISLAMIENTO_ADMIN) as cur:
        return await verify_and_consume_totp_in_cursor(
            cur, challenge_token=challenge.token,
            otp=pyotp.TOTP(secret, digits=6, interval=30).at((int(time.time()) // 30 + offset) * 30), server_correlation_id=correlation,
        )


async def _session(user_id: int, email: str, proof):
    rows = await sql("SELECT tenant_id,role,token_version,mfa_version FROM jax_users WHERE user_id=%s", (user_id,), True)
    tenant_id, role, token_version, mfa_version = rows[0]
    return AuthUser(user_id=str(user_id), tenant_id=str(tenant_id), role=role, email=email,
                    token_version=int(token_version), account_class="INTERNAL",
                    mfa_proof_event_id=proof.event_id, mfa_version=int(mfa_version),
                    mfa_at=int(proof.occurred_at.timestamp()))


def test_bootstrap_uses_current_self_proof_and_replays_without_second_cut(client, monkeypatch):
    """The receipt survives the bootstrap token cut, but a new proof is needed."""
    for name, value in {
        "OFFICE_MFA_CHALLENGE_LIFETIME_SECONDS": "120", "OFFICE_MFA_CHALLENGE_MAX_ATTEMPTS": "3",
        "OFFICE_MFA_ENROLLMENT_LIFETIME_SECONDS": "300", "OFFICE_MFA_RECENCY_SECONDS": "300",
        "OFFICE_MFA_RECOVERY_CODE_COUNT": "3",
    }.items():
        monkeypatch.setenv(name, value)
    async def scenario():
        from db.connection import get_pool
        pool = await get_pool()
        async with pool.acquire() as conn:
            async with conn.cursor() as cur:
                await apply_office_migrations(cur)
                # The permanent ceremony is intentionally global.  This test
                # owns the private schema lease and restores an empty table.
                await cur.execute("DROP TRIGGER IF EXISTS no_update_office_bootstrap_state")
                await cur.execute("DROP TRIGGER IF EXISTS no_delete_office_bootstrap_state")
                await cur.execute("DROP TABLE IF EXISTS office_bootstrap_state")
                for statement in __import__("virtual_office.migrations", fromlist=["OFFICE_BOOTSTRAP_DDL"]).OFFICE_BOOTSTRAP_DDL:
                    await cur.execute(statement)
        user_id, email = await crear_usuario(password="bootstrap-test-password")
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            started = await start_first_enrollment_after_password_in_cursor(cur, preauthenticated_user_id=user_id, config=CONFIG)
        async with transaccion(AISLAMIENTO_ADMIN) as cur:
            await confirm_enrollment_in_cursor(cur, enrollment_capability_token=started.enrollment_id,
                                               otp=pyotp.TOTP(started.secret, digits=6, interval=30).now(),
                                               config=CONFIG, server_correlation_id=b"B" * 16)
        first = await _login_proof(user_id, started.secret, b"C" * 16, 1)
        # A real current proof alone does not establish that the subject owns
        # its credential; no profile or version cut may appear on this denial.
        from virtual_office.bootstrap import BootstrapDenied
        before_denial = (await sql("SELECT token_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0][0]
        try:
            await bootstrap_initial_office_root(session_user=await _session(user_id, email, first),
                                                target_user_id=user_id, expected_email=email, basis_ref="test-bootstrap")
        except BootstrapDenied:
            pass
        else:
            raise AssertionError("bootstrap accepted an administrator-known credential")
        assert await sql("SELECT user_id FROM office_account_profile WHERE user_id=%s", (user_id,), True) == ()
        assert (await sql("SELECT token_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0][0] == before_denial
        await sql("INSERT INTO user_admin_audit (ts,actor_user_id,target_user_id,action) VALUES (UTC_TIMESTAMP(6),%s,%s,'create')", (user_id, user_id))
        await sql("INSERT INTO user_admin_audit (ts,actor_user_id,target_user_id,action) VALUES (UTC_TIMESTAMP(6),%s,%s,'password_reset_completed')", (user_id, user_id))
        # A pre-existing profile is an incompatible bootstrap state; the
        # singleton ceremony cannot silently reinterpret it as a receipt.
        await sql("INSERT INTO office_account_profile (user_id,global_role,status) VALUES (%s,'COLLABORATOR','ACTIVE')", (user_id,))
        try:
            await bootstrap_initial_office_root(session_user=await _session(user_id, email, first),
                                                target_user_id=user_id, expected_email=email, basis_ref="test-bootstrap")
        except BootstrapDenied:
            pass
        else:
            raise AssertionError("bootstrap accepted incompatible profile")
        await sql("DELETE FROM office_account_profile WHERE user_id=%s", (user_id,))
        # A durable provenance write failure is a real database failure: the
        # preceding profile/reset/version writes must all roll back together.
        await sql("CREATE TRIGGER test_bootstrap_provenance_failure BEFORE INSERT ON office_bootstrap_state FOR EACH ROW SIGNAL SQLSTATE '45000' SET MESSAGE_TEXT='bootstrap test failure'")
        try:
            await bootstrap_initial_office_root(session_user=await _session(user_id, email, first),
                                                target_user_id=user_id, expected_email=email, basis_ref="test-bootstrap")
        except Exception as error:
            assert "bootstrap test failure" in str(error)
        else:
            raise AssertionError("bootstrap survived provenance failure")
        assert await sql("SELECT user_id FROM office_account_profile WHERE user_id=%s", (user_id,), True) == ()
        assert (await sql("SELECT token_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0][0] == before_denial
        await sql("DROP TRIGGER test_bootstrap_provenance_failure")
        session = await _session(user_id, email, first)
        async def ceremony():
            try:
                return await bootstrap_initial_office_root(session_user=session, target_user_id=user_id,
                                                           expected_email=email, basis_ref="test-bootstrap")
            except BootstrapDenied:
                return None
        # Separate transactions from the shared pool contend on the same
        # canonical user/profile rows. Exactly one initial write can win;
        # the stale-proof loser cannot create a second grant or version cut.
        outcomes = await asyncio.gather(ceremony(), ceremony())
        winners = [outcome for outcome in outcomes if outcome is not None]
        assert len(winners) == 1
        receipt = winners[0]
        assert receipt.replay is False
        assert await sql("SELECT COUNT(*) FROM office_bootstrap_state", (), True) == ((1,),)
        after_first = (await sql("SELECT token_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0][0]
        second = await _login_proof(user_id, started.secret, b"D" * 16, -1)
        replay = await bootstrap_initial_office_root(session_user=await _session(user_id, email, second),
                                                     target_user_id=user_id, expected_email=email, basis_ref="test-bootstrap")
        assert replay.replay is True
        assert replay.identity_proof_event_id == receipt.identity_proof_event_id
        assert (await sql("SELECT token_version FROM jax_users WHERE user_id=%s", (user_id,), True))[0][0] == after_first + 1
        # Replay binds immutable ceremony subject and basis; a fresh proof is
        # never a generic capability to retrieve or rewrite another receipt.
        fresh_session = await _session(user_id, email, second)
        for wrong_target, wrong_basis in ((user_id, "other-basis"), (user_id + 999999, "test-bootstrap")):
            try:
                await bootstrap_initial_office_root(session_user=fresh_session,
                                                    target_user_id=wrong_target,
                                                    expected_email=email, basis_ref=wrong_basis)
            except BootstrapDenied:
                pass
            else:
                raise AssertionError("bootstrap accepted a wrong ceremony binding")
        forged = AuthUser(user_id=str(user_id), tenant_id="1", role="operator", email=email,
                          token_version=fresh_session.token_version, account_class="INTERNAL",
                          mfa_proof_event_id=999999999, mfa_version=fresh_session.mfa_version,
                          mfa_at=fresh_session.mfa_at)
        try:
            await bootstrap_initial_office_root(session_user=forged, target_user_id=user_id,
                                                expected_email=email, basis_ref="test-bootstrap")
        except BootstrapDenied:
            pass
        else:
            raise AssertionError("bootstrap accepted forged proof")
        # Durable proof freshness is evaluated from the ledger timestamp, not
        # caller-controlled AuthUser.mfa_at.  Both stale and future evidence
        # fail before receipt replay.
        for interval in ("- INTERVAL 301 SECOND", "+ INTERVAL 600 SECOND"):
            event_id = await sql(
                "INSERT INTO office_mfa_security_event "
                "(event_kind,actor_user_id,principal_kind,challenge_id,subject_user_id,operation,purpose,token_version,mfa_version,previous_token_version,previous_mfa_version,proof_event_id,authorization_event_id,result,server_correlation_id,fence_key,occurred_at) "
                "VALUES ('MFA_PROOF',%s,'USER_SESSION',NULL,%s,'MFA_PROOF','STEP_UP',%s,%s,NULL,NULL,NULL,NULL,'VERIFIED',%s,NULL,UTC_TIMESTAMP(6) " + interval + ")",
                (user_id, user_id, fresh_session.token_version, fresh_session.mfa_version, b"F" * 16),
            )
            invalid_time = AuthUser(user_id=str(user_id), tenant_id="1", role="operator", email=email,
                                    token_version=fresh_session.token_version, account_class="INTERNAL",
                                    mfa_proof_event_id=int(event_id), mfa_version=fresh_session.mfa_version,
                                    mfa_at=fresh_session.mfa_at)
            try:
                await bootstrap_initial_office_root(session_user=invalid_time, target_user_id=user_id,
                                                    expected_email=email, basis_ref="test-bootstrap")
            except BootstrapDenied:
                pass
            else:
                raise AssertionError("bootstrap accepted invalid-time proof")
    client.portal.call(scenario)
