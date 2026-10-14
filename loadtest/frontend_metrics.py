"""P100 HTTP cold-navigation matrix and fail-closed statistics (stdlib).

The unit of comparison is a whole HTTP navigation workflow; Vite's many
transitive development modules cannot artificially improve request throughput.
No metric here measures rendering or concurrent human/browser sessions.
"""
import argparse
import hashlib
import json
import math
from pathlib import Path
import re
import statistics

LEVELS = (1, 8, 32, 64, 128)
REPETITIONS = (1, 2, 3)
CRITERION = {
    'error_rate_limit': .001,
    'navigation_p95_baseline_multiplier': 2,
    'navigation_per_second_per_vu_baseline_fraction': .5,
    'latency_aggregate': 'median of three repetition p95 values (not pooled p95)',
    'error_aggregate': 'maximum repetition request/navigation error rate; medians also reported',
    'strict_comparison': '>',
    'throughput_comparison': '<',
    'baseline_vus': 1,
}


def build_matrix(variants, duration_seconds, warmup_seconds):
    if len(variants) != 2 or len(set(variants)) != 2 or duration_seconds <= 0 or warmup_seconds <= 0:
        raise ValueError('two distinct variants and positive measured/warmup durations required')
    matrix = []
    for vus in LEVELS:
        for variant in variants:
            matrix.append({'variant': variant, 'vus': vus, 'repetition': 0,
                           'warmup': True, 'duration_seconds': warmup_seconds})
        for repetition in REPETITIONS:
            # Alternate variant order to avoid assigning all time drift to one variant.
            for variant in variants if repetition % 2 else variants[::-1]:
                matrix.append({'variant': variant, 'vus': vus, 'repetition': repetition,
                               'warmup': False, 'duration_seconds': duration_seconds})
    return matrix


def _metric(run, name, field, positive=False, maximum=None):
    try:
        value = run['metrics'][name]['values'][field]
    except (KeyError, TypeError) as exc:
        raise ValueError(f'missing metric {name}.{field}') from exc
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError(f'nonfinite metric {name}.{field}')
    if value < 0 or positive and value == 0 or maximum is not None and value > maximum:
        raise ValueError(f'out-of-range metric {name}.{field}')
    return value


