const sealed = new WeakSet()
const PROFILE = 'pipeline-results.v1'
const SHA = /^sha256:[a-f0-9]{64}$/
function freeze(v) { if (v && typeof v === 'object' && !Object.isFrozen(v)) { Object.values(v).forEach(freeze); Object.freeze(v) }; return v }
export function isGovernedPipelineResults(v) { return !!v && typeof v === 'object' && sealed.has(v) }
async function digest(text) { if (!globalThis.crypto?.subtle) throw new Error('governed output verification unavailable'); const b = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text)); return `sha256:${[...new Uint8Array(b)].map(x=>x.toString(16).padStart(2,'0')).join('')}` }
export async function governedPipelineResults(response) {
 const h=response.headers; const ct=h['content-type']||''; const raw=response.data
 if (response.status!==200 || typeof raw!=='string' || !ct.includes('application/json') || h['x-axioma-governed-output']!=='f2-e.output.1' || h['x-axioma-structured-renderer']!=='f2-c.structured-renderer.2' || h['x-axioma-structured-schema']!=='f2-c.structured-output.2' || h['x-axioma-domain-spec']!=='f2-c.domain.5' || h['x-axioma-provenance-profile']!==PROFILE || !SHA.test(h['x-axioma-body-sha256']||'') || !h['x-axioma-response-id'] || !SHA.test(h['x-axioma-origin-manifest-sha256']||'') || !SHA.test(h['x-axioma-provenance-profile-digest']||'')) throw new Error('governed output unavailable')
 if (await digest(raw)!==h['x-axioma-body-sha256']) throw new Error('governed output digest mismatch')
 let value; try { value=JSON.parse(raw) } catch { throw new Error('governed output malformed') }
 if (!Array.isArray(value?.steps)) throw new Error('governed output shape invalid'); freeze(value); sealed.add(value); return value
}
