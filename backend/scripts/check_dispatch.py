"""Exercise the Celery + Redis admission SQL of ADR 0012 against a throwaway PostgreSQL database.

The script creates jp_check_<8 hex>, applies every migration, runs 35 scenarios through the real Services facade
and repositories, and drops the database in a finally block. It never connects to the jobpilot database, starts no
worker, and touches neither launchd, Redis nor the network: the dispatch hook stays unset, so no broker is contacted
and Neo is never contacted (no browser session row is created in a scenario that closes a run).

Run from backend/ with the shell-loaded .env:
    PYTHONPATH=src .venv/bin/python scripts/check_dispatch.py
"""

from __future__ import annotations

import json
import os
import re
import secrets
import subprocess
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path
from typing import Any
from unittest.mock import patch

import psycopg
from psycopg import sql

from jobpilot.browser.neo import BrowserUnavailable, PageState, Tab
from jobpilot.browser.reaper import TAB_LOST_GRACE_SECONDS, Reaper
from jobpilot.config import RuntimeSettings, Settings, get_settings
from jobpilot.migrate import MIGRATIONS, migrate
from jobpilot.projection.vault import GIT_LOCK_KEY, GIT_LOCK_NAMESPACE, commit_cv, ensure_vault
from jobpilot.repository.automation import AutomationRepository
from jobpilot.repository.browser import BrowserRepository
from jobpilot.repository.database import Database, J, Row
from jobpilot.service import Services
from jobpilot.service._support import Dispatch
from jobpilot.service.park_watch import ParkWatchService, Reading, fingerprint
from jobpilot.worker.names import NODE_TAKEOVER_SECONDS

LOCAL = "mac"  # the node this script dispatches from; scenario 9 asserts Services.node == LOCAL
HELD_AGE = 60  # seconds: offline but inside the takeover window, so the job is held
GONE_AGE = NODE_TAKEOVER_SECONDS + 100  # seconds: past the takeover window, so the job goes to the shared queue
DB_NAME = re.compile(r"jp_check_[0-9a-f]{8}")


@dataclass
class Verdict:
    """Collects the checks of one scenario; the scenario passes when no check failed."""

    number: int
    title: str
    checks: int = 0
    failures: list[str] = field(default_factory=list)

    def expect(self, condition: bool, reason: str) -> None:
        self.checks += 1
        if not condition:
            self.failures.append(reason)

    def line(self) -> str:
        if self.failures:
            return f"{self.number:>2}  {self.title:<27} FAIL  {'; '.join(self.failures)}"
        return f"{self.number:>2}  {self.title:<27} PASS  {self.checks} checks held"


def only(verdict: Verdict, found: Sequence[Dispatch], what: str) -> Dispatch | None:
    """The single lease a dispatch returned, or None (recorded as a failure) when there is not exactly one."""
    if len(found) == 1:
        return found[0]
    verdict.expect(False, f"{what}: expected one lease, got {len(found)}")
    return None


class FakeBinder:
    """Stands in for TabBinder: no Neo runs in this script; `up` is what the loopback probe would answer."""

    def __init__(self) -> None:
        self.up = True
        self.browser = FakeNeo()

    def online(self) -> bool:
        return self.up


class FakeNeo:
    def __init__(self) -> None:
        self.tab_list: list[Tab] = []

    def tabs(self) -> list[Tab]:
        return list(self.tab_list)


class Harness:
    """Seeds and inspects state with plain SQL; admission itself always goes through the Services facade."""

    def __init__(self, db: Database, svc: Services) -> None:
        self.db = db
        self.svc = svc
        self.binder = FakeBinder()
        svc.browser._tabs = self.binder  # type: ignore[assignment]

    def reset(self, *, concurrency: int, browsers: int) -> None:
        self.binder.up = True
        self.binder.browser = FakeNeo()
        self.db.run(
            """TRUNCATE jobs, tasks, browser_sessions, job_forms, cost_ledger, job_events,
                       approvals, worker_processes, domain_pacing CASCADE"""
        )
        self.svc.save_runtime_settings(
            RuntimeSettings(workerConcurrency=concurrency, maxOpenBrowsers=browsers, domainGapSeconds=0)
        )
        self.svc.set_automation(True)

    def ingest(self, prefix: str, count: int) -> list[str]:
        """Greenhouse postings: distinct tenant per job, so no board/tenant dedupe and no host pacing gap."""
        urls = [f"https://job-boards.greenhouse.io/{prefix}{i}/jobs/{1000 + i}" for i in range(count)]
        ids = [str(self.svc.ingest(url)["id"]) for url in urls]
        # Most scenarios exercise browser admission after successful ingest preparation.
        self.db.run("UPDATE job_preflights SET state='done' WHERE job_id=ANY(%s)", (ids,))
        return ids

    def dispatch(self) -> list[Dispatch]:
        return list(self.svc.dispatch_claim(LOCAL))

    def task(self, job_id: str) -> Row:
        row = self.db.one("SELECT * FROM tasks WHERE job_id = %s", (job_id,))
        if row is None:
            raise LookupError(f"no task row for {job_id}")
        return row

    def count_state(self, state: str) -> int:
        row = self.db.one("SELECT count(*)::int AS n FROM tasks WHERE state = %s::task_state", (state,))
        return int(row["n"]) if row else 0

    def event_count(self, kind: str, job_id: str) -> int:
        row = self.db.one("SELECT count(*)::int AS n FROM job_events WHERE job_id = %s AND kind = %s", (job_id, kind))
        return int(row["n"]) if row else 0

    def last_event(self, kind: str, job_id: str) -> Row:
        row = self.db.one(
            "SELECT payload FROM job_events WHERE job_id = %s AND kind = %s ORDER BY id DESC LIMIT 1", (job_id, kind)
        )
        return dict(row["payload"]) if row else {}

    def sweep(self, node: str) -> set[str]:
        return {str(r["job_id"]) for r in BrowserRepository(self.db).list_browser_sessions_for_sweep_browsers(node)}

    def park(self, job_id: str, session_node: str | None) -> None:
        """A filled form waiting for the owner: awaiting_approval, task done, browser kept on `session_node`."""
        self.db.run("UPDATE jobs SET status='awaiting_approval', waiting_on='you' WHERE id=%s", (job_id,))
        self.db.run("UPDATE tasks SET state='done', leased_by=NULL WHERE job_id=%s", (job_id,))
        self.session(job_id, session_node)

    def park_for_answers(
        self, job_id: str, session_node: str | None, *, reported: bool = True, open_questions: bool = True
    ) -> None:
        """A direct-mode filled form waiting on owner answers: preparing, task blocked, tab kept on `session_node`."""
        self.db.run("UPDATE jobs SET status='preparing', waiting_on='you' WHERE id=%s", (job_id,))
        self.db.run("UPDATE tasks SET state='blocked', leased_by=NULL WHERE job_id=%s", (job_id,))
        inventory = [{"key": "reported:0", "label": "Work authorization", "type": "select", "required": True}]
        if reported:
            inventory[0]["reported"] = True
        missing = ["page:0:reported:0"] if open_questions else []
        self.db.run(
            """INSERT INTO job_forms(job_id, inventory, missing) VALUES(%s, %s::jsonb, %s::jsonb)
               ON CONFLICT (job_id) DO UPDATE SET inventory=EXCLUDED.inventory, missing=EXCLUDED.missing""",
            (job_id, json.dumps(inventory), json.dumps(missing)),
        )
        self.session(job_id, session_node)

    def session(self, job_id: str, node: str | None, *, close_requested: bool = False, legacy: bool = False) -> None:
        target = None if legacy or node is None else f"T-{job_id}"
        self.db.run(
            "INSERT INTO browser_sessions(job_id, node, target_id, close_requested) VALUES(%s, %s, %s, %s)",
            (job_id, node, target, close_requested),
        )
        if target and node == LOCAL:
            self.binder.browser.tab_list.append(Tab(target, "https://fixture.example/"))

    def requeue(self, job_id: str) -> None:
        self.db.run("UPDATE tasks SET state='queued', leased_by=NULL, visible_at=now() WHERE job_id=%s", (job_id,))
        self.db.run("UPDATE jobs SET waiting_on='agent' WHERE id=%s", (job_id,))

    def heartbeat(self, node: str, age: int, *, role: str = "jobs") -> None:
        name = f"{node}:{role}"
        status: dict[str, Any] = {"role": role, "node": node, "offline": False}
        if role == "jobs":
            status["slots"] = 3
        self.svc.publish_worker(name, status, node=node)
        self.db.run("UPDATE worker_processes SET heartbeat_at = now() - make_interval(secs => %s) WHERE name = %s", (age, name))
        if role == "jobs":  # every node with a job worker also reports a reachable Neo
            self.browser_heartbeat(node, age)

    def browser_heartbeat(self, node: str, age: int, *, offline: bool = False) -> None:
        name = f"{node}:browser"
        self.svc.publish_worker(name, {"role": "browser", "node": node, "offline": offline}, node=node)
        self.db.run("UPDATE worker_processes SET heartbeat_at = now() - make_interval(secs => %s) WHERE name = %s", (age, name))

    def route_case(self, session_node: str | None, box_age: int | None) -> tuple[str, Dispatch | None]:
        """A resumed run whose kept tab is on `session_node`; `box-2:jobs` heartbeats `box_age` seconds ago (None: no row)."""
        self.reset(concurrency=3, browsers=6)
        jid = self.ingest("s7-", 1)[0]
        self.park_for_answers(jid, session_node)
        self.requeue(jid)
        if box_age is not None:
            self.heartbeat("box-2", box_age)
        found = [d for d in self.dispatch() if d.job_id == jid]
        return jid, (found[0] if found else None)


# --- scenarios ----------------------------------------------------------------------------------------------------


def s1_concurrency(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=2, browsers=6)
    h.ingest("s1-", 5)
    first = h.dispatch()
    second = h.dispatch()
    v.expect(len(first) == 2, f"first dispatch leased {len(first)} jobs, expected 2")
    v.expect(second == [], f"second dispatch leased {len(second)} more jobs at capacity")
    for d in first:
        row = h.task(d.job_id)
        v.expect(
            row["state"] == "leased" and row["leased_by"] == d.task_id and row["attempts"] == 0,
            f"{d.job_id}: state={row['state']} leased_by_match={row['leased_by'] == d.task_id} attempts={row['attempts']}",
        )
    v.expect(h.count_state("leased") == 2 and h.count_state("queued") == 3, "expected 2 leased and 3 queued task rows")


