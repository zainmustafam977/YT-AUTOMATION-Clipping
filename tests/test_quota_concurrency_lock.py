from concurrent.futures import ThreadPoolExecutor
from shorts_engine.services.publishing.quota import QuotaTracker


def test_concurrent_record_publish_accumulates_correctly(tmp_path):
    """Verify that concurrent calls to record_publish accurately accumulate without lost updates."""
    state_file = tmp_path / "quota_state.json"
    tracker = QuotaTracker(state_file=state_file)

    num_threads = 10
    cost_per_call = 1600

    def worker():
        # Create separate tracker instance sharing same state file to simulate independent workers
        t = QuotaTracker(state_file=state_file)
        t.record_publish(cost=cost_per_call)

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker) for _ in range(num_threads)]
        for f in futures:
            f.result()

    metrics = tracker.get_usage_metrics()
    expected_total = num_threads * cost_per_call
    assert metrics["used_units"] == expected_total, (
        f"Expected {expected_total} units after {num_threads} concurrent writes, but found {metrics['used_units']}"
    )


def test_concurrent_llm_record_request_accumulates_correctly(tmp_path):
    """Verify that concurrent LLM quota tracking updates accurately accumulate without lost updates."""
    from shorts_engine.services.llm.quota_tracker import APIUsageTracker
    state_file = tmp_path / "api_key_usage.json"
    
    num_threads = 10

    def worker():
        tracker = APIUsageTracker(state_file=state_file)
        tracker.record_request(provider="groq", key="gsk_test_1234567890abcdef1234567890", status="SUCCESS")

    with ThreadPoolExecutor(max_workers=num_threads) as executor:
        futures = [executor.submit(worker) for _ in range(num_threads)]
        for f in futures:
            f.result()

    final_tracker = APIUsageTracker(state_file=state_file)
    keys = final_tracker.get_per_key_metrics()
    groq_keys = [k for k in keys if k["provider"] == "groq" and "gsk_test" in k["key_masked"]]
    assert len(groq_keys) == 1
    assert groq_keys[0]["requests_today"] == num_threads
