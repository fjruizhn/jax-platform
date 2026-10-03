"""Siembra y limpieza de la carga del chat web F2-D (2026-10-02).

SIEMBRA, contra `jax_memory_test` (JAMAS `jax_memory`):
  - N_USUARIOS usuarios descartables (`carga-chat-f2d-<n>-<id>@example.invalid`,
    tenant 1, rol operator): cada "usuario simultaneo" de la carga es uno distinto,
    con SU PROPIO historial de conversacion en RAM y SU PROPIA conversacion de memoria.
  - N_RELLENO filas de relleno en `governed_output_outbox` (+ 3 eventos de ciclo de vida
    cada una, como un turno completo) para que el EXPLAIN y la carga corran con volumen
    de miles de filas, no contra una tabla casi vacia. Set-based (motor SEQUENCE de
    MariaDB). Las marca `subject_id = carga-f2d-<k>`.

LIMPIEZA: por MARCADORES (email / subject_id), no por un JSON que pudiera perderse:
funciona aunque la siembra se haya cortado a la mitad y se puede correr a mano.
Al terminar verifica que ninguna tabla con `user_id` conserve filas de los usuarios
de la carga y lanza si queda resto.

USO (normalmente lo invoca chat_f2d_orquestar.py):
    python3 loadtest/chat_f2d_siembra.py sembrar <salida.json>
    python3 loadtest/chat_f2d_siembra.py limpiar <salida.json>
"""
from __future__ import annotations

import json
import sys
import time
import uuid

import bcrypt
import pymysql

# This is deliberately a closed value.  The runner never accepts a database name
# from its caller: every write in this file is guarded by this exact name.
BASE_DE_PRUEBA = "jax_memory_test_f2esr"
N_USUARIOS = 50                     # el nivel de concurrencia mas alto de la corrida
N_RELLENO = 5_000                   # filas de relleno en la bandeja (miles, no decenas)
BYTES_PAYLOAD_RELLENO = 2_000       # payload chico: el plan no depende de el y la base es compartida
PATRON_EMAIL = "carga-chat-f2d-%@example.invalid"
PREFIJO_SUJETO_RELLENO = "carga-f2d-"
TENANT_DE_CARGA = 1
LOCK_DE_CORRIDA = "axioma:f2e-sr-load:jax_memory_test_f2esr"


def exigir_base_de_prueba(db: str) -> None:
    """BARRERA DURA (no un assert, que -O pelaria): ni una fila se escribe en otra base."""
    if db != BASE_DE_PRUEBA:
        raise RuntimeError(
            f"conectado a {db!r}, no a {BASE_DE_PRUEBA!r} -- ABORTANDO sin escribir nada.")


def conectar(env: dict):
    conn = pymysql.connect(
        host=env["JAX_DB_HOST"], port=int(env["JAX_DB_PORT"]),
        user=env["JAX_DB_USER"], password=env["JAX_DB_PASSWORD"],
        database=env["JAX_DB_NAME"], autocommit=False, charset="utf8mb4",
    )
    with conn.cursor() as cur:
        cur.execute("SELECT DATABASE()")
        (db,) = cur.fetchone()
    exigir_base_de_prueba(db)
    return conn


def health_base(conn) -> int:
    """Linea base de `facet_health_event` (el backend escribe ahi un evento por turno): la
    limpieza borra solo lo POSTERIOR a esta marca."""
    with conn.cursor() as cur:
        cur.execute("SELECT COALESCE(MAX(id), 0) FROM facet_health_event")
        (base,) = cur.fetchone()
    conn.commit()
    return base


def adquirir_exclusion(conn) -> None:
    """Only one destructive seed/cleanup cycle may use the closed test DB."""
    with conn.cursor() as cur:
        cur.execute("SELECT GET_LOCK(%s, 0)", (LOCK_DE_CORRIDA,))
        (adquirido,) = cur.fetchone()
    if adquirido != 1:
        raise RuntimeError("another F2-E SR load run owns the isolated database")


def liberar_exclusion(conn) -> None:
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT RELEASE_LOCK(%s)", (LOCK_DE_CORRIDA,))
    except pymysql.Error:
        # The connection can be gone after an interrupted run; MariaDB releases
        # named locks with that connection, so there is no unsafe fallback action.
        pass


def preparar_corrida(conn) -> None:
    """Under the named lock, remove any interrupted predecessor before seeding."""
    adquirir_exclusion(conn)
    try:
        limpiar(conn)
    except Exception:
        liberar_exclusion(conn)
        raise


