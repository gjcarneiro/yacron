import asyncio
import datetime
import time
from pathlib import Path

import pytest
import pytz

import yacron.cron
from yacron.config import JobConfig
from yacron.job import RunningJob


@pytest.fixture(autouse=True)
def fixed_current_time(monkeypatch):
    FIXED_TIME = datetime.datetime(
        year=1999, month=12, day=31, hour=12, minute=0, second=0
    )

    def get_now(timezone):
        now = FIXED_TIME
        if timezone is not None:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone)
            else:
                now = now.astimezone(timezone)
        return now

    monkeypatch.setattr("yacron.cron.get_now", get_now)


@pytest.fixture()
def tracing_running_job(monkeypatch):
    TracingRunningJob._TRACE = asyncio.Queue()
    monkeypatch.setattr(yacron.cron, "RunningJob", TracingRunningJob)
    yield TracingRunningJob
    TracingRunningJob._TRACE = asyncio.Queue()


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
    schedule: "@reboot"
"""

JOB_THAT_FAILS = """
jobs:
  - name: test
    command: |
      echo "foobar"
      exit 2
    schedule: "@reboot"
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
@pytest.mark.asyncio
async def test_simple(tracing_running_job, config_yaml, expected_events):
    cron = yacron.cron.Cron(None, config_yaml=config_yaml)

    events = []

    async def wait_and_quit():
        the_job = None
        while True:
            ts, event, job = await tracing_running_job._TRACE.get()
            print(ts, event)
            if the_job is None:
                job = the_job
            else:
                assert job is the_job
            events.append(event)
            if event in {"report_success", "report_permanent_failure"}:
                break
        cron.signal_shutdown()

    await asyncio.gather(wait_and_quit(), cron.run())
    assert events == expected_events


RETRYING_JOB_THAT_FAILS = """
jobs:
  - name: test
    command: |
      echo "foobar"
      exit 2
    schedule: "@reboot"
    onFailure:
      retry:
        maximumRetries: 2
        initialDelay: 0.1
        maximumDelay: 1
        backoffMultiplier: 2
"""


@pytest.mark.asyncio
async def test_fail_retry(tracing_running_job):
    cron = yacron.cron.Cron(None, config_yaml=RETRYING_JOB_THAT_FAILS)

    events = []

    async def wait_and_quit():
        known_jobs = {}
        while True:
            ts, event, job = await tracing_running_job._TRACE.get()
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

    await asyncio.gather(wait_and_quit(), cron.run())
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
    schedule: "@reboot"
    captureStdout: true
    executionTimeout: 0.25
    killTimeout: 0.25
"""


@pytest.mark.asyncio
async def test_execution_timeout(tracing_running_job):
    cron = yacron.cron.Cron(None, config_yaml=JOB_THAT_HANGS)

    events = []
    jobs_stdout = {}

    async def wait_and_quit():
        known_jobs = {}
        while True:
            ts, event, job = await tracing_running_job._TRACE.get()
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

    await asyncio.gather(wait_and_quit(), cron.run())
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
    schedule: "@reboot"
    captureStdout: true
    concurrencyPolicy: {policy}
"""


@pytest.mark.xfail
@pytest.mark.parametrize(
    "policy,expected_numjobs,expected_max_running",
    [("Allow", 2, 2), ("Forbid", 1, 1), ("Replace", 2, 1)],
)
@pytest.mark.asyncio
async def test_concurrency_policy(
    monkeypatch,
    tracing_running_job,
    policy,
    expected_numjobs,
    expected_max_running,
):
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

    def get_now(timezone):
        now = START_TIME + datetime.timedelta(
            seconds=(time.perf_counter() - t0)
        )
        if timezone is not None:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone)
            else:
                now = now.astimezone(timezone)
        return now

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
            ts, event, job = await tracing_running_job._TRACE.get()
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

    await asyncio.gather(wait_and_quit(), cron.run())
    import pprint

    pprint.pprint(events)
    assert (numjobs, max_running) == (expected_numjobs, expected_max_running)


