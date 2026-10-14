/** P100 real-UI driver. Only the remote API/WS boundary is synthetic.
 * Playwright contract read from installed playwright-core/types/types.d.ts.
 * Resources come from every browser requestfinished + PerformanceResourceTiming,
 * never a hand-picked module or a raw HAR containing auth headers/bodies.
 */
import {createHash} from 'node:crypto';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {pathToFileURL} from 'node:url';
import path from 'node:path';

export function allowedDestination(value, ports) {
  try {
    const url = new URL(value);
    return ['http:', 'ws:'].includes(url.protocol) &&
      ['axioma-ia.io', '127.0.0.1', 'localhost'].includes(url.hostname) &&
      !url.username && !url.password && ports.includes(Number(url.port)) &&
      ![8080, 5173].includes(Number(url.port));
  } catch { return false; }
}

export function safePath(value, metadataOnly = false) {
  const url = new URL(value);
  if (metadataOnly) return ['http:','ws:','https:','wss:'].includes(url.protocol) ? url.pathname : '/[non-network]';
  for (const key of url.searchParams.keys()) {
    if (!['v', 't', 'import', 'direct', 'url', 'raw', 'inline'].includes(key)) {
      throw new Error('unreviewed resource query; refusing to persist');
    }
  }
  return url.pathname + url.search;
}

export function isExpectedResponse(record) {
  return record.status >= 200 && record.status < 300 ||
    record.status === 401 && record.method === 'POST' &&
    record.path === '/api/auth/refresh' && record.beforeLogin === true;
}

export function validateSnapshot(view, lang, theme) {
  if (view.selected_language !== lang ||
      (theme === 'light' ? view.theme_attribute !== 'claro' : view.theme_attribute !== null)) {
    throw new Error('rendered language/theme mismatch');
  }
}

export function resourcePlan(records) {
  const found = new Map();
  for (const record of records) {
    if (record.path.startsWith('/api') || record.path.startsWith('/ws') || record.type === 'document') continue;
    if (record.method !== 'GET' || record.status !== 200 || !/^[0-9a-f]{64}$/.test(record.sha256 ?? '')) {
      throw new Error('failed/unverifiable discovered resource');
    }
    const item = {path: record.path, type: record.type, mime: record.mime,
      bytes: record.bytes, sha256: record.sha256};
    const previous = found.get(record.path);
    if (previous && JSON.stringify(previous) !== JSON.stringify(item)) throw new Error('resource changed during discovery');
    found.set(record.path, item);
  }
  if (!found.size) throw new Error('empty resource graph');
  return [...found.values()].sort((a,b)=>a.path.localeCompare(b.path));
}

/** CDP Fetch sees redirect hops as separate requests. route() alone documents
 * only the first URL of a redirect chain, so it cannot enforce this contract.
 * WS routing connects only allowed URLs to the actual server (no mocked WS).
 */
export async function attachGuard(context, page, ports, denials, websocketEvidence = []) {
  // Context scope also covers initial requests from subframes/new targets.
  await context.route(/.*/, async route => {
    const url = route.request().url();
    if (allowedDestination(url, ports)) await route.continue();
    else {
      denials.push({type:'http',path:safePath(url,true),reason:'destination_not_allowed'});
      await route.abort('blockedbyclient');
    }
  });
  await context.routeWebSocket(/.*/, route => {
    if (!allowedDestination(route.url(), ports)) {
      denials.push({type: 'websocket', path: safePath(route.url(), true), reason: 'destination_not_allowed'});
      route.close({code: 1008, reason: 'P100 destination denied'});
    } else {
      const server = route.connectToServer();
      server.onMessage(message => {
        try {
          if (JSON.parse(String(message))?.type === 'auth_ok') websocketEvidence.push({path:safePath(route.url(),true),auth_ok:true});
        } catch { websocketEvidence.push({path:safePath(route.url(),true),invalid_frame:true}); }
        route.send(message);
      });
    }
  });
  const cdp = await context.newCDPSession(page);
  cdp.on('Fetch.requestPaused', async event => {
    try {
      const allowed = allowedDestination(event.request.url, ports);
      if (!allowed) denials.push({type: 'http', path: safePath(event.request.url, true), reason: 'destination_not_allowed'});
      await cdp.send(allowed ? 'Fetch.continueRequest' : 'Fetch.failRequest', allowed
        ? {requestId: event.requestId} : {requestId: event.requestId, errorReason: 'BlockedByClient'});
    } catch (error) {
      denials.push({type: 'guard', path: '/', reason: 'guard_cdp_failure'});
      // Never continue if inspection or the guard itself fails.
      await cdp.send('Fetch.failRequest', {requestId: event.requestId, errorReason: 'BlockedByClient'}).catch(()=>{});
    }
  });
  await cdp.send('Fetch.enable', {patterns: [{urlPattern: '*', requestStage: 'Request'}]});
  return cdp;
}