def sembrar(conn) -> dict:
    """Devuelve {user_ids, emails, health_id_base, relleno}. Registra la linea base de
    `facet_health_event` ANTES de que corra nada (la limpieza borra solo lo posterior)."""
    health_id_base = health_base(conn)

    pw_hash = bcrypt.hashpw(b"x", bcrypt.gensalt(rounds=4)).decode()
    user_ids, emails = [], []
    with conn.cursor() as cur:
        for i in range(N_USUARIOS):
            email = f"carga-chat-f2d-{i}-{uuid.uuid4().hex[:8]}@example.invalid"
            cur.execute(
                "INSERT INTO jax_users (tenant_id, email, password_hash, role, status, token_version) "
                "VALUES (%s, %s, %s, 'operator', 'active', 0)",
                (TENANT_DE_CARGA, email, pw_hash),
            )
            user_ids.append(cur.lastrowid)
            emails.append(email)
    conn.commit()
    print(f"{N_USUARIOS} usuarios de carga sembrados (ids {user_ids[0]}..{user_ids[-1]})", file=sys.stderr)

    with conn.cursor() as cur:
        cur.execute(
            f"""INSERT INTO governed_output_outbox (
                  outbox_id, attempt_id, previous_attempt_id, attempt_sequence,
                  tenant_id, project_id, subject_id, audience, scope_digest,
                  request_id, trace_id, response_id, transport_kind, idempotency_key,
                  lifecycle_version, record_schema_version, envelope_schema_version,
                  transport_projection_version, renderer_api_version, domain_spec_version,
                  contract_state, effective_output_digest, effective_projection_digest,
                  original_envelope_digest, transport_payload_digest,
                  governance_reference_ids, claim_ids, contains_current_claim, current_not_after,
                  state, response_payload, prepared_at, committing_at, committed_at, row_version)
                SELECT UUID(), UUID(), NULL, 1,
                  {TENANT_DE_CARGA}, NULL, CONCAT('{PREFIJO_SUJETO_RELLENO}', MOD(seq, 500)),
                  CONCAT('user:', MOD(seq, 500)),
                  CONCAT('sha256:', SHA2(CONCAT('scope-f2d-', MOD(seq, 500)), 256)),
                  UUID(), UUID(), UUID(), 'web-chat-http-json',
                  SHA2(CONCAT('idem-f2d-', seq), 256),
                  'f2-d.lifecycle.2', 'f2-d.outbox.record.2', 'f2-c.1', 'f2-d.web-chat-json.1',
                  'f2-c.renderer.2', 'f2-c.domain.2',
                  'VALID', CONCAT('sha256:', SHA2(CONCAT('out-', seq), 256)),
                  CONCAT('sha256:', SHA2(CONCAT('proj-', seq), 256)),
                  CONCAT('sha256:', SHA2(CONCAT('env-', seq), 256)),
                  CONCAT('sha256:', SHA2(CONCAT('pay-', seq), 256)),
                  '[]', '[]', 0, NULL,
                  IF(MOD(seq, 20) = 0, 'TRANSPORT_COMMITTING', 'OUTPUT_COMMITTED_TO_TRANSPORT'),
                  REPEAT('x', {BYTES_PAYLOAD_RELLENO}),
                  NOW(6) - INTERVAL seq SECOND, NOW(6) - INTERVAL seq SECOND, NOW(6) - INTERVAL seq SECOND, 3
                FROM seq_1_to_{N_RELLENO}"""
        )
        cur.execute(
            """INSERT INTO governed_output_lifecycle_events
                 (outbox_id, tenant_id, scope_digest, sequence_no, event_type, from_state, to_state, occurred_at)
               SELECT o.outbox_id, o.tenant_id, o.scope_digest, s.seq,
                      ELT(s.seq, 'OUTPUT_PREPARED', 'TRANSPORT_COMMITTING', 'OUTPUT_COMMITTED_TO_TRANSPORT'),
                      ELT(s.seq, NULL, 'OUTPUT_PREPARED', 'TRANSPORT_COMMITTING'),
                      ELT(s.seq, 'OUTPUT_PREPARED', 'TRANSPORT_COMMITTING', 'OUTPUT_COMMITTED_TO_TRANSPORT'),
                      o.prepared_at
               FROM governed_output_outbox o JOIN seq_1_to_3 s
               WHERE o.subject_id LIKE %s""",
            (PREFIJO_SUJETO_RELLENO + "%",),
        )
    conn.commit()
    print(f"{N_RELLENO} filas de relleno en la bandeja (+3 eventos c/u)", file=sys.stderr)
    return {"user_ids": user_ids, "emails": emails, "health_id_base": health_id_base,
            "relleno": N_RELLENO}


def _ids_de_usuarios(cur) -> list[int]:
    cur.execute("SELECT user_id FROM jax_users WHERE email LIKE %s", (PATRON_EMAIL,))
    return [r[0] for r in cur.fetchall()]


def _en(valores) -> str:
    return ", ".join(["%s"] * len(valores))


