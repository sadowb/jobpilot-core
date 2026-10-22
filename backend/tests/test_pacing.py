"""Pacing identities, host classification and conservative migration; no network or database."""

import unittest
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import MagicMock, patch

from jobpilot.boards import resolve_board
from jobpilot.config import RuntimeSettings
from jobpilot.domain.canonical import pacing_hostname
from jobpilot.migrate import apply_data_migration
from jobpilot.service import Services
from jobpilot.service._support import is_local_host
from jobpilot.service.automation import AutomationService


class PacingKeyTests(unittest.TestCase):
    def test_adapter_tenants_on_shared_hosts(self) -> None:
        cases = {
            "https://job-boards.greenhouse.io/Brevium/jobs/123": "job-boards.greenhouse.io/brevium",
            "https://boards.greenhouse.io/Solutions/jobs/123": "boards.greenhouse.io/solutions",
            "https://jobs.ashbyhq.com/ACME/job-123": "jobs.ashbyhq.com/acme",
            "https://jobs.lever.co/Acme-2/job-123": "jobs.lever.co/acme-2",
            "https://jobs.eu.lever.co/Acme_2/job-123": "jobs.eu.lever.co/acme_2",
            "https://jobs.smartrecruiters.com/Acme/123-role": "jobs.smartrecruiters.com/acme",
            "https://apply.workable.com/Acme/j/123": "apply.workable.com/acme",
            "https://wd1.myworkdaysite.com/recruiting/Acme/Careers/job/Role_123": "wd1.myworkdaysite.com/acme",
            "https://boards.greenhouse.io/embed/job_app?for=ACME&token=123": "boards.greenhouse.io/acme",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                _, match = resolve_board(url)
                self.assertEqual(pacing_hostname(match.canonical_url, match.tenant), expected)
                self.assertEqual(pacing_hostname(expected), expected)

    def test_unknown_embedded_custom_single_employer_and_local_hosts(self) -> None:
        cases = {
            "https://boards.greenhouse.io/embed/job_app?token=123": "boards.greenhouse.io",
            "https://jobs.ashbyhq.com/": "jobs.ashbyhq.com",
            "https://careers.example.com/Acme/jobs/123": "careers.example.com",
            "https://acme.myworkdayjobs.com/en-US/Careers/job/Role_123": "acme.myworkdayjobs.com",
            "https://acme.greenhouse.io/jobs/123": "acme.greenhouse.io",
            "http://localhost:4100/Acme/jobs/123": "localhost",
            "http://127.0.0.1:4100/Acme/jobs/123": "127.0.0.1",
            "http://[::1]:4100/Acme/jobs/123": "::1",
        }
        for url, expected in cases.items():
            with self.subTest(url=url):
                _, match = resolve_board(url)
                self.assertEqual(pacing_hostname(match.canonical_url, match.tenant), expected)
                self.assertEqual(pacing_hostname(expected), expected)
        self.assertEqual(pacing_hostname("https://jobs.lever.co/acme/123"), "jobs.lever.co")
        self.assertEqual(pacing_hostname("https://jobs.lever.co.evil.test/acme/123", "acme"), "jobs.lever.co.evil.test")

    def test_host_normalization_ignores_scheme_port_and_userinfo(self) -> None:
        self.assertEqual(pacing_hostname("http://user:pass@JOBS.LEVER.CO:8080/acme/123", "ACME"), "jobs.lever.co/acme")
        self.assertEqual(pacing_hostname("https://[2001:db8::1]:443/a", "acme"), "2001:db8::1")
        self.assertEqual(pacing_hostname("2001:db8::1"), "2001:db8::1")

    def test_hostile_tenants_fall_back_without_truncation_or_decoding(self) -> None:
        for tenant in (
            "",
            "a" * 101,
            "../localhost",
            "acme/localhost",
            "acme.localhost",
            "@localhost",
            "a:443",
            "%2flocalhost",
            "a?for=b",
            "a#b",
            " a",
            "a ",
            "a\n",
            "a\\b",
            "é",
            "K",
            "_acme",
            "-acme",
        ):
            with self.subTest(tenant=tenant):
                self.assertEqual(pacing_hostname("https://jobs.lever.co/a/123", tenant), "jobs.lever.co")
                self.assertEqual(pacing_hostname(f"jobs.lever.co/{tenant}"), "jobs.lever.co")
        self.assertEqual(pacing_hostname("https://jobs.lever.co/a/123", "A" * 100), f"jobs.lever.co/{'a' * 100}")

    def test_local_classification_uses_only_the_host_part(self) -> None:
        for key in ("localhost/remote", "LOCALHOST./remote", "127.0.0.1/remote", "::1/remote", "[::1]/remote", "a.test/remote"):
            with self.subTest(key=key):
                self.assertTrue(is_local_host(key))
        for key in (
            "jobs.lever.co/localhost",
            "jobs.lever.co/acme.test",
            "remote.example/localhost.test",
            "remote.example/https://localhost",
            "remote.example/@127.0.0.1",
            "localhost.evil.com/acme",
        ):
            with self.subTest(key=key):
                self.assertFalse(is_local_host(key))
        self.assertFalse(is_local_host(None))


class BrowserPacingTests(unittest.TestCase):
    def test_stored_board_key_and_native_host_override_share_one_queue(self) -> None:
        app: Any = MagicMock(spec=Services)
        app.db = MagicMock()
        app._lease_worker = ContextVar("test_pacing_worker", default=None)
        app.job.return_value = {"pacing_hostname": "jobs.lever.co/ACME"}
        app.runtime_settings.return_value = RuntimeSettings(domainGapSeconds=5)
        service = AutomationService(app)
        repository: Any = MagicMock()
        service.repository = repository
        repository.reserve_domain_hit.return_value = {"delay": 0}
        for override, expected in (
            (None, "jobs.lever.co/acme"),
            ("JOBS.LEVER.CO", "jobs.lever.co/acme"),
            ("remote.example", "remote.example"),
            ("remote.example/localhost.test", "remote.example"),
        ):
            with self.subTest(override=override):
                service.pace_browser("job", domain=override)
                repository.reserve_domain_hit.assert_called_with(expected, 5)
        repository.reserve_domain_hit.reset_mock()
        service.pace_browser("job", domain="localhost/remote")
        repository.reserve_domain_hit.assert_not_called()


class PacingMigrationTests(unittest.TestCase):
    def test_hook_rederives_bound_urls_and_preserves_latest_reservations_on_rerun(self) -> None:
        now = datetime(2026, 10, 12, tzinfo=UTC)
        jobs: dict[str, list[str]] = {
            "a": ["https://job-boards.greenhouse.io/Brevium/jobs/123", "job-boards.greenhouse.io"],
            "b": ["https://job-boards.greenhouse.io/Solutions/jobs/456", "job-boards.greenhouse.io"],
            "c": ["https://jobs.ashbyhq.com/Acme/789", "careers.example.com"],
            "d": ["https://boards.greenhouse.io/embed/job_app?token=123", "boards.greenhouse.io"],
            "e": ["http://[::1]:4100/job", "::1"],
        }
        pacing = {
            "job-boards.greenhouse.io": now + timedelta(seconds=30),
            "job-boards.greenhouse.io/brevium": now + timedelta(seconds=60),
            "careers.example.com": now + timedelta(seconds=90),
            "jobs.ashbyhq.com": now + timedelta(seconds=10),
            "boards.greenhouse.io": now,
        }
        connection: Any = MagicMock()

        def execute(query: str, params: tuple[Any, ...] = ()) -> Any:
            if query.startswith("SELECT id,"):
                self.assertIn("coalesce(apply_url,url)", query)
                return MagicMock(fetchall=lambda: [(jid, *values) for jid, values in jobs.items()])
            if query.startswith("UPDATE jobs"):
                key, jid = params
                jobs[jid][1] = key
            else:
                self.assertIn("SELECT %s,max(last_hit)", query)
                self.assertIn("greatest(domain_pacing.last_hit,EXCLUDED.last_hit)", query)
                key, host, old_key = params
                sources = [pacing[k] for k in (host, old_key) if k in pacing]
                if sources:
                    pacing[key] = max([*sources, *([pacing[key]] if key in pacing else [])])
            return MagicMock()

        connection.execute.side_effect = execute
        apply_data_migration(connection, "0019_pacing_per_tenant.sql")
        self.assertEqual(jobs["a"][1], "job-boards.greenhouse.io/brevium")
        self.assertEqual(jobs["b"][1], "job-boards.greenhouse.io/solutions")
        self.assertEqual(jobs["c"][1], "jobs.ashbyhq.com/acme")
        self.assertEqual(pacing[jobs["a"][1]], now + timedelta(seconds=60))
        self.assertEqual(pacing[jobs["b"][1]], now + timedelta(seconds=30))
        self.assertEqual(pacing[jobs["c"][1]], now + timedelta(seconds=90))
        self.assertEqual(jobs["d"][1], "boards.greenhouse.io")
        self.assertEqual(jobs["e"][1], "::1")
        self.assertNotIn("::1", pacing)  # do not create a reservation where there was none
        snapshot = pacing.copy()
        apply_data_migration(connection, "0019_pacing_per_tenant.sql")
        self.assertEqual(pacing, snapshot)

    def test_unrelated_migration_does_not_run_hook(self) -> None:
        connection: Any = MagicMock()
        with patch("jobpilot.migrate.resolve_board") as resolve:
            apply_data_migration(connection, "0018_drop_retired_worker_rows.sql")
        connection.execute.assert_not_called()
        resolve.assert_not_called()
