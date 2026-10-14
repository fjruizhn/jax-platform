import unittest
try:
    from loadtest.frontend_metrics import summarize, build_matrix
except ImportError:
    summarize = build_matrix = None


def run(variant, vus, repetition, navs=10, p95=100, errors=0, naverrors=0):
    return {'variant': variant, 'vus': vus, 'repetition': repetition, 'warmup': False,
            'source_sha': 'a' * 40, 'harness_sha': 'b' * 40,
            'resource_digest': ('c' if variant == 'vite' else 'd') * 64,
            'metrics': {
                'http_reqs': {'values': {'count': navs * 100, 'rate': navs * 10}},
                'p100_requests': {'values': {'count': navs * 100, 'rate': navs * 10}},
                'p100_navigations': {'values': {'count': navs, 'rate': navs / 10}},
                'p100_request_duration': {'values': {'p(95)': p95 / 10}},
                'p100_navigation_duration': {'values': {'p(95)': p95}},
                'p100_request_errors': {'values': {'rate': errors}},
                'p100_navigation_errors': {'values': {'rate': naverrors}},
                'data_received': {'values': {'count': navs * 4096}},
            }}


class MetricsTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(summarize, 'statistics not implemented')

    def dataset(self):
        return [run(name, vu, rep, navs=10 * vu) for name in ('vite', 'nginx')
                for vu in (1, 8, 32, 64, 128) for rep in (1, 2, 3)] + [
                    dict(run(name, vu, 0), warmup=True) for name in ('vite', 'nginx')
                    for vu in (1, 8, 32, 64, 128)]

    def test_three_repetition_medians_keep_request_and_navigation_denominators_separate(self):
        runs = self.dataset()
        runs[0]['metrics']['p100_navigation_duration']['values']['p(95)'] = 50000
        result = summarize(runs)
        row = result['variants']['vite']['levels'][0]
        self.assertEqual(row['navigation_p95_ms'], 100)
        self.assertEqual(row['navigation_per_second'], 1)
        self.assertEqual(row['requests_per_second'], 100)
        self.assertEqual(row['payload_bytes'], 40960)
        self.assertEqual(result['variants']['vite']['degradation']['censored_at_vus'], 128)
        self.assertIsNone(result['variants']['vite']['degradation']['first_observed_vus'])
        self.assertFalse(result['browser_concurrent'])

    def test_degradation_uses_logical_navigation_efficiency_not_raw_module_rps(self):
        runs = self.dataset()
        for item in runs:
            if item['variant'] == 'vite' and item['vus'] >= 32:
                item['metrics']['p100_navigations']['values']['rate'] = item['vus'] * .49
                item['metrics']['http_reqs']['values']['rate'] = 50000
        result = summarize(runs)
        self.assertEqual(result['variants']['vite']['degradation']['first_observed_vus'], 32)
        self.assertIn('navigation_throughput_per_vu', result['variants']['vite']['levels'][2]['degradation_reasons'])

    def test_any_bad_repetition_reports_errors_even_if_median_is_zero(self):
        runs = self.dataset()
        runs[3]['metrics']['p100_request_errors']['values']['rate'] = .002
        result = summarize(runs)
        row = result['variants']['vite']['levels'][1]
        self.assertEqual(row['request_error_rate_median'], 0)
        self.assertEqual(row['request_error_rate_max'], .002)
        self.assertEqual(result['variants']['vite']['degradation']['first_observed_vus'], 8)

    def test_navigation_failure_and_doubled_latency_count_as_degradation(self):
        runs = self.dataset()
        for item in runs:
            if item['variant'] == 'nginx' and item['vus'] == 8:
                item['metrics']['p100_navigation_errors']['values']['rate'] = .01
            if item['variant'] == 'vite' and item['vus'] == 64:
                item['metrics']['p100_navigation_duration']['values']['p(95)'] = 201
        result = summarize(runs)
        self.assertEqual(result['variants']['nginx']['degradation']['first_observed_vus'], 8)
        self.assertEqual(result['variants']['vite']['degradation']['first_observed_vus'], 64)

    def test_missing_duplicate_empty_nan_runs_fail_closed(self):
        cases = [self.dataset()[:-1], self.dataset() + [self.dataset()[0]], [], self.dataset()[:30]]
        missing = self.dataset()
        del missing[0]['metrics']['p100_navigation_duration']
        cases.append(missing)
        invalid = self.dataset()
        invalid[0]['metrics']['p100_navigations']['values']['rate'] = float('nan')
        cases.append(invalid)
        empty = self.dataset()
        empty[0]['metrics']['p100_navigations']['values']['count'] = 0
        cases.append(empty)
        for data in cases:
            with self.subTest(case=len(data)):
                with self.assertRaises(ValueError):
                    summarize(data)

    def test_source_harness_and_graph_mismatches_fail_closed(self):
        for field, value in [('source_sha', 'e' * 40), ('harness_sha', 'e' * 40),
                             ('resource_digest', 'e' * 64), ('source_sha', 'not-a-sha')]:
            data = self.dataset()
            data[0][field] = value
            with self.subTest(field=field):
                with self.assertRaises(ValueError):
                    summarize(data)

    def test_boolean_concurrency_is_not_a_real_matrix_run(self):
        data = self.dataset()
        data[0]['vus'] = True
        with self.assertRaises(ValueError):
            summarize(data)

    def test_warmups_are_excluded_but_required_by_matrix(self):
        result = summarize(self.dataset())
        self.assertEqual(len(result['variants']['vite']['levels']), 5)
        matrix = build_matrix(['vite', 'nginx'], duration_seconds=10, warmup_seconds=2)
        self.assertEqual(len([x for x in matrix if x['warmup']]), 10)
        self.assertEqual(len([x for x in matrix if not x['warmup']]), 30)
        self.assertEqual([x['variant'] for x in matrix[:2]], ['vite', 'nginx'])
        self.assertEqual([x['vus'] for x in matrix if not x['warmup'] and x['variant'] == 'vite' and x['repetition'] == 1], [1, 8, 32, 64, 128])
        with self.assertRaises(ValueError):
            build_matrix(['vite', 'nginx'], duration_seconds=0, warmup_seconds=2)


if __name__ == '__main__':
    unittest.main()
