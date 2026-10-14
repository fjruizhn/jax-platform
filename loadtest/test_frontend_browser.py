"""Node helper behavior tests; Chromium is not started by this module."""
from pathlib import Path
import subprocess
import unittest

ROOT = Path(__file__).resolve().parents[1]
MODULE = ROOT / 'loadtest/frontend_browser.mjs'


class BrowserHelpersTests(unittest.TestCase):
    def node(self, script):
        self.assertTrue(MODULE.exists(), 'browser harness not implemented')
        source = f'import {{allowedDestination, isExpectedResponse, resourcePlan, safePath}} from {str(MODULE)!r};\n' + script
        result = subprocess.run(['node', '--input-type=module', '-e', source], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_http_ws_guard_is_exact_and_redirect_target_is_rechecked(self):
        self.node('''
import assert from 'node:assert/strict';
const ports = [49001, 49002];
for (const url of ['http://axioma-ia.io:49001/a', 'ws://axioma-ia.io:49001/ws/270', 'http://127.0.0.1:49002/api/state']) {
  assert.equal(allowedDestination(url, ports), true);
}
for (const url of ['http://axioma-ia.io:8080/', 'ws://localhost:8080/ws', 'http://127.0.0.1:5173/',
 'http://evil.invalid:49001/', 'http://axioma-ia.io.evil.invalid:49001/', 'file:///etc/passwd',
 'http://axioma-ia.io:49003/', 'http://user:pass@axioma-ia.io:49001/', 'https://axioma-ia.io:49001/']) {
  assert.equal(allowedDestination(url, ports), false, url);
}
assert.equal(allowedDestination(new URL('http://external.invalid/x', 'http://axioma-ia.io:49001/a').href, ports), false);
''')

    def test_expected_401_is_only_prelogin_post_refresh(self):
        self.node('''
import assert from 'node:assert/strict';
const refresh = {path:'/api/auth/refresh',method:'POST',status:401,beforeLogin:true};
assert.equal(isExpectedResponse(refresh), true);
for (const change of [{path:'/api/state'}, {beforeLogin:false}, {method:'GET'}, {status:500}, {path:'/api/auth/refresh?x=1'}]) {
  assert.equal(isExpectedResponse({...refresh,...change}), false);
}
assert.equal(isExpectedResponse({...refresh,status:200}), true);
''')

    def test_full_observed_transitive_graph_deduplicates_and_preserves_vite_queries(self):
        self.node('''
import assert from 'node:assert/strict';
const one = {method:'GET',status:200,type:'script',mime:'text/javascript',bytes:123,sha256:'a'.repeat(64)};
const resources = resourcePlan([
 {...one,path:'/src/main.jsx'}, {...one,path:'/src/store/a.js'},
 {...one,path:'/node_modules/.vite/deps/react.js?v=abc123'},
 {...one,path:'/src/store/a.js'}, {...one,path:'/api/state',type:'xhr'},
 {...one,path:'/font.woff2',type:'font',mime:'font/woff2'},
 {...one,path:'/style.css?direct',type:'stylesheet',mime:'text/css'}]);
assert.equal(resources.length, 5);
assert.ok(resources.some(x=>x.path==='/node_modules/.vite/deps/react.js?v=abc123'));
assert.ok(resources.some(x=>x.path==='/font.woff2'));
assert.ok(resources.some(x=>x.path==='/src/store/a.js'));
assert.throws(()=>resourcePlan([{...one,path:'/a.js',status:404}]));
assert.throws(()=>resourcePlan([{...one,path:'/a.js'}, {...one,path:'/a.js',sha256:'b'.repeat(64)}]));
assert.throws(()=>resourcePlan([]));
''')

    def test_ui_language_uses_actual_choice_and_translations_not_static_html_attribute(self):
        self.node('''
import assert from 'node:assert/strict';
const browser=await import(MODULE);
assert.equal(typeof browser.validateSnapshot, 'function', 'UI snapshot validator not implemented');
const view={selected_language:'en',document_lang:'es',theme_attribute:'claro'};
assert.doesNotThrow(()=>browser.validateSnapshot(view,'en','light'));
assert.throws(()=>browser.validateSnapshot({...view,selected_language:'es'},'en','light'));
assert.throws(()=>browser.validateSnapshot(view,'en','dark'));
'''.replace('MODULE', repr(str(MODULE))))

    def test_sensitive_query_is_rejected_instead_of_serialized(self):
        self.node('''
import assert from 'node:assert/strict';
assert.equal(safePath('http://axioma-ia.io:49001/a.js?v=abc'), '/a.js?v=abc');
assert.equal(safePath('http://axioma-ia.io:49001/api/x?token=secret', true), '/api/x');
assert.equal(safePath('data:text/plain,secret-body', true), '/[non-network]');
assert.throws(()=>safePath('http://axioma-ia.io:49001/a.js?token=secret'));
assert.throws(()=>safePath('http://axioma-ia.io:49001/a.js?unreviewed=query'));
''')


if __name__ == '__main__':
    unittest.main()
