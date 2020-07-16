import time
import datetime
from pathlib import Path

import yacron.cron
from yacron.job import RunningJob
from yacron.config import JobConfig
import asyncio
import pytest


@pytest.fixture(autouse=True)
def fixed_current_time(monkeypatch):
    FIXED_TIME = datetime.datetime(
        year=1999, month=12, day=31, hour=12, minute=0, second=0
    )

    def get_now(utc):
        return FIXED_TIME

    monkeypatch.setattr("yacron.cron.get_now", get_now)


class TracingRunningJob(RunningJob):

    _TRACE = asyncio.Queue()

    def __init__(self, config: JobConfig, retry_state) -> None:
        super().__init__(config, retry_state)
        self._TRACE.put_nowait((time.perf_counter(), "create", self))

    async def start(self) -> None:
        self._TRACE.put_nowait((time.perf_counter(), "start", self))
        await super().start()
        self._TRACE.put_nowait((time.perf_counter(), "started", self))

    async def wait(self) -> None:
        self._TRACE.put_nowait((time.perf_counter(), "wait", self))
        await super().wait()
        self._TRACE.put_nowait((time.perf_counter(), "waited", self))

    async def cancel(self) -> None:
        self._TRACE.put_nowait((time.perf_counter(), "cancel", self))
        await super().cancel()
        self._TRACE.put_nowait((time.perf_counter(), "cancelled", self))

    async def report_failure(self):
        self._TRACE.put_nowait((time.perf_counter(), "report_failure", self))
        await super().report_failure()

    async def report_permanent_failure(self):
        self._TRACE.put_nowait(
            (time.perf_counter(), "report_permanent_failure", self)
        )
        await super().report_permanent_failure()

    async def report_success(self):
        self._TRACE.put_nowait((time.perf_counter(), "report_success", self))
        await super().report_success()


JOB_THAT_SUCCEEDS = """
jobs:
  - name: test
    command: |
      echo "foobar"
    schedule: "* * * * *"
"""

JOB_THAT_FAILS = """
jobs:
  - name: test
    command: |
      echo "foobar"
      exit 2
    schedule: "* * * * *"
"""


@pytest.mark.parametrize(
    "config_yaml, expected_events",
    [
        (
            JOB_THAT_SUCCEEDS,
            ["create", "start", "started", "wait", "waited", "report_success"],
        ),
        (
            JOB_THAT_FAILS,
            [
                "create",
                "start",
                "started",
                "wait",
                "waited",
                "report_failure",
                "report_permanent_failure",
            ],
        ),
    ],
)
def test_simple(monkeypatch, config_yaml, expected_events):
    monkeypatch.setattr(yacron.cron, "RunningJob", TracingRunningJob)
    cron = yacron.cron.Cron(None, config_yaml=config_yaml)

    events = []

    async def wait_and_quit():
        the_job = None
        while True:
            ts, event, job = await TracingRunningJob._TRACE.get()
            print(ts, event)
            if the_job is None:
                job = the_job
            else:
                assert job is the_job
            events.append(event)
            if event in {"report_success", "report_permanent_failure"}:
                break
        cron.signal_shutdown()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(wait_and_quit(), cron.run()))
    assert events == expected_events


RETRYING_JOB_THAT_FAILS = """
jobs:
  - name: test
    command: |
      echo "foobar"
      exit 2
    schedule: "* * * * *"
    onFailure:
      retry:
        maximumRetries: 2
        initialDelay: 0.1
        maximumDelay: 1
        backoffMultiplier: 2
"""