def test_simple_config_file(tracing_running_job):
    config_arg = str(Path(__file__).parent / "testconfig.yaml")
    yacron.cron.Cron(config_arg)


RETRYING_JOB_THAT_FAILS2 = """
jobs:
  - name: test
    command: |
      echo "foobar"
      exit 2
    schedule: "@reboot"
    onFailure:
      retry:
        maximumRetries: 1
        initialDelay: 0.4
        maximumDelay: 1
        backoffMultiplier: 1
"""


@pytest.mark.asyncio
async def test_concurrency_and_backoff(monkeypatch, tracing_running_job):  # noqa: C901
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

    def get_now(timezone):
        now = START_TIME + datetime.timedelta(
            seconds=(time.perf_counter() - t0)
        )
        if timezone is not None:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone)
            else:
                now = now.astimezone(timezone)
        return now

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
        while get_now(None) < STOP_TIME:
            try:
                ts, event, job = await asyncio.wait_for(
                    tracing_running_job._TRACE.get(), 0.1
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

    await asyncio.gather(wait_and_quit(), cron.run())
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


DT = datetime.datetime
UTC = datetime.timezone.utc
LONDON = pytz.timezone("Europe/London")


@pytest.mark.parametrize(
    "schedule, timezone, utc, now, startup, enabled, result",
    [
        (
            "* * * * *",
            "",
            "",
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC),
            False,
            "",
            True,
        ),
        (
            "59 14 * * *",
            "",
            "",
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC),
            False,
            "",
            True,
        ),
        (
            "59 14 * * *",
            "",
            "",
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC),
            True,  # startup
            "",
            False,
        ),
        (
            "49 14 * * *",
            "",
            "",
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC),
            False,
            "",
            False,
        ),
        (
            "59 14 * * *",
            "",
            "utc: true",
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC),
            False,
            "",
            True,
        ),
        (
            "59 14 * * *",
            "",
            "utc: true",  # London is UTC+1 during DST
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC).astimezone(LONDON),
            False,
            "",
            True,
        ),
        (
            "59 14 * * *",
            "",
            "utc: false",  # London is UTC+1 during DST
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC).astimezone(LONDON),
            False,
            "",
            False,
        ),
        (
            "1 8 * * *",
            "timezone: America/Los_Angeles",
            "",
            DT(2020, 7, 20, 15, 1, 1, tzinfo=UTC),
            False,
            "",
            True,
        ),
        (
            "1 8 * * *",
            "timezone: Europe/London",
            "",
            DT(2020, 7, 20, 15, 1, 1, tzinfo=UTC),
            False,
            "",
            False,
        ),
        (
            "@reboot",
            "",
            "",
            DT(2020, 7, 20, 15, 1, 1, tzinfo=UTC),
            False,
            "",
            False,
        ),
        (
            "@reboot",
            "",
            "",
            DT(2020, 7, 20, 15, 1, 1, tzinfo=UTC),
            True,
            "",
            True,
        ),
        # enabled: false
        (
            "* * * * *",
            "",
            "",
            DT(2020, 7, 20, 14, 59, 1, tzinfo=UTC),
            False,
            "enabled: false",
            False,
        ),
    ],
)
def test_job_should_run(
    monkeypatch, schedule, timezone, utc, now, startup, enabled, result
):
    def get_now(timezone):
        print("timezone: ", timezone)
        retval = now
        if timezone is not None:
            retval = retval.astimezone(timezone)
        print("now: ", retval)
        return retval

    monkeypatch.setattr("yacron.cron.get_now", get_now)

    config_yaml = f"""
jobs:
  - name: test
    command: |
      echo "foobar"
    schedule: "{schedule}"
    {timezone}
    {utc}
    {enabled}
                            """
    print(config_yaml)
    cron = yacron.cron.Cron(None, config_yaml=config_yaml)
    job = list(cron.cron_jobs.values())[0]
    assert cron.job_should_run(startup, job) == result
