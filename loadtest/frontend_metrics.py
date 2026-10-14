"""P100 schema2: successful HTTP workflows, explicit deadline censoring, no pooled p95."""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

LEVELS = (1, 8, 32, 64, 128)
REPETITIONS = (1, 2, 3)
MEASURED_SECONDS, WARMUP_SECONDS, GRACEFUL_STOP_SECONDS = 30, 5, 30
CRITERION = {
    'error_rate_limit': .001,
    'navigation_p95_baseline_multiplier': 2,
    'navigation_per_second_per_vu_baseline_fraction': .5,
    'latency_aggregate': 'median of three uncensored repetition p95 values; null if any is censored/missing; never pooled',
    'navigation_error': '(failed + incomplete) / started; deadline incompleteness is not a server error',
    'error_aggregate': 'maximum repetition rate; medians also reported',
    'throughput': 'successful terminal workflows / observed elapsed seconds including drain',
    'baseline_vus': 1,
    'strict_comparison': '>', 'throughput_comparison': '<',
}


def build_matrix(variants, duration_seconds, warmup_seconds, graceful_stop_seconds):
    if len(variants) != 2 or len(set(variants)) != 2 or (
            duration_seconds, warmup_seconds, graceful_stop_seconds) != (
            MEASURED_SECONDS, WARMUP_SECONDS, GRACEFUL_STOP_SECONDS):
        raise ValueError('two distinct variants and the predeclared 30/5/30 timing required')
    matrix = []
    for vus in LEVELS:
        for variant in variants:
            matrix.append({'variant': variant, 'vus': vus, 'repetition': 0, 'warmup': True,
                           'duration_seconds': warmup_seconds, 'graceful_stop_seconds': graceful_stop_seconds})
        for repetition in REPETITIONS:
            for variant in variants if repetition % 2 else variants[::-1]:
                matrix.append({'variant': variant, 'vus': vus, 'repetition': repetition, 'warmup': False,
                               'duration_seconds': duration_seconds, 'graceful_stop_seconds': graceful_stop_seconds})
    return matrix


def _number(value, label, *, integer=False, positive=False):
    if type(value) not in (int, float) or not math.isfinite(value) or value < 0 or (
            integer and int(value) != value) or (positive and value == 0):
        raise ValueError(f'invalid numeric {label}')
    return value


def _count(run, name, required=False):
    metrics = run['metrics']
    if name not in metrics:
        if required:
            raise ValueError(f'missing start evidence {name}')
        return 0
    try:
        value = metrics[name]['values']['count']
    except (KeyError, TypeError) as exc:
        raise ValueError(f'invalid counter {name}') from exc
    return _number(value, name, integer=True)


def _p95(run, name, count):
    if not count:
        return None
    try:
        value = run['metrics'][name]['values']['p(95)']
    except (KeyError, TypeError) as exc:
        raise ValueError(f'missing p95 for observed samples {name}') from exc
    return _number(value, name)


