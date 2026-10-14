/** k6 v2.2.0 HTTP resource replay. Does not launch concurrent browsers.
 * Invoke only after controller B approval in the isolated real-unit rehearsal.
 * CLI must supply an empty private k6 config, --include-system-env-vars=false,
 * --no-usage-report, --new-machine-readable-summary=false and P100_RUN_CONFIG.
 */
import http from 'k6/http';
import crypto from 'k6/crypto';
import {Counter, Rate, Trend} from 'k6/metrics';
import {coldNavigation, validatePlan} from './frontend_http_flow.mjs';

if (!__ENV.P100_RUN_CONFIG) throw new Error('P100_RUN_CONFIG explicit JSON file required');
const config = JSON.parse(open(__ENV.P100_RUN_CONFIG));
const discovery = JSON.parse(open(config.resources_file));
const resources = discovery.resources;
const hostname = validatePlan(config,resources);
for (const field of ['source_sha','harness_sha']) {
  if (!/^[0-9a-f]{40}$/.test(config[field] ?? '') || discovery[field]!==config[field]) {
    throw new Error('source/harness provenance does not match discovery');
  }
}
if (discovery.variant!==config.variant || crypto.sha256(JSON.stringify(resources),'hex')!==discovery.resource_digest ||
    ![1,8,32,64,128].includes(config.vus) || ![true,false].includes(config.warmup) ||
    !(config.warmup ? config.repetition===0 : [1,2,3].includes(config.repetition)) ||
    !Number.isInteger(config.duration_seconds) || config.duration_seconds<=0 || !config.summary_file) {
  throw new Error('invalid run matrix/discovery identity');
}

export const options = {
  scenarios: {workflow: {executor:'constant-vus',vus:config.vus,duration:`${config.duration_seconds}s`,gracefulStop:'5s'}},
  maxRedirects: 0, batch: 6, batchPerHost: 6, noCookiesReset: false,
  hosts: {[hostname]: '127.0.0.1'},
  summaryTrendStats: ['avg','min','med','max','p(95)'],
  thresholds: {p100_request_errors: ['rate<=0.001'], p100_navigation_errors: ['rate<=0.001']},
};

const requests = new Counter('p100_requests');
const navigations = new Counter('p100_navigations');
const requestErrors = new Rate('p100_request_errors');
const navigationErrors = new Rate('p100_navigation_errors');
const requestDuration = new Trend('p100_request_duration',true);
const navigationDuration = new Trend('p100_navigation_duration',true);

function wrapped(response) {
  return {status:response.status, body:response.body,
    mime:response.headers['Content-Type'] ?? '',
    sha256:response.body instanceof ArrayBuffer ? crypto.sha256(response.body,'hex') : '',
    duration_ms:response.timings.duration};
}
const transport = {
  clearCookies: ()=>http.cookieJar().clear(config.base_url+'/api/auth/refresh'),
  request: (method,target,body,headers)=>wrapped(http.request(method,config.base_url+target,body,{
    headers,redirects:0,timeout:'5s',tags:{name:target.split('?')[0]},
    responseCallback:http.expectedStatuses(target==='/api/auth/refresh'?401:200),
  })),
  batch: definitions=>http.batch(definitions.map(([method,target,body,headers])=>[
    method,config.base_url+target,body,{headers,redirects:0,timeout:'5s',responseType:'binary',
      tags:{name:target.split('?')[0]},responseCallback:http.expectedStatuses(200)},
  ])).map(wrapped),
};
const stats = {
  request: (response,ok)=>{requests.add(1);requestErrors.add(!ok);requestDuration.add(response.duration_ms);},
  navigation: (duration,ok)=>{navigations.add(1);navigationErrors.add(!ok);navigationDuration.add(duration);},
};

export default function() {coldNavigation(config,resources,transport,stats);}

export function handleSummary(data) {
  // Legacy metric schema is deliberately selected on the pinned k6 CLI.
  if (!data.metrics || !data.metrics.p100_navigations?.values) throw new Error('unsupported k6 summary schema');
  const result = {schema:'p100-k6-run-v1',variant:config.variant,vus:config.vus,
    repetition:config.repetition,warmup:config.warmup,duration_seconds:config.duration_seconds,
    source_sha:config.source_sha,harness_sha:config.harness_sha,resource_digest:discovery.resource_digest,
    browser_concurrent:false,resource_count:resources.length,
    metrics:data.metrics};
  return {[config.summary_file]:JSON.stringify(result,null,2)+'\n'};
}
