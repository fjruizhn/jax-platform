from pathlib import Path
import subprocess
import unittest

MODULE = Path(__file__).with_name('frontend_http_result.mjs')


class RunResultTests(unittest.TestCase):
    def node(self, case):
        self.assertTrue(MODULE.exists(), 'schema2 result builder missing')
        source = '''
import assert from 'node:assert/strict';
import {buildRunResult} from MODULE;
const config={variant:'vite',vus:128,repetition:1,warmup:false,duration_seconds:30,graceful_stop_seconds:30,source_sha:'a'.repeat(40),harness_sha:'b'.repeat(40),synthetic_counter_canary:false};
const discovery={resource_digest:'c'.repeat(64),resources:[{path:'/main.js'}]};
const counter=count=>({values:{count}});
const data={state:{testRunDurationMs:60000},metrics:{p100_nav_started:counter(128),http_reqs:counter(10)}};
CASE
'''.replace('MODULE', repr(str(MODULE))).replace('CASE', case)
        result = subprocess.run(['node','--input-type=module','-e',source],capture_output=True,text=True,timeout=10)
        self.assertEqual(result.returncode,0,result.stderr)

    def test_every_iteration_interrupted_emits_counts_and_null_not_trend_zero(self):
        self.node('''
const result=buildRunResult(config,discovery,data);
assert.equal(result.schema,'p100-k6-run-v2');
assert.deepEqual(result.navigation_counts,{started:128,successful:0,failed:0,incomplete:128});
assert.equal(result.navigation_p95_ms,null);
assert.equal(result.completed_only_navigation_p95_ms,null);
assert.equal(result.incomplete_cause,'scenario_deadline');
assert.equal(result.latency_sample_count,0);
assert.ok(!('p100_successful_nav_duration' in result.metrics));
''')

    def test_no_median_can_hide_a_censored_navigation_with_successful_samples(self):
        self.node('''
data.metrics.p100_nav_successful=counter(127);
data.metrics.p100_successful_nav_duration={values:{count:127,'p(95)':1234}};
const result=buildRunResult(config,discovery,data);
assert.equal(result.navigation_counts.incomplete,1);
assert.equal(result.navigation_p95_ms,null);
assert.equal(result.completed_only_navigation_p95_ms,1234);
assert.equal(result.latency_censored,true);
''')

    def test_partial_latency_commit_is_censored_and_negative_accounting_rejected(self):
        self.node('''
data.metrics.p100_nav_successful=counter(128);
data.metrics.p100_successful_nav_duration={values:{count:127,'p(95)':100}};
let result=buildRunResult(config,discovery,data);
assert.equal(result.navigation_counts.incomplete,0);
assert.equal(result.navigation_p95_ms,null);
assert.equal(result.completed_only_navigation_p95_ms,null);
assert.equal(result.latency_reason,'latency_sample_incomplete');
data.metrics.p100_nav_successful=counter(129);
assert.throws(()=>buildRunResult(config,discovery,data));
''')

    def test_missing_started_or_nonfinite_elapsed_is_invalid_not_censored(self):
        self.node('''
delete data.metrics.p100_nav_started;
assert.throws(()=>buildRunResult(config,discovery,data));
data.metrics.p100_nav_started=counter(128);
data.state.testRunDurationMs=NaN;
assert.throws(()=>buildRunResult(config,discovery,data));
''')


if __name__=='__main__':
    unittest.main()
