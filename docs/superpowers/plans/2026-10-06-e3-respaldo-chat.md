# E3 scanned PDF fallback implementation plan

> **For agentic workers:** execute this plan task by task with TDD. Keep each change independently reviewable.

**Goal:** Route a scanned PDF attached from chat into the existing project document queue and LAS MANOS processor, returning immediately with an honest, user-visible processing state.

**Architecture:** Preserve the raw upload only when the existing pypdf extractor raises `PdfSinTexto`. Stage it through the same `project_documents` storage, authorization, database admission, and dispatcher used by project uploads. Expose a project-authorized status read keyed by document ID; the browser shows the server state while polling. Do not submit a scanned document to the model as if it had text.

**Tech Stack:** FastAPI, async MariaDB repositories, project document dispatcher, React, Vitest, pytest.

**Spec:** `/home/fruiz/encargos-codex/jxp-e3-respaldo-chat.md`; current LAS MANOS contract and project authorization in `/home/fruiz/jax` `origin/master`; related design in `jax/docs/superpowers/specs/2026-10-02-proyectos-e2-adenda-design.md`.

## Global Constraints

- Do not modify or switch branches in either primary checkout; use `/home/fruiz/wt/jxp-e3-respaldo` and, only if needed, `/home/fruiz/wt/jax-e3`.
- Never use the production `jax_memory` database; do not integrate, deploy, or edit `PENDIENTES.md`.
- Use `POST /procesamiento/trabajos` through the existing project document dispatcher; do not create an ephemeral platform task as the queue.
- Derive user, tenant, and project from authenticated context; fail closed without an active project and sufficient write role.
- Enforce the configured chat attachment byte/page limits and project document byte limit; no new hardcoded caps.
- Show LAS MANOS' actual terminal state and closed error code (`ocr_sin_texto` only if LAS MANOS actually reports it).
- All new visible strings use Spanish and English translations and existing theme tokens; never use browser/system dialogs.
- Run the exact tests added, relevant backend/frontend suites, measure test deltas, and record a safe load measurement for 20 concurrent chat users while processing is queued.

## Review Focus

1. No project or stale project membership: reject before retaining or dispatching the scanned PDF.
2. Viewer or archived project: reject through project authority and the conditional repository write.
3. Duplicate content or uncertain insert/dispatch: preserve the canonical project row and never lose the only PDF copy.
4. Oversize, over-page-limit, malformed, or partially textual PDF: retain existing limits and do not misclassify it as a scan.
5. OCR failure or result state drift: show only the closed server status/error mapping, not invented text or raw processor diagnostics.

---

### Task 1: Retain and queue scanned uploads through project documents

**Files:**
- Modify: `backend/adjuntos/pdf.py`
- Modify: `backend/api/upload.py`
- Modify: `backend/proyectos_documentos/almacen.py`
- Modify: `backend/proyectos_documentos/repositorio.py`
- Modify: `backend/api/proyectos_documentos.py`
- Test: `backend/tests/test_adjuntos_pdf.py`
- Test: `backend/tests/test_adjuntos_upload.py`
- Test: `backend/tests/test_proyectos_documentos_api.py`

- [ ] Add failing tests proving `PdfSinTexto` reports total pages and that a scan with a valid writer project becomes a `project_documents` row in `en_cola` while the missing-project and viewer cases fail closed.
- [ ] Run the new tests and verify they fail for the missing behavior.
- [ ] Factor a shared safe single-file admission path from the existing project document upload flow. It must stream into `proyectos/<uuid>/entrada/<lote>`, hash while writing, check `ACTIVE` plus a B9 writer role again in the conditional insert, deduplicate by `(project_id, sha256)`, and wake the existing dispatcher.
- [ ] On `PdfSinTexto`, retain/stage the original only if the request supplies a project context; apply `min(JAX_ADJUNTO_MAX_BYTES, proyectos.documentos.max_bytes_archivo)` and configured `JAX_ADJUNTO_MAX_PAGINAS`. Keep existing behavior for text PDFs and all other rejection cases.
- [ ] Run the focused tests and verify the queue admission, duplicate, retry/uncertain insert, and cleanup behavior.

### Task 2: Add authorized status reads and fail-closed chat binding

**Files:**
- Modify: `backend/proyectos_documentos/repositorio.py`
- Modify: `backend/api/proyectos_documentos.py`
- Modify: `backend/api/chat.py`
- Test: `backend/tests/test_proyectos_documentos_api.py`
- Test: `backend/tests/test_adjuntos_chat_endpoint.py`

- [ ] Add failing tests for exact document status lookup, foreign project/document isolation, a stale project attachment submitted on a different turn, and no-project scanned attachment rejection.
- [ ] Run the new tests and verify they fail for the missing behavior.
- [ ] Add a `GET /api/proyectos/{project_id}/documentos/{document_id}` read that revalidates project visibility and returns the existing closed state/error fields only.
- [ ] Bind a scanned attachment to the same authenticated user and resolved `memory_scope.project_id` as the chat turn; reject absent or mismatched scope before any model call. Never add scanned bytes or pretend extraction text to the prompt.
- [ ] Run the focused tests and verify all negative authorization paths fail closed.

### Task 3: Show processing state in the chat UI

**Files:**
- Modify: `frontend/src/components/BottomBar/BottomBar.jsx`
- Modify: `frontend/src/components/chat/FileAttachment.jsx`
- Modify: `frontend/src/components/CenterPanel/Message.jsx`
- Modify: `frontend/src/api/proyectos.js`
- Modify: `frontend/src/i18n/es.js`
- Modify: `frontend/src/i18n/en.js`
- Test: `frontend/src/components/BottomBar/BottomBar.test.jsx`
- Test: `frontend/src/components/chat/FileAttachment.test.jsx`
- Test: `frontend/src/components/CenterPanel/Message.test.jsx`

- [ ] Add failing tests that pass the active project with the upload, show a queued status, poll only the bound project/document, stop polling at terminal state, and display the processor's honest error/status in es/en.
- [ ] Run the new tests and verify they fail for the missing behavior.
- [ ] Render scan states as normal component content with theme tokens and an `aria-live` status region. Do not use `confirm`, `alert`, `prompt`, or system dialogs.
- [ ] Run focused Vitest files and the i18n parity/policy checks.

### Task 4: Verify full scope, floors, and load

**Files:**
- Modify: `.github/workflows/policy.yml`
- Create: `docs/carga-e3-respaldo-chat-2026-10-06.md`
- Test: existing related backend, frontend, and `loadtest/` suites.

- [ ] Run the relevant backend, frontend, and loadtest suites in isolated test infrastructure; explicitly verify no production database is configured or contacted.
- [ ] Measure current test floors on the branch and the exact delta contributed by E3, then update only the corresponding `policy.yml` floors with measured values.
- [ ] Measure 20 concurrent chat users while a scanned PDF processing job is queued; record request count, p95 chat-turn latency, concurrency, setup, and whether the OCR processor was real or simulated. Do not claim a real processor run from a fake.
- [ ] Review the diff for hardcoded UI strings/colors, native dialogs, blocking work in async paths, and ownership/project scope bypasses.
- [ ] Leave PR creation and integration out of this task; report PR status, SHA, tests, load result, and floor deltas.
