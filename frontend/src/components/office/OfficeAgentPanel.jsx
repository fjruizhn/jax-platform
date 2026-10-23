import { useEffect, useRef, useState } from 'react'
import { useI18n } from '../../i18n/index.jsx'
import Dialogo from '../Dialogo'
import { TAMANO_BOTON_ACCION } from '../../tema/botones'
import { officeAgentsApi } from '../../api/virtualOffice'

const INPUT = 'w-full min-h-11 bg-hundido border border-borde-control rounded-lg px-3 py-2 text-sm text-texto focus:outline-none focus-visible:ring-2 focus-visible:ring-foco'
const BUTTON = `${TAMANO_BOTON_ACCION} rounded border border-borde px-3 text-texto hover:bg-hundido disabled:opacity-60`
const CLASSIFICATION = 'INTERNAL'
const validAgent = (value) => value && Number.isInteger(value.agent_id) && typeof value.agent_key === 'string' && typeof value.display_name === 'string' && typeof value.engine_key === 'string' && typeof value.model_id === 'string' && typeof value.data_classification === 'string' && Number.isInteger(value.version) && ['ACTIVE', 'DISABLED'].includes(value.status)
const validEngine = (value) => value && typeof value.engine_key === 'string' && typeof value.display_name === 'string' && typeof value.model_id === 'string' && Array.isArray(value.allowed_data_classifications) && value.allowed_data_classifications.every((classification) => typeof classification === 'string')
const validItems = (data, validator) => data && Array.isArray(data.items) && data.items.every(validator)

