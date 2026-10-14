/** Normalizes only observed k6 metrics; never depends on a cancelled JS finally.
 * k6 v2.2.0 runner Interrupt -> Sobek 7781506a890f uncatchable InterruptedError:
 * https://raw.githubusercontent.com/grafana/sobek/7781506a890f/runtime.go
 */
function number(value, label, integer = false) {
  if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || integer && !Number.isInteger(value)) {
    throw new Error(`invalid metric ${label}`);
  }
  return value;
}
function count(metrics, name, required = false) {
  if (!(name in metrics)) {
    if (required) throw new Error(`missing start evidence ${name}`);
    return 0; // Only an absent terminal/counter is zero, never an absent Trend latency.
  }
  return number(metrics[name]?.values?.count, name, true);
}
export function buildRunResult(config, discovery, data) {
  const metrics = data.metrics;
  if (!metrics || !data.state) throw new Error('unsupported k6 legacy summary');
  const elapsed = number(data.state.testRunDurationMs,'observed_duration_ms');
  if (!elapsed) throw new Error('no observed duration');
  const started = count(metrics,'p100_nav_started',true);
  const successful = count(metrics,'p100_nav_successful');
  const failed = count(metrics,'p100_nav_failed');
  const incomplete = started-successful-failed;
  if (!started || incomplete < 0) throw new Error('navigation counters do not reconcile');
  const sampleCount = count(metrics,'p100_successful_nav_duration');
  if (sampleCount > successful) throw new Error('uncommitted successful latency samples');
  const latencyMissing = successful > sampleCount;
  const censored = incomplete > 0 || latencyMissing;
  const atDeadline = elapsed >= (config.duration_seconds + config.graceful_stop_seconds)*1000-100;
  const completedP95 = successful > 0 && sampleCount===successful
    ? number(metrics.p100_successful_nav_duration.values['p(95)'],'successful_nav_p95') : null;
  return {
    schema:'p100-k6-run-v2', synthetic_counter_canary:config.synthetic_counter_canary===true,
    variant:config.variant,vus:config.vus,repetition:config.repetition,warmup:config.warmup,
    duration_seconds:config.duration_seconds,graceful_stop_seconds:config.graceful_stop_seconds,
    observed_duration_ms:elapsed,source_sha:config.source_sha,harness_sha:config.harness_sha,
    resource_digest:discovery.resource_digest,resource_count:discovery.resources.length,
    browser_concurrent:false,
    navigation_counts:{started,successful,failed,incomplete},
    incomplete_cause:incomplete ? atDeadline ? 'scenario_deadline' : 'unexpected_termination' : null,
    latency_sample_count:sampleCount,latency_censored:censored,
    navigation_p95_ms:censored || !successful ? null : completedP95,
    completed_only_navigation_p95_ms:completedP95,
    latency_reason:incomplete ? atDeadline ? 'scenario_deadline' : 'unexpected_termination'
      : latencyMissing ? 'latency_sample_incomplete' : !successful ? 'no_successful_navigation' : null,
    metrics,
  };
}
