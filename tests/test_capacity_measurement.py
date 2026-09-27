from scripts.measure_capacity import summarize


def test_timeouts_and_rejections_remain_in_denominator_and_percentiles():
    report = summarize([{'outcome': 'success', 'elapsed_ms': 10},
                        {'outcome': 'rejected', 'elapsed_ms': 1},
                        {'outcome': 'timeout', 'elapsed_ms': 90000}], 100)
    assert report['attempts'] == 3
    assert report['successful_per_second'] == .01
    assert report['outcomes'] == {'success': 1, 'rejected': 1, 'timeout': 1}
    assert report['latency_all_attempts_ms'] == {'p50': 10, 'p95': 90000, 'p99': 90000}


def test_empty_distribution_is_unknown():
    report = summarize([], 0)
    assert report['latency_all_attempts_ms']['p95'] is None
    assert report['successful_per_second'] is None
