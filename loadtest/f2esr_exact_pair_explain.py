"""EXPLAIN de las consultas REALES de `backend/webchat_f2d/repository.py` (2026-10-02).

Las sentencias de abajo son copia textual de las del repositorio (mismas columnas,
mismos predicados, mismo `FOR UPDATE`), con los `%s` reemplazados por valores de UNA
fila real de la bandeja. Se corre con la tabla ya cargada de volumen (relleno + las
filas de la propia carga). Si el repositorio cambia una consulta, hay que copiarla
aca: este modulo no la importa a proposito (importar el repositorio arrastraria el
nucleo JAX), pero `test_explain_cubre_todas_las_consultas_del_repositorio` en
`test_chat_f2d_orquestar.py` compara las tablas/predicados contra el codigo vivo.

Lo que se busca en cada plan (CLAUDE.md, "Las cuatro del rendimiento", regla 1):
`type = ALL` (full scan), `Using filesort`, `Using temporary`, y el indice elegido.
INSERT: no se explican (no filtran); su costo sale de la medicion de carga.
"""
from __future__ import annotations

import re

# nombre -> (sentencia con %(campo)s, es_select, camino)
CONSULTAS = {
    "transition: SELECT ... FOR UPDATE (x2 por turno)": (
        """SELECT state, row_version, lifecycle_version, record_schema_version,
                  envelope_schema_version, transport_projection_version,
                  renderer_api_version, domain_spec_version,
                  transport_kind, contract_state, transport_payload_digest,
                  effective_output_digest, effective_projection_digest, original_envelope_digest,
                  response_payload, subject_id, response_id, request_id,
                  idempotency_key, scope_digest, project_id, audience, trace_id,
                  governance_reference_ids, claim_ids, contains_current_claim, current_not_after
           FROM governed_output_outbox
           WHERE outbox_id=%(outbox_id)s AND tenant_id=%(tenant_id)s AND scope_digest=%(scope_digest)s
             AND request_id=%(request_id)s AND response_id=%(response_id)s AND subject_id=%(subject_id)s
             AND attempt_id=%(attempt_id)s AND idempotency_key=%(idempotency_key)s
           FOR UPDATE""", True, "caliente"),
    "transition: UPDATE (x2 por turno)": (
        """UPDATE governed_output_outbox SET state='OUTPUT_COMMITTED_TO_TRANSPORT', row_version=row_version,
             committing_at=committing_at, committed_at=committed_at, acknowledged_at=acknowledged_at,
             failure_class=NULL
           WHERE outbox_id=%(outbox_id)s AND row_version=%(row_version)s AND state=%(state)s""",
        False, "caliente"),
    "_read_idempotent: SELECT ... FOR UPDATE (solo en conflicto de idempotencia)": (
        """SELECT outbox_id, attempt_id, attempt_sequence, previous_attempt_id,
                  project_id, subject_id, audience, scope_digest, request_id, trace_id, response_id,
                  transport_kind, idempotency_key, lifecycle_version,
                  record_schema_version, envelope_schema_version, transport_projection_version,
                  renderer_api_version, domain_spec_version, contract_state,
                  effective_output_digest, effective_projection_digest,
                  original_envelope_digest, transport_payload_digest,
                  governance_reference_ids, claim_ids, contains_current_claim,
                  current_not_after,
                  response_payload, state
           FROM governed_output_outbox WHERE tenant_id=%(tenant_id)s AND idempotency_key=%(idempotency_key)s
           FOR UPDATE""", True, "frio"),
    "_next_retry_sequence / _validate_retry_parent: SELECT ... FOR UPDATE (solo reintento)": (
        """SELECT attempt_sequence FROM governed_output_outbox
           WHERE tenant_id=%(tenant_id)s AND scope_digest=%(scope_digest)s AND request_id=%(request_id)s
             AND attempt_id=%(attempt_id)s
           FOR UPDATE""", True, "frio"),
    "record_secondary_event: SELECT ... FOR UPDATE (solo si falla la proyeccion post-commit)": (
        """SELECT state, lifecycle_version FROM governed_output_outbox
           WHERE outbox_id=%(outbox_id)s AND tenant_id=%(tenant_id)s AND scope_digest=%(scope_digest)s
             AND request_id=%(request_id)s AND attempt_id=%(attempt_id)s FOR UPDATE""", True, "frio"),
    "record_secondary_event: MAX(sequence_no) de eventos": (
        """SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM governed_output_lifecycle_events
           WHERE outbox_id=%(outbox_id)s""", True, "frio"),
    "recovery_snapshot: GROUP BY state (una vez, al arrancar)": (
        """SELECT state, COUNT(*) FROM governed_output_outbox
           WHERE state IN ('OUTPUT_PREPARED','TRANSPORT_COMMITTING',
                           'OUTPUT_COMMITTED_TO_TRANSPORT') GROUP BY state""", True, "arranque"),
}

