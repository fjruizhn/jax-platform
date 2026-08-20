# Frontend Pipeline-Completion `set()` Batching Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Collapse the N+2 separate `set()` calls that `useJaxStore.js`'s `pipeline_step_changed`(completed) handler currently fires per WS event into 2 — one synchronous mark (unchanged, preserves the existing duplicate-fetch race guard) and one batched append of every completed-step message plus the summary message — so `CenterPanel.jsx` (the sole subscriber to `messages`) re-renders once per pipeline completion instead of once per completed step.

**Architecture:** Pure refactor inside the existing `handleEvent` branch in `frontend/src/store/useJaxStore.js` (lines 178-214). Build the full array of new messages first (map instead of a `for` loop with N individual `get().addMessage(...)` calls), then commit them with one `set()` that both appends the deduped-by-id messages and (already-done) marks `_pipelineCompletedShown`. No new files, no new dependencies, no change to the WS event shape or any component's props.

**Tech Stack:** React 19, Zustand 5.0.14, Vitest 4.1.10 + `@testing-library/jest-dom`/`@testing-library/react` (already installed, not needed here since this is a store-only unit test, same style as the existing `useJaxStore.test.js`).

## Global Constraints

- The existing race guard must be preserved exactly: `_pipelineCompletedShown` is still marked **synchronously**, before the `api.get(...)` call resolves, so a second `pipeline_step_changed`(completed) event for the same `pipeline_id` arriving while the fetch is in flight still short-circuits via `if (shown.has(pipeline_id)) return` and does not trigger a second fetch.
- Message de-duplication by `id` (already present in `addMessage`) must be preserved for the batched messages too — the new step/summary messages must never be appended twice if `handleEvent` somehow runs the `.then()` callback more than once for the same pipeline.
- No other `handleEvent` branch (`facet_status_changed`, `pipeline_step_changed`-general, `las_manos_health_changed`, `kill_switch_activated`, `human_gate_requested`, `facet_response_completed`, `command_completed`) may be touched — this plan is scoped to the completed-pipeline branch only (lines 178-214).
- Run `cd /home/fruiz/jax-platform/frontend && npm run build` before considering this done (repo convention, see `CLAUDE.md` "Rebuild + deploy frontend después de cada cambio").
- Tests run via `cd /home/fruiz/jax-platform/frontend && npx vitest run src/store/<file>`.

---

### Task 1: Batch the completed-pipeline message writes into a single `set()`

**Files:**
- Modify: `frontend/src/store/useJaxStore.js:178-214`
- Test: `frontend/src/store/useJaxStore.pipelineBatching.test.js`

**Interfaces:**
- Consumes: nothing new — same `event` shape (`{event_type, payload: {pipeline_id, status}}`) and same `api.get(`/pipelines/${pipeline_id}/results`)` response shape (`{steps: [...], total_duration_seconds}`) as today.
- Produces: no change to `handleEvent`'s external signature or to any other store field — `messages` still ends up with the same set of `{id, facet, content, timestamp}` objects, just appended in one batch instead of one-by-one.

- [ ] **Step 1: Write the failing test**

```javascript
// frontend/src/store/useJaxStore.pipelineBatching.test.js
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../api/client', () => ({
  default: {
    post: vi.fn(),
    get: vi.fn(),
  },
}))

import api from '../api/client'
import { useJaxStore } from './useJaxStore'

const INITIAL_STATE = useJaxStore.getState()

describe('pipeline_step_changed completion batching', () => {
  beforeEach(() => {
    useJaxStore.setState(INITIAL_STATE, true)
    vi.clearAllMocks()
  })

  it('applies at most 2 store notifications for a 3-step completed pipeline', async () => {
    api.get.mockResolvedValue({
      data: {
        steps: [
          { step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0' },
          { step_index: 1, facet: 'hyde', capability: 'write', status: 'completed', result: 'r1' },
          { step_index: 2, facet: 'thot', capability: 'review', status: 'completed', result: 'r2' },
        ],
        total_duration_seconds: 12.4,
      },
    })

    let notifications = 0
    const unsubscribe = useJaxStore.subscribe(() => { notifications++ })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-1', status: 'completed' },
    })

    // deja correr el microtask del .then() de api.get
    await new Promise((resolve) => setTimeout(resolve, 0))
    unsubscribe()

    expect(notifications).toBeLessThanOrEqual(2)

    const { messages } = useJaxStore.getState()
    expect(messages.map((m) => m.id)).toEqual([
      'pipeline-pid-1-step-0',
      'pipeline-pid-1-step-1',
      'pipeline-pid-1-step-2',
      'pipeline-pid-1-done',
    ])
    expect(messages[3].content).toContain('3 de 3 steps')
  })

  it('does not re-fetch results for a duplicate completed event for the same pipeline', async () => {
    api.get.mockResolvedValue({
      data: {
        steps: [{ step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0' }],
        total_duration_seconds: 5,
      },
    })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-2', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-2', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    expect(api.get).toHaveBeenCalledTimes(1)
    expect(useJaxStore.getState().messages).toHaveLength(2)
  })

  it('skips steps that are already in messages by id instead of duplicating them', async () => {
    api.get.mockResolvedValue({
      data: {
        steps: [{ step_index: 0, facet: 'jekyll', capability: 'research', status: 'completed', result: 'r0' }],
        total_duration_seconds: 3,
      },
    })
    useJaxStore.setState({
      messages: [{ id: 'pipeline-pid-3-step-0', facet: 'jekyll', content: 'ya estaba', timestamp: 't0' }],
    })

    useJaxStore.getState().handleEvent({
      event_type: 'pipeline_step_changed',
      payload: { pipeline_id: 'pid-3', status: 'completed' },
    })
    await new Promise((resolve) => setTimeout(resolve, 0))

    const { messages } = useJaxStore.getState()
    expect(messages).toHaveLength(2)
    expect(messages[0].content).toBe('ya estaba')
    expect(messages[1].id).toBe('pipeline-pid-3-done')
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run src/store/useJaxStore.pipelineBatching.test.js`
Expected: FAIL on the first test — `notifications` is 5 (1 mark + 3 per-step `addMessage` + 1 summary `addMessage`), not `<= 2`.

