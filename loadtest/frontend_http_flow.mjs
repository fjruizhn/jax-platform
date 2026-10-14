/** A cold HTTP workflow, not JavaScript execution or render-time navigation.
 * Two documents (/login then /proyectos), the complete observed transitive
 * resource graph for each, and identical logical synthetic API operations.
 */
export function validatePlan(config, resources) {
  const match = /^http:\/\/(axioma-ia\.io|127\.0\.0\.1|localhost):([0-9]+)$/.exec(config.base_url ?? '');
  if (!match || !Array.isArray(config.allowed_ports) || !config.allowed_ports.includes(Number(match[2])) ||
      [8080,5173].includes(Number(match[2])) || Number(match[2])<1024 || Number(match[2])>65535) {
    throw new Error('HTTP base_url is not an explicit allowed private destination');
  }
  if (!config.fixture_credentials?.email || !config.fixture_credentials?.password || !config.marker ||
      !Array.isArray(resources) || !resources.length) throw new Error('explicit fixture/complete resource graph required');
  const seen = new Set();
  for (const resource of resources) {
    if (!/^\/(?!\/)[^#\\\s]*$/.test(resource.path ?? '') || resource.path.startsWith('/api') || resource.path.startsWith('/ws') ||
        !/^[0-9a-f]{64}$/.test(resource.sha256 ?? '') || !resource.mime || seen.has(resource.path)) {
      throw new Error('invalid, duplicated or remote resource graph entry');
    }
    for (const query of (resource.path.split('?')[1] ?? '').split('&').filter(Boolean)) {
      if (!['v','t','import','direct','url','raw','inline'].includes(decodeURIComponent(query.split('=')[0]))) {
        throw new Error('unreviewed resource query');
      }
    }
    seen.add(resource.path);
  }
  return match[1];
}

function json(response) {
  try {return JSON.parse(response.body);} catch {return null;}
}
function mediaType(value) {return (value ?? '').split(';')[0].trim().toLowerCase();}
function html(response) {
  return response.status===200 && mediaType(response.mime)==='text/html' &&
    /id=["']root["']/.test(response.body) && /type=["']module["']/.test(response.body);
}

export function coldNavigation(config, resources, transport, stats) {
  validatePlan(config,resources);
  const started = Date.now();
  let success = true, completed = false;
  transport.clearCookies();
  const consume = (response, condition) => {
    const ok = Boolean(condition(response));
    stats.request(response,ok);
    if (!ok) success = false;
    return response;
  };
  const request = (method,target,body,headers,condition) => consume(transport.request(method,target,body,headers),condition);
  const staticResources = () => {
    const responses = transport.batch(resources.map(resource=>['GET',resource.path,null,{}]));
    if (responses.length!==resources.length) throw new Error('transport lost discovered resources');
    responses.forEach((response,index)=>consume(response, current=>current.status===200 &&
      mediaType(current.mime)===mediaType(resources[index].mime) && current.sha256===resources[index].sha256));
  };
  try {
    request('GET','/login',null,{},html);
    staticResources();
    request('GET','/api/apariencia',null,{},r=>r.status===200&&typeof json(r)?.system_name==='string'&&
      ['es','en'].includes(json(r)?.lang_default)&&['dark','light'].includes(json(r)?.theme_default));
    request('POST','/api/auth/refresh','{}',{'Content-Type':'application/json'},r=>r.status===401);
    const login = request('POST','/api/auth/login',JSON.stringify(config.fixture_credentials),
      {'Content-Type':'application/json'},r=>r.status===200&&typeof json(r)?.access_token==='string'&&json(r)?.user_id!=null);
    const token = json(login)?.access_token;
    if (!token) return false;
    const auth = {Authorization:`Bearer ${token}`};
    request('GET','/api/state',null,auth,r=>r.status===200&&typeof json(r)?.facets==='object'&&
      typeof json(r)?.active_pipelines==='object'&&typeof json(r)?.las_manos_alive==='boolean'&&
      typeof json(r)?.kill_switch_active==='boolean');
    const marker = r=>r.status===200&&Array.isArray(json(r)?.proyectos)&&
      json(r).proyectos.some(project=>project.nombre===config.marker)&&json(r).siguiente===null;
    request('GET','/api/proyectos?vista=activos&limite=100',null,auth,marker);
    request('GET','/proyectos',null,{},html);
    staticResources();
    request('GET','/api/auth/me',null,auth,r=>r.status===200&&json(r)?.email===config.fixture_credentials.email&&json(r)?.user_id!=null);
    request('GET','/api/proyectos?vista=activos&limite=50',null,auth,marker);
    completed = true;
    return success;
  } finally {
    // Exceptions, incomplete batches and missing login payload must also count.
    stats.navigation(Date.now()-started,success && completed);
  }
}