_BANDERAS = ("Using filesort", "Using temporary")


def _plano(sql: str) -> str:
    return re.sub(r"\s+", " ", sql).strip()


def muestra(cur) -> dict:
    """Una fila REAL de la bandeja (la mas reciente de los usuarios de la carga; si no hay,
    cualquiera) con los campos que usan las consultas."""
    cur.execute(
        """SELECT outbox_id, tenant_id, scope_digest, request_id, response_id, subject_id,
                  attempt_id, idempotency_key, row_version, state
           FROM governed_output_outbox WHERE subject_id NOT LIKE 'carga-f2d-%' AND state='OUTPUT_COMMITTED_TO_TRANSPORT'
           ORDER BY prepared_at DESC LIMIT 1""")
    fila = cur.fetchone()
    if fila is None:
        raise RuntimeError("no hay una fila real de la carga en la bandeja para tomar de muestra")
    nombres = ("outbox_id", "tenant_id", "scope_digest", "request_id", "response_id",
               "subject_id", "attempt_id", "idempotency_key", "row_version", "state")
    return dict(zip(nombres, fila))


def explicar(conn) -> list[dict]:
    """EXPLAIN (y ANALYZE de los SELECT: ejecuta de verdad y da filas leidas reales) de cada
    consulta. Devuelve una lista de dicts con el plan y las banderas encontradas."""
    resultados = []
    with conn.cursor() as cur:
        params = muestra(cur)
        cur.execute("SELECT COUNT(*) FROM governed_output_outbox")
        (n_outbox,) = cur.fetchone()
        cur.execute("SELECT COUNT(*) FROM governed_output_lifecycle_events")
        (n_eventos,) = cur.fetchone()
        for nombre, (sql, es_select, camino) in CONSULTAS.items():
            plan, columnas = _correr(cur, "EXPLAIN " + sql, params)
            banderas = []
            for fila in plan:
                d = dict(zip(columnas, fila))
                extra = str(d.get("Extra") or "")
                banderas += [b for b in _BANDERAS if b in extra]
                if d.get("type") == "ALL":
                    banderas.append("full scan (type=ALL)")
            analisis = None
            if es_select:
                filas, cols = _correr(cur, "ANALYZE " + sql, params)
                analisis = [dict(zip(cols, f)) for f in filas]
            conn.rollback()  # suelta los FOR UPDATE de arriba
            resultados.append({
                "consulta": nombre, "camino": camino, "sql": _plano(sql),
                "plan": [dict(zip(columnas, f)) for f in plan], "analyze": analisis,
                "banderas": sorted(set(banderas)),
            })
    return [{"filas_bandeja": n_outbox, "filas_eventos": n_eventos}] + resultados


def _correr(cur, sql: str, params: dict):
    cur.execute(sql, params)
    return cur.fetchall(), [c[0] for c in cur.description]