- [ ] **Step 3: Batch the writes**

In `frontend/src/store/useJaxStore.js`, replace the branch at lines 178-214:

```javascript
    if (event_type === 'pipeline_step_changed' && payload.status === 'completed') {
      const { pipeline_id } = payload
      const shown = get()._pipelineCompletedShown
      if (shown.has(pipeline_id)) return
      set((s) => ({ _pipelineCompletedShown: new Set([...s._pipelineCompletedShown, pipeline_id]) }))

      api.get(`/pipelines/${pipeline_id}/results`).then(({ data }) => {
        const allSteps = data.steps || []
        const completedSteps = allSteps.filter((s) => s.status === 'completed')
        const ts = new Date().toISOString()
        for (const step of completedSteps) {
          const header = `● **${step.facet}** — ${step.capability}`
          const body = step.result || '_(sin resultado)_'
          const sourceParts = (step.sources || []).map(
            (s) => `- [${s.title || s.url}](${s.url})`
          )
          const sourcesBlock = sourceParts.length
            ? `\n\n**Fuentes**\n${sourceParts.join('\n')}`
            : ''
          get().addMessage({
            id: `pipeline-${pipeline_id}-step-${step.step_index}`,
            facet: step.facet,
            content: `${header}\n\n${body}${sourcesBlock}`,
            timestamp: ts,
          })
        }
        const secs = data.total_duration_seconds
          ? `, ${Math.round(data.total_duration_seconds)}s totales`
          : ''
        get().addMessage({
          id: `pipeline-${pipeline_id}-done`,
          facet: 'jacobs',
          content: `**Pipeline completado** — ${completedSteps.length} de ${allSteps.length} steps${secs}`,
          timestamp: ts,
        })
      }).catch(() => {})
    }
```

with:

```javascript
    if (event_type === 'pipeline_step_changed' && payload.status === 'completed') {
      const { pipeline_id } = payload
      const shown = get()._pipelineCompletedShown
      if (shown.has(pipeline_id)) return
      set((s) => ({ _pipelineCompletedShown: new Set([...s._pipelineCompletedShown, pipeline_id]) }))

      api.get(`/pipelines/${pipeline_id}/results`).then(({ data }) => {
        const allSteps = data.steps || []
        const completedSteps = allSteps.filter((s) => s.status === 'completed')
        const ts = new Date().toISOString()

        const newMessages = completedSteps.map((step) => {
          const header = `● **${step.facet}** — ${step.capability}`
          const body = step.result || '_(sin resultado)_'
          const sourceParts = (step.sources || []).map(
            (s) => `- [${s.title || s.url}](${s.url})`
          )
          const sourcesBlock = sourceParts.length
            ? `\n\n**Fuentes**\n${sourceParts.join('\n')}`
            : ''
          return {
            id: `pipeline-${pipeline_id}-step-${step.step_index}`,
            facet: step.facet,
            content: `${header}\n\n${body}${sourcesBlock}`,
            timestamp: ts,
          }
        })

        const secs = data.total_duration_seconds
          ? `, ${Math.round(data.total_duration_seconds)}s totales`
          : ''
        newMessages.push({
          id: `pipeline-${pipeline_id}-done`,
          facet: 'jacobs',
          content: `**Pipeline completado** — ${completedSteps.length} de ${allSteps.length} steps${secs}`,
          timestamp: ts,
        })

        set((s) => {
          const existingIds = new Set(s.messages.map((m) => m.id))
          const toAppend = newMessages.filter((m) => !existingIds.has(m.id))
          return toAppend.length ? { messages: [...s.messages, ...toAppend] } : s
        })
      }).catch(() => {})
    }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /home/fruiz/jax-platform/frontend && npx vitest run src/store/useJaxStore.pipelineBatching.test.js`
Expected: PASS (3 passed)

- [ ] **Step 5: Run the full frontend test suite and build, then commit**

```bash
cd /home/fruiz/jax-platform/frontend
npx vitest run
npm run build
```
Expected: all existing tests pass (including `src/store/useJaxStore.test.js`, unaffected since it covers `restoreSession`, a different branch), and the build succeeds with no errors.

```bash
cd /home/fruiz/jax-platform
git add frontend/src/store/useJaxStore.js frontend/src/store/useJaxStore.pipelineBatching.test.js
git commit -m "perf(frontend): batch pipeline-completion messages into one set() call"
```

**Remember (per `CLAUDE.md`):** this change alone does not reach production — it still needs the explicit rsync deploy step to the dev VM (`<IP interna, ver /etc/jax/.env>`) documented in `CLAUDE.md` under "DEPLOY FRONTEND" before it's live at axioma-ia.io. Do not run that deploy as part of this plan without the user's explicit go-ahead.
