/** Independent PREP test: two owned localhost HTTP servers, no app/B/services. */
import assert from 'node:assert/strict';
import http from 'node:http';
import {readFile, writeFile} from 'node:fs/promises';
import {launchPrivate} from './frontend_browser.mjs';

const args = process.argv.slice(2);
if (args.length !== 2 || args[0] !== '--config') throw new Error('usage: frontend_guard_check.mjs --config runtime.json');
const runtime = JSON.parse(await readFile(args[1],'utf8'));
let forbiddenCount = 0;
const forbidden = http.createServer((request,response)=>{forbiddenCount++;response.end('FORBIDDEN CONTACT');});
await new Promise(resolve=>forbidden.listen(0,'127.0.0.1',resolve));
const forbiddenPort = forbidden.address().port;
const forbiddenUrl = `http://127.0.0.1:${forbiddenPort}`;
const allowed = http.createServer((request,response)=>{
  if (request.url==='/hop') {response.writeHead(302,{Location:'/redirect'});response.end();}
  else if (request.url==='/redirect') {response.writeHead(302,{Location:forbiddenUrl+'/redirect-denied'});response.end();}
  else if (request.url==='/main.js') {response.setHeader('Content-Type','text/javascript');response.end('window.p100Allowed = true;');}
  else {response.setHeader('Content-Type','text/html');response.end('<html><script src="/main.js"></script><body>P100 guard only</body></html>');}
});
await new Promise(resolve=>allowed.listen(0,'127.0.0.1',resolve));
const port = allowed.address().port;
let session;
try {
  session = await launchPrivate({...runtime,allowed_ports:[port],base_url:`http://axioma-ia.io:${port}`});
  const {page,denials} = session;
  await page.goto(`http://axioma-ia.io:${port}`,{waitUntil:'networkidle'});
  assert.equal(await page.evaluate(()=>window.p100Allowed),true,'allowed resource was blocked');
  await page.evaluate(async url=>{
    await Promise.all([
      fetch('/hop').then(()=>{throw new Error('redirect escaped guard');},()=>{}),
      fetch(url+'/direct-denied').then(()=>{throw new Error('direct request escaped guard');},()=>{}),
    ]);
    await new Promise((resolve,reject)=>{
      const ws = new WebSocket(url.replace('http:','ws:')+'/ws-denied');
      const timeout = setTimeout(()=>reject(new Error('denied WebSocket did not close')),3000);
      ws.onopen = ()=>{clearTimeout(timeout);reject(new Error('WebSocket escaped guard'));};
      ws.onclose = ()=>{clearTimeout(timeout);resolve();};
      ws.onerror = ()=>{};
    });
  },forbiddenUrl);
  assert.equal(forbiddenCount,0,'guard leaked network contact');
  assert.ok(denials.some(item=>item.type==='http'&&item.path==='/redirect-denied'));
  assert.ok(denials.some(item=>item.type==='http'&&item.path==='/direct-denied'));
  assert.ok(denials.some(item=>item.type==='websocket'&&item.path==='/ws-denied'));
  const result = {scope:'synthetic owned loopback HTML; guard only, not real frontend/load',
    allowed_script_loaded:true,forbidden_server_requests:forbiddenCount,denials,ok:true};
  if (runtime.output) await writeFile(runtime.output,JSON.stringify(result,null,2)+'\n',{mode:0o600});
  process.stdout.write(JSON.stringify(result)+'\n');
} finally {
  if (session) {await session.context.close();await session.browser.close();}
  allowed.closeAllConnections();forbidden.closeAllConnections();
  await Promise.all([new Promise(resolve=>allowed.close(resolve)),new Promise(resolve=>forbidden.close(resolve))]);
}