export default function OfficeAgentPanel({ projectId, canManage = false, onForbidden }) {
  const { t } = useI18n()
  const [agents, setAgents] = useState(null)
  const [engines, setEngines] = useState(null)
  const [activeAgentId, setActiveAgentId] = useState(null)
  const [input, setInput] = useState('')
  const [result, setResult] = useState(null)
  const [error, setError] = useState(null)
  const [busy, setBusy] = useState(false)
  const [creating, setCreating] = useState(false)
  const [draft, setDraft] = useState({ agent_key: '', display_name: '', engine_key: '', instructions: '' })
  const runIntent = useRef(null)

  const active = agents?.find((agent) => agent.agent_id === activeAgentId) || agents?.find((agent) => agent.status === 'ACTIVE') || null

  async function forbidden() {
    setAgents(null)
    setEngines(null)
    await onForbidden?.()
  }

  async function load() {
    setError(null)
    try {
      const [{ data: agentData }, { data: engineData }] = await Promise.all([officeAgentsApi.list(projectId), officeAgentsApi.engines(projectId)])
      if (!validItems(agentData, validAgent) || !validItems(engineData, validEngine)) throw new Error('invalid Office agent response')
      setAgents(agentData.items)
      setEngines(engineData.items)
      setActiveAgentId((current) => agentData.items.some((agent) => agent.agent_id === current) ? current : agentData.items.find((agent) => agent.status === 'ACTIVE')?.agent_id || null)
      setDraft((current) => current.engine_key ? current : { ...current, engine_key: engineData.items.find((engine) => engine.allowed_data_classifications.includes(CLASSIFICATION))?.engine_key || '' })
    } catch (err) {
      if (err?.response?.status === 403) await forbidden()
      else setError(t.virtualOffice.agents.loadError)
    }
  }

  useEffect(() => {
    setAgents(null); setEngines(null); setActiveAgentId(null); setInput(''); setResult(null); setError(null); runIntent.current = null
    load()
  }, [projectId])

  async function run(event) {
    event.preventDefault()
    const trimmed = input.trim()
    if (!active || active.status !== 'ACTIVE' || !trimmed || busy) return
    const signature = JSON.stringify({ agentId: active.agent_id, input: trimmed })
    if (runIntent.current?.signature !== signature) runIntent.current = { signature, key: crypto.randomUUID() }
    setBusy(true); setError(null); setResult(null)
    try {
      const { data } = await officeAgentsApi.run(projectId, active.agent_id, { input: trimmed, idempotency_key: runIntent.current.key })
      if (!data || typeof data.run_id !== 'string' || !['SUCCEEDED', 'UNKNOWN'].includes(data.status) || typeof data.model_id !== 'string' || (data.output !== null && data.output !== undefined && typeof data.output !== 'string')) throw new Error('invalid Office agent run response')
      runIntent.current = null
      setResult(data)
    } catch (err) {
      if (err?.response?.status === 403) await forbidden()
      else setError(t.virtualOffice.agents.runError)
    } finally { setBusy(false) }
  }

  async function create(event) {
    event.preventDefault()
    const payload = { agent_key: draft.agent_key.trim(), display_name: draft.display_name.trim(), engine_key: draft.engine_key, data_classification: CLASSIFICATION, instructions: draft.instructions.trim() }
    if (busy || !payload.agent_key || !payload.display_name || !payload.engine_key || !payload.instructions) return
    setBusy(true); setError(null)
    try {
      const { data } = await officeAgentsApi.create(projectId, payload)
      if (!validAgent(data)) throw new Error('invalid Office agent create response')
      setAgents((current) => [...(current || []), data])
      setActiveAgentId(data.agent_id)
      setDraft({ agent_key: '', display_name: '', engine_key: engines?.find((engine) => engine.allowed_data_classifications.includes(CLASSIFICATION))?.engine_key || '', instructions: '' })
      setCreating(false)
    } catch (err) {
      if (err?.response?.status === 403) await forbidden()
      else setError(t.virtualOffice.agents.createError)
    } finally { setBusy(false) }
  }

  async function bind(agent, engineKey) {
    if (busy || agent.engine_key === engineKey) return
    setBusy(true); setError(null)
    try {
      const { data } = await officeAgentsApi.bind(projectId, agent.agent_id, { engine_key: engineKey, expected_version: agent.version })
      if (!validAgent(data)) throw new Error('invalid Office agent binding response')
      setAgents((current) => current?.map((entry) => entry.agent_id === data.agent_id ? data : entry) || current)
    } catch (err) {
      if (err?.response?.status === 403) await forbidden()
      else setError(t.virtualOffice.agents.bindError)
    } finally { setBusy(false) }
  }

  return <section className="mt-5 border-t border-borde pt-5" aria-labelledby="office-agents-title">
    <div className="flex flex-wrap items-center justify-between gap-3">
      <div><h3 id="office-agents-title" className="text-base font-semibold">{t.virtualOffice.agents.title}</h3><p className="mt-1 text-sm text-texto-suave">{t.virtualOffice.agents.privateNotice}</p></div>
      {canManage && <button type="button" onClick={() => setCreating(true)} className={`${TAMANO_BOTON_ACCION} rounded bg-accion px-3 text-sobre-color`}>{t.virtualOffice.agents.createOpen}</button>}
    </div>
    {error && <p role="alert" className="mt-3 text-sm text-peligro">{error}</p>}
    {agents === null && !error && <p role="status" className="mt-3 text-sm text-texto-suave">{t.virtualOffice.agents.loading}</p>}
    {agents?.length === 0 && <p role="status" className="mt-3 text-sm text-texto-suave">{t.virtualOffice.agents.empty}</p>}
    {agents?.length > 0 && <>
      <div className="mt-3 flex flex-wrap gap-2">{agents.filter((agent) => agent.status === 'ACTIVE').map((agent) => <button type="button" key={agent.agent_id} onClick={() => { setActiveAgentId(agent.agent_id); setInput(''); setResult(null); setError(null); runIntent.current = null }} aria-pressed={active?.agent_id === agent.agent_id} className={`${BUTTON} ${active?.agent_id === agent.agent_id ? 'bg-hundido' : ''}`}>{agent.display_name}</button>)}</div>
      {active && <form onSubmit={run} className="mt-4 space-y-3">
        {canManage && <label className="block text-sm text-texto-suave" htmlFor={`office-agent-engine-${active.agent_id}`}>{t.virtualOffice.agents.engineFor(active.display_name)}<select id={`office-agent-engine-${active.agent_id}`} aria-label={t.virtualOffice.agents.engineFor(active.display_name)} value={active.engine_key} disabled={busy || engines === null} onChange={(event) => bind(active, event.target.value)} className={`${INPUT} mt-1`}>{engines?.filter((engine) => engine.allowed_data_classifications.includes(active.data_classification)).map((engine) => <option key={engine.engine_key} value={engine.engine_key}>{engine.display_name} · {engine.model_id}</option>)}</select></label>}
        <label className="block text-sm text-texto-suave" htmlFor={`office-agent-input-${active.agent_id}`}>{t.virtualOffice.agents.messageFor(active.display_name)}<textarea id={`office-agent-input-${active.agent_id}`} value={input} disabled={busy} onChange={(event) => { setInput(event.target.value); setError(null); setResult(null); runIntent.current = null }} className={`${INPUT} mt-1 min-h-24`} /></label>
        <button disabled={busy || !input.trim()} className={`${TAMANO_BOTON_ACCION} rounded bg-accion px-3 text-sobre-color disabled:opacity-60`}>{busy ? t.virtualOffice.agents.running : t.virtualOffice.agents.run}</button>
        {result?.status === 'UNKNOWN' && <p role="status" className="text-sm text-texto-suave">{t.virtualOffice.agents.unknown}</p>}
        {result?.status === 'SUCCEEDED' && <div aria-label={t.virtualOffice.agents.output} className="rounded border border-borde bg-hundido p-3 text-sm text-texto"><p className="mb-2 text-xs font-semibold uppercase tracking-wide text-texto-suave">{t.virtualOffice.agents.output}</p><p className="whitespace-pre-wrap break-words">{result.output}</p></div>}
      </form>}
    </>}
    {creating && <Dialogo idTitulo="office-agent-create" titulo={t.virtualOffice.agents.createTitle} onCerrar={() => { if (!busy) setCreating(false) }}>
      <form onSubmit={create} className="space-y-3">
        <p className="text-sm text-texto-suave">{t.virtualOffice.agents.createIntro}</p>
        <label className="block text-sm text-texto-suave" htmlFor="office-agent-key">{t.virtualOffice.agents.key}<input id="office-agent-key" maxLength={64} required disabled={busy} value={draft.agent_key} onChange={(event) => setDraft((current) => ({ ...current, agent_key: event.target.value }))} className={`${INPUT} mt-1`} /></label>
        <label className="block text-sm text-texto-suave" htmlFor="office-agent-name">{t.virtualOffice.agents.name}<input id="office-agent-name" maxLength={128} required disabled={busy} value={draft.display_name} onChange={(event) => setDraft((current) => ({ ...current, display_name: event.target.value }))} className={`${INPUT} mt-1`} /></label>
        <label className="block text-sm text-texto-suave" htmlFor="office-agent-engine">{t.virtualOffice.agents.engine}<select id="office-agent-engine" required disabled={busy || engines === null} value={draft.engine_key} onChange={(event) => setDraft((current) => ({ ...current, engine_key: event.target.value }))} className={`${INPUT} mt-1`}>{engines?.filter((engine) => engine.allowed_data_classifications.includes(CLASSIFICATION)).map((engine) => <option key={engine.engine_key} value={engine.engine_key}>{engine.display_name} · {engine.model_id}</option>)}</select></label>
        <label className="block text-sm text-texto-suave" htmlFor="office-agent-instructions">{t.virtualOffice.agents.instructions}<textarea id="office-agent-instructions" required disabled={busy} value={draft.instructions} onChange={(event) => setDraft((current) => ({ ...current, instructions: event.target.value }))} className={`${INPUT} mt-1 min-h-24`} /></label>
        <div className="flex flex-wrap justify-end gap-2"><button type="button" disabled={busy} onClick={() => setCreating(false)} className={BUTTON}>{t.virtualOffice.cancel}</button><button disabled={busy || !draft.engine_key} className={`${TAMANO_BOTON_ACCION} rounded bg-accion px-3 text-sobre-color disabled:opacity-60`}>{busy ? t.virtualOffice.agents.creating : t.virtualOffice.agents.create}</button></div>
      </form>
    </Dialogo>}
  </section>
}