def test_fail_retry(monkeypatch):
    monkeypatch.setattr(yacron.cron, "RunningJob", TracingRunningJob)
    cron = yacron.cron.Cron(None, config_yaml=RETRYING_JOB_THAT_FAILS)

    events = []

    async def wait_and_quit():
        known_jobs = {}
        while True:
            ts, event, job = await TracingRunningJob._TRACE.get()
            try:
                jobnum = known_jobs[job]
            except KeyError:
                if known_jobs:
                    jobnum = max(known_jobs.values()) + 1
                else:
                    jobnum = 1
                known_jobs[job] = jobnum
            print(ts, event, jobnum)
            events.append((jobnum, event))
            if jobnum == 3 and event == "report_permanent_failure":
                break
        cron.signal_shutdown()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(wait_and_quit(), cron.run()))
    assert events == [
        # initial attempt
        (1, "create"),
        (1, "start"),
        (1, "started"),
        (1, "wait"),
        (1, "waited"),
        (1, "report_failure"),
        # first retry
        (2, "create"),
        (2, "start"),
        (2, "started"),
        (2, "wait"),
        (2, "waited"),
        (2, "report_failure"),
        # second retry
        (3, "create"),
        (3, "start"),
        (3, "started"),
        (3, "wait"),
        (3, "waited"),
        (3, "report_failure"),
        (3, "report_permanent_failure"),
    ]


JOB_THAT_HANGS = """
jobs:
  - name: test
    command: |
      trap "echo '(ignoring SIGTERM)'" TERM
      echo "starting..."
      sleep 10
      echo "all done."
    schedule: "* * * * *"
    captureStdout: true
    executionTimeout: 0.25
    killTimeout: 0.25
"""


def test_execution_timeout(monkeypatch):
    monkeypatch.setattr(yacron.cron, "RunningJob", TracingRunningJob)
    cron = yacron.cron.Cron(None, config_yaml=JOB_THAT_HANGS)

    events = []
    jobs_stdout = {}

    async def wait_and_quit():
        known_jobs = {}
        while True:
            ts, event, job = await TracingRunningJob._TRACE.get()
            try:
                jobnum = known_jobs[job]
            except KeyError:
                if known_jobs:
                    jobnum = max(known_jobs.values()) + 1
                else:
                    jobnum = 1
                known_jobs[job] = jobnum
            print(ts, event, jobnum)
            events.append((jobnum, event))
            if jobnum == 1 and event == "report_permanent_failure":
                jobs_stdout[jobnum] = job.stdout
                break
        cron.signal_shutdown()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(wait_and_quit(), cron.run()))
    assert events == [
        # initial attempt
        (1, "create"),
        (1, "start"),
        (1, "started"),
        (1, "wait"),
        (1, "cancel"),
        (1, "cancelled"),
        (1, "waited"),
        (1, "report_failure"),
        (1, "report_permanent_failure"),
    ]
    assert jobs_stdout[1] == "starting...\n"


CONCURRENT_JOB = """
jobs:
  - name: test
    command: |
      echo "starting..."
      sleep 0.5
      echo "all done."
    schedule: "* * * * *"
    captureStdout: true
    concurrencyPolicy: {policy}
"""


@pytest.mark.xfail
@pytest.mark.parametrize(
    "policy,expected_numjobs,expected_max_running",
    [("Allow", 2, 2), ("Forbid", 1, 1), ("Replace", 2, 1)],
)
def test_concurrency_policy(
    monkeypatch, policy, expected_numjobs, expected_max_running
):
    monkeypatch.setattr(yacron.cron, "RunningJob", TracingRunningJob)
    START_TIME = datetime.datetime(
        year=1999,
        month=12,
        day=31,
        hour=12,
        minute=0,
        second=59,
        microsecond=750000,
    )

    t0 = time.perf_counter()

    def get_now(utc):
        return START_TIME + datetime.timedelta(
            seconds=(time.perf_counter() - t0)
        )

    monkeypatch.setattr("yacron.cron.get_now", get_now)

    cron = yacron.cron.Cron(
        None, config_yaml=CONCURRENT_JOB.format(policy=policy)
    )

    events = []
    numjobs = 0
    max_running = 0

    async def wait_and_quit():
        nonlocal numjobs, max_running
        known_jobs = {}
        pending_jobs = set()
        running_jobs = set()
        while not (known_jobs and not pending_jobs):
            ts, event, job = await TracingRunningJob._TRACE.get()
            try:
                jobnum = known_jobs[job]
            except KeyError:
                if known_jobs:
                    jobnum = max(known_jobs.values()) + 1
                else:
                    jobnum = 1
                known_jobs[job] = jobnum
                pending_jobs.add(jobnum)
                running_jobs.add(jobnum)
                numjobs += 1
            print(ts, event, jobnum)
            events.append((jobnum, event))
            if event in {"report_success", "report_permanent_failure"}:
                pending_jobs.discard(jobnum)
            if event in {
                "report_success",
                "report_permanent_failure",
                "cancelled",
            }:
                running_jobs.discard(jobnum)
            max_running = max(len(running_jobs), max_running)
        cron.signal_shutdown()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(wait_and_quit(), cron.run()))
    import pprint

    pprint.pprint(events)
    assert (numjobs, max_running) == (expected_numjobs, expected_max_running)


