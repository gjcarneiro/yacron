import asyncio
import asyncio.subprocess
import datetime
import logging
from collections import OrderedDict, defaultdict
from typing import Any, Awaitable, Dict, List, Optional, Union  # noqa
from urllib.parse import urlparse
from aiohttp import web
import yacron.version
from yacron.config import (
    JobConfig,
    parse_config,
    ConfigError,
    parse_config_string,
    WebConfig,
)
from yacron.job import RunningJob, JobRetryState
from crontab import CronTab  # noqa

logger = logging.getLogger("yacron")
WAKEUP_INTERVAL = datetime.timedelta(minutes=1)


def naturaltime(seconds: float, future=False) -> str:
    assert future
    if seconds < 120:
        return "in {} second{}".format(
            int(seconds), "s" if seconds >= 2 else ""
        )
    minutes = seconds / 60
    if minutes < 120:
        return "in {} minute{}".format(
            int(minutes), "s" if minutes >= 2 else ""
        )
    hours = minutes / 60
    if hours < 48:
        return "in {} hour{}".format(int(hours), "s" if hours >= 2 else "")
    days = hours / 24
    return "in {} day{}".format(int(days), "s" if days >= 2 else "")


def get_now(timezone: Optional[datetime.tzinfo]) -> datetime.datetime:
    return datetime.datetime.now(timezone)


def next_sleep_interval() -> float:
    now = get_now(datetime.timezone.utc)
    target = now.replace(second=0) + WAKEUP_INTERVAL
    return (target - now).total_seconds()


def create_task(coro: Awaitable) -> asyncio.Task:
    return asyncio.get_event_loop().create_task(coro)


def web_site_from_url(runner: web.AppRunner, url: str) -> web.BaseSite:
    parsed = urlparse(url)
    if parsed.scheme == "http":
        assert parsed.hostname is not None
        assert parsed.port is not None
        return web.TCPSite(runner, parsed.hostname, parsed.port)
    elif parsed.scheme == "unix":
        return web.UnixSite(runner, parsed.path)
    else:
        logger.warning(
            "Ignoring web listen url %s: scheme %r not supported",
            url,
            parsed.scheme,
        )
        raise ValueError(url)