def inspect_run(run):
    """Verify raw/normalized accounting before allowing a row into the matrix."""
    if not isinstance(run, dict) or run.get('schema') != 'p100-k6-run-v2' or run.get('synthetic_counter_canary') is not False:
        raise ValueError('only real schema2 matrix runs may be summarized')
    if type(run.get('warmup')) is not bool or type(run.get('vus')) is not int or type(run.get('repetition')) is not int:
        raise ValueError('invalid run identity types')
    if run.get('duration_seconds') != (WARMUP_SECONDS if run['warmup'] else MEASURED_SECONDS) or type(run.get('duration_seconds')) is not int or run.get('graceful_stop_seconds') != GRACEFUL_STOP_SECONDS or type(run.get('graceful_stop_seconds')) is not int:
        raise ValueError('run does not match predeclared 30/5/30 timing')
    if not isinstance(run.get('metrics'), dict):
        raise ValueError('raw metrics missing')
    for key, width in (('source_sha', 40), ('harness_sha', 40), ('resource_digest', 64)):
        if not isinstance(run.get(key), str) or not re.fullmatch(f'[0-9a-f]{{{width}}}', run[key]):
            raise ValueError(f'invalid provenance {key}')
    resource_count = _number(run.get('resource_count'), 'resource_count', integer=True, positive=True)
    elapsed = _number(run.get('observed_duration_ms'), 'observed_duration_ms', positive=True)
    if elapsed < run['duration_seconds'] * 1000 - 100:
        raise ValueError('run ended before the configured observation window')
    started = _count(run, 'p100_nav_started', True)
    successful, failed = _count(run, 'p100_nav_successful'), _count(run, 'p100_nav_failed')
    incomplete = started - successful - failed
    if not started or incomplete < 0:
        raise ValueError('navigation counters do not reconcile or no execution evidence')
    expected_counts = {'started': started, 'successful': successful, 'failed': failed, 'incomplete': incomplete}
    observed_counts = run.get('navigation_counts')
    if not isinstance(observed_counts, dict) or observed_counts.keys() != expected_counts.keys() or any(
            type(observed_counts[key]) is not int or observed_counts[key] != value for key, value in expected_counts.items()):
        raise ValueError('normalized navigation counts disagree with raw metrics')
    samples = _count(run, 'p100_successful_nav_duration')
    if samples > successful or type(run.get('latency_sample_count')) is not int or run['latency_sample_count'] != samples:
        raise ValueError('successful latency samples do not reconcile')
    missing_latency = samples < successful
    at_deadline = elapsed >= (run['duration_seconds'] + run['graceful_stop_seconds']) * 1000 - 100
    if (incomplete or missing_latency) and not at_deadline:
        raise ValueError('incomplete telemetry lacks scenario-deadline evidence')
    cause = 'scenario_deadline' if incomplete else None
    reason = cause or ('latency_sample_incomplete' if missing_latency else 'no_successful_navigation' if not successful else None)
    censored = bool(incomplete or missing_latency)
    completed_p95 = _p95(run, 'p100_successful_nav_duration', samples) if successful and samples == successful else None
    headline = completed_p95 if successful and not censored else None
    if run.get('incomplete_cause') != cause or type(run.get('latency_censored')) is not bool or run['latency_censored'] != censored or run.get('latency_reason') != reason:
        raise ValueError('censoring metadata disagrees with execution evidence')
    for key, expected in (('navigation_p95_ms', headline), ('completed_only_navigation_p95_ms', completed_p95)):
        if key not in run or (run[key] is None) != (expected is None) or (expected is not None and _number(run[key], key) != expected):
            raise ValueError('normalized p95 disagrees with observed/censored samples')
    http_count, validated = _count(run, 'http_reqs'), _count(run, 'p100_validated_requests')
    validation_failed = _count(run, 'p100_validation_failures')
    if validation_failed > validated or validated > http_count or http_count < successful * (2 * resource_count + 9):
        raise ValueError('HTTP/request validation accounting is inconsistent')
    if validation_failed and not failed + incomplete:
        raise ValueError('failed resource checks cannot be successful navigation workflows')
    request_samples = _count(run, 'http_req_duration')
    if request_samples != http_count:
        raise ValueError('HTTP duration samples do not reconcile with observed requests')
    payload = _count(run, 'data_received')
    if successful and not payload:
        raise ValueError('successful workflow without observed payload bytes')
    return {
        'repetition': run['repetition'], 'warmup': run['warmup'],
        'observed_duration_ms': elapsed, 'navigation_counts': expected_counts,
        'incomplete_cause': cause, 'latency_reason': reason, 'latency_censored': censored,
        'navigation_per_second': successful * 1000 / elapsed,
        'navigation_error_rate': (failed + incomplete) / started,
        'terminal_failure_rate': failed / started, 'incomplete_rate': incomplete / started,
        'navigation_p95_ms': headline, 'completed_only_navigation_p95_ms': completed_p95,
        'requests_per_second': http_count * 1000 / elapsed,
        'observed_http_request_count': http_count, 'validated_request_count': validated,
        'validated_request_error_rate': validation_failed / validated if validated else None,
        'validation_observation_complete': http_count == validated and incomplete == 0,
        'request_p95_ms': _p95(run, 'http_req_duration', request_samples),
        'payload_bytes': payload,
    }


def _median_present(values):
    return statistics.median(values) if all(value is not None for value in values) else None