def summarize(runs):
    if not isinstance(runs, list) or not runs:
        raise ValueError('no measured runs')
    variants = sorted({item.get('variant') for item in runs if isinstance(item, dict) and isinstance(item.get('variant'), str)})
    if len(variants) != 2:
        raise ValueError('exactly two variants required')
    expected = {(variant, vus, repetition, False) for variant in variants for vus in LEVELS for repetition in REPETITIONS}
    expected |= {(variant, vus, 0, True) for variant in variants for vus in LEVELS}
    indexed = {}
    sources, harnesses = set(), set()
    resources = {variant: set() for variant in variants}
    for run in runs:
        if not isinstance(run, dict) or type(run.get('vus')) is not int or type(run.get('repetition')) is not int:
            raise ValueError('invalid matrix identity types')
        key = (run.get('variant'), run.get('vus'), run.get('repetition'), run.get('warmup'))
        if key not in expected or key in indexed or type(run.get('warmup')) is not bool:
            raise ValueError('duplicate or unexpected matrix run')
        for field, width in (('source_sha', 40), ('harness_sha', 40), ('resource_digest', 64)):
            if not re.fullmatch(f'[0-9a-f]{{{width}}}', run.get(field, '')):
                raise ValueError(f'invalid provenance {field}')
        sources.add(run['source_sha'])
        harnesses.add(run['harness_sha'])
        resources[run['variant']].add(run['resource_digest'])
        # Validate warmups as well: merely marking an absent/failed run warmup is not evidence.
        for name, field, positive, maximum in (
            ('p100_navigations', 'count', True, None), ('p100_navigations', 'rate', True, None),
            ('p100_requests', 'count', True, None), ('p100_requests', 'rate', True, None),
            ('p100_request_duration', 'p(95)', False, None),
            ('p100_navigation_duration', 'p(95)', False, None),
            ('p100_request_errors', 'rate', False, 1), ('p100_navigation_errors', 'rate', False, 1),
            ('data_received', 'count', True, None)):
            _metric(run, name, field, positive, maximum)
        indexed[key] = run
    if indexed.keys() != expected:
        raise ValueError('incomplete exact matrix including warmups')
    if len(sources) != 1 or len(harnesses) != 1 or any(len(values) != 1 for values in resources.values()):
        raise ValueError('source/harness changed or per-variant resource graph changed between runs')
    result = {'schema': 'p100-http-navigation-summary-v1', 'criterion': CRITERION,
              'source_sha': next(iter(sources)), 'harness_sha': next(iter(harnesses)),
              'browser_concurrent': False, 'render_p95_measured': False,
              'scope': 'private fixture boundary, cold HTTP resources, not production load',
              'warmups_verified': True, 'variants': {}}
    for variant in variants:
        rows = []
        for vus in LEVELS:
            repeats = [indexed[(variant, vus, rep, False)] for rep in REPETITIONS]
            median = lambda name, field: statistics.median(_metric(run, name, field) for run in repeats)
            maximum = lambda name, field: max(_metric(run, name, field) for run in repeats)
            row = {'vus': vus, 'repetitions': 3,
                   'requests_per_second': median('p100_requests', 'rate'),
                   'navigation_per_second': median('p100_navigations', 'rate'),
                   'request_p95_ms': median('p100_request_duration', 'p(95)'),
                   'navigation_p95_ms': median('p100_navigation_duration', 'p(95)'),
                   'request_error_rate_median': median('p100_request_errors', 'rate'),
                   'request_error_rate_max': maximum('p100_request_errors', 'rate'),
                   'navigation_error_rate_median': median('p100_navigation_errors', 'rate'),
                   'navigation_error_rate_max': maximum('p100_navigation_errors', 'rate'),
                   'payload_bytes': median('data_received', 'count'),
                   'navigation_count_median': median('p100_navigations', 'count')}
            row['navigation_per_second_per_vu'] = row['navigation_per_second'] / vus
            baseline = rows[0] if rows else row
            reasons = []
            if row['request_error_rate_max'] > CRITERION['error_rate_limit']:
                reasons.append('request_errors')
            if row['navigation_error_rate_max'] > CRITERION['error_rate_limit']:
                reasons.append('navigation_errors')
            if row['navigation_p95_ms'] > baseline['navigation_p95_ms'] * CRITERION['navigation_p95_baseline_multiplier']:
                reasons.append('navigation_latency')
            if row['navigation_per_second_per_vu'] < baseline['navigation_per_second_per_vu'] * CRITERION['navigation_per_second_per_vu_baseline_fraction']:
                reasons.append('navigation_throughput_per_vu')
            row['degradation_reasons'] = reasons
            rows.append(row)
        first = next((row['vus'] for row in rows if row['degradation_reasons']), None)
        result['variants'][variant] = {
            'resource_digest': next(iter(resources[variant])), 'levels': rows,
            'degradation': {'first_observed_vus': first,
                            'censored_at_vus': LEVELS[-1] if first is None else None,
                            'baseline_degraded': bool(rows[0]['degradation_reasons'])}}
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    matrix = sub.add_parser('matrix')
    matrix.add_argument('--variants', nargs=2, required=True)
    matrix.add_argument('--duration-seconds', type=int, required=True)
    matrix.add_argument('--warmup-seconds', type=int, required=True)
    matrix.add_argument('--output', type=Path, required=True)
    summary = sub.add_parser('summary')
    summary.add_argument('--inputs', nargs='+', type=Path, required=True)
    summary.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    if args.command == 'matrix':
        result = {'criterion': CRITERION, 'runs': build_matrix(args.variants, args.duration_seconds, args.warmup_seconds)}
    else:
        result = summarize([json.loads(path.read_text()) for path in args.inputs])
        result['input_digests'] = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in args.inputs}
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + '\n')


if __name__ == '__main__':
    main()