def _limpiar_una_vez(conn, health_id_base: int | None = None) -> dict:
    """Borra TODO lo que dejo la corrida, por marcadores. Devuelve lo borrado por tabla y
    lanza si queda resto en alguna tabla con `user_id`."""
    cur = conn.cursor()
    cur.execute("SELECT DATABASE()")
    exigir_base_de_prueba(cur.fetchone()[0])
    borrado: dict[str, int] = {}

    def borrar(nombre: str, sql: str, params=()) -> None:
        cur.execute(sql, params)
        borrado[nombre] = cur.rowcount

    uids = _ids_de_usuarios(cur)
    if uids:
        cur.execute(f"SELECT id, conversation_uuid FROM conversations WHERE user_id IN ({_en(uids)})", uids)
        convs = cur.fetchall()
        cids = [c[0] for c in convs]
        cuuids = [c[1] for c in convs]
        if cuuids:
            for tabla in ("shadow_claim_verdicts", "shadow_vocab_hits", "shadow_messages"):
                borrar(tabla, f"DELETE FROM {tabla} WHERE conv_uuid IN ({_en(cuuids)})", cuuids)
        if cids:
            for tabla in ("memory_extraction_results", "memory_extraction_jobs"):
                borrar(tabla, f"DELETE FROM {tabla} WHERE conversation_id IN ({_en(cids)})", cids)
            borrar("messages", f"DELETE FROM messages WHERE conversation_id IN ({_en(cids)})", cids)
        borrar("conversations", f"DELETE FROM conversations WHERE user_id IN ({_en(uids)})", uids)
        borrar("axioma_usage", f"DELETE FROM axioma_usage WHERE user_id IN ({_en(uids)})", uids)
        suj = [str(u) for u in uids]
    else:
        suj = []

    # Bandeja: filas reales de la carga (subject_id = user_id) + relleno (prefijo).
    cond = "subject_id LIKE %s" + (f" OR subject_id IN ({_en(suj)})" if suj else "")
    params = [PREFIJO_SUJETO_RELLENO + "%"] + suj
    borrar("governed_output_lifecycle_events",
           f"DELETE FROM governed_output_lifecycle_events WHERE outbox_id IN "
           f"(SELECT outbox_id FROM governed_output_outbox WHERE {cond})", params)
    borrar("governed_output_outbox", f"DELETE FROM governed_output_outbox WHERE {cond}", params)

    if health_id_base is not None:
        borrar("facet_health_event",
               "DELETE FROM facet_health_event WHERE id > %s AND facet = 'jax_local' AND source = 'chat'",
               (health_id_base,))
    if uids:
        borrar("jax_users", f"DELETE FROM jax_users WHERE user_id IN ({_en(uids)})", uids)
    conn.commit()

    # Verificacion de resto: ninguna tabla con user_id conserva filas de la carga.
    resto = {}
    if uids:
        cur.execute(
            "SELECT table_name FROM information_schema.columns "
            "WHERE table_schema = %s AND column_name = 'user_id'", (BASE_DE_PRUEBA,))
        for (tabla,) in cur.fetchall():
            cur.execute(f"SELECT COUNT(*) FROM `{tabla}` WHERE user_id IN ({_en(uids)})", uids)
            (n,) = cur.fetchone()
            if n:
                resto[tabla] = n
    cur.execute("SELECT COUNT(*) FROM governed_output_outbox WHERE subject_id LIKE %s",
                (PREFIJO_SUJETO_RELLENO + "%",))
    (n_relleno,) = cur.fetchone()
    if n_relleno:
        resto["governed_output_outbox(relleno)"] = n_relleno
    cur.close()
    if resto:
        raise RuntimeError(f"la limpieza dejo resto: {resto}")
    return borrado


def limpiar(conn, health_id_base: int | None = None) -> dict:
    """Cleanup retries the only transient MariaDB contention we can observe.

    A named run lock avoids another harness seed, but the application's post-send
    work can still briefly overlap this final cleanup.
    """
    for intento in range(4):
        try:
            return _limpiar_una_vez(conn, health_id_base)
        except pymysql.err.OperationalError as exc:
            if exc.args and exc.args[0] not in {1205, 1213} or intento == 3:
                raise
            conn.rollback()
            time.sleep(0.2 * (2 ** intento))
    raise AssertionError("unreachable")


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1] not in ("sembrar", "limpiar"):
        raise SystemExit("uso: chat_f2d_siembra.py sembrar|limpiar <salida.json>")
    conn = conectar()
    if sys.argv[1] == "sembrar":
        with open(sys.argv[2], "w") as f:
            json.dump(sembrar(conn), f)
    else:
        base = json.load(open(sys.argv[2])).get("health_id_base")
        print(json.dumps(limpiar(conn, base), indent=2))


if __name__ == "__main__":
    main()