def s2_duplicate_start(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s2-", 1)[0]
    d = only(v, h.dispatch(), "dispatch")
    if d is None:
        return
    v.expect(h.svc.start_run(jid, d.task_id, LOCAL) is True, "first delivery did not win the start")
    v.expect(h.svc.start_run(jid, d.task_id, LOCAL) is False, "duplicate delivery with the same task id started again")
    row = h.task(jid)
    v.expect(row["started_at"] is not None, "started_at not set by the winning start")
    v.expect(row["leased_node"] == LOCAL, f"leased_node={row['leased_node']!r}, expected {LOCAL!r}")


def s3_stale_task_id(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s3-", 1)[0]
    d = only(v, h.dispatch(), "dispatch")
    if d is None:
        return
    v.expect(h.svc.start_run(jid, "not-the-lease", LOCAL) is False, "a stale task id started a run")
    row = h.task(jid)
    v.expect(
        row["state"] == "leased" and row["leased_by"] == d.task_id and row["started_at"] is None,
        f"stale start changed the lease: state={row['state']} started_at_set={row['started_at'] is not None}",
    )


def s4_redispatch_unstarted(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s4-", 1)[0]
    first = only(v, h.dispatch(), "first dispatch")
    h.db.run("UPDATE tasks SET visible_at = now() - interval '1 second' WHERE job_id = %s", (jid,))
    second = only(v, h.dispatch(), "re-dispatch after lease expiry")
    if first is None or second is None:
        return
    v.expect(
        second.job_id == jid and second.task_id != first.task_id, "expired unstarted lease was not re-dispatched under a new id"
    )
    v.expect(h.svc.start_run(jid, first.task_id, LOCAL) is False, "the old task id started after its lease expired")
    v.expect(h.svc.start_run(jid, second.task_id, LOCAL) is True, "the new task id could not start the re-dispatched lease")


def s5_dispatch_failed(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s5-", 1)[0]
    d = only(v, h.dispatch(), "dispatch")
    if d is None:
        return
    v.expect(h.task(jid)["attempts"] == 0, "dispatch charged an attempt (it is charged by start_run)")
    h.svc.dispatch_failed(jid, d.task_id, "redis down")
    row = h.task(jid)
    v.expect(
        row["state"] == "queued" and row["leased_by"] is None and row["attempts"] == 0,
        f"after dispatch_failed: state={row['state']} leased_by={row['leased_by']} attempts={row['attempts']}",
    )
    v.expect(h.event_count("dispatch_failed", jid) == 1, "no dispatch_failed job_event was written")
    v.expect(h.last_event("dispatch_failed", jid).get("reason") == "redis down", "dispatch_failed event lost its reason")
    snapshot = h.task(jid)
    h.svc.dispatch_failed(jid, d.task_id, "again")
    h.svc.dispatch_failed(jid, "wrong-task", "wrong task id")
    v.expect(h.task(jid) == snapshot, "a repeated or wrong-id dispatch_failed changed the task row")
    v.expect(h.event_count("dispatch_failed", jid) == 1, "a repeated or wrong-id dispatch_failed wrote another event")


def s6_browser_cap(h: Harness, v: Verdict) -> None:
    # (a) three parked forms fill maxOpenBrowsers=3: a fresh preparation job is refused
    h.reset(concurrency=3, browsers=3)
    parked = h.ingest("s6p-", 3)
    for jid in parked:
        h.park(jid, LOCAL)
    fresh = h.ingest("s6f-", 1)[0]
    refused = h.dispatch()
    v.expect(refused == [], f"{len(refused)} lease(s) admitted at the browser cap, expected none (fresh={fresh})")

    # (b) a form that waits for the owner to submit (awaiting_approval) is never claimed, even if its task is queued (ADR 0026)
    h.requeue(parked[0])
    waiting = h.dispatch()
    v.expect(waiting == [], f"awaiting_approval job claimed for an agent run at the cap: {[d.job_id for d in waiting]}")

    # (c) a queued job that already holds an open session (answers sent, resumed) is admitted at the cap
    h.reset(concurrency=3, browsers=3)
    h.heartbeat(LOCAL, 1)  # the node's job worker is alive, so a run for its session is routed there
    parked = h.ingest("s6q-", 3)
    for jid in parked:
        h.park_for_answers(jid, LOCAL)
    h.requeue(parked[0])
    held = h.dispatch()
    v.expect(
        [d.job_id for d in held] == [parked[0]],
        f"queued job with an open session at the cap: got {[d.job_id for d in held]}",
    )

    # (d) start_run on a full node hands a preparation run back, attempt refunded
    h.reset(concurrency=3, browsers=3)
    target = h.ingest("s6t-", 1)[0]
    d = only(v, h.dispatch(), "dispatch below the cap")
    others = h.ingest("s6o-", 4)
    for jid in others[:3]:
        h.session(jid, LOCAL)
    h.db.run(
        """UPDATE tasks SET state='leased', leased_by='unrelated-task', started_at=now(), leased_node=%s,
               visible_at=now() + make_interval(secs => 600) WHERE job_id=%s""",
        (LOCAL, others[3]),
    )
    if d is None:
        return
    v.expect(h.svc.start_run(target, d.task_id, LOCAL) is False, "start_run admitted a preparation run on a full node")
    row = h.task(target)
    v.expect(
        row["state"] == "queued" and row["leased_by"] is None and row["attempts"] == 0,
        f"full-node bounce: state={row['state']} leased_by={row['leased_by']} attempts={row['attempts']}, expected queued/None/0",
    )
    v.expect(h.event_count("start_deferred", target) == 1, "no start_deferred job_event for the bounced run")


def s7_routing(h: Harness, v: Verdict) -> None:
    jid, online = h.route_case("box-2", 0)
    v.expect(
        online is not None and online.node == "box-2",
        f"parked on online box-2: routed to {None if online is None else online.node}",
    )

    jid, held = h.route_case("box-2", HELD_AGE)
    v.expect(held is None, f"parked on box-2 offline {HELD_AGE}s (inside takeover window) was dispatched")
    v.expect(h.task(jid)["state"] == "queued", "held job left the queued state")

    _, gone = h.route_case("box-2", GONE_AGE)
    v.expect(gone is not None and gone.node is None, f"box-2 offline {GONE_AGE}s: node={None if gone is None else gone.node}")

    _, absent = h.route_case("box-2", None)
    v.expect(
        absent is not None and absent.node is None, "no worker_processes row: not dispatched to the shared queue with node None"
    )

    _, legacy = h.route_case(None, 0)
    v.expect(legacy is not None and legacy.node is None, "legacy NULL session node was routed to a node queue")


def s8_sweep_skips_approvals(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s8-", 1)[0]
    h.park(jid, LOCAL)
    v.expect(jid not in h.sweep(LOCAL), "a parked awaiting_approval browser was returned by the sweep")
    h.db.run("UPDATE browser_sessions SET close_requested = true WHERE job_id = %s", (jid,))
    v.expect(jid in h.sweep(LOCAL), "a close_requested parked session was not returned by the sweep")
    h.db.run("UPDATE browser_sessions SET close_requested = false WHERE job_id = %s", (jid,))
    h.db.run("UPDATE jobs SET archived_at = now() WHERE id = %s", (jid,))
    v.expect(jid in h.sweep(LOCAL), "an archived job's session was not returned by the sweep")
    # positive control: the same session outside awaiting_approval must be returned, or the negative above is vacuous
    h.db.run("UPDATE jobs SET archived_at = NULL, status = 'ready', waiting_on = 'agent' WHERE id = %s", (jid,))
    v.expect(jid in h.sweep(LOCAL), "control failed: a non-approval session with a done task was not swept")


def s25_parked_question_tab(h: Harness, v: Verdict) -> None:
    """A filled form waiting on owner answers keeps its tab: never swept, counted by the cap, holds no worker slot."""
    h.reset(concurrency=1, browsers=3)
    h.heartbeat(LOCAL, 1)
    parked, other = h.ingest("s25a-", 2)
    h.park_for_answers(parked, LOCAL)
    v.expect(parked not in h.sweep(LOCAL), "a parked question tab was returned by the sweep")
    admitted = h.dispatch()
    v.expect(
        [d.job_id for d in admitted] == [other],
        f"admitted {[d.job_id for d in admitted]} next to a parked question tab, expected only the other job (no worker slot held)",
    )
    h.requeue(parked)  # the owner answered: the follow-up run is queued, the tab stays for it
    v.expect(parked not in h.sweep(LOCAL), "the parked tab was swept once its follow-up run was queued")
    h.reset(concurrency=3, browsers=2)
    h.heartbeat(LOCAL, 1)
    parked, first, second = h.ingest("s25b-", 3)
    h.park_for_answers(parked, LOCAL)
    got = h.dispatch()
    v.expect(len(got) == 1, f"admitted {len(got)} jobs with one of two browser slots held by a parked question tab, expected 1")
    # controls: each exemption condition off must make the very same session sweepable, or the checks above are vacuous
    h.db.run("DELETE FROM browser_sessions WHERE job_id=%s", (parked,))
    h.park_for_answers(parked, LOCAL)
    h.db.run("UPDATE jobs SET waiting_on = 'agent' WHERE id = %s", (parked,))
    v.expect(parked in h.sweep(LOCAL), "control failed: a session no longer waiting on the owner was not swept")
    h.db.run("UPDATE jobs SET waiting_on = 'you' WHERE id = %s", (parked,))
    h.db.run(
        "INSERT INTO job_events(job_id, kind, actor, payload) VALUES(%s, 'submit_clicked', 'system', '{}'::jsonb)", (parked,)
    )
    v.expect(parked in h.sweep(LOCAL), "control failed: a session whose submit was clicked was not swept")
    h.db.run("DELETE FROM job_events WHERE job_id=%s AND kind='submit_clicked'", (parked,))
    h.db.run("DELETE FROM browser_sessions WHERE job_id=%s", (parked,))
    h.park_for_answers(parked, LOCAL)
    h.db.run("UPDATE browser_sessions SET close_requested = true WHERE job_id = %s", (parked,))
    v.expect(parked in h.sweep(LOCAL), "a close_requested parked question tab was not swept")
    h.db.run("UPDATE browser_sessions SET close_requested = false WHERE job_id = %s", (parked,))
    h.db.run("UPDATE jobs SET archived_at = now() WHERE id = %s", (parked,))
    v.expect(parked in h.sweep(LOCAL), "an archived job's parked question tab was not swept")
    h.db.run("UPDATE jobs SET archived_at = NULL, status = 'closed' WHERE id = %s", (parked,))
    v.expect(parked in h.sweep(LOCAL), "a question tab of a closed job was not swept")
    h.db.run("UPDATE jobs SET status = 'preparing', waiting_on = 'site' WHERE id = %s", (parked,))
    v.expect(parked in h.sweep(LOCAL), "a question tab of a job no longer waiting on the owner was not swept")
    v.expect(len([first, second]) == 2, "seeded jobs")


def s9_node_isolation(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    a, b, c, d = h.ingest("s9-", 4)
    h.session(a, "box-2", close_requested=True)
    h.session(b, None, close_requested=True)
    h.session(c, "box-2")
    h.session(d, LOCAL)
    v.expect(h.svc.node == LOCAL, f"cfg.jobpilot_node is {h.svc.node!r}; this scenario assumes {LOCAL!r}")
    mac = h.sweep(LOCAL)
    box = h.sweep("box-2")
    v.expect(a not in mac, "a box-2 session with close_requested was swept by mac")
    v.expect(a in box, "a box-2 session with close_requested was not swept by box-2")
    v.expect(b in mac, "a legacy NULL-node session with close_requested was not swept by mac")
    v.expect(h.svc.browser_is_open(c) is False, "browser_is_open is True for a session parked on box-2")
    v.expect(h.svc.browser_is_open(d) is True, "control failed: a local open session is not reported open")
    v.expect(h.event_count("browser_lost", c) == 0, "this node judged a remote session lost")
    v.expect(
        h.db.one("SELECT target_id FROM browser_sessions WHERE job_id=%s", (c,)) is not None,
        "browser_is_open deleted the remote session",
    )


def s10_finish_run(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    one, two, early = h.ingest("s10-", 3)
    dispatched = {d.job_id: d for d in h.dispatch()}
    run = dispatched.get(one)
    gate = dispatched.get(two)
    released = dispatched.get(early)
    if run is None or gate is None or released is None:
        v.expect(False, f"expected three leases, got {sorted(dispatched)}")
        return
    v.expect(h.svc.start_run(one, run.task_id, LOCAL) is True, "start_run refused the first delivery")
    h.svc.finish_run(one, run.task_id, "queued", 1.2, refund_attempt=True)
    row = h.task(one)
    v.expect(
        row["state"] == "queued" and row["started_at"] is None and row["attempts"] == 0,
        f"release: state={row['state']} started_at_set={row['started_at'] is not None} attempts={row['attempts']}",
    )
    expected = {"task": run.task_id, "node": LOCAL, "outcome": "queued", "seconds": 1.2, "refunded": True, "released": True}
    v.expect(h.last_event("task_finished", one) == expected, f"task_finished payload was {h.last_event('task_finished', one)}")

    before = h.task(one)
    h.svc.finish_run(one, run.task_id, "queued", 1.2)
    v.expect(h.task(one) == before, "a second finish_run with the same task id changed the task row")
    v.expect(
        h.event_count("task_finished", one) == 1 and h.last_event("task_finished", one) == expected,
        "a duplicate finish_run changed or duplicated the first task_finished event",
    )

    # An early release is not a finish: the first finish must still record the run exactly once.
    v.expect(h.svc.start_run(early, released.task_id, LOCAL) is True, "early-release run did not start")
    h.svc.release(early, released.task_id, "queued")
    v.expect(h.event_count("task_finished", early) == 0, "release itself recorded task_finished")
    before = h.task(early)
    h.svc.finish_run(early, released.task_id, "queued", 0.7)
    early_expected = {
        "task": released.task_id,
        "node": None,
        "outcome": "queued",
        "seconds": 0.7,
        "refunded": False,
        "released": False,
    }
    v.expect(h.task(early) == before, "finish_run after early release changed the task row")
    v.expect(
        h.event_count("task_finished", early) == 1 and h.last_event("task_finished", early) == early_expected,
        "first finish_run after early release did not record released=false",
    )
    h.svc.finish_run(early, released.task_id, "queued", 0.7)
    v.expect(h.event_count("task_finished", early) == 1, "duplicate finish after early release recorded another event")

    try:
        h.svc.finish_run(two, gate.task_id, "done", 0.5)
        v.expect(False, "outcome 'done' with work remaining was accepted")
    except PermissionError:
        pass
    v.expect(h.task(two)["state"] == "leased", "a refused 'done' changed the lease")


def s11_liveness(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=3, browsers=6)
    h.heartbeat(LOCAL, 0, role="control")
    h.heartbeat(LOCAL, 0, role="jobs")
    status = h.svc.automation_status()
    limit = h.svc.runtime_settings().maxOpenBrowsers
    v.expect(status["queueOnline"] is True, "queueOnline is not True with a fresh control row")
    v.expect(status["workerOnline"] is True, "workerOnline is not True with a fresh jobs row")
    v.expect(status["capacity"] == 3, f"capacity={status['capacity']}, expected 3 slots")
    v.expect(status["browserLimit"] == limit, f"browserLimit={status['browserLimit']}, expected maxOpenBrowsers={limit}")

    h.db.run("DELETE FROM worker_processes WHERE name = %s", (f"{LOCAL}:jobs",))
    status = h.svc.automation_status()
    v.expect(
        status["workerOnline"] is False and "job worker" in (status["idleReason"] or ""),
        f"control-only: workerOnline={status['workerOnline']} idleReason={status['idleReason']!r}",
    )

    h.db.run("DELETE FROM worker_processes")
    status = h.svc.automation_status()
    v.expect(
        status["queueOnline"] is False and "queue" in (status["idleReason"] or ""),
        f"no rows: queueOnline={status['queueOnline']} idleReason={status['idleReason']!r}",
    )


def s12_claim(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=2, browsers=6)
    h.ingest("s12-", 3)
    held = h.dispatch()
    v.expect(len(held) == 2, f"dispatch leased {len(held)} jobs, expected 2 before the claim check")
    v.expect(h.svc.claim("jp-next") is None, "claim() admitted a job while workerConcurrency leases were live")
    if not held:
        return
    h.svc.dispatch_failed(held[0].job_id, held[0].task_id, "free one slot")
    got = h.svc.claim("jp-next")
    v.expect(got is not None, "claim() returned None with a free slot and a queued job")
    if got is None:
        return
    v.expect(got not in {d.job_id for d in held}, f"claim() returned {got}, which already holds a lease")
    row = h.task(got)
    v.expect(
        row["state"] == "leased" and row["leased_by"] == "jp-next" and row["leased_node"] == LOCAL,
        f"claim() lease: state={row['state']} leased_by={row['leased_by']} leased_node={row['leased_node']}",
    )
    v.expect(row["attempts"] == 1, f"claim() runs the job at once, so it charges the attempt; attempts={row['attempts']}")


def s13_spend_never_blocks(h: Harness, v: Verdict) -> None:
    """0025_remove_daily_limits.sql: spend above the former daily ceilings cannot block dispatch or starts."""
    h.reset(concurrency=3, browsers=6)
    jobs = h.ingest("s13-", 3)
    h.svc.cost(jobs[0], "test", 22_223_167, 0, "direct", 1000)
    first = h.dispatch()
    for d in first:
        h.svc.dispatch_failed(d.job_id, d.task_id, "redis down")
    h.db.run("UPDATE tasks SET visible_at = now() - interval '1 second'")
    second = h.dispatch()
    v.expect(len(first) == 3 and len(second) == 3, "spend prevented dispatch or redispatch")
    v.expect(all(h.svc.start_run(d.job_id, d.task_id, LOCAL) for d in second), "spend prevented a start")
    v.expect(all(h.task(job)["attempts"] == 1 for job in jobs), "attempts must still be charged exactly once")


def s14_dead_node_session(h: Harness, v: Verdict) -> None:
    """A session on a dead node neither occupies the cap nor exempts its job; dispatch and start_run agree."""
    h.reset(concurrency=3, browsers=1)
    h.heartbeat(LOCAL, 1)
    parked, ghost = h.ingest("s14-", 2)
    h.park(parked, LOCAL)  # fills the only browser slot on this node
    h.session(ghost, "box-2")  # its browser lives on a node with no heartbeat at all
    h.requeue(ghost)
    held = h.dispatch()
    v.expect(held == [], f"admitted {[(d.job_id, d.node) for d in held]} while this node is full, expected none")
    v.expect(h.task(ghost)["attempts"] == 0, "the job was leased and bounced instead of simply not admitted")
    h.db.run("DELETE FROM browser_sessions WHERE job_id=%s", (parked,))  # the parked form was approved and closed
    free = only(v, h.dispatch(), "dispatch once a slot is free")
    if free is None:
        return
    v.expect(free.node is None and free.job_id == ghost, f"takeover job routed to {free.node!r}, expected the shared queue")
    v.expect(h.svc.start_run(ghost, free.task_id, LOCAL) is True, "start_run refused the taken-over run on a free node")


def s15_no_worker_burns_no_attempts(h: Harness, v: Verdict) -> None:
    """Workers down, control + beat up: every expired unstarted lease is re-dispatched without charging an attempt."""
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s15-", 1)[0]
    for _ in range(7):  # more than the five attempts a job gets
        h.db.run("UPDATE tasks SET visible_at = now() - interval '1 second' WHERE job_id = %s", (jid,))
        h.dispatch()
    row = h.task(jid)
    v.expect(row["attempts"] == 0, f"{row['attempts']} attempts burnt by expired unstarted leases, expected 0")
    v.expect(row["state"] != "blocked", f"job was blocked ({row['state']}) although no agent ever ran")
    job = h.db.one("SELECT waiting_on FROM jobs WHERE id = %s", (jid,))
    v.expect(job is not None and job["waiting_on"] == "agent", "job was handed to the owner without ever running")
    last = h.db.one("SELECT leased_by FROM tasks WHERE job_id = %s", (jid,))
    v.expect(last is not None and h.svc.start_run(jid, str(last["leased_by"]), LOCAL) is True, "start_run refused the live lease")
    v.expect(h.task(jid)["attempts"] == 1, f"start_run did not charge exactly one attempt: {h.task(jid)['attempts']}")


def s16_expired_lease_cannot_start(h: Harness, v: Verdict) -> None:
    """A delivery that outlived its dispatch lease must not start: dispatch may already have admitted a replacement."""
    h.reset(concurrency=1, browsers=6)
    jid = h.ingest("s16-", 1)[0]
    d = only(v, h.dispatch(), "dispatch")
    if d is None:
        return
    h.db.run("UPDATE tasks SET visible_at = now() - interval '1 second' WHERE job_id = %s", (jid,))
    v.expect(h.svc.start_run(jid, d.task_id, LOCAL) is False, "start_run accepted a delivery after its lease expired")
    row = h.task(jid)
    v.expect(
        row["started_at"] is None and row["attempts"] == 0,
        f"expired start changed the row: started_at_set={row['started_at'] is not None} attempts={row['attempts']}",
    )
    again = only(v, h.dispatch(), "re-dispatch of the expired lease")
    v.expect(again is not None and again.task_id != d.task_id, "the expired lease was not re-dispatched under a new id")
    v.expect(len(h.dispatch()) == 0, "workerConcurrency=1 exceeded after the re-dispatch")


def s17_missed_ping_starts_the_takeover_clock(h: Harness, v: Verdict) -> None:
    """control writes offline=true over the jobs row on a missed ping: the node is no longer alive, but it stays 'recent'."""
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s17-", 1)[0]
    h.park_for_answers(jid, "box-2")
    h.requeue(jid)
    h.heartbeat("box-2", 3)  # healthy three seconds ago
    h.svc.publish_worker("box-2:jobs", {"role": "jobs", "node": "box-2", "offline": True, "slots": 3}, node="box-2")
    held = h.dispatch()
    v.expect(held == [], f"parked job dispatched after one missed ping: {[(d.job_id, d.node) for d in held]}")
    row = h.db.one("SELECT status FROM worker_processes WHERE name='box-2:jobs'")
    v.expect(row is not None and bool(row["status"].get("okAt")), "the offline heartbeat lost the last healthy time")
    # a second missed ping must not move the clock forward
    stamp = row["status"].get("okAt") if row else None
    h.svc.publish_worker("box-2:jobs", {"role": "jobs", "node": "box-2", "offline": True, "slots": 3}, node="box-2")
    again = h.db.one("SELECT status FROM worker_processes WHERE name='box-2:jobs'")
    v.expect(again is not None and again["status"].get("okAt") == stamp, "a repeated offline heartbeat moved okAt")
    # the window elapses
    h.db.run(
        """UPDATE worker_processes SET status = jsonb_set(status, '{okAt}', to_jsonb(to_char(
               (now() - make_interval(secs => %s)) AT TIME ZONE 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US"Z"')))
           WHERE name='box-2:jobs'""",
        (GONE_AGE,),
    )
    gone = [d for d in h.dispatch() if d.job_id == jid]
    v.expect(len(gone) == 1 and gone[0].node is None, "job was not taken over once the healthy time left the window")
    # recovery clears the marker
    h.svc.publish_worker("box-2:jobs", {"role": "jobs", "node": "box-2", "offline": False, "slots": 3}, node="box-2")
    back = h.db.one("SELECT status FROM worker_processes WHERE name='box-2:jobs'")
    v.expect(back is not None and "okAt" not in back["status"], "a healthy heartbeat kept a stale okAt")


def s18_start_serializes_per_node(h: Harness, v: Verdict) -> None:
    """Two start_runs of one node cannot interleave count-then-start: the second waits on the node lock."""
    h.reset(concurrency=2, browsers=2)
    jid = h.ingest("s18-", 1)[0]
    d = only(v, h.dispatch(), "dispatch")
    if d is None:
        return
    blocker = Database(h.svc.cfg, single=True)
    outcome: list[bool] = []
    try:
        with blocker.transaction():
            AutomationRepository(blocker).lock_node_for_start(LOCAL)
            worker = threading.Thread(target=lambda: outcome.append(h.svc.start_run(jid, d.task_id, LOCAL)))
            worker.start()
            worker.join(1.0)
            v.expect(worker.is_alive() and not outcome, "start_run did not wait for the node lock")
        worker.join(10.0)
    finally:
        blocker.close()
    v.expect(outcome == [True], f"start_run after the lock was released returned {outcome}")
    other = h.db.one("SELECT started_at FROM tasks WHERE job_id = %s", (jid,))
    v.expect(other is not None and other["started_at"] is not None, "the run did not start once the lock was free")


def s19_agent_env_carries_node(h: Harness, v: Verdict) -> None:
    from jobpilot.worker.claude import child_environment

    env = child_environment(h.svc.node)
    v.expect(env.get("JOBPILOT_NODE") == h.svc.node, f"agent env JOBPILOT_NODE={env.get('JOBPILOT_NODE')!r}")
    v.expect("PGPASSWORD" not in env and "REDIS_PASSWORD" not in env, "database or broker secrets reached the agent env")


def s20_neo_offline(h: Harness, v: Verdict) -> None:
    """Workers up but the node's Neo down: backpressure with no lease, no attempt, no failed job."""
    h.reset(concurrency=3, browsers=6)
    jid = h.ingest("s20-", 1)[0]
    h.heartbeat(LOCAL, 1, role="control")
    h.heartbeat(LOCAL, 1)  # jobs row + neo row
    h.browser_heartbeat(LOCAL, 1, offline=True)
    status = h.svc.automation_status()
    v.expect(status["browserOnline"] is False, f"browserOnline={status['browserOnline']} with an offline neo row")
    v.expect("Neo is not reachable" in (status["idleReason"] or ""), f"idleReason={status['idleReason']!r}")
    held = h.dispatch()
    v.expect(held == [], f"{len(held)} lease(s) admitted while the Neo is down")
    row = h.task(jid)
    v.expect(row["state"] == "queued" and row["attempts"] == 0, f"state={row['state']} attempts={row['attempts']}")
    h.browser_heartbeat(LOCAL, 1)
    back = only(v, h.dispatch(), "dispatch once Neo is back")
    if back is None:
        return
    h.binder.up = False  # Neo died between dispatch and start
    v.expect(h.svc.start_run(jid, back.task_id, LOCAL) is False, "start_run ran a job with no Neo")
    row = h.task(jid)
    v.expect(
        row["state"] == "queued" and row["attempts"] == 0 and row["started_at"] is None,
        f"bounce: state={row['state']} attempts={row['attempts']} started_at_set={row['started_at'] is not None}",
    )
    v.expect(h.last_event("start_deferred", jid).get("reason") == "Neo offline", "no start_deferred reason")
    h.binder.up = True
    h.db.run("UPDATE tasks SET visible_at = now() - interval '1 second' WHERE job_id = %s", (jid,))
    again = only(v, h.dispatch(), "re-dispatch")
    v.expect(again is not None and h.svc.start_run(jid, again.task_id, LOCAL) is True, "run did not start after Neo came back")


def s21_tab_reconcile(h: Harness, v: Verdict) -> None:
    """Rows judged against Neo's tab list; a daemon idling out is not a loss; fresh tabs are not reaped."""
    h.reset(concurrency=3, browsers=6)
    kept, gone, legacy, remote = h.ingest("s21-", 4)
    for jid, target in ((kept, "T-kept"), (gone, "T-gone")):
        h.db.run("INSERT INTO browser_sessions(job_id, node, target_id) VALUES(%s, %s, %s)", (jid, LOCAL, target))
    h.session(legacy, LOCAL, legacy=True)  # pre-tab row: dropped immediately with browser_lost
    h.session(remote, "box-2")  # absent from this Neo: only box-2 may judge this tab
    h.binder.browser.tab_list = [Tab("T-kept", "https://x.example/"), Tab("T-stray", "https://y.example/")]
    reaper = Reaper(h.svc)
    lost = reaper.reconcile_tabs(1000.0)
    v.expect(lost == [legacy], f"first pass: lost={lost}, expected only the legacy loss")
    v.expect(h.event_count("browser_lost", legacy) == 1, "legacy row was dropped without browser_lost")
    v.expect(h.last_event("browser_lost", legacy).get("reason") == "legacy session", "legacy loss reason missing")
    lost = reaper.reconcile_tabs(1000.0 + TAB_LOST_GRACE_SECONDS)
    v.expect(lost == [gone], f"lost rows {lost}, expected [{gone}]")
    v.expect(
        [t.target_id for t in h.binder.browser.tab_list] == ["T-kept", "T-stray"],
        "an unowned tab in the shared Neo was touched",
    )
    rows = {str(r["job_id"]) for r in h.db.all("SELECT job_id FROM browser_sessions")}
    v.expect(rows == {kept, remote}, f"rows left {sorted(rows)}, expected the kept local tab and untouched remote tab")
    v.expect(h.event_count("browser_lost", legacy) == 1, "legacy row produced duplicate browser_lost events")
    v.expect(h.event_count("browser_lost", remote) == 0, "this node judged a remote tab lost")
    v.expect(h.event_count("browser_lost", gone) == 1, "no browser_lost event for the vanished tab")
    v.expect(h.event_count("browser_lost", kept) == 0, "the live tab's row was judged lost")


def s22_busy_domain_admission(h: Harness, v: Verdict) -> None:
    """Busy-domain actions reserve future turns; admission never waits for silence or changes those turns."""
    h.reset(concurrency=4, browsers=4)
    h.svc.set_automation(False)
    gap = 6
    h.svc.save_runtime_settings(RuntimeSettings(workerConcurrency=4, maxOpenBrowsers=4, domainGapSeconds=gap))
    h.svc.set_automation(True)
    jobs = [str(h.svc.ingest(f"https://job-boards.greenhouse.io/s22/jobs/{1000 + i}")["id"]) for i in range(4)]
    # Start after ingest planning, as Harness.ingest does for distinct-tenant fixtures.
    h.db.run("UPDATE job_preflights SET state='done' WHERE job_id=ANY(%s)", (jobs,))
    domain = "job-boards.greenhouse.io/s22"
    pacing = h.db.one(
        """INSERT INTO domain_pacing(domain,last_hit) VALUES(%s,clock_timestamp() + interval '30 seconds')
           RETURNING last_hit""",
        (domain,),
    )
    assert pacing is not None
    previous = pacing["last_hit"]
    claimed: set[str] = set()
    for _ in range(4):
        d = only(v, h.svc.dispatch_claim(LOCAL, limit=1), "busy-domain dispatch pass")
        if d is None:
            return
        claimed.add(d.job_id)
        pacing = h.db.one("SELECT last_hit FROM domain_pacing WHERE domain=%s", (domain,))
        v.expect(pacing is not None and pacing["last_hit"] == previous, "admission changed an outstanding reservation")
        # Exercise real browser pacing without sleeping or starting a browser. Ongoing hits keep the domain busy.
        with patch("jobpilot.service.automation.time.sleep") as sleep:
            h.svc.pace_browser(d.job_id)
        v.expect(sleep.called, "the first action did not wait for its future pacing slot")
        pacing = h.db.one("SELECT last_hit FROM domain_pacing WHERE domain=%s", (domain,))
        assert pacing is not None
        v.expect(pacing["last_hit"] >= previous + timedelta(seconds=gap), "browser pacing moved backwards or skipped its gap")
        previous = pacing["last_hit"]
    v.expect(claimed == set(jobs) and h.count_state("leased") == 4, "not all four same-tenant jobs were admitted")
    v.expect(h.dispatch() == [], "busy-domain admission exceeded worker/browser capacity")
    # Expired unstarted deliveries can be repeatedly admitted without building an unbounded action queue.
    for _ in range(7):
        h.db.run("UPDATE tasks SET visible_at=now() - interval '1 second'")
        v.expect(len(h.dispatch()) == 4, "expired busy-domain leases were not re-dispatched")
        pacing = h.db.one("SELECT last_hit FROM domain_pacing WHERE domain=%s", (domain,))
        v.expect(pacing is not None and pacing["last_hit"] == previous, "redispatch grew the pacing queue")


def s23_per_tenant_pacing(h: Harness, v: Verdict) -> None:
    """Different employers on one ATS host have independent slots; the same employer still waits."""
    h.reset(concurrency=3, browsers=3)
    h.svc.set_automation(False)
    gap = 6
    h.svc.save_runtime_settings(RuntimeSettings(workerConcurrency=3, maxOpenBrowsers=3, domainGapSeconds=gap))
    h.svc.set_automation(True)
    first, other = h.ingest("s23-", 2)
    same = str(h.svc.ingest("https://job-boards.greenhouse.io/s23-0/jobs/9000")["id"])
    domain = "job-boards.greenhouse.io/s23-0"
    v.expect(h.svc.job(first)["pacing_hostname"] == domain, "ingest lost the first employer's pacing key")
    v.expect(h.svc.job(other)["pacing_hostname"] == "job-boards.greenhouse.io/s23-1", "ingest lost the other employer")
    reservation = h.db.one(
        """INSERT INTO domain_pacing(domain,last_hit) VALUES(%s,clock_timestamp() + interval '30 seconds')
           RETURNING last_hit""",
        (domain,),
    )
    assert reservation is not None
    with patch("jobpilot.service.automation.time.sleep") as sleep:
        h.svc.pace_browser(first)
        v.expect(sleep.called, "first employer did not wait for its outstanding reservation")
        sleep.reset_mock()
        h.svc.pace_browser(other, domain="job-boards.greenhouse.io")
        v.expect(not sleep.called, "a different employer waited for the first employer's reservation")
        sleep.reset_mock()
        h.svc.pace_browser(same, domain="job-boards.greenhouse.io")
        v.expect(sleep.called, "a second job at the same employer bypassed its pacing queue")
    pacing = h.db.one("SELECT last_hit FROM domain_pacing WHERE domain=%s", (domain,))
    v.expect(
        pacing is not None and pacing["last_hit"] >= reservation["last_hit"] + timedelta(seconds=2 * gap),
        "same-employer actions skipped a gap or moved a reservation earlier",
    )


def s24_long_run_lease(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=2, browsers=6)
    jid = h.ingest("s24-", 1)[0]
    first = only(v, h.dispatch(), "dispatch")
    if first is None:
        return
    v.expect(h.svc.start_run(jid, first.task_id, LOCAL), "run could not start")
    # The run has been alive for over a lease period. Advance to just before each expiry,
    # then renew through the same Services heartbeat used by the subprocess monitor.
    h.db.run("UPDATE tasks SET started_at=now() - interval '30 minutes' WHERE job_id=%s", (jid,))
    for _ in range(3):
        h.db.run("UPDATE tasks SET visible_at=now() + interval '1 second' WHERE job_id=%s", (jid,))
        h.svc.heartbeat(jid, first.task_id)
        v.expect(h.dispatch() == [], "a heartbeating long run was re-dispatched")
        v.expect(h.task(jid)["leased_by"] == first.task_id, "heartbeat changed ownership")
    # No more beats: simulate reaching the renewed expiry and recover under a new task id.
    h.db.run("UPDATE tasks SET visible_at=now() - interval '1 second' WHERE job_id=%s", (jid,))
    second = only(v, h.dispatch(), "dispatch after heartbeats stop")
    if second is not None:
        v.expect(second.job_id == jid and second.task_id != first.task_id, "dead run was not recovered")
    try:
        h.svc.heartbeat(jid, first.task_id)
    except PermissionError:
        v.expect(True, "lost lease refused")
    else:
        v.expect(False, "old run renewed a replacement lease")


def s26_ingest_fence(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=1, browsers=2)
    jobs = h.ingest("preflight", 1)
    h.db.run("UPDATE job_preflights SET state='pending' WHERE job_id=%s", (jobs[0],))
    v.expect(not h.dispatch(), "pending preflight prevents browser admission")
    h.db.run("UPDATE job_preflights SET state='running' WHERE job_id=%s", (jobs[0],))
    v.expect(not h.dispatch(), "running preflight prevents browser admission")
    h.db.run("UPDATE job_preflights SET deadline=now()-interval '1 second' WHERE job_id=%s", (jobs[0],))
    v.expect(len(h.dispatch()) == 1, "expired preflight falls back to browser admission")


def s27_question_batch_gate(h: Harness, v: Verdict) -> None:
    h.reset(concurrency=1, browsers=2)
    h.heartbeat(LOCAL, 1)
    job = h.ingest("batch", 1)[0]
    h.svc.set_inventory(
        job,
        [
            {"key": "first", "label": "Fixture question alpha", "type": "text", "required": True},
            {"key": "second", "label": "Fixture question beta", "type": "text", "required": True},
        ],
        page=0,
        complete=False,
    )
    h.db.run("UPDATE job_forms SET missing=%s WHERE job_id=%s", (J(["page:0:first", "page:0:second"]), job))
    h.svc.wait(job, "you", "answer the missing application questions")
    h.svc.set_job_answers(job, [("page:0:first", "A", f"job:{job}")])
    v.expect(not h.dispatch(), "partial batch must not dispatch")
    h.svc.set_job_answers(job, [("page:0:second", "B", f"job:{job}")])
    h.svc.resume(job)
    v.expect(h.event_count("resume", job) == 1, "complete batch and repeated send wake only once")
    v.expect(len(h.dispatch()) == 1, "complete batch becomes dispatchable")

    h.reset(concurrency=1, browsers=2)
    h.heartbeat(LOCAL, 1)
    job = h.ingest("try-anyway", 1)[0]
    h.park_for_answers(job, LOCAL)
    h.svc.resume(job, try_anyway=True)
    v.expect(len(h.dispatch()) == 1, "explicit try-anyway admits an unresolved batch")


def s28_daily_limit_migration(h: Harness, v: Verdict) -> None:
    """0025 upgrade fixture: retired schema, persisted settings and known blocked reasons. UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    jobs = h.ingest("s28-", 7)
    reasons = [
        "daily cost budget exhausted (UTC); waiting for next day or a higher limit",
        "daily token budget exhausted (UTC); waiting for next day or a higher limit",
        "automation error: daily board browsing cap reached (UTC)",
        "daily board cap reached",
        "automation retry limit reached",
        "agent run budget exceeded",
        "daily board preparation cap reached (UTC)",
    ]
    for job, reason in zip(jobs, reasons, strict=True):
        h.svc.wait(job, "you", reason)
    h.db.run("UPDATE jobs SET archived_at=now() WHERE id=%s", (jobs[6],))
    h.db.run("UPDATE tasks SET state='blocked',attempts=2")
    # Reconstitute the removed parts of the old schema only, then execute the actual forward migration.
    h.db.run(
        "ALTER TABLE boards ADD COLUMN max_per_day integer, ADD COLUMN max_prepare_per_day integer, "
        "ADD COLUMN max_browser_per_day integer, ADD COLUMN max_account_per_day integer"
    )
    h.db.run("CREATE TABLE budget_reservations(id integer)")
    h.db.run("CREATE TABLE board_daily_usage(board_id text)")
    h.db.run("DROP INDEX cost_ledger_job_idx")
    h.db.run(
        "UPDATE runtime_settings SET value=value || %s",
        (J({"dailyBudgetEnabled": True, "dailyBudgetUsd": 0, "dailyBudgetTokens": 0, "runTokenReservation": 100000}),),
    )
    with h.db.transaction():
        for stmt in (MIGRATIONS / "0025_remove_daily_limits.sql").read_text().split("--> statement-breakpoint"):
            if stmt.strip():
                h.db.run(stmt)
    for job in jobs[:4]:
        row = h.svc.job(job)
        task = h.task(job)
        v.expect(row["waiting_on"] == "agent" and row["blocked_reason"] is None, f"daily block not cleared: {job}")
        v.expect(task["state"] == "queued" and task["attempts"] == 2, "requeue must preserve attempts")
    for job in jobs[4:]:
        v.expect(h.svc.job(job)["waiting_on"] == "you" and h.task(job)["state"] == "blocked", "unrelated/archive block changed")
    stored = h.db.one("SELECT value FROM runtime_settings WHERE singleton")
    v.expect(
        stored is not None and not any(k.startswith("daily") or k == "runTokenReservation" for k in stored["value"]),
        "retired settings survived",
    )
    tables = h.db.one("SELECT to_regclass('budget_reservations') AS budget, to_regclass('board_daily_usage') AS board")
    v.expect(tables is not None and tables["budget"] is None and tables["board"] is None, "retired tables survived")
    columns = h.db.all(
        "SELECT column_name FROM information_schema.columns WHERE table_name='boards' AND column_name LIKE 'max%day'"
    )
    v.expect(not columns, "retired board limits survived")


def s29_monitoring_aggregation(h: Harness, v: Verdict) -> None:
    """UTC boundaries, unknown usage, distinct jobs, active runs, queue and error attribution. UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    jobs = h.ingest("s29-", 3)
    with h.db.transaction():  # one stable now(), including around UTC midnight
        for job, days, usd, tokens, step in [
            (jobs[0], 0, 1, 10, "direct"),
            (jobs[0], 0, 2, 20, "cv"),
            (jobs[1], 0, 3, 30, None),
            (None, 0, 10, 40, "cleanup"),
            (jobs[0], 6, 4, 50, "direct"),
            (jobs[2], 7, 5, 60, "direct"),
            (jobs[0], 0, 999, 999, "unknown_model_usage"),
        ]:
            h.db.run(
                """INSERT INTO cost_ledger(job_id,ts,model,usd,tokens_in,step)
                        VALUES(%s,(date_trunc('day',now() AT TIME ZONE 'UTC') - make_interval(days=>%s)) AT TIME ZONE 'UTC',
                               'test',%s,%s,%s)""",
                (job, days, usd, tokens, step),
            )
        h.svc.event(jobs[0], "error", "system", {"message": "first failure"})
        h.svc.event(jobs[0], "task_finished", "system", {"task": "old", "outcome": "blocked"})
        h.svc.event(jobs[0], "agent_run_failed", "system", {"detail": "latest failure"})
        h.svc.event(jobs[0], "task_finished", "system", {"task": "recent", "outcome": "queued"})
        h.svc.event(jobs[0], "task_finished", "system", {"task": "clean", "outcome": "queued"})
        runs = h.dispatch()
        h.svc.start_run(runs[0].job_id, runs[0].task_id, LOCAL)
        result = h.svc.reporting.monitoring()
        expected = [("today", 16, 100, 2, 3), ("last7Days", 20, 150, 2, 5), ("allTime", 25, 210, 3, 5)]
        for period, usd, tokens, count, average in expected:
            usage = result[period]
            v.expect(
                (usage["usd"], usage["tokens"], usage["jobs"], usage["averageCostPerJob"]) == (usd, tokens, count, average),
                f"{period}: {usage}",
            )
            v.expect(usage["runs"] == 4, f"{period} double counted or lost a run")
        v.expect(result["queueDepth"] == 0, "unstarted live leases counted as queued")
        failures = result["recentFailures"]
        v.expect([f["task"] for f in failures] == ["recent", "old"], "clean retry inherited an old error")
        v.expect(failures[0]["lastError"] == "latest failure", "wrong last error")
        active = h.svc.reporting.repository.list_active_workers()
        started = [r for r in active if r["started"]]
        v.expect(len(started) == 1 and started[0]["node"] == LOCAL, "active run metadata")
        v.expect(all(r["elapsedSeconds"] >= 0 for r in active), "negative elapsed time")
        job_usage = h.svc.reporting.job_usage(jobs[0])
        v.expect(job_usage == {"usd": 7.0, "tokens": 80}, f"per-job usage: {job_usage}")


def s30_requeue_streak(h: Harness, v: Verdict) -> None:
    """Identical-requeue streaks, the idle-reason source and the retry-limit handoff naming its reason. UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    jobs = h.ingest("s30-", 2)
    repo = AutomationRepository(h.db)

    def requeue(job: str, reason: str) -> None:
        h.svc.event(job, "run_requeued", "system", {"reason": reason, "retryAfter": 30})

    for _ in range(3):
        requeue(jobs[0], "browser_page_error")
    h.svc.event(jobs[0], "transition", "system", {"from": "screened", "to": "preparing"})  # progress resets the streak
    for _ in range(2):
        requeue(jobs[0], "browser_page_error")
    requeue(jobs[0], "tab_lost")
    requeue(jobs[1], "tab_lost")
    v.expect(repo.count_requeues_since_progress(jobs[0], "browser_page_error") == 2, "streak did not restart at progress")
    v.expect(repo.count_requeues_since_progress(jobs[0], "tab_lost") == 1, "streak is per reason")
    v.expect(repo.count_requeues_since_progress(jobs[1], "tab_lost") == 1, "streak leaked across jobs")
    v.expect(repo.find_last_requeue_reason(jobs[0]) == "tab_lost", "wrong last requeue reason")
    recent = h.svc.reporting.repository.find_recent_requeue()
    v.expect(recent is not None and recent["reason"] in ("browser_page_error", "tab_lost"), f"recent requeue: {recent}")
    h.db.run("UPDATE tasks SET attempts=5, state='queued', visible_at=now()-interval '1 second' WHERE job_id=%s", (jobs[0],))
    h.dispatch()
    job = h.db.one("SELECT waiting_on, blocked_reason FROM jobs WHERE id=%s", (jobs[0],))
    v.expect(
        job is not None and job["waiting_on"] == "you" and job["blocked_reason"] == "automation retry limit reached: tab_lost",
        f"retry-limit handoff: {job}",
    )


def s31_awaiting_approval_never_claimed(h: Harness, v: Verdict) -> None:
    """ADR 0026: the owner submits, so no agent run is ever claimed for an awaiting_approval job."""
    h.reset(concurrency=3, browsers=6)
    h.heartbeat(LOCAL, 1)
    waiting, control = h.ingest("s31-", 2)
    h.park(waiting, LOCAL)
    h.requeue(waiting)  # queued task, waiting_on='agent', still awaiting_approval
    h.park_for_answers(control, LOCAL)
    h.requeue(control)  # positive control: the same shape one status earlier IS claimed, so the refusal is not vacuous
    claimed = [d.job_id for d in h.dispatch()]
    v.expect(waiting not in claimed, "an awaiting_approval job was claimed for an agent run")
    v.expect(control in claimed, f"control failed: a resumed preparing job with a tab was not claimed: {claimed}")
    v.expect(h.task(waiting)["state"] == "queued" and h.task(waiting)["attempts"] == 0, "the refused job's task was touched")


def s32_owner_submits_migration(h: Harness, v: Verdict) -> None:
    """0027: a job approved under the old flow is handed to the owner and its queued task is closed. UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    leftover, untouched = h.ingest("s32-", 2)
    h.db.run("UPDATE jobs SET status='awaiting_approval', waiting_on='agent' WHERE id=%s", (leftover,))
    h.db.run("UPDATE jobs SET status='preparing', waiting_on='agent' WHERE id=%s", (untouched,))
    with h.db.transaction():
        for stmt in (MIGRATIONS / "0027_owner_submits.sql").read_text().split("--> statement-breakpoint"):
            if stmt.strip():
                h.db.run(stmt)
    row = h.svc.job(leftover)
    v.expect(row["waiting_on"] == "you" and bool(row["blocked_reason"]), "the old-flow job was not handed to the owner")
    v.expect(row["status"] == "awaiting_approval", "the migration changed a status")
    v.expect(h.task(leftover)["state"] == "done", "the leftover job kept a queued task")
    other = h.svc.job(untouched)
    v.expect(other["waiting_on"] == "agent" and h.task(untouched)["state"] == "queued", "an unrelated job was touched")


def _park_with_form(h: Harness, job_id: str, text: str, *, node: str | None = LOCAL) -> dict[str, Any]:
    """A filled, verified application parked for the owner with a watcher baseline built from `text`."""
    h.park(job_id, node)
    labels = [{"label": name, "reported": True} for name in ("First name", "Last name", "Email", "Phone")]
    baseline = fingerprint(
        PageState("https://job-boards.greenhouse.io/acme/jobs/1", "complete", text),
        [r"thank you for applying"],
        labels,
    )
    h.db.run(
        """INSERT INTO job_forms(job_id, inventory, draft_hash, verified_at, park_baseline)
           VALUES(%s, %s::jsonb, 'draft-1', now(), %s::jsonb)
           ON CONFLICT (job_id) DO UPDATE SET inventory=EXCLUDED.inventory, draft_hash='draft-1', verified_at=now(),
                                              park_baseline=EXCLUDED.park_baseline, park_signal=NULL""",
        (job_id, json.dumps(labels), json.dumps(baseline)),
    )
    return baseline


_FORM = "Apply to Acme\nFirst name\nLast name\nEmail\nPhone\nSubmit application"
_THANKS = PageState("https://job-boards.greenhouse.io/acme/jobs/1/confirmation", "complete", "Thank you for applying.")


def s33_auto_mode(h: Harness, v: Verdict) -> None:
    """ADR 0026 amendment: mode decision against the real settings/job rows, the auto fence's SQL (partitioned
    job_events) and the auto record. UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    default, override, tried, board = h.ingest("s33-", 4)
    v.expect(h.svc.submit_mode.decide(default).mode == "review", "the default is not review")
    h.svc.configure_runtime(RuntimeSettings(submitMode="auto"))
    v.expect(h.svc.submit_mode.decide(default).mode == "auto", "global auto was not applied")
    v.expect(h.event_count("mode_changed", default) == 0, "a global change must not write a per-job event")
    h.svc.set_submit_mode(override, "review")
    decided = h.svc.submit_mode.decide(override)
    v.expect(decided.mode == "review" and bool(decided.reason), "a per-job review override did not win with a reason")
    h.svc.set_submit_mode(override, None)
    v.expect(h.svc.submit_mode.decide(override).mode == "auto", "clearing the override did not restore the default")
    h.db.run("UPDATE jobs SET try_questions_anyway=true WHERE id=%s", (tried,))
    v.expect(h.svc.submit_mode.decide(tried).mode == "review", "try-anyway answers did not force review")
    h.db.run("UPDATE boards SET automation='assisted' WHERE id=(SELECT board_id FROM jobs WHERE id=%s)", (board,))
    v.expect(h.svc.submit_mode.decide(board).mode == "review", "a non-auto board did not force review")
    # The fence: written before the run, released only when it is the auto one.
    with h.db.transaction():
        h.svc.approvals.begin_auto_submit(default, {"requested": "auto"})
    v.expect(h.svc.submit_clicked(default), "the auto fence was not written")
    with h.db.transaction():
        h.svc.approvals.begin_auto_submit(default, {})
    v.expect(h.event_count("submit_clicked", default) == 1, "the fence was written twice")
    with h.db.transaction():
        h.svc.approvals.release_auto_fence(default)
    v.expect(
        not h.svc.submit_clicked(default) and h.event_count("submit_fence_released", default) == 1,
        "the auto fence was not released",
    )
    h.svc.event(default, "submit_clicked", "user", {"owner": True})
    with h.db.transaction():
        h.svc.approvals.release_auto_fence(default)
    v.expect(h.svc.submit_clicked(default), "release_auto_fence removed a fence that was not the auto one")
    # The auto record: system approval bound to the reported draft, consumed; application agent-reported.
    h.db.run("UPDATE jobs SET status='preparing', waiting_on='agent' WHERE id=%s", (override,))
    with h.db.transaction():
        h.svc.approvals.begin_auto_submit(override, {"requested": "auto"})
        h.svc.approvals.record_auto_submitted(override, "draft-auto", "Thank you for applying")
    row = h.svc.job(override)
    v.expect(row["status"] == "submitted", f"auto record did not submit: {row['status']}")
    app = h.db.one("SELECT confirmation::text AS confirmation FROM applications WHERE job_id=%s", (override,))
    v.expect(app is not None and app["confirmation"] == "agent-reported", "the application was not recorded as agent-reported")
    approval = h.db.one("SELECT scope, consumed_at FROM approvals WHERE job_id=%s AND kind='submit'", (override,))
    v.expect(
        approval is not None and approval["scope"] == "auto-mode" and approval["consumed_at"] is not None,
        "the auto-mode approval was not recorded and consumed",
    )
    with h.db.transaction():
        h.svc.approvals.record_auto_submitted(override, "draft-auto", "Thank you for applying")
    n = h.db.one("SELECT count(*)::int AS n FROM applications WHERE job_id=%s", (override,))
    v.expect(n is not None and n["n"] == 1, "a second auto record created a second application")
    # A parked auto-mode job is still never claimed for an agent run.
    h.heartbeat(LOCAL, 1)
    waiting = h.ingest("s33b-", 1)[0]
    h.park(waiting, LOCAL)
    h.requeue(waiting)
    v.expect(waiting not in [d.job_id for d in h.dispatch()], "auto mode let an awaiting_approval job be claimed")


def s34_park_watch(h: Harness, v: Verdict) -> None:
    """ADR 0026 amendment: watcher selection SQL, detection, ambiguity, closed tab and idempotency with the manual
    button, against real rows. Reads are faked (no Neo). UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    submit, ambiguous, thanking, gone, other_node, finished = h.ingest("s34-", 6)
    _park_with_form(h, submit, _FORM)
    _park_with_form(h, ambiguous, _FORM)
    _park_with_form(h, thanking, _FORM + "\nThank you for applying! We read every application.")
    _park_with_form(h, gone, _FORM)
    _park_with_form(h, other_node, _FORM, node="box-2")
    _park_with_form(h, finished, _FORM)
    h.db.run("UPDATE job_forms SET park_signal='tab_closed' WHERE job_id=%s", (finished,))
    watch = ParkWatchService(h.svc)
    listed = {str(r["id"]) for r in watch.repository.list_watchable(LOCAL, 40)}
    v.expect({submit, ambiguous, thanking, gone} <= listed, f"a parked job was not watched: {listed}")
    v.expect(other_node not in listed, "a job parked on another node was watched from this one")
    v.expect(finished not in listed, "a job already flagged tab_closed was watched")
    pages = {
        f"T-{submit}": Reading(_THANKS),
        f"T-{ambiguous}": Reading(PageState("https://acme.com/careers", "complete", "Open roles at Acme")),
        f"T-{thanking}": Reading(
            PageState(
                "https://job-boards.greenhouse.io/acme/jobs/1",
                "complete",
                _FORM + "\nThank you for applying! We read every application.",
            )
        ),
        f"T-{gone}": Reading(None, gone=True),
    }
    with patch("jobpilot.service.evidence.neo_browser", side_effect=BrowserUnavailable("no Neo in this script")):
        counts = watch.run_once(reader=lambda target: pages[target], sleep=lambda _: None)
        v.expect(
            counts["detected"] == 1 and counts["looks_submitted"] == 1 and counts["tab_closed"] == 1,
            f"unexpected outcomes: {counts}",
        )
        v.expect(h.svc.job(submit)["status"] == "submitted", "the detected job was not recorded as submitted")
        approval = h.db.one(
            "SELECT actor, payload FROM job_events WHERE job_id=%s AND kind='approval' ORDER BY id DESC LIMIT 1", (submit,)
        )
        v.expect(
            approval is not None and approval["actor"] == "user" and approval["payload"].get("method") == "auto-detected",
            "the detection was not recorded as a user submit by method auto-detected",
        )
        v.expect(h.svc.job(ambiguous)["status"] == "awaiting_approval", "an ambiguous page was recorded as submitted")
        v.expect(h.svc.job(thanking)["status"] == "awaiting_approval", "a baseline that already thanked triggered a submit")
        sig = h.db.one("SELECT park_signal FROM job_forms WHERE job_id=%s", (ambiguous,))
        v.expect(sig is not None and sig["park_signal"] == "looks_submitted", "the ambiguous page did not raise looks_submitted")
        sig = h.db.one("SELECT park_signal FROM job_forms WHERE job_id=%s", (gone,))
        v.expect(sig is not None and sig["park_signal"] == "tab_closed", "the closed tab did not raise tab_closed")
        # A second pass changes nothing and writes no second signal event.
        watch.run_once(reader=lambda target: pages[target], sleep=lambda _: None)
        v.expect(h.event_count("park_signal", ambiguous) == 1, "the signal event was written twice")
        # The manual button after a detection is a no-op; a detection after the button too.
        v.expect(h.svc.owner_submitted(submit, user_session=True)["already"], "the button after a detection was not idempotent")
        v.expect(not h.svc.auto_detected_submit(submit), "a second detection recorded again")
        manual = h.svc.owner_submitted(ambiguous, user_session=True)
        v.expect(
            not manual["already"] and not h.svc.auto_detected_submit(ambiguous),
            "a detection after the manual button recorded again",
        )
    n = h.db.one("SELECT count(*)::int AS n FROM applications WHERE job_id = ANY(%s)", ([submit, ambiguous],))
    v.expect(n is not None and n["n"] == 2, "applications were duplicated")
    # Dismissing / reopening: signals clear; reopen sends the job back to preparation.
    h.svc.reopen_application(gone, user_session=True)
    v.expect(h.svc.job(gone)["status"] == "preparing", "reopen did not send the job back to preparation")


def s35_live_notifications(h: Harness, v: Verdict) -> None:
    """Committed job_events and automation/settings changes notify; rolled-back ones do not; payload is ids only."""
    h.reset(concurrency=3, browsers=6)
    job = h.ingest("notify", 1)[0]
    with psycopg.connect(get_settings().dsn, autocommit=True) as listener:
        listener.execute("LISTEN jobpilot_events")
        with h.db.transaction():
            row = h.db.one(
                "INSERT INTO job_events(job_id,kind,actor,payload) VALUES(%s,'run_requeued','system',%s) RETURNING id",
                (job, J({"private": "must not be broadcast"})),
            )
            v.expect(not list(listener.notifies(timeout=0.05, stop_after=1)), "NOTIFY arrived before commit")
        found = list(listener.notifies(timeout=2, stop_after=1))
        v.expect(len(found) == 1, "committed job_event did not notify")
        if found and row:
            v.expect(found[0].channel == "jobpilot_events", "wrong notification channel")
            v.expect(
                json.loads(found[0].payload) == {"job": job, "kind": "run_requeued", "id": row["id"]},
                "notification included private payload or lost its identifiers",
            )
        try:
            with h.db.transaction():
                h.db.run("INSERT INTO job_events(job_id,kind,actor) VALUES(%s,'check','system')", (job,))
                raise RuntimeError("rollback fixture")
        except RuntimeError:
            pass
        v.expect(not list(listener.notifies(timeout=0.05, stop_after=1)), "rolled-back event notified")
        for table in ("automation", "runtime_settings"):
            h.db.run(sql.SQL("UPDATE {} SET updated_at=now()").format(sql.Identifier(table)).as_string())
            found = list(listener.notifies(timeout=2, stop_after=1))
            v.expect(len(found) == 1, f"{table} change did not notify")
            if found:
                hint = json.loads(found[0].payload)
                v.expect(set(hint) == {"job", "kind", "id"} and hint["job"] is None, f"{table} leaked data")


def s36_vault_git_lock(h: Harness, v: Verdict) -> None:
    """Vault git writes are serialized by a PostgreSQL advisory lock held across connections, and a stale
    index.lock is moved aside under it. Temp repository, real git. UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    # The harness Database is single-connection, and an advisory lock is re-entrant within one session, so it cannot
    # model two processes. Production uses a pool (one connection per thread/process): use one here too.
    pooled = Database(h.svc.cfg, pool_size=4)
    try:
        _s36_body(h, v, pooled)
    finally:
        if pooled.pool is not None:
            pooled.pool.close()


def _s36_body(h: Harness, v: Verdict, pooled: Database) -> None:
    with tempfile.TemporaryDirectory() as tmp:
        vault = Path(tmp) / "vault"
        ensure_vault(vault)
        store = vault / "_cv-store"
        store.mkdir(exist_ok=True)
        (store / "a.tex").write_text("a")
        done: list[str | None] = []
        errors: list[BaseException] = []

        def commit() -> None:
            try:
                done.append(commit_cv(vault, "cv: blocked", ["_cv-store/a.tex"], db=pooled))
            except BaseException as error:  # noqa: BLE001 - reported as a failed check
                errors.append(error)

        with pooled.session_lock(GIT_LOCK_KEY, GIT_LOCK_NAMESPACE):
            worker = threading.Thread(target=commit)
            worker.start()
            time.sleep(1.0)
            v.expect(worker.is_alive() and not done, "a commit ran while another connection held the vault git lock")
        worker.join(15)
        v.expect(
            not worker.is_alive() and not errors and len(done) == 1 and bool(done[0]),
            f"the commit did not run after release: {errors}",
        )
        # Contention: two connections, many commits, none may fail on index.lock.
        failures: list[BaseException] = []

        def burst(tag: str) -> None:
            for i in range(6):
                (store / f"{tag}{i}.tex").write_text(str(i))
                try:
                    commit_cv(vault, f"cv: {tag}{i}", [f"_cv-store/{tag}{i}.tex"], db=pooled)
                except BaseException as error:  # noqa: BLE001
                    failures.append(error)

        threads = [threading.Thread(target=burst, args=(tag,)) for tag in ("x", "y")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(60)
        v.expect(not failures, f"concurrent vault commits failed: {failures[:1]}")
        count = subprocess.run(["git", "rev-list", "--count", "HEAD"], cwd=vault, capture_output=True, text=True).stdout.strip()
        v.expect(count == "13", f"expected 13 commits (1 + 12), got {count}")
        # A crash leftover older than the threshold is moved aside, recorded, and the commit goes through.
        lock = vault / ".git" / "index.lock"
        lock.write_text("")
        os.utime(lock, (time.time() - 600, time.time() - 600))
        (store / "z.tex").write_text("z")
        sha = commit_cv(vault, "cv: z", ["_cv-store/z.tex"], db=pooled, on_recovered=h.svc.cv._lock_recovered)
        v.expect(bool(sha) and not lock.exists(), "the stale index.lock was not recovered")
        v.expect(len(list((vault / ".git").glob("index.lock.stale-*"))) == 1, "the stale lock was not kept aside")
        n = h.db.one("SELECT count(*)::int AS n FROM job_events WHERE kind='vault_lock_recovered'")
        v.expect(n is not None and n["n"] == 1, "no vault_lock_recovered event")


def s37_reasked_answered_label(h: Harness, v: Verdict) -> None:
    """Task 10: a question the owner already answered, re-reported under a new per-run key, is sent back to the agent
    instead of blocking; a genuinely new one is still asked; the owner's tab-completion and "already submitted" actions
    clear the card. Real answers/job_events/job_forms rows. UNRUN without PostgreSQL."""
    from jobpilot.dto.direct import DirectResult
    from jobpilot.service.direct import MAX_REASK_RUNS, DirectService

    h.reset(concurrency=3, browsers=6)
    reask, mixed, mine, sent = h.ingest("s37-", 4)
    direct = DirectService(h.svc)

    def report(*labels: str, role: str = "Engineer") -> DirectResult:
        return DirectResult.model_validate(
            {
                "status": "needs_answers",
                "role": role,
                "company": "Acme",
                "fields": [],
                "questions": [{"label": x, "kind": "text", "required": True, "options": []} for x in labels],
                "note": "step 2 of 8",
                "confirmation": None,
            }
        )

    def running(job_id: str) -> None:
        h.db.run("UPDATE jobs SET status='preparing', waiting_on='agent' WHERE id=%s", (job_id,))

    h.svc.answer_set("Referral Source", "LinkedIn", "global", "user (dashboard)")
    running(reask)
    outcome = direct.apply(reask, report("referral source", role="Role A"))  # same label, new run key
    v.expect(outcome == "reask", f"an answered label did not go back to the agent: {outcome}")
    v.expect(h.svc.job(reask)["waiting_on"] == "agent", "the job was parked for an answer that already existed")
    v.expect(h.event_count("answers_reasked", reask) == 1, "the re-ask was not recorded")
    for _ in range(MAX_REASK_RUNS):
        direct.apply(reask, report("Referral Source", role="Role A"))
    v.expect(h.svc.job(reask)["waiting_on"] == "you", "the re-ask loop was not capped")
    running(mixed)
    outcome = direct.apply(mixed, report("Referral Source", "Favourite colour", role="Role B"))
    form = h.svc.form(mixed) or {}
    asked = [f["label"] for f in form.get("inventory", []) if f.get("key") in (form.get("missing") or [])]
    v.expect(outcome == "blocked" and asked == ["Favourite colour"], f"only the new question should be asked: {asked}")
    v.expect(direct._reasked_hint(mixed) != [], "the re-asked answer was not offered to the next run")
    # "I filled it in the tab myself": clears the pending question, resumes the agent, never asks it again.
    result = h.svc.owner_completed(mixed, user_session=True)
    v.expect(result == {"ok": True, "already": False}, f"owner_completed: {result}")
    v.expect(h.svc.job(mixed)["waiting_on"] == "agent", "the agent was not resumed after owner_completed")
    v.expect(h.svc.owner_completed(mixed, user_session=True)["already"], "owner_completed was not idempotent")
    try:
        h.svc.owner_completed(mixed, user_session=False)
        v.expect(False, "owner_completed accepted a non-user session")
    except PermissionError:
        pass
    running(mine)
    outcome = direct.apply(mine, report("Favourite colour", role="Role C"))
    v.expect(outcome == "blocked", "the control case should still ask a new question")
    h.svc.owner_completed(mine, user_session=True)
    running(mine)
    outcome = direct.apply(mine, report("favourite  colour", role="Role C"))
    v.expect(outcome == "reask", f"a label the owner filled in the tab was asked again: {outcome}")
    # "I already submitted this": user-confirmed from a question state; idempotent; one applications row.
    running(sent)
    direct.apply(sent, report("Favourite colour", role="Role D"))
    for _ in range(2):
        h.svc.record_submitted(sent, "user-confirmed", "User reported submitting this application.", user_session=True)
    n = h.db.one("SELECT count(*)::int AS n FROM applications WHERE job_id=%s", (sent,))
    v.expect(
        h.svc.job(sent)["status"] == "submitted" and n is not None and n["n"] == 1, "already-submitted was not recorded once"
    )
    try:
        h.svc.record_submitted(mixed, "user-confirmed", "x", user_session=False)
        v.expect(False, "user-confirmed accepted a non-user session")
    except PermissionError:
        pass


def s38_guidance_on_parked_job(h: Harness, v: Verdict) -> None:
    """Owner guidance / resume on an awaiting_approval job moves it back to preparation and the claim picks it up;
    refused once a submit began or the job is finished. Real jobs/tasks/job_forms/events rows. UNRUN without PostgreSQL."""
    h.reset(concurrency=3, browsers=6)
    h.heartbeat(LOCAL, 1)
    guided, resumed, fenced, finished = h.ingest("s38-", 4)
    for job in (guided, resumed, fenced):
        h.park(job, LOCAL)
    h.db.run(
        "INSERT INTO job_forms(job_id, inventory, missing, draft_hash) VALUES(%s, '[]'::jsonb, '[]'::jsonb, 'd1') "
        "ON CONFLICT (job_id) DO UPDATE SET draft_hash='d1'",
        (guided,),
    )
    h.svc.guide_agent(guided, "you still need to fill everything; do not use the saved email")
    row = h.svc.job(guided)
    v.expect(row["status"] == "preparing" and row["waiting_on"] == "agent", f"guidance left it parked: {row['status']}")
    reason = h.db.one(
        "SELECT payload->>'reason' AS r FROM job_events WHERE job_id=%s AND kind='resume' ORDER BY id DESC LIMIT 1", (guided,)
    )
    v.expect(reason is not None and reason["r"] == "owner guidance", f"the resume event does not say why: {reason}")
    draft = h.db.one("SELECT draft_hash FROM job_forms WHERE job_id=%s", (guided,))
    v.expect(draft is not None and draft["draft_hash"] is None, "the old draft survived the send-back")
    v.expect(h.svc.latest_agent_guidance(guided) is not None, "the owner guidance was not recorded for the run")
    h.svc.resume(resumed)
    claimed = [d.job_id for d in h.dispatch()]
    v.expect(guided in claimed and resumed in claimed, f"send-back jobs were not claimed by an agent run: {claimed}")
    # Refusals: submit began, job finished.
    h.svc.event(fenced, "submit_clicked", "user", {"owner": True})
    for call in (lambda: h.svc.guide_agent(fenced, "x"), lambda: h.svc.resume(fenced)):
        try:
            call()
            v.expect(False, "a fenced parked job accepted guidance/resume")
        except PermissionError:
            pass
    h.db.run("UPDATE jobs SET status='submitted', waiting_on='you' WHERE id=%s", (finished,))
    try:
        h.svc.guide_agent(finished, "x")
        v.expect(False, "guidance on a submitted job was accepted")
    except PermissionError:
        pass
    try:
        h.svc.enqueue(fenced)
        v.expect(False, "enqueue accepted an awaiting_approval job the claim can never pick up")
    except PermissionError:
        pass


def s39_email_code_verification(h: Harness, v: Verdict) -> None:
    """ADR 0026 part C: the emailed-code run is the one agent run allowed on a parked job - only after an owner click is on
    record, one-shot for the watcher, owner retry after it finished. Real job_forms/tasks/job_events rows. UNRUN without
    PostgreSQL."""
    from jobpilot.dto.direct import DirectResult
    from jobpilot.service.verification import VerificationService

    h.reset(concurrency=3, browsers=6)
    h.heartbeat(LOCAL, 1)
    asked, plain, preparing = h.ingest("s39-", 3)
    for job in (asked, plain):
        h.park(job, LOCAL)
        h.db.run(
            "INSERT INTO job_forms(job_id, inventory, missing, draft_hash) VALUES(%s, '[]'::jsonb, '[]'::jsonb, 'd1') "
            "ON CONFLICT (job_id) DO UPDATE SET draft_hash='d1'",
            (job,),
        )
        h.requeue(job)
    verification = VerificationService(h.svc)
    claimed = [d.job_id for d in h.dispatch()]
    v.expect(asked not in claimed and plain not in claimed, f"a parked job without a request was claimed: {claimed}")
    v.expect(verification.request(asked, "watcher", "security code"), "the watcher's request was refused")
    v.expect(h.svc.submit_clicked(asked), "the owner-click fence was not written")
    v.expect(h.event_count("verification_challenge", asked) == 1, "the challenge was not recorded")
    v.expect(verification.pending(asked) and not verification.pending(plain), "pending() does not follow the request")
    v.expect(not verification.request(asked, "watcher"), "the watcher could ask twice")
    claimed = [d.job_id for d in h.dispatch()]
    v.expect(asked in claimed and plain not in claimed, f"only the requested parked job may be claimed: {claimed}")
    try:
        verification.request(preparing, "owner")
        v.expect(False, "a request on a job that is not parked was accepted")
    except PermissionError:
        pass
    blocked = DirectResult.model_validate(
        {
            "status": "blocked",
            "role": None,
            "company": None,
            "fields": [],
            "questions": [],
            "note": "no fresh mail",
            "confirmation": None,
        }
    )
    v.expect(verification.finish(asked, blocked) == "blocked", "a blocked report was not surfaced")
    row = h.svc.job(asked)
    v.expect(row["status"] == "awaiting_approval" and row["waiting_on"] == "you", f"blocked outcome left {row['waiting_on']}")
    v.expect("Paste the code yourself" in (row["blocked_reason"] or ""), "the card reason is not the paste-the-code one")
    v.expect(not verification.pending(asked), "the request stayed open after the run finished")
    v.expect(not verification.request(asked, "watcher"), "the watcher may not ask again after a finished run")
    v.expect(verification.request(asked, "owner"), "the owner's retry after a finished run was refused")
    v.expect(h.event_count("submit_clicked", asked) == 1, "the fence was written twice")
    # An application recorded meanwhile closes the door.
    h.db.run("UPDATE jobs SET status='submitted' WHERE id=%s", (asked,))
    v.expect(not verification.pending(asked), "pending() ignored that the job was submitted")


def _save_card_jobs(h: Harness, prefix: str, count: int) -> list[str]:
    """Jobs parked on the save-password card after a generated-password account (ADR 0028), real events/jobs rows."""
    from jobpilot.service.account import PROMPT_REASON, AccountService

    h.reset(concurrency=3, browsers=6)
    h.heartbeat(LOCAL, 1)
    jobs = h.ingest(prefix, count)
    for job in jobs:
        h.db.run("UPDATE jobs SET status='preparing', waiting_on='agent' WHERE id=%s", (job,))
        h.svc.event(job, "account_created", "system", {"method": "generated"})
        AccountService(h.svc).pause_for_save(job)
        row = h.svc.job(job)
        if row["waiting_on"] != "you" or row["blocked_reason"] != PROMPT_REASON:
            raise AssertionError(f"card not parked: {row['waiting_on']} {row['blocked_reason']}")
    return jobs


def _resumed(h: Harness, job: str) -> bool:
    row = h.svc.job(job)
    return row["waiting_on"] == "agent" and row["blocked_reason"] is None


def s40_save_card_confirm(h: Harness, v: Verdict) -> None:
    """'I saved it' (user session only) resumes the job and the claim picks it up. UNRUN without PostgreSQL."""
    from jobpilot.service.account import AccountService

    (job,) = _save_card_jobs(h, "s40-", 1)
    try:
        AccountService(h.svc).resolve(job, "done", user_session=False)
        v.expect(False, "a non-user session resolved the card")
    except PermissionError:
        pass
    v.expect(not _resumed(h, job), "a refused click resumed the job")
    v.expect(AccountService(h.svc).resolve(job, "done", user_session=True)["already"] is False, "done was not applied")
    v.expect(_resumed(h, job), "confirm did not resume the job")
    payload = h.last_event("password_save_resolved", job)
    v.expect(payload == {"choice": "done"}, f"resolution payload: {payload}")
    v.expect(h.event_count("account_created", job) == 1, "the account fact was written twice")


def s41_save_card_skip(h: Harness, v: Verdict) -> None:
    """'Skip: I can reset by email' resumes the same way and records the choice. UNRUN without PostgreSQL."""
    from jobpilot.service.account import AccountService

    (job,) = _save_card_jobs(h, "s41-", 1)
    v.expect(AccountService(h.svc).resolve(job, "skip", user_session=True)["already"] is False, "skip was not applied")
    v.expect(_resumed(h, job), "skip did not resume the job")
    v.expect(h.last_event("password_save_resolved", job) == {"choice": "skip"}, "the skip was not recorded")


def s42_save_card_timeout(h: Harness, v: Verdict) -> None:
    """A card open past 10 minutes continues on its own, once; fresh cards and cards of fenced jobs are left alone.
    UNRUN without PostgreSQL."""
    from jobpilot.service.account import AccountService

    stale, fresh, fenced = _save_card_jobs(h, "s42-", 3)
    account = AccountService(h.svc)
    v.expect(account.expire() == 0, "a fresh card was expired")
    for job in (stale, fenced):
        h.db.run(
            "UPDATE job_events SET ts = now() - interval '11 minutes' WHERE job_id=%s AND kind='password_save_prompt'", (job,)
        )
    h.svc.event(fenced, "submit_clicked", "user", {"owner": True})
    v.expect(account.expire() == 1, "expected exactly the stale, unfenced card to expire")
    v.expect(_resumed(h, stale) and not _resumed(h, fresh) and not _resumed(h, fenced), "wrong jobs were resumed")
    v.expect(h.event_count("password_save_resolved", stale) == 1, "the timeout was not recorded as an event")
    v.expect(account.expire() == 0, "the sweep resumed the same job twice")
    v.expect(h.event_count("password_save_resolved", stale) == 1, "a second sweep wrote another resolution")


def s43_save_card_double_click(h: Harness, v: Verdict) -> None:
    """Double click (or Done then Skip, or a click racing the timeout) resumes exactly once: one resolution, one resume
    event, one claim. UNRUN without PostgreSQL."""
    from jobpilot.service.account import AccountService

    (job,) = _save_card_jobs(h, "s43-", 1)
    account = AccountService(h.svc)
    results = [
        account.resolve(job, "done", user_session=True)["already"],
        account.resolve(job, "done", user_session=True)["already"],
        account.resolve(job, "skip", user_session=True)["already"],
    ]
    v.expect(results == [False, True, True], f"repeat clicks were not no-ops: {results}")
    v.expect(h.event_count("password_save_resolved", job) == 1, "more than one resolution was written")
    resumes = h.event_count("resume", job)
    v.expect(resumes == 1, f"expected one resume event, got {resumes}")
    h.db.run("UPDATE job_events SET ts = now() - interval '11 minutes' WHERE job_id=%s AND kind='password_save_prompt'", (job,))
    v.expect(account.expire() == 0, "the timeout resumed a card that was already resolved")
    claimed = [d.job_id for d in h.dispatch()].count(job)
    v.expect(claimed <= 1, f"the job was claimed {claimed} times")


def _form_with(**meta: str) -> Any:
    from unittest.mock import MagicMock

    form = MagicMock()
    form.meta.as_set_meta.return_value = meta
    return form


def _job_snapshot(h: Harness, job: str) -> dict[str, Any]:
    row = h.db.one(
        """SELECT status::text AS status, waiting_on::text AS waiting_on, blocked_reason, cv_mode::text AS cv_mode,
                  (SELECT count(*) FROM tasks WHERE job_id=j.id)::int AS tasks,
                  (SELECT count(*) FROM approvals WHERE job_id=j.id)::int AS approvals,
                  (SELECT count(*) FROM job_events WHERE job_id=j.id AND kind='submit_clicked')::int AS fences,
                  (SELECT count(*) FROM job_forms WHERE job_id=j.id)::int AS forms
           FROM jobs j WHERE id=%s""",
        (job,),
    )
    assert row is not None
    return dict(row)


def s44_refresh_details_keeps_the_job(h: Harness, v: Verdict) -> None:
    """ADR 0029: Refresh details on a SUBMITTED job fills the placeholder role/company, never replaces a real value or an
    owner-edited field, writes one names-only event, and leaves status, waiting_on, tasks, forms, approvals and the submit
    fence exactly as they were. Real jobs/events rows. UNRUN without PostgreSQL."""
    from jobpilot.service.details import DetailsService

    h.reset(concurrency=3, browsers=6)
    submitted, edited = h.ingest("s44-", 2)
    for job in (submitted, edited):
        cid = h.svc.upsert_company("ats.rippling.com")
        h.db.run(
            "UPDATE jobs SET role='(role not captured yet)', company_id=%s, status='submitted', waiting_on='site' WHERE id=%s",
            (cid, job),
        )
    h.svc.event(edited, "tracking_updated", "user", {"fields": ["location"]})  # the owner cleared/edited location by hand
    before = {job: _job_snapshot(h, job) for job in (submitted, edited)}
    details = DetailsService(h.svc)
    v.expect(details.request(submitted, user_session=True)["already"] is False, "first request was not queued")
    v.expect(details.request(submitted, user_session=True)["already"] is True, "a second request was not a no-op")
    try:
        details.request(submitted, user_session=False)
        v.expect(False, "a non-user session queued a refresh")
    except PermissionError:
        pass
    details.request(edited, user_session=True)
    published: list[str] = []
    v.expect(details.pump(published.append) == 2, f"pump claimed {published}")
    form = _form_with(role="Backend Engineer", company="Zenith Labs", location="Remote", work_mode="Remote", pay="$1")
    with patch("jobpilot.service.details.fetch_posting_form", return_value=form):
        for job in published:
            v.expect(details.run(job) == "done", f"run for {job} did not finish")
    row = h.db.one(
        "SELECT j.role, c.name AS company, j.location, j.work_mode, j.pay FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=%s",
        (submitted,),
    )
    v.expect(
        row is not None
        and (row["role"], row["company"], row["location"], row["work_mode"])
        == ("Backend Engineer", "Zenith Labs", "Remote", "Remote"),
        f"placeholders were not filled: {row}",
    )
    kept = h.db.one("SELECT role, location, work_mode, pay FROM jobs WHERE id=%s", (edited,))
    v.expect(kept is not None and not kept["location"], f"an owner-edited field was overwritten: {kept}")
    v.expect(kept is not None and kept["role"] == "Backend Engineer", "the other placeholders were not filled")
    for job in (submitted, edited):
        v.expect(_job_snapshot(h, job) == before[job], f"job state moved: {_job_snapshot(h, job)} != {before[job]}")
    payload = h.last_event("details_refreshed", submitted)
    v.expect(sorted(payload.get("fields", [])) == ["company", "location", "pay", "role", "work_mode"], f"event fields: {payload}")
    v.expect("Zenith" not in json.dumps(payload) and "Backend" not in json.dumps(payload), "the event carries values")
    # Real values are never replaced by a second refresh.
    details.request(submitted, user_session=True)
    other = _form_with(role="Intern", company="Other")
    published.clear()
    details.pump(published.append)
    with patch("jobpilot.service.details.fetch_posting_form", return_value=other):
        details.run(submitted)
    again = h.db.one(
        "SELECT j.role, c.name AS company FROM jobs j JOIN companies c ON c.id=j.company_id WHERE j.id=%s", (submitted,)
    )
    v.expect(
        again is not None and (again["role"], again["company"]) == ("Backend Engineer", "Zenith Labs"),
        f"real values replaced: {again}",
    )


def s45_refresh_details_one_run_per_job(h: Harness, v: Verdict) -> None:
    """One run per job, at most MAX_RUNNING (and the worker concurrency) at once, a lost run expires, a failed publish goes
    back to the queue, and no `tasks` row is ever created. UNRUN without PostgreSQL."""
    from jobpilot.service.details import MAX_RUNNING, DetailsService

    h.reset(concurrency=3, browsers=6)
    jobs = h.ingest("s45-", 4)
    details = DetailsService(h.svc)
    tasks_before = h.db.one("SELECT count(*)::int AS n FROM tasks")
    for job in jobs:
        details.request(job, user_session=True)
    sent: list[str] = []
    v.expect(details.pump(sent.append) == MAX_RUNNING, f"claimed {len(sent)} instead of {MAX_RUNNING}")
    v.expect(details.pump(sent.append) == 0, "a second tick started more while two were running")
    v.expect(len(set(sent)) == len(sent) == MAX_RUNNING, f"a job was published twice: {sent}")
    h.db.run("UPDATE job_detail_requests SET started_at = now() - interval '7 minutes' WHERE job_id=%s", (sent[0],))
    more: list[str] = []
    details.pump(more.append)
    state = h.db.one("SELECT state, reason FROM job_detail_requests WHERE job_id=%s", (sent[0],))
    v.expect(state is not None and (state["state"], state["reason"]) == ("failed", "timeout"), f"stale run: {state}")
    v.expect(h.event_count("details_refresh_failed", sent[0]) == 1, "the expiry was not recorded once")

    def broken(job_id: str) -> None:
        raise RuntimeError("broker down")

    h.db.run("UPDATE job_detail_requests SET state='failed' WHERE state='running'")
    h.db.run("UPDATE job_detail_requests SET state='queued' WHERE job_id=%s", (jobs[3],))
    details.pump(broken)
    back = h.db.one("SELECT state FROM job_detail_requests WHERE job_id=%s", (jobs[3],))
    v.expect(back is not None and back["state"] == "queued", f"a failed publish was not requeued: {back}")
    tasks_after = h.db.one("SELECT count(*)::int AS n FROM tasks")
    v.expect(tasks_before == tasks_after, "a details refresh created or changed a task row")


def s46_refresh_missing_details_bulk(h: Harness, v: Verdict) -> None:
    """The bulk button queues only live jobs with a placeholder role/company, once, and not deleted ones.
    UNRUN without PostgreSQL."""
    from jobpilot.service.details import DetailsService

    h.reset(concurrency=3, browsers=6)
    placeholder, complete, deleted, ats_host = h.ingest("s46-", 4)
    real = h.svc.upsert_company("Zenith Labs")
    host = h.svc.upsert_company("jobs.ashbyhq.com")
    h.db.run("UPDATE jobs SET role='(role not captured yet)' WHERE id=%s", (placeholder,))
    h.db.run("UPDATE jobs SET role='Engineer', company_id=%s WHERE id=%s", (real, complete))
    h.db.run("UPDATE jobs SET role='(role not captured yet)', archived_at=now() WHERE id=%s", (deleted,))
    h.db.run("UPDATE jobs SET role='Engineer', company_id=%s WHERE id=%s", (host, ats_host))
    details = DetailsService(h.svc)
    result = details.request_missing(user_session=True)
    queued = {r["job_id"] for r in h.db.all("SELECT job_id FROM job_detail_requests")}
    v.expect(queued == {placeholder, ats_host} and result["queued"] == 2, f"queued: {queued} {result}")
    v.expect(details.request_missing(user_session=True)["queued"] == 0, "the bulk button queued the same jobs twice")


SCENARIOS: list[tuple[str, Callable[[Harness, Verdict], None]]] = [
    ("concurrency limit", s1_concurrency),
    ("duplicate start_run", s2_duplicate_start),
    ("stale task id", s3_stale_task_id),
    ("unstarted lease redispatch", s4_redispatch_unstarted),
    ("dispatch_failed refunds", s5_dispatch_failed),
    ("browser cap", s6_browser_cap),
    ("node routing", s7_routing),
    ("sweep skips approvals", s8_sweep_skips_approvals),
    ("node isolation", s9_node_isolation),
    ("finish_run release", s10_finish_run),
    ("automation liveness", s11_liveness),
    ("claim() admission", s12_claim),
    ("spend never blocks (0025)", s13_spend_never_blocks),
    ("dead-node session", s14_dead_node_session),
    ("no worker burns no attempt", s15_no_worker_burns_no_attempts),
    ("expired lease cannot start", s16_expired_lease_cannot_start),
    ("missed ping keeps takeover", s17_missed_ping_starts_the_takeover_clock),
    ("start serialized per node", s18_start_serializes_per_node),
    ("agent env carries node", s19_agent_env_carries_node),
    ("neo offline", s20_neo_offline),
    ("tab reconciliation", s21_tab_reconcile),
    ("busy domain does not starve admission", s22_busy_domain_admission),
    ("per-employer browser pacing", s23_per_tenant_pacing),
    ("long run keeps its lease", s24_long_run_lease),
    ("parked question tab", s25_parked_question_tab),
    ("ingest preflight fence and expiry", s26_ingest_fence),
    ("question batch gate and send once", s27_question_batch_gate),
    ("0025 migration recovery", s28_daily_limit_migration),
    ("0025 monitoring aggregates", s29_monitoring_aggregation),
    ("requeue streak and retry-limit handoff", s30_requeue_streak),
    ("awaiting_approval never claimed", s31_awaiting_approval_never_claimed),
    ("0027 owner-submits migration", s32_owner_submits_migration),
    ("auto mode decision, fence, record", s33_auto_mode),
    ("parked-tab watcher", s34_park_watch),
    ("transactional live notifications", s35_live_notifications),
    ("vault git advisory lock", s36_vault_git_lock),
    ("re-asked answered label does not block", s37_reasked_answered_label),
    ("guidance on awaiting_approval reaches an agent run", s38_guidance_on_parked_job),
    ("emailed-code verification run", s39_email_code_verification),
    ("save-password card: confirm", s40_save_card_confirm),
    ("save-password card: skip", s41_save_card_skip),
    ("save-password card: timeout once", s42_save_card_timeout),
    ("save-password card: double click", s43_save_card_double_click),
    ("refresh details keeps the job", s44_refresh_details_keeps_the_job),
    ("refresh details: one run per job", s45_refresh_details_one_run_per_job),
    ("refresh missing details (bulk)", s46_refresh_missing_details_bulk),
]


# --- throwaway database lifecycle ---------------------------------------------------------------------------------


def run_scenarios(name: str) -> list[Verdict]:
    """Point the process at `name` (migrate() and get_settings() read PGDATABASE), run every scenario, restore state."""
    previous = os.environ.get("PGDATABASE")
    os.environ["PGDATABASE"] = name
    get_settings.cache_clear()
    db: Database | None = None
    try:
        cfg = get_settings()
        if cfg.pgdatabase != name:
            raise RuntimeError("settings do not point at the throwaway database")
        applied = migrate()
        print(f"throwaway database {name}: {len(applied)} migrations applied")
        db = Database(cfg, single=True)
        harness = Harness(db, Services(cfg, db))
        verdicts: list[Verdict] = []
        for number, (title, scenario) in enumerate(SCENARIOS, start=1):
            verdict = Verdict(number, title)
            try:
                scenario(harness, verdict)
            except Exception as exc:  # a crash fails that scenario only; the type and message are reported
                verdict.failures.append(f"raised {type(exc).__name__}: {exc}")
            print(verdict.line())
            verdicts.append(verdict)
        return verdicts
    finally:
        if db is not None:
            db.close()
        if previous is None:
            os.environ.pop("PGDATABASE", None)
        else:
            os.environ["PGDATABASE"] = previous
        get_settings.cache_clear()


def drop_database(admin: psycopg.Connection[Any], name: str) -> None:
    if not DB_NAME.fullmatch(name):
        raise RuntimeError(f"refusing to drop {name!r}: not a throwaway database name")
    admin.execute(
        "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE datname = %s AND pid <> pg_backend_pid()", (name,)
    )
    admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(name)))
    print(f"dropped throwaway database {name}")


def main() -> int:
    base = Settings()  # environment only: reads no file and opens no connection (get_settings() is not called here)
    name = f"jp_check_{secrets.token_hex(4)}"
    if not DB_NAME.fullmatch(name) or name == base.pgdatabase:
        raise SystemExit("refusing to run: the throwaway database name is unsafe")
    admin = psycopg.connect(
        host=base.pghost,
        port=base.pgport,
        user=base.pguser,
        password=base.pgpassword,
        dbname="postgres",
        autocommit=True,
    )
    try:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        verdicts = run_scenarios(name)
    finally:
        drop_database(admin, name)
        admin.close()
    passed = sum(1 for v in verdicts if not v.failures)
    print(f"result: {passed}/{len(SCENARIOS)} scenarios passed")
    return 0 if passed == len(SCENARIOS) else 1


if __name__ == "__main__":
    raise SystemExit(main())
