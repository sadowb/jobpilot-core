"""Frozen Celery + Redis contract (ADR 0012): names, settings, dispatch hook; no broker or database starts."""

import os
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from pydantic import ValidationError

from jobpilot.config import MAX_OPEN_BROWSERS, RuntimeSettings, Settings
from jobpilot.service import Services
from jobpilot.service._support import LIVE_SETTINGS
from jobpilot.worker import names
from jobpilot.worker.claude import child_environment


class NamesTests(unittest.TestCase):
    def test_node_queue_and_drain_flag_follow_the_node_name(self) -> None:
        self.assertEqual(names.node_queue("mac"), "node.mac")
        self.assertEqual(names.draining_flag(Path("/d"), "box-2"), Path("/d/run/box-2.draining"))

    def test_invalid_node_names_never_reach_a_queue_or_path(self) -> None:
        for bad in ("", "Mac", "a/b", "../x", "-x", "x" * 33, "a b"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    names.node_queue(bad)
                with self.assertRaises(ValueError):
                    names.draining_flag(Path("/d"), bad)

    def test_time_limits_nest(self) -> None:
        self.assertLess(names.DISPATCH_LEASE_SECONDS, names.RUN_LEASE_SECONDS)
        self.assertIsNone(names.SOFT_TIME_LIMIT)
        self.assertIsNone(names.HARD_TIME_LIMIT)


class SettingsTests(unittest.TestCase):
    def test_defaults(self) -> None:
        s = Settings(_env_file=None)
        self.assertEqual((s.jobpilot_node, s.node_slots, s.jobpilot_pool), ("mac", 4, "prefork"))
        self.assertEqual((s.worker_concurrency, s.max_open_browsers), (4, 8))
        self.assertEqual(s.redis_url, "redis://127.0.0.1:6380/0")

    def test_redis_password_is_url_quoted(self) -> None:
        s = Settings(_env_file=None, redis_password="p@ss/w:rd")  # noqa: S106 - test value
        self.assertEqual(s.redis_url, "redis://:p%40ss%2Fw%3Ard@127.0.0.1:6380/0")

    def test_node_name_is_validated(self) -> None:
        with self.assertRaises(ValidationError):
            Settings(_env_file=None, jobpilot_node="Bad Node")

    def test_runtime_max_open_browsers_is_bounded_and_live(self) -> None:
        self.assertEqual(RuntimeSettings().maxOpenBrowsers, 8)
        for bad in (0, MAX_OPEN_BROWSERS + 1):
            with self.assertRaises(ValidationError):
                RuntimeSettings(maxOpenBrowsers=bad)
        self.assertIn("maxOpenBrowsers", LIVE_SETTINGS)


class ChildEnvironmentTests(unittest.TestCase):
    def test_agent_subprocess_receives_the_workers_effective_node(self) -> None:
        self.assertEqual(child_environment("box-2")["JOBPILOT_NODE"], "box-2")

    def test_no_node_means_no_variable_and_nothing_else_leaks(self) -> None:
        env = child_environment()
        self.assertNotIn("JOBPILOT_NODE", env)
        self.assertFalse({k for k in env if k.startswith(("PG", "REDIS", "JP_"))})

    def test_the_claude_login_token_is_forwarded_only_when_set(self) -> None:
        with patch.dict(os.environ, {"CLAUDE_CODE_OAUTH_TOKEN": "tok"}):  # noqa: S105 - test value
            self.assertEqual(child_environment()["CLAUDE_CODE_OAUTH_TOKEN"], "tok")
        with patch.dict(os.environ, {}, clear=True):
            self.assertNotIn("CLAUDE_CODE_OAUTH_TOKEN", child_environment())


class RequestDispatchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app: Any = MagicMock(spec=Services)
        self.app.db = MagicMock()
        self.app._fire_dispatch_hook.side_effect = lambda: Services._fire_dispatch_hook(self.app)

    def request(self) -> None:
        Services.request_dispatch(self.app)

    def test_no_hook_is_a_noop(self) -> None:
        self.app.dispatch_hook = None
        self.request()
        self.app.db.defer.assert_not_called()

    def test_inside_a_transaction_the_hook_waits_for_commit(self) -> None:
        hook = MagicMock()
        self.app.dispatch_hook = hook
        self.app.db.defer.return_value = True
        self.request()
        hook.assert_not_called()
        self.app.db.defer.assert_called_once_with(self.app._fire_dispatch_hook)

    def test_outside_a_transaction_the_hook_fires_immediately(self) -> None:
        hook = MagicMock()
        self.app.dispatch_hook = hook
        self.app.db.defer.return_value = False
        self.request()
        hook.assert_called_once_with()

    def test_broker_failure_is_swallowed(self) -> None:
        self.app.dispatch_hook = MagicMock(side_effect=ConnectionError("redis down"))
        self.app.db.defer.return_value = False
        with self.assertLogs("jobpilot.service", level="WARNING"):
            self.request()  # must not raise


if __name__ == "__main__":
    unittest.main()
