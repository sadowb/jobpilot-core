"""Monitoring contracts and service aggregation, without a database or browser.

Real SQL boundary/recovery scenarios are in scripts/check_dispatch.py (0025); run separately with PostgreSQL.
"""

import unittest
from typing import Any
from unittest.mock import MagicMock

from pydantic import ValidationError

from jobpilot.config import RuntimeSettings, Settings
from jobpilot.dto.responses import ActiveWorker, Monitoring
from jobpilot.repository.reporting import ReportingRepository
from jobpilot.service.reporting import ReportingService


class MonitoringTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock()
        self.service = ReportingService(self.app)
        self.repo: Any = MagicMock(spec=ReportingRepository)
        self.service.repository = self.repo
        self.repo.queue_depth.return_value = 7
        self.repo.recent_failed_runs.return_value = []
        self.repo.usage_windows.return_value = [
            {"period": period, "usd": 0.0, "tokens": 0, "jobs": 0, "job_usd": 0.0, "runs": 0}
            for period in ("today", "last7Days", "allTime")
        ]

    def test_empty_periods_have_zero_totals_and_average(self) -> None:
        result = Monitoring.model_validate(self.service.monitoring())
        self.assertEqual(result.today.usd, 0)
        self.assertEqual(result.allTime.averageCostPerJob, 0)
        self.assertEqual(result.queueDepth, 7)
        self.assertEqual(result.recentFailures, [])

    def test_average_uses_job_spend_and_distinct_metered_jobs_not_model_calls(self) -> None:
        self.repo.usage_windows.return_value = [
            {"period": "today", "usd": 1003.0, "tokens": 2_223_167, "job_usd": 3.0, "jobs": 2, "runs": 5},
            {"period": "last7Days", "usd": 1006.0, "tokens": 9_000_000, "job_usd": 6.0, "jobs": 3, "runs": 10},
            {"period": "allTime", "usd": 1012.0, "tokens": 90_000_000, "job_usd": 12.0, "jobs": 4, "runs": 20},
        ]
        result = Monitoring.model_validate(self.service.monitoring())
        self.assertEqual(
            (result.today.averageCostPerJob, result.last7Days.averageCostPerJob, result.allTime.averageCostPerJob), (1.5, 2, 3)
        )
        self.assertEqual((result.today.usd, result.today.tokens, result.today.runs), (1003, 2_223_167, 5))

    def test_recent_failures_are_preserved_as_inert_text(self) -> None:
        self.repo.recent_failed_runs.return_value = [
            {
                "task": "task-1",
                "job": "j1",
                "role": None,
                "company": None,
                "node": "mac",
                "at": "2026-10-18T12:00:00Z",
                "lastError": "<script>not markup</script>",
            }
        ]
        result = Monitoring.model_validate(self.service.monitoring())
        self.assertEqual(result.recentFailures[0].lastError, "<script>not markup</script>")

    def test_dto_rejects_negative_nonfinite_usage_and_invalid_phase(self) -> None:
        for field, bad in (("usd", -1), ("usd", float("nan")), ("tokens", -1), ("runs", -1)):
            with self.subTest(field=field, bad=bad):
                payload = self.service.monitoring()
                payload["today"][field] = bad
                with self.assertRaises(ValidationError):
                    Monitoring.model_validate(payload)
        with self.assertRaises(ValidationError):
            ActiveWorker.model_validate(
                {"worker": "t", "job": "j", "since": 0, "phase": "unknown", "node": None, "started": True, "elapsedSeconds": -1}
            )

    def test_cost_records_actual_usage_without_a_reservation_or_admission_check(self) -> None:
        self.service.cost("j", "claude-haiku-5-5", 2_223_167, 12, "direct", 100)
        self.repo.insert_cost_ledger_for_cost.assert_called_once_with(
            "j", "claude-haiku-5-5", 2_223_167, 12, 100, "direct", False
        )
        self.repo.usage_windows.assert_not_called()

    def test_retired_settings_are_absent_and_rejected_by_runtime_api(self) -> None:
        for key in ("dailyBudgetEnabled", "dailyBudgetUsd", "dailyBudgetTokens", "runTokenReservation"):
            self.assertNotIn(key, RuntimeSettings.model_fields)
            with self.assertRaises(ValidationError):
                RuntimeSettings.model_validate({key: 1})
        for key in ("daily_budget_usd", "daily_budget_tokens", "run_token_reservation"):
            self.assertNotIn(key, Settings.model_fields)
