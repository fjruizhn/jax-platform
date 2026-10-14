from pathlib import Path
import subprocess
import unittest

MODULE = Path(__file__).with_name('frontend_http_flow.mjs')


class HttpFlowTests(unittest.TestCase):
    def run_node(self, case):
        self.assertTrue(MODULE.exists(), 'HTTP navigation flow not implemented')
        script = '''
import assert from 'node:assert/strict';
import {validatePlan, coldNavigation} from MODULE;
const config={base_url:'http://axioma-ia.io:49001',allowed_ports:[49001],fixture_credentials:{email:'p100@example.invalid',password:'synthetic'},marker:'p100-marker'};
const plan=[{path:'/main.js',mime:'text/javascript',sha256:'a'.repeat(64),bytes:20},{path:'/deep/module.js?v=abc',mime:'text/javascript',sha256:'b'.repeat(64),bytes:20},{path:'/font.woff2',mime:'font/woff2',sha256:'c'.repeat(64),bytes:20}];
let counters={requests:0,errors:0,navigations:0,failed:0};
const stats={request:(r,ok)=>{counters.requests++;if(!ok)counters.errors++;},navigation:(duration,ok)=>{counters.navigations++;if(!ok)counters.failed++;}};
const transport={clearCookies:()=>{},request:(method,target,body,headers)=>{
 let payload, status=200, mime='application/json',hash='';
 if(target==='/login'||target==='/proyectos'){payload='<html><div id="root"></div><script type="module"></script></html>';mime='text/html';}
 else if(target==='/api/auth/refresh'){status=401;payload={detail:'no_session'};}
 else if(target==='/api/auth/login'){payload={access_token:'fixture-token',user_id:270};}
 else if(target==='/api/auth/me'){payload={user_id:270,email:config.fixture_credentials.email};}
 else if(target==='/api/state'){payload={facets:{},active_pipelines:{},las_manos_alive:true,kill_switch_active:false};}
 else if(target.startsWith('/api/proyectos')){payload={proyectos:[{nombre:config.marker}],siguiente:null};}
 else if(target==='/api/apariencia'){payload={system_name:'P100',theme_default:'dark',lang_default:'es'};}
 else {const resource=plan.find(x=>x.path===target); assert.ok(resource,'resource graph was bypassed');payload='resource';mime=resource.mime;hash=resource.sha256;}
 return {status,body:typeof payload==='string'?payload:JSON.stringify(payload),mime,sha256:hash,duration_ms:1};
},batch:(requests)=>requests.map(request=>transport.request(...request))};
CASE
'''.replace('MODULE', repr(str(MODULE))).replace('CASE', case)
        result = subprocess.run(['node', '--input-type=module', '-e', script], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_complete_cold_workflow_fetches_every_transitive_resource_and_marker(self):
        self.run_node('''
validatePlan(config,plan);
assert.equal(coldNavigation(config,plan,transport,stats),true);
assert.equal(counters.navigations,1);assert.equal(counters.failed,0);assert.equal(counters.errors,0);
assert.equal(counters.requests,15); // two documents+two complete graphs+seven logical API operations
''')

    def test_fast_404_and_wrong_200_resource_body_are_failures(self):
        self.run_node('''
const original=transport.request;
transport.request=(...args)=>args[1]==='/deep/module.js?v=abc'?{status:404,body:'missing',mime:'text/html',sha256:'b'.repeat(64),duration_ms:.1}:original(...args);
assert.equal(coldNavigation(config,plan,transport,stats),false);
assert.equal(counters.failed,1);assert.equal(counters.errors,2);
transport.request=(...args)=>args[1]==='/main.js'?{...original(...args),sha256:'f'.repeat(64)}:original(...args);
assert.equal(coldNavigation(config,plan,transport,stats),false);
assert.equal(counters.failed,2);assert.equal(counters.errors,4);
''')

    def test_200_json_without_marker_is_navigation_failure(self):
        self.run_node('''
const original=transport.request;
transport.request=(...args)=>args[1].startsWith('/api/proyectos')?{status:200,body:'{"proyectos":[],"siguiente":null}',mime:'application/json',duration_ms:1}:original(...args);
assert.equal(coldNavigation(config,plan,transport,stats),false);
assert.equal(counters.failed,1);assert.equal(counters.errors,2);
''')

    def test_missing_login_and_incomplete_graph_count_failed_navigation(self):
        self.run_node('''
const original=transport.request;
transport.request=(...args)=>args[1]==='/api/auth/login'?{status:200,body:'{}',mime:'application/json',duration_ms:1}:original(...args);
assert.equal(coldNavigation(config,plan,transport,stats),false);
assert.equal(counters.failed,1);assert.equal(counters.errors,1);
transport.request=original;
transport.batch=()=>[];
assert.throws(()=>coldNavigation(config,plan,transport,stats));
assert.equal(counters.failed,2);
''')

    def test_unreviewed_destinations_or_resource_graph_fail_before_transport(self):
        self.run_node('''
for(const base of ['http://127.0.0.1:8080','http://axioma-ia.io:5173','http://evil.invalid:49001','https://axioma-ia.io:49001']){
 assert.throws(()=>validatePlan({...config,base_url:base},plan));
}
for(const resource of [{...plan[0],path:'//evil.invalid/a'}, {...plan[0],path:'/a?token=secret'}, {...plan[0],sha256:'bad'}]){
 assert.throws(()=>validatePlan(config,[resource]));
}
assert.throws(()=>validatePlan(config,[]));
assert.throws(()=>validatePlan(config,[plan[0],plan[0]]));
assert.equal(counters.requests,0);
''')


if __name__ == '__main__':
    unittest.main()