export async function launchPrivate(config) {
  if (!Array.isArray(config.allowed_ports) || !config.allowed_ports.length ||
      config.allowed_ports.some(port=>!Number.isInteger(port) || port<1024 || port>65535 || [8080,5173].includes(port))) {
    throw new Error('explicit private allowed_ports required');
  }
  if (!allowedDestination(config.base_url, config.allowed_ports) || new URL(config.base_url).hostname !== 'axioma-ia.io') {
    throw new Error('base_url must use axioma-ia.io on an allowed private port');
  }
  const playwright = await import(pathToFileURL(path.join(config.playwright_module, 'index.mjs')).href);
  const browser = await playwright.chromium.launch({executablePath: config.chromium_binary, headless: true,
    args: ['--host-resolver-rules=MAP axioma-ia.io 127.0.0.1', '--no-proxy-server', '--disable-background-networking']});
  const context = await browser.newContext({viewport: {width: 1440, height: 1000}, serviceWorkers: 'block'});
  context.setDefaultTimeout(config.timeout_ms ?? 15000);
  const page = await context.newPage();
  const denials = [], websocketEvidence = [];
  // The supplied app never opens new windows. Close unexpected pages immediately.
  context.on('page', other => { if (other !== page) { denials.push({type:'popup', path:'/', reason:'unexpected_page'}); other.close(); } });
  await attachGuard(context, page, config.allowed_ports, denials, websocketEvidence);
  return {browser, context, page, denials, websocketEvidence};
}

