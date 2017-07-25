import time
import yacron.cron
from yacron.job import RunningJob
from yacron.config import JobConfig
import asyncio
import pytest


class TracingRunningJob(RunningJob):

    _TRACE = asyncio.Queue()

    def __init__(self, config: JobConfig) -> None:
        super().__init__(config)
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
        self._TRACE.put_nowait((time.perf_counter(), "canceled", self))

    async def report_failure(self):
        self._TRACE.put_nowait((time.perf_counter(), "report_failure", self))
        await super().report_failure()

    async def report_permanent_failure(self):
        self._TRACE.put_nowait((time.perf_counter(),
                                "report_permanent_failure", self))
        await super().report_permanent_failure()

    async def report_success(self):
        self._TRACE.put_nowait((time.perf_counter(), "report_success", self))
        await super().report_success()


JOB_THAT_SUCCEEDS = '''
jobs:
  - name: test
    command: |
      echo "foobar"
    schedule: "* * * * *"
'''

JOB_THAT_FAILS = '''
jobs:
  - name: test
    command: |
      echo "foobar"
      exit 2
    schedule: "* * * * *"
'''


@pytest.mark.parametrize("config_yaml, expected_events", [
    (JOB_THAT_SUCCEEDS, ['create', 'start', 'started', 'wait', 'waited',
                         'report_success']),
    (JOB_THAT_FAILS, ['create', 'start', 'started', 'wait', 'waited',
                      'report_failure', 'report_permanent_failure']),
])
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
            if event in {'report_success', 'report_permanent_failure'}:
                break
        cron.signal_shutdown()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(
        wait_and_quit(),
        cron.run()))
    assert events == expected_events


RETRYING_JOB_THAT_FAILS = '''
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
'''


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
            if jobnum == 3 and event == 'report_permanent_failure':
                break
        cron.signal_shutdown()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(asyncio.gather(
        wait_and_quit(),
        cron.run()))
    assert events == [
        # initial attempt
        (1, 'create'),
        (1, 'start'), (1, 'started'),
        (1, 'wait'), (1, 'waited'),
        (1, 'report_failure'),
        # first retry
        (2, 'create'), (2, 'start'), (2, 'started'),
        (2, 'wait'), (2, 'waited'), (2, 'report_failure'),
        # second retry
        (3, 'create'), (3, 'start'), (3, 'started'),
        (3, 'wait'), (3, 'waited'),
        (3, 'report_failure'), (3, 'report_permanent_failure')]
