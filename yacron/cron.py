import asyncio
import asyncio.subprocess
import datetime
import logging
import sys
from collections import OrderedDict, defaultdict
from typing import Any, Awaitable, Dict, List, Optional  # noqa

from yacron.config import JobConfig, parse_config
from yacron.job import RunningJob

logger = logging.getLogger('yacron')
WAKEUP_INTERVAL = datetime.timedelta(minutes=1)


def next_sleep_interval() -> float:
    now = datetime.datetime.utcnow()
    target = (datetime.datetime(now.year, now.month, now.day,
                                now.hour, now.minute) +
              WAKEUP_INTERVAL)
    return (target - now).total_seconds()


def create_task(coro: Awaitable) -> asyncio.Task:
    return asyncio.get_event_loop().create_task(coro)


class JobRetryState:

    def __init__(self, initialDelay: float) -> None:
        self.delay = initialDelay
        self.count = 0  # number of times retried
        self.task = None  # type: Optional[asyncio.Task]


class Cron:

    def __init__(self, config_arg: str) -> None:
        # list of cron jobs we /want/ to run
        self.cron_jobs = OrderedDict()  # type: Dict[str, JobConfig]
        # list of cron jobs already running
        # name -> list of RunningJob
        self.running_jobs = \
            defaultdict(list)  # type: Dict[str, List[RunningJob]]
        self.config_arg = config_arg
        self._wait_for_running_jobs_task = None  # type: Optional[asyncio.Task]
        self._stop_event = asyncio.Event()
        self.retry_state = {}  # type: Dict[str, JobRetryState]
        try:
            self.update_config()
        except FileNotFoundError:
            logger.error("Config file %r not found", config_arg)
            sys.exit(66)
        except Exception as exc:
            logger.exception("Error in a config file: %s", exc)
            sys.exit(65)

    async def run(self) -> None:
        self._wait_for_running_jobs_task = \
            create_task(self._wait_for_running_jobs())

        while not self._stop_event.is_set():
            try:
                self.update_config()
            except Exception as exc:
                logger.exception("Error in a config file: %s", exc)
            await self.spawn_jobs()
            sleep_interval = next_sleep_interval()
            logger.debug("Will sleep for %.1f seconds", sleep_interval)
            try:
                await asyncio.wait_for(self._stop_event.wait(), sleep_interval)
            except asyncio.TimeoutError:
                pass

        logger.info("Shutting down (after currently running jobs finish)...")

        for state in self.retry_state.values():
            if state.task is not None:
                if state.task.done():
                    await state.task
                else:
                    state.task.cancel()

        await self._wait_for_running_jobs_task

    def signal_shutdown(self) -> None:
        logger.debug("Signalling shutdown")
        self._stop_event.set()

    def update_config(self) -> None:
        config = parse_config(self.config_arg)
        self.cron_jobs = OrderedDict((job.name, job) for job in config)

    async def spawn_jobs(self) -> None:
        now = datetime.datetime.now()
        for job in self.cron_jobs.values():
            if job.schedule.test(now):
                logger.debug("Job %s (%s) is scheduled for now",
                             job.name, job.schedule_unparsed)
                await self.cancel_job_retries(job.name)
                await self.maybe_launch_job(job)
            else:
                logger.debug("Job %s (%s) not scheduled for now",
                             job.name, job.schedule_unparsed)

    async def maybe_launch_job(self, job: JobConfig) -> None:
        if self.running_jobs[job.name]:
            logger.warning("Job %s: still running and concurrencyPolicy is %s",
                           job.name, job.concurrencyPolicy)
            if job.concurrencyPolicy == 'Allow':
                pass
            elif job.concurrencyPolicy == 'Forbid':
                return
            elif job.concurrencyPolicy == 'Replace':
                for running_job in self.running_jobs[job.name]:
                    await running_job.cancel()
            else:
                raise AssertionError
        logger.info("Starting job %s", job.name)
        running_job = RunningJob(job)
        await running_job.start()
        self.running_jobs[job.name].append(running_job)
        logger.info("Job %s spawned", job.name)

    # continually watches for the running jobs, clean them up when they exit
    async def _wait_for_running_jobs(self) -> None:
        # job -> wait task
        wait_tasks = {}  # type: Dict[RunningJob, asyncio.Task]
        while self.running_jobs or not self._stop_event.is_set():
            try:
                for jobs in self.running_jobs.values():
                    for job in jobs:
                        if job not in wait_tasks:
                            wait_tasks[job] = create_task(job.wait())
                if not wait_tasks:
                    await asyncio.sleep(1)
                    continue
                # wait for at least one task with timeout
                done_tasks, _ = await asyncio.wait(
                    wait_tasks.values(),
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED)
                done_jobs = set()
                for job, task in list(wait_tasks.items()):
                    if task in done_tasks:
                        done_jobs.add(job)
                for job in done_jobs:
                    task = wait_tasks.pop(job)
                    try:
                        task.result()
                    except Exception:
                        logger.exception("zbr")

                    jobs_list = self.running_jobs[job.config.name]
                    jobs_list.remove(job)
                    if not jobs_list:
                        del self.running_jobs[job.config.name]

                    logger.info("Job %s exit code %s; has stdout: %s, "
                                "has stderr: %s; failed: %s",
                                job.config.name, job.retcode,
                                str(bool(job.stdout)).lower(),
                                str(bool(job.stderr)).lower(),
                                str(job.failed).lower())
                    if job.failed:
                        await self.handle_job_failure(job)
                    else:
                        await self.handle_job_success(job)
            except Exception:
                logger.exception("blah")
                await asyncio.sleep(1)

    async def handle_job_failure(self, job: RunningJob) -> None:
        if self._stop_event.is_set():
            return
        if job.stdout:
            logger.info("Job %s STDOUT:\n%s",
                        job.config.name, job.stdout.rstrip())
        if job.stderr:
            logger.info("Job %s STDERR:\n%s",
                        job.config.name, job.stderr.rstrip())
        await job.report_failure()
        retry = job.config.onFailure['retry']
        logger.debug("Job %s retry config: %s",
                     job.config.name, retry)
        if not retry['maximumRetries']:
            await job.report_permanent_failure()
            return

        # Handle retries...
        try:
            state = self.retry_state[job.config.name]
        except KeyError:
            state = JobRetryState(retry['initialDelay'])
            self.retry_state[job.config.name] = state
        logger.debug("Job %s has been retried %i times",
                     job.config.name, state.count)
        if state.task is not None:
            if state.task.done():
                await state.task
            else:
                state.task.cancel()
        if state.count >= retry['maximumRetries']:
            await self.cancel_job_retries(job.config.name)
            await job.report_permanent_failure()
        else:
            state.count += 1
            state.task = create_task(
                self.schedule_retry_job(job.config.name, state.delay,
                                        state.count))
            multiplier = retry['backoffMultiplier']
            max_delay = retry['maximumDelay']
            state.delay = min(state.delay * multiplier, max_delay)

    async def schedule_retry_job(self, job_name: str, delay: float,
                                 retry_num: int) -> None:
        logger.info("Cron job %s scheduled to be retried (#%i) "
                    "in %.1f seconds",
                    job_name, retry_num, delay)
        await asyncio.sleep(delay)
        try:
            job = self.cron_jobs[job_name]
        except KeyError:
            logger.warning("Cron job %s was scheduled for retry, but "
                           "disappeared from the configuration", job_name)
        await self.maybe_launch_job(job)

    async def handle_job_success(self, job: RunningJob) -> None:
        await self.cancel_job_retries(job.config.name)
        await job.report_success()

    async def cancel_job_retries(self, name: str) -> None:
        try:
            state = self.retry_state.pop(name)
        except KeyError:
            return
        if state.task is not None:
            if state.task.done():
                await state.task
            else:
                state.task.cancel()
