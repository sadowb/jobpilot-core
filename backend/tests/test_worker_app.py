"""Spawned pool children (billiard's macOS default) must get Celery's trace cache built against the module app."""

import unittest
from typing import Any

from celery.app import trace

from jobpilot.worker import tasks as _tasks  # noqa: F401 - registers jobpilot.* on the module app
from jobpilot.worker.app import _prime_fast_trace, app

# Private Celery API on purpose: a rename must fail these tests loudly, not fall back silently.
LOCALIZED: list[Any] = trace._localized  # type: ignore[attr-defined]
REGISTRY: Any = app._tasks  # type: ignore[attr-defined]


class PrimeFastTraceTests(unittest.TestCase):
    def setUp(self) -> None:
        saved = list(LOCALIZED)
        tracers = {name: getattr(task, "__trace__", None) for name, task in app.tasks.items()}
        self.addCleanup(self._restore, saved, tracers)

    def _restore(self, cache: list[Any], tracers: dict[str, Any]) -> None:
        trace.reset_worker_optimizations(app)
        LOCALIZED[:] = cache
        for name, task in app.tasks.items():
            task.__trace__ = tracers[name]

    def test_spawned_child_with_an_empty_cache_is_primed_against_the_module_app(self) -> None:
        LOCALIZED[:] = []
        _prime_fast_trace()
        self.assertEqual(len(LOCALIZED), 3)
        self.assertIs(LOCALIZED[0], REGISTRY)
        jobpilot_tasks = [t for name, t in app.tasks.items() if name.startswith("jobpilot.")]
        self.assertGreaterEqual(len(jobpilot_tasks), 4)
        for task in jobpilot_tasks:
            self.assertTrue(callable(task.__trace__), task.name)

    def test_forked_child_with_an_inherited_cache_is_left_alone(self) -> None:
        inherited: list[Any] = [object(), object(), "jobs-mac@host"]
        LOCALIZED[:] = inherited
        _prime_fast_trace()
        self.assertEqual(LOCALIZED, inherited)


if __name__ == "__main__":
    unittest.main()