class Cron:
    def __init__(
        self, config_arg: Optional[str], *, config_yaml: Optional[str] = None
    ) -> None:
        # list of cron jobs we /want/ to run
        self.cron_jobs = OrderedDict()  # type: Dict[str, JobConfig]
        # list of cron jobs already running
        # name -> list of RunningJob
        self.running_jobs = defaultdict(
            list
        )  # type: Dict[str, List[RunningJob]]
        self.config_arg = config_arg
        if config_arg is not None:
            self.update_config()
        if config_yaml is not None:
            # config_yaml is for unit testing
            config, _ = parse_config_string(config_yaml)
            self.cron_jobs = OrderedDict((job.name, job) for job in config)

        self._wait_for_running_jobs_task = None  # type: Optional[asyncio.Task]
        self._stop_event = asyncio.Event()
        self._jobs_running = asyncio.Event()
        self.retry_state = {}  # type: Dict[str, JobRetryState]
        self.web_runner = None  # type: Optional[web.AppRunner]
        self.web_config = None  # type: Optional[WebConfig]

    async def run(self) -> None:
        self._wait_for_running_jobs_task = create_task(
            self._wait_for_running_jobs()
        )

        startup = True
        while not self._stop_event.is_set():
            try:
                web_config = self.update_config()
                await self.start_stop_web_app(web_config)
            except ConfigError as err:
                logger.error(
                    "Error in configuration file(s), so not updating "
                    "any of the config.:\n%s",
                    str(err),
                )
            except Exception:  # pragma: nocover
                logger.exception("please report this as a bug (1)")
            await self.spawn_jobs(startup)
            startup = False
            sleep_interval = next_sleep_interval()
            logger.debug("Will sleep for %.1f seconds", sleep_interval)
            try:
                await asyncio.wait_for(self._stop_event.wait(), sleep_interval)
            except asyncio.TimeoutError:
                pass

        logger.info("Shutting down (after currently running jobs finish)...")
        while self.retry_state:
            cancel_all = [
                self.cancel_job_retries(name) for name in self.retry_state
            ]
            await asyncio.gather(*cancel_all)
        await self._wait_for_running_jobs_task

        if self.web_runner is not None:
            logger.info("Stopping http server")
            await self.web_runner.cleanup()

    def signal_shutdown(self) -> None:
        logger.debug("Signalling shutdown")
        self._stop_event.set()

    def update_config(self) -> Optional[WebConfig]:
        if self.config_arg is None:
            return None
        config, web_config = parse_config(self.config_arg)
        self.cron_jobs = OrderedDict((job.name, job) for job in config)
        return web_config

    async def _web_get_version(self, request: web.Request) -> web.Response:
        return web.Response(text=yacron.version.version)

    async def _web_get_status(self, request: web.Request) -> web.Response:
        out = []
        for name, job in self.cron_jobs.items():
            running = self.running_jobs.get(name, None)
            if running:
                out.append(
                    {
                        "job": name,
                        "status": "running",
                        "pid": [
                            runjob.proc.pid
                            for runjob in running
                            if runjob.proc is not None
                        ],
                    }
                )
            else:
                crontab = job.schedule  # type: Union[CronTab, str]
                now = get_now(job.timezone)
                out.append(
                    {
                        "job": name,
                        "status": "scheduled",
                        "scheduled_in": (
                            crontab.next(now=now, default_utc=job.utc)
                            if isinstance(crontab, CronTab)
                            else str(crontab)
                        ),
                    }
                )
        if request.headers.get("Accept") == "application/json":
            return web.json_response(out)
        else:
            lines = []
            for jobstat in out:  # type: Dict[str, Any]
                if jobstat["status"] == "running":
                    status = "running (pid: {pid})".format(
                        pid=", ".join(str(pid) for pid in jobstat["pid"])
                    )
                else:
                    status = "scheduled ({})".format(
                        (
                            jobstat["scheduled_in"]
                            if type(jobstat["scheduled_in"]) is str
                            else naturaltime(
                                jobstat["scheduled_in"], future=True
                            )
                        )
                    )
                lines.append(
                    "{name}: {status}".format(
                        name=jobstat["job"], status=status
                    )
                )
            return web.Response(text="\n".join(lines))

    async def _web_start_job(self, request: web.Request) -> web.Response:
        name = request.match_info["name"]
        try:
            job = self.cron_jobs[name]
        except KeyError:
            raise web.HTTPNotFound()
        await self.maybe_launch_job(job)
        return web.Response()

    async def start_stop_web_app(self, web_config: Optional[WebConfig]):
        if self.web_runner is not None and (
            web_config is None or web_config != self.web_config
        ):
            # assert self.web_runner is not None
            logger.info("Stopping http server")
            await self.web_runner.cleanup()
            self.web_runner = None

        if (
            web_config is not None
            and web_config["listen"]
            and self.web_runner is None
        ):
            app = web.Application()
            app.add_routes(
                [
                    web.get("/version", self._web_get_version),
                    web.get("/status", self._web_get_status),
                    web.post("/jobs/{name}/start", self._web_start_job),
                ]
            )
            self.web_runner = web.AppRunner(app)
            await self.web_runner.setup()
            for addr in web_config["listen"]:
                site = web_site_from_url(self.web_runner, addr)
                logger.info("web: started listening on %s", addr)
                try:
                    await site.start()
                except ValueError:
                    pass
            self.web_config = web_config

    async def spawn_jobs(self, startup: bool) -> None:
        for job in self.cron_jobs.values():
            if self.job_should_run(startup, job):
                await self.launch_scheduled_job(job)

    @staticmethod
    def job_should_run(startup: bool, job: JobConfig) -> bool:
        if (
            startup
            and isinstance(job.schedule, str)
            and job.schedule == "@reboot"
        ):
            logger.debug(
                "Job %s (%s) is scheduled for startup (@reboot)",
                job.name,
                job.schedule_unparsed,
            )
            return True
        elif isinstance(job.schedule, CronTab):
            crontab = job.schedule  # type: CronTab
            if crontab.test(get_now(job.timezone).replace(second=0)):
                logger.debug(
                    "Job %s (%s) is scheduled for now",
                    job.name,
                    job.schedule_unparsed,
                )
                return True
            else:
                logger.debug(
                    "Job %s (%s) not scheduled for now",
                    job.name,
                    job.schedule_unparsed,
                )
                return False
        else:
            return False

    async def launch_scheduled_job(self, job: JobConfig) -> None:
        await self.cancel_job_retries(job.name)
        assert job.name not in self.retry_state

        retry = job.onFailure["retry"]
        logger.debug("Job %s retry config: %s", job.name, retry)
        if retry["maximumRetries"]:
            retry_state = JobRetryState(
                retry["initialDelay"],
                retry["backoffMultiplier"],
                retry["maximumDelay"],
            )
            self.retry_state[job.name] = retry_state

        await self.maybe_launch_job(job)

    async def maybe_launch_job(self, job: JobConfig) -> None:
        if self.running_jobs[job.name]:
            logger.warning(
                "Job %s: still running and concurrencyPolicy is %s",
                job.name,
                job.concurrencyPolicy,
            )
            if job.concurrencyPolicy == "Allow":
                pass
            elif job.concurrencyPolicy == "Forbid":
                return
            elif job.concurrencyPolicy == "Replace":
                for running_job in self.running_jobs[job.name]:
                    await running_job.cancel()
            else:
                raise AssertionError  # pragma: no cover
        logger.info("Starting job %s", job.name)
        running_job = RunningJob(job, self.retry_state.get(job.name))
        await running_job.start()
        self.running_jobs[job.name].append(running_job)
        logger.info("Job %s spawned", job.name)
        self._jobs_running.set()

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
                    try:
                        await asyncio.wait_for(self._jobs_running.wait(), 1)
                    except asyncio.TimeoutError:
                        pass
                    continue
                self._jobs_running.clear()
                # wait for at least one task with timeout
                done_tasks, _ = await asyncio.wait(
                    wait_tasks.values(),
                    timeout=1.0,
                    return_when=asyncio.FIRST_COMPLETED,
                )
                done_jobs = set()
                for job, task in list(wait_tasks.items()):
                    if task in done_tasks:
                        done_jobs.add(job)
                for job in done_jobs:
                    task = wait_tasks.pop(job)
                    try:
                        task.result()
                    except Exception:  # pragma: no cover
                        logger.exception("please report this as a bug (2)")

                    jobs_list = self.running_jobs[job.config.name]
                    jobs_list.remove(job)
                    if not jobs_list:
                        del self.running_jobs[job.config.name]

                    fail_reason = job.fail_reason
                    logger.info(
                        "Job %s exit code %s; has stdout: %s, "
                        "has stderr: %s; fail_reason: %r",
                        job.config.name,
                        job.retcode,
                        str(bool(job.stdout)).lower(),
                        str(bool(job.stderr)).lower(),
                        fail_reason,
                    )
                    if fail_reason is not None:
                        await self.handle_job_failure(job)
                    else:
                        await self.handle_job_success(job)
            except asyncio.CancelledError:
                raise
            except Exception:  # pragma: no cover
                logger.exception("please report this as a bug (3)")
                await asyncio.sleep(1)

    async def handle_job_failure(self, job: RunningJob) -> None:
        if self._stop_event.is_set():
            return
        if job.stdout:
            logger.info(
                "Job %s STDOUT:\n%s", job.config.name, job.stdout.rstrip()
            )
        if job.stderr:
            logger.info(
                "Job %s STDERR:\n%s", job.config.name, job.stderr.rstrip()
            )
        await job.report_failure()

        # Handle retries...
        state = job.retry_state
        if state is None or state.cancelled:
            await job.report_permanent_failure()
            return

        logger.debug(
            "Job %s has been retried %i times", job.config.name, state.count
        )
        if state.task is not None:
            if state.task.done():
                await state.task
            else:
                state.task.cancel()
        retry = job.config.onFailure["retry"]
        if (
            state.count >= retry["maximumRetries"]
            and retry["maximumRetries"] != -1
        ):
            await self.cancel_job_retries(job.config.name)
            await job.report_permanent_failure()
        else:
            retry_delay = state.next_delay()
            state.task = create_task(
                self.schedule_retry_job(
                    job.config.name, retry_delay, state.count
                )
            )

    async def schedule_retry_job(
        self, job_name: str, delay: float, retry_num: int
    ) -> None:
        logger.info(
            "Cron job %s scheduled to be retried (#%i) " "in %.1f seconds",
            job_name,
            retry_num,
            delay,
        )
        await asyncio.sleep(delay)
        try:
            job = self.cron_jobs[job_name]
        except KeyError:
            logger.warning(
                "Cron job %s was scheduled for retry, but "
                "disappeared from the configuration",
                job_name,
            )
        await self.maybe_launch_job(job)

    async def handle_job_success(self, job: RunningJob) -> None:
        await self.cancel_job_retries(job.config.name)
        await job.report_success()

    async def cancel_job_retries(self, name: str) -> None:
        try:
            state = self.retry_state.pop(name)
        except KeyError:
            return
        state.cancelled = True
        if state.task is not None:
            if state.task.done():
                await state.task
            else:
                state.task.cancel()
