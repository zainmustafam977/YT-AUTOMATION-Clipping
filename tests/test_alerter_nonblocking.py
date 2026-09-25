from unittest.mock import patch, MagicMock
from shorts_engine.services.monitoring.alerter import PipelineAlerter


def test_alerter_dispatches_via_threadpool():
    """PipelineAlerter dispatches webhook calls to threadpool in non-blocking mode (PLAN-P2-07 / F-24)."""
    alerter = PipelineAlerter()
    alerter.slack_url = "https://hooks.slack.com/services/fake"

    with patch("shorts_engine.services.monitoring.alerter._alerter_pool.submit") as mock_submit:
        res = alerter.alert(title="Test Alert", message="Pipeline warning", level="WARNING", wait=False)
        assert res is True
        assert mock_submit.called, "Alert should submit to background threadpool by default"
        args = mock_submit.call_args[0]
        assert args[1] == "Test Alert"
        assert args[2] == "Pipeline warning"


def test_alerter_synchronous_when_wait_true():
    """PipelineAlerter dispatches synchronously when wait=True."""
    alerter = PipelineAlerter()
    alerter.slack_url = "https://hooks.slack.com/services/fake"

    with patch.object(alerter, "_dispatch_webhooks", return_value=True) as mock_dispatch:
        res = alerter.alert(title="Sync Alert", message="Immediate error", level="ERROR", wait=True)
        assert res is True
        assert mock_dispatch.called