export async function runBrowser(config) {
  const outputDir = config.output_dir;
  if (!outputDir || !config.fixture_credentials || !config.marker || !config.project_role_labels ||
      !/^[0-9a-f]{40}$/.test(config.source_sha ?? '') || !/^[0-9a-f]{40}$/.test(config.harness_sha ?? '')) {
    throw new Error('explicit output, fixture credentials, marker/role labels and provenance required');
  }
  await mkdir(outputDir, {recursive:true, mode:0o700});
  const {browser, context, page, denials, websocketEvidence} = await launchPrivate(config);
  const records = [], captureTasks = [], issues = [], snapshots = [], performance = [], websocket = websocketEvidence;
  const beforeLogin = new WeakMap();
  let loginCompleted = false;
  page.on('request', request => beforeLogin.set(request, !loginCompleted));
  page.on('requestfailed', request => issues.push({type:'requestfailed', path:safePath(request.url(), true)}));
  page.on('pageerror', ()=>issues.push({type:'pageerror', reason:'uncaught_browser_error'}));
  page.on('console', message => {
    if (message.type() === 'error') {
      // Chromium's own HTTP401 console message is associated with a response below;
      // no application errors or unrelated 401 are discarded here.
      const own401 = /^Failed to load resource: the server responded with a status of 401 \(Unauthorized\)$/.test(message.text());
      const location = message.location();
      const path_ = location.url ? safePath(location.url, true) : '/';
      issues.push({type:'console', path:path_, own401});
    }
  });
  page.on('websocket', ws => {
    ws.on('framereceived', event => {
      try { if (JSON.parse(String(event.payload))?.type === 'auth_ok') websocket.push({path:safePath(ws.url(),true), auth_ok:true}); }
      catch { issues.push({type:'websocket', path:safePath(ws.url(),true), reason:'invalid_frame'}); }
    });
    ws.on('socketerror', ()=>issues.push({type:'websocket', path:safePath(ws.url(),true), reason:'socket_error'}));
  });
  page.on('requestfinished', request => {
    captureTasks.push((async()=>{
      const response = await request.response();
      const path_ = safePath(request.url(), new URL(request.url()).pathname.startsWith('/api'));
      const headers = await response.allHeaders();
      const sizes = await request.sizes();
      const record = {path:path_, method:request.method(), status:response.status(),
        type:request.resourceType(), mime:headers['content-type'] ?? '', bytes:sizes.responseBodySize,
        timing:request.timing(), beforeLogin:beforeLogin.get(request) === true};
      if (!path_.startsWith('/api') && record.type !== 'document') {
        record.sha256 = createHash('sha256').update(await response.body()).digest('hex');
      }
      records.push(record);
      if (path_ === '/api/auth/login' && record.status === 200) loginCompleted = true;
    })().catch(()=>issues.push({type:'capture', path:'/', reason:'resource_capture_failed'})));
  });
  const capturePerformance = async () => {
    const entries = await page.evaluate(()=>performance.getEntriesByType('resource').map(item=>({
      url:item.name, type:item.initiatorType, bytes:item.encodedBodySize, duration_ms:item.duration})));
    for (const entry of entries) performance.push({...entry, url:undefined, path:safePath(entry.url, new URL(entry.url).pathname.startsWith('/api'))});
  };
  const snapshot = async (lang, theme) => {
    await page.getByText(config.marker, {exact:true}).waitFor();
    await page.getByText(config.project_role_labels[lang], {exact:true}).waitFor();
    const view = await page.evaluate(()=>{
      const root = document.documentElement;
      const element = document.querySelector('#root > div');
      const styles = getComputedStyle(element);
      return {document_lang:root.lang, selected_language:localStorage.getItem('jax_lang'), theme_attribute:root.getAttribute('data-tema'),
        background:styles.backgroundColor, color:styles.color, font_family:styles.fontFamily};
    });
    validateSnapshot(view,lang,theme);
    const screenshot = `${config.variant}-${lang}-${theme}.png`;
    await page.screenshot({path:path.join(outputDir,screenshot),fullPage:true});
    snapshots.push({lang,theme,...view,screenshot,marker_present:true,role_translated:true});
    await capturePerformance();
  };
  let runFailure = null;
  try {
    await page.goto(config.base_url + '/login', {waitUntil:'networkidle'});
    await page.getByRole('button',{name:'es',exact:true}).click();
    await page.locator('input[type="email"]').fill(config.fixture_credentials.email);
    await page.locator('input[type="password"]').fill(config.fixture_credentials.password);
    const loginResponse = page.waitForResponse(response=>new URL(response.url()).pathname==='/api/auth/login' && response.status()===200);
    await page.locator('button[type="submit"]').click();
    await loginResponse;
    await page.getByRole('button',{name:'Modo claro',exact:true}).waitFor();
    await page.waitForFunction(marker=>Array.from(document.querySelectorAll('#selector-proyecto-chat option')).some(option=>option.textContent===marker),config.marker);
    await capturePerformance();
    for (const lang of ['es','en']) {
      for (const theme of ['dark','light']) {
        await page.goto(config.base_url + '/', {waitUntil:'networkidle'});
        if (lang==='en' && await page.getByRole('button',{name:'Cambiar idioma a English',exact:true}).count()) {
          await page.getByRole('button',{name:'Cambiar idioma a English',exact:true}).click();
        }
        if (lang==='es' && await page.getByRole('button',{name:'Switch language to Español',exact:true}).count()) {
          await page.getByRole('button',{name:'Switch language to Español',exact:true}).click();
        }
        const targetToggle = lang==='es' ? theme==='light' ? 'Modo claro' : 'Modo oscuro'
          : theme==='light' ? 'Light mode' : 'Dark mode';
        if (await page.getByRole('button',{name:targetToggle,exact:true}).count()) {
          await page.getByRole('button',{name:targetToggle,exact:true}).click();
        }
        await page.locator('a[href="/proyectos"]').click();
        await snapshot(lang,theme);
        // A deep cold document navigation proves the server fallback + fixture refresh.
        await page.goto(config.base_url + '/proyectos', {waitUntil:'networkidle'});
        await page.getByText(config.marker,{exact:true}).waitFor();
        await capturePerformance();
      }
    }
    await Promise.all(captureTasks);
    const backgrounds = new Set(snapshots.map(item=>item.background));
    if (backgrounds.size < 2 || snapshots.some(item=>item.background==='rgba(0, 0, 0, 0)')) throw new Error('theme CSS did not visibly change');
  } catch (error) {
    runFailure = {type:'workflow', reason:'browser_workflow_failed', error_class:error.name};
    issues.push(runFailure);
  } finally {
    await context.close();
    await browser.close();
    await Promise.all(captureTasks);
  }
  const expected401 = records.filter(record=>record.status===401 && isExpectedResponse(record));
  let console401Budget = expected401.length;
  const unexpected = issues.filter(issue=>{
    if (issue.type==='console' && issue.own401 && issue.path==='/api/auth/refresh' && console401Budget>0) {
      console401Budget--; return false;
    }
    return true;
  });
  const responseErrors = records.filter(record=>!isExpectedResponse(record));
  if (!expected401.length) unexpected.push({type:'auth',reason:'missing_prelogin_refresh401'});
  if (!websocket.some(item=>item.auth_ok)) unexpected.push({type:'websocket',reason:'missing_actual_auth_ok'});
  let resources = [];
  try { resources = resourcePlan(records); }
  catch { unexpected.push({type:'resource_graph',reason:'incomplete_resource_graph'}); }
  const result = {schema:'p100-browser-v1', source_sha:config.source_sha,harness_sha:config.harness_sha,
    variant:config.variant, synthetic_boundary:true, snapshots, records, performance,
    websocket, guard_denials:denials, expected_prelogin_refresh401:expected401.length,
    unexpected, response_errors:responseErrors, ok:!runFailure&&!unexpected.length&&!responseErrors.length&&!denials.length};
  await writeFile(path.join(outputDir,'browser.json'),JSON.stringify(result,null,2)+'\n',{mode:0o600});
  const resourceDigest = createHash('sha256').update(JSON.stringify(resources)).digest('hex');
  await writeFile(path.join(outputDir,'resources.json'),JSON.stringify({schema:'p100-resources-v1',
    variant:config.variant,source_sha:config.source_sha,harness_sha:config.harness_sha,
    resource_digest:resourceDigest,resources},null,2)+'\n',{mode:0o600});
  return result;
}

if (process.argv[1] && import.meta.url === pathToFileURL(process.argv[1]).href) {
  const args = process.argv.slice(2);
  if (args.length !== 2 || args[0] !== '--config') throw new Error('usage: frontend_browser.mjs --config explicit.json');
  const result = await runBrowser(JSON.parse(await readFile(args[1],'utf8')));
  process.stdout.write(JSON.stringify({ok:result.ok,variant:result.variant,resource_record_count:result.records.length})+'\n');
  if (!result.ok) process.exitCode = 1;
}
