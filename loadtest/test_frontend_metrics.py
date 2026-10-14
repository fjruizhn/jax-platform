"""Controlled schema2 cohorts: cancellation is evidence, never a fake latency."""
import unittest
from loadtest.frontend_metrics import summarize, build_matrix


def run(variant, vus, repetition, successful=30, failed=0, incomplete=0, p95=100,
        validation_failures=0, warmup=False):
    elapsed = 35000 if warmup else 60000 if incomplete else 30000
    started = successful + failed + incomplete
    observed = (successful + failed) * 100
    samples = successful
    data = {'schema': 'p100-k6-run-v2', 'synthetic_counter_canary': False,
            'variant': variant, 'vus': vus, 'repetition': repetition, 'warmup': warmup,
            'duration_seconds': 5 if warmup else 30, 'graceful_stop_seconds': 30,
            'observed_duration_ms': elapsed, 'resource_count': 1,
            'source_sha': 'a' * 40, 'harness_sha': 'b' * 40,
            'resource_digest': ('c' if variant == 'vite' else 'd') * 64,
            'navigation_counts': {'started': started, 'successful': successful,
                                  'failed': failed, 'incomplete': incomplete},
            'incomplete_cause': 'scenario_deadline' if incomplete else None,
            'latency_sample_count': samples,
            'latency_censored': bool(incomplete),
            'navigation_p95_ms': p95 if successful and not incomplete else None,
            'completed_only_navigation_p95_ms': p95 if successful else None,
            'latency_reason': 'scenario_deadline' if incomplete else 'no_successful_navigation' if not successful else None,
            'metrics': {
                'p100_nav_started': {'values': {'count': started}},
                'p100_nav_successful': {'values': {'count': successful}},
                'p100_nav_failed': {'values': {'count': failed}},
                'p100_validated_requests': {'values': {'count': observed}},
                'p100_validation_failures': {'values': {'count': validation_failures}},
                'http_reqs': {'values': {'count': observed}},
                'data_received': {'values': {'count': observed * 4096}},
            }}
    if successful:
        data['metrics']['p100_successful_nav_duration'] = {'values': {'count': successful, 'p(95)': p95}}
    if observed:
        data['metrics']['http_req_duration'] = {'values': {'count': observed, 'p(95)': p95 / 10}}
    return data