def test_simple_config_file(monkeypatch):
    monkeypatch.setattr(yacron.cron, "RunningJob", TracingRunningJob)
    config_arg = str(Path(__file__).parent / "testconfig.yaml")
    yacron.cron.Cron(config_arg)


RETRYING_JOB_THAT_FAILS2 = """
jobs:
  - name: test
    command: |
      echo "foobar"
      exit 2
    schedule: "* * * * *"
    onFailure:
      retry:
        maximumRetries: 1
        initialDelay: 0.4
        maximumDelay: 1
        backoffMultiplier: 1
"""


@pytest.mark.xfail
def test_concurrency_and_backoff(monkeypatch):
    monkeypatch.setattr(yacron.cron, "RunningJob", TracingRunningJob)
    START_TIME = datetime.datetime(
        year=1999,
        month=12,
        day=31,
        hour=12,
        minute=0,
        second=59,
        microsecond=750000,
    )
    STOP_TIME = datetime.datetime(
        year=1999,
        month=12,
        day=31,
        hour=12,
        minute=1,
        second=00,
        microsecond=250000,
    )

    t0 = time.perf_counter()

    def get_now(utc):
        return START_TIME + datetime.timedelta(
            seconds=(time.perf_counter() - t0)
        )

    def get_reltime(ts):
        return START_TIME + datetime.timedelta(seconds=(ts - t0))

    monkeypatch.setattr("yacron.cron.get_now", get_now)

    cron = yacron.cron.Cron(None, config_yaml=RETRYING_JOB_THAT_FAILS2)

    events = []
    numjobs = 0

    async def wait_and_quit():
        nonlocal numjobs
        known_jobs = {}
        pending_jobs = set()
        running_jobs = set()
        while get_now(True) < STOP_TIME:
            try:
                ts, event, job = await asyncio.wait_for(
                    TracingRunningJob._TRACE.get(), 0.1
                )
            except asyncio.TimeoutError:
                continue
            try:
                jobnum = known_jobs[job]
            except KeyError:
                if known_jobs:
                    jobnum = max(known_jobs.values()) + 1
                else:
                    jobnum = 1
                known_jobs[job] = jobnum
                pending_jobs.add(jobnum)
                running_jobs.add(jobnum)
                numjobs += 1
            print(get_reltime(ts), event, jobnum)
            events.append((jobnum, event))
            if event in {"report_success", "report_permanent_failure"}:
                pending_jobs.discard(jobnum)
            if event in {
                "report_success",
                "report_permanent_failure",
                "cancelled",
            }:
                running_jobs.discard(jobnum)
        cron.signal_shutdown()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(wait_and_quit(), cron.run()))
    import pprint

    pprint.pprint(events)
    assert numjobs == 2


@pytest.mark.parametrize(
    "value_in, out",
    [
        (10, "in 10 seconds"),
        (305.0, "in 5 minutes"),
        (5000.0, "in 83 minutes"),
        (50000.0, "in 13 hours"),
        (500000.0, "in 5 days"),
    ],
)
def test_naturaltime(value_in, out):
    got_out = yacron.cron.naturaltime(value_in, future=True)
    assert got_out == out
