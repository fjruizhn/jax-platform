/** k6 v2.2.0 HTTP resource replay. Does not launch concurrent browsers.
 * Invoke only after controller B approval in the isolated real-unit rehearsal.
 * CLI must supply an empty private k6 config, --include-system-env-vars=false,
 * --no-usage-report, --new-machine-readable-summary=false and P100_RUN_CONFIG.
 */
import http from 'k6/http';
import crypto from 'k6/crypto';
import {Counter, Rate, Trend} from 'k6/metrics';
import {coldNavigation, validatePlan} from './frontend_http_flow.mjs';
import {buildRunResult} from './frontend_http_result.mjs';

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

const canary = config.synthetic_counter_canary === true;
if (typeof config.synthetic_counter_canary !== 'boolean' ||
    (canary ? config.variant!=='synthetic-counter-canary' || config.vus!==1 || config.duration_seconds!==1 ||
      config.graceful_stop_seconds!==1 || config.source_sha!=='a'.repeat(40) || config.harness_sha!=='b'.repeat(40)
    : config.duration_seconds!==(config.warmup?5:30) || config.graceful_stop_seconds!==30)) {
  throw new Error('run timing must match predeclared schema2 matrix or explicit synthetic counter canary');
}
export const options = {
  scenarios: {workflow: {executor:'constant-vus',vus:config.vus,duration:`${config.duration_seconds}s`,
    gracefulStop:`${config.graceful_stop_seconds}s`}},
  maxRedirects: 0, batch: 6, batchPerHost: 6, noCookiesReset: false,
  hosts: {[hostname]: '127.0.0.1'},
  summaryTrendStats: ['count','avg','min','med','max','p(95)'],
  thresholds: {p100_request_errors: ['rate<=0.001'], p100_terminal_navigation_errors: ['rate<=0.001']},
};

const requests = new Counter('p100_validated_requests');
const validationFailures = new Counter('p100_validation_failures');
const started = new Counter('p100_nav_started');
const successful = new Counter('p100_nav_successful');
const failed = new Counter('p100_nav_failed');
const requestErrors = new Rate('p100_request_errors');
const terminalErrors = new Rate('p100_terminal_navigation_errors');
const requestDuration = new Trend('p100_validated_request_duration',true);
const successfulDuration = new Trend('p100_successful_nav_duration',true);

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
  start: ()=>started.add(1),
  request: (response,ok)=>{
    requests.add(1);if(!ok)validationFailures.add(1);
    requestErrors.add(!ok);requestDuration.add(response.duration_ms);
  },
  terminal: (duration,ok)=>{
    if(ok) {successful.add(1);successfulDuration.add(duration);} else failed.add(1);
    terminalErrors.add(!ok);
  },
};

export default function() {coldNavigation(config,resources,transport,stats);}

export function handleSummary(data) {
  const result=buildRunResult(config,discovery,data);
  return {[config.summary_file]:JSON.stringify(result,null,2)+'\n'};
}