class MetricsTests(unittest.TestCase):
    def summary(self, runs):
        try:
            return summarize(runs)
        except ValueError as exc:
            self.fail(f'valid schema2/censored cohort rejected: {exc}')

    def dataset(self):
        return [run(name, vu, rep, successful=30 * vu) for name in ('vite', 'nginx')
                for vu in (1, 8, 32, 64, 128) for rep in (1, 2, 3)] + [
                    run(name, vu, 0, successful=5 * vu, warmup=True) for name in ('vite', 'nginx')
                    for vu in (1, 8, 32, 64, 128)]

    def replace(self, runs, variant, vus, rep, **kwargs):
        index = next(i for i, item in enumerate(runs) if
                     (item['variant'], item['vus'], item['repetition'], item['warmup']) == (variant, vus, rep, False))
        runs[index] = run(variant, vus, rep, **kwargs)

    def test_three_repetition_p95_medians_and_successful_throughput_use_observed_time(self):
        runs = self.dataset()
        self.replace(runs, 'vite', 1, 1, p95=50000)
        result = self.summary(runs)
        row = result['variants']['vite']['levels'][0]
        self.assertEqual(row['navigation_p95_ms'], 100)
        self.assertEqual(row['navigation_per_second'], 1)
        self.assertEqual(row['requests_per_second'], 100)
        self.assertEqual(row['payload_bytes'], 30 * 100 * 4096)
        self.assertIsNone(result['variants']['vite']['degradation']['first_observed_vus'])
        self.assertEqual(result['variants']['vite']['degradation']['censored_at_vus'], 128)
        self.assertFalse(result['browser_concurrent'])

    def test_builtin_http_requests_include_partial_batch_but_not_fake_navigation_success(self):
        runs = self.dataset()
        self.replace(runs, 'vite', 32, 1, successful=0, incomplete=32)
        partial = next(x for x in runs if x['variant']=='vite' and x['vus']==32 and x['repetition']==1)
        partial['metrics']['http_reqs']['values']['count'] = 1000
        partial['metrics']['http_req_duration'] = {'values': {'count': 1000, 'p(95)': 500}}
        row = self.summary(runs)['variants']['vite']['levels'][2]
        one = row['runs'][0]
        self.assertEqual(one['requests_per_second'], 1000 / 60)
        self.assertEqual(one['validated_request_count'], 0)
        self.assertEqual(one['navigation_per_second'], 0)
        self.assertEqual(one['navigation_counts']['incomplete'], 32)
        self.assertIsNone(row['navigation_p95_ms'])
        self.assertEqual(row['navigation_error_rate_max'], 1)
        self.assertIn('navigation_incomplete', row['degradation_reasons'])

    def test_one_censored_repetition_keeps_headline_null_and_all_three_diagnostics(self):
        runs = self.dataset()
        self.replace(runs, 'vite', 8, 2, successful=200, incomplete=1, p95=900)
        row = self.summary(runs)['variants']['vite']['levels'][1]
        self.assertIsNone(row['navigation_p95_ms'])
        self.assertEqual(row['completed_only_p95_ms_by_repetition'], [100, 900, 100])
        self.assertEqual(len(row['runs']), 3)
        self.assertFalse(row['latency_relative_evaluable'])
        self.assertEqual(row['navigation_error_rate_max'], 1 / 201)

    def test_all_interrupted_and_censored_warmup_are_preserved_without_trend_zero(self):
        runs = self.dataset()
        for rep in (1, 2, 3):
            self.replace(runs, 'vite', 128, rep, successful=0, incomplete=128)
        index = next(i for i,x in enumerate(runs) if x['variant']=='vite' and x['vus']==128 and x['warmup'])
        runs[index] = run('vite', 128, 0, successful=0, incomplete=128, warmup=True)
        result = self.summary(runs)
        row = result['variants']['vite']['levels'][4]
        self.assertEqual(row['navigation_per_second'], 0)
        self.assertIsNone(row['request_p95_ms'])
        self.assertIsNone(row['navigation_p95_ms'])
        self.assertEqual(row['completed_only_p95_ms_by_repetition'], [None] * 3)
        self.assertEqual(result['variants']['vite']['warmups'][4]['navigation_counts']['incomplete'], 128)
        self.assertEqual(len(result['variants']['vite']['levels']), 5)

    def test_censored_baseline_does_not_invent_relative_capacity(self):
        runs = self.dataset()
        for rep in (1, 2, 3):
            self.replace(runs, 'vite', 1, rep, successful=0, incomplete=1)
        variant = self.summary(runs)['variants']['vite']
        self.assertFalse(variant['baseline_established'])
        self.assertTrue(all(not row['latency_relative_evaluable'] for row in variant['levels']))
        self.assertTrue(all(not row['throughput_relative_evaluable'] for row in variant['levels']))
        self.assertIsNone(variant['degradation']['censored_at_vus'])

    def test_error_and_latency_and_half_baseline_criteria_are_not_lowered(self):
        runs = self.dataset()
        for rep in (1, 2, 3):
            self.replace(runs, 'vite', 8, rep, successful=117) # .4875 nav/s/VU vs baseline1
            self.replace(runs, 'nginx', 32, rep, successful=959, failed=1) # >.1%
            self.replace(runs, 'vite', 64, rep, successful=1920, p95=201)
        result = self.summary(runs)
        self.assertIn('navigation_throughput_per_vu', result['variants']['vite']['levels'][1]['degradation_reasons'])
        self.assertIn('navigation_errors', result['variants']['nginx']['levels'][2]['degradation_reasons'])
        self.assertIn('navigation_latency', result['variants']['vite']['levels'][3]['degradation_reasons'])

    def test_validation_errors_in_one_repetition_are_not_hidden_by_zero_median(self):
        runs = self.dataset()
        self.replace(runs, 'vite', 8, 1, successful=239, failed=1, validation_failures=25)
        row = self.summary(runs)['variants']['vite']['levels'][1]
        self.assertEqual(row['request_error_rate_median'], 0)
        self.assertGreater(row['request_error_rate_max'], .001)
        self.assertIn('request_errors', row['degradation_reasons'])

    def test_partial_latency_metric_sample_is_censored_not_a_zero_or_fake_quantile(self):
        runs = self.dataset()
        item = runs[0]
        item['metrics'].pop('p100_successful_nav_duration')
        item.update(observed_duration_ms=60000, latency_sample_count=0, latency_censored=True, navigation_p95_ms=None,
                    completed_only_navigation_p95_ms=None, latency_reason='latency_sample_incomplete')
        row = self.summary(runs)['variants']['vite']['levels'][0]
        self.assertIsNone(row['navigation_p95_ms'])
        self.assertEqual(row['completed_only_p95_ms_by_repetition'], [None, 100, 100])

    def test_missing_duplicate_provenance_schema_duration_and_counts_fail_closed(self):
        cases = [self.dataset()[:-1], self.dataset()+[self.dataset()[0]], [], self.dataset()[:30]]
        for key, value in [('schema', 'p100-k6-run-v1'), ('vus', True), ('source_sha', 'bad'),
                           ('harness_sha', 'e'*40), ('resource_digest', 'f'*64),
                           ('duration_seconds', 10), ('graceful_stop_seconds', 5),
                           ('observed_duration_ms', float('nan')), ('synthetic_counter_canary', True)]:
            runs = self.dataset()
            runs[0][key] = value
            cases.append(runs)
        for key in ('started', 'successful', 'failed', 'incomplete'):
            runs = self.dataset()
            runs[0]['navigation_counts'][key] = -1
            cases.append(runs)
        missing = self.dataset()
        missing[0]['metrics'].pop('p100_nav_started')
        cases.append(missing)
        empty = self.dataset()
        empty[0] = run('vite',1,1,successful=0)
        cases.append(empty)
        forged = self.dataset()
        forged[0]['navigation_counts']['incomplete'] = 1
        cases.append(forged)
        unjustified = self.dataset()
        unjustified[0] = run('vite',1,1,successful=0,incomplete=1)
        unjustified[0]['observed_duration_ms'] = 31000
        cases.append(unjustified)
        for data in cases:
            with self.subTest(index=cases.index(data) if data else 0):
                with self.assertRaises(ValueError):
                    summarize(data)

    def test_matrix_fixes_30_measured_5_warmup_30_grace_before_any_data(self):
        try:
            matrix = build_matrix(['vite', 'nginx'], duration_seconds=30, warmup_seconds=5, graceful_stop_seconds=30)
        except TypeError:
            self.fail('matrix does not expose the schema2 graceful-stop contract')
        self.assertEqual(len(matrix), 40)
        self.assertEqual(sum(x['warmup'] for x in matrix), 10)
        self.assertEqual([x['variant'] for x in matrix[:2]], ['vite','nginx'])
        for item in matrix:
            self.assertEqual(item['duration_seconds'], 5 if item['warmup'] else 30)
            self.assertEqual(item['graceful_stop_seconds'], 30)
        with self.assertRaises(ValueError):
            build_matrix(['vite','nginx'],duration_seconds=10,warmup_seconds=5,graceful_stop_seconds=30)


if __name__ == '__main__':
    unittest.main()