def summarize(runs):
    if not isinstance(runs, list) or not runs:
        raise ValueError('no runs')
    variants = sorted({item.get('variant') for item in runs if isinstance(item, dict) and isinstance(item.get('variant'), str)})
    if len(variants) != 2:
        raise ValueError('exactly two variants required')
    expected = {(v, vu, rep, False) for v in variants for vu in LEVELS for rep in REPETITIONS}
    expected |= {(v, vu, 0, True) for v in variants for vu in LEVELS}
    indexed, sources, harnesses = {}, set(), set()
    graphs = {variant: set() for variant in variants}
    for run in runs:
        observation = inspect_run(run)
        key = (run.get('variant'), run['vus'], run['repetition'], run['warmup'])
        if key not in expected or key in indexed:
            raise ValueError('duplicate/unexpected matrix run')
        indexed[key] = observation
        sources.add(run['source_sha'])
        harnesses.add(run['harness_sha'])
        graphs[run['variant']].add((run['resource_digest'], run['resource_count']))
    if indexed.keys() != expected:
        raise ValueError('incomplete exact 40-run matrix including warmups')
    if len(sources) != 1 or len(harnesses) != 1 or any(len(value) != 1 for value in graphs.values()):
        raise ValueError('source/harness/graph changed across the matrix')
    result = {'schema': 'p100-http-navigation-summary-v2', 'criterion': CRITERION,
              'source_sha': next(iter(sources)), 'harness_sha': next(iter(harnesses)),
              'browser_concurrent': False, 'render_p95_measured': False,
              'scope': 'private synthetic boundary, cold HTTP workflows; not production/browser users',
              'observed_time_includes_drain': True, 'variants': {}}
    for variant in variants:
        rows = []
        for vus in LEVELS:
            observations = [indexed[(variant, vus, rep, False)] for rep in REPETITIONS]
            med = lambda key: statistics.median(item[key] for item in observations)
            validation_rates = [item['validated_request_error_rate'] for item in observations]
            row = {'vus': vus, 'repetitions': 3, 'runs': observations,
                   'requests_per_second': med('requests_per_second'),
                   'navigation_per_second': med('navigation_per_second'),
                   'request_p95_ms': _median_present([item['request_p95_ms'] for item in observations]),
                   'navigation_p95_ms': _median_present([item['navigation_p95_ms'] for item in observations]),
                   'completed_only_p95_ms_by_repetition': [item['completed_only_navigation_p95_ms'] for item in observations],
                   'completed_only_p95_scope': 'conditional on fully recorded successful workflows; per repetition, never pooled',
                   'navigation_error_rate_median': med('navigation_error_rate'),
                   'navigation_error_rate_max': max(item['navigation_error_rate'] for item in observations),
                   'request_error_rate_median': _median_present(validation_rates),
                   'request_error_rate_max': max((rate for rate in validation_rates if rate is not None), default=None),
                   'validation_observation_complete': all(item['validation_observation_complete'] for item in observations),
                   'payload_bytes': med('payload_bytes')}
            row['navigation_per_second_per_vu'] = row['navigation_per_second'] / vus
            row['censoring_reasons'] = sorted({item['latency_reason'] for item in observations if item['latency_reason'] is not None})
            rows.append(row)
        baseline = rows[0]
        baseline_ok = baseline['navigation_p95_ms'] is not None and baseline['navigation_per_second'] > 0 and baseline['validation_observation_complete']
        for row in rows:
            row['latency_relative_evaluable'] = baseline_ok and row['navigation_p95_ms'] is not None
            row['throughput_relative_evaluable'] = baseline_ok
            reasons = []
            if row['request_error_rate_max'] is not None and row['request_error_rate_max'] > CRITERION['error_rate_limit']:
                reasons.append('request_errors')
            if row['navigation_error_rate_max'] > CRITERION['error_rate_limit']:
                reasons.append('navigation_errors')
                if any(item['navigation_counts']['incomplete'] for item in row['runs']):
                    reasons.append('navigation_incomplete')
            if row['latency_relative_evaluable'] and row['navigation_p95_ms'] > baseline['navigation_p95_ms'] * CRITERION['navigation_p95_baseline_multiplier']:
                reasons.append('navigation_latency')
            if row['throughput_relative_evaluable'] and row['navigation_per_second_per_vu'] < baseline['navigation_per_second_per_vu'] * CRITERION['navigation_per_second_per_vu_baseline_fraction']:
                reasons.append('navigation_throughput_per_vu')
            row['degradation_reasons'] = reasons
            row['status'] = 'degraded' if reasons else 'healthy' if row['latency_relative_evaluable'] and row['validation_observation_complete'] else 'inconclusive'
        first = next((row['vus'] for row in rows if row['status'] == 'degraded'), None)
        result['variants'][variant] = {
            'resource_digest': next(iter(graphs[variant]))[0], 'baseline_established': baseline_ok,
            'levels': rows, 'warmups': [indexed[(variant, vus, 0, True)] for vus in LEVELS],
            'degradation': {'first_observed_vus': first, 'tested_through_vus': LEVELS[-1],
                            'censored_at_vus': LEVELS[-1] if all(row['status'] == 'healthy' for row in rows) else None,
                            'inconclusive_levels': [row['vus'] for row in rows if row['status'] == 'inconclusive'],
                            'baseline_degraded': rows[0]['status'] == 'degraded'}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    matrix = sub.add_parser('matrix')
    matrix.add_argument('--variants', nargs=2, required=True)
    matrix.add_argument('--duration-seconds', type=int, required=True)
    matrix.add_argument('--warmup-seconds', type=int, required=True)
    matrix.add_argument('--graceful-stop-seconds', type=int, required=True)
    matrix.add_argument('--output', type=Path, required=True)
    summary = sub.add_parser('summary')
    summary.add_argument('--inputs', nargs='+', type=Path, required=True)
    summary.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'matrix':
        result = {'schema': 'p100-matrix-v2', 'criterion': CRITERION,
                  'runs': build_matrix(args.variants, args.duration_seconds, args.warmup_seconds, args.graceful_stop_seconds)}
    else:
        result = summarize([json.loads(path.read_text()) for path in args.inputs])
        result['input_digests'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in args.inputs}
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
