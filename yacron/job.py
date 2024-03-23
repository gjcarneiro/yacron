import asyncio
import asyncio.subprocess
import logging
import os
import subprocess
import sys
import time
from datetime import datetime, timezone
from email.message import EmailMessage
from socket import gethostname
from typing import Any, Dict, List, Optional, Tuple

import aiosmtplib
import jinja2
import sentry_sdk
import sentry_sdk.utils

from yacron.config import JobConfig
from yacron.statsd import StatsdJobMetricWriter

logger = logging.getLogger("yacron")


if "HOSTNAME" not in os.environ:
    os.environ["HOSTNAME"] = gethostname()


def fixup_pyinstaller_env(env: Dict[str, str]) -> None:
    # check for pyinstaller env, fix clobbered env vars
    # https://github.com/gjcarneiro/yacron/issues/68
    if getattr(sys, "frozen", False):
        for env_var in "LD_LIBRARY_PATH", "LIBPATH":
            env[env_var] = env.get(f"{env_var}_ORIG", "")


class StreamReader:
    def __init__(
        self,
        job_name: str,
        stream_name: str,
        stream: asyncio.StreamReader,
        stream_prefix: str,
        save_limit: int,
    ) -> None:
        self.save_top: List[str] = []
        self.save_bottom: List[str] = []
        self.job_name = job_name
        self.save_limit = save_limit
        self.stream_name = stream_name
        self.stream_prefix = stream_prefix
        self._reader = asyncio.create_task(self._read(stream))
        self.discarded_lines = 0

    async def _read(self, stream):
        prefix = self.stream_prefix.format(
            job_name=self.job_name, stream_name=self.stream_name
        )
        limit_top = self.save_limit // 2
        limit_bottom = self.save_limit - limit_top
        while True:
            try:
                line = (await stream.readline()).decode("utf-8")
            except ValueError:
                logger.warning(
                    "job %s: ignored a very long line", self.job_name
                )
                continue
            if not line:
                return
            out_line = prefix + line
            try:
                sys.stdout.buffer.write(out_line.encode())
            except UnicodeEncodeError:
                out_line = out_line.encode("ascii", "replace").decode("ascii")
                sys.stdout.write(out_line)
            sys.stdout.flush()
            if self.save_limit > 0:
                if len(self.save_top) < limit_top:
                    self.save_top.append(line)
                else:
                    if len(self.save_bottom) == limit_bottom:
                        del self.save_bottom[0]
                        self.discarded_lines += 1
                    self.save_bottom.append(line)
            else:
                self.discarded_lines += 1

    async def join(self) -> Tuple[str, int]:
        await self._reader
        if self.save_bottom:
            middle = (
                [
                    "   [.... {} lines discarded ...]\n".format(
                        self.discarded_lines
                    )
                ]
                if self.discarded_lines
                else []
            )
            output = "".join(self.save_top + middle + self.save_bottom)
        else:
            output = "".join(self.save_top)
        return output, self.discarded_lines


class Reporter:
    async def report(
        self, success: bool, job: "RunningJob", config: Dict[str, Any]
    ) -> None:
        raise NotImplementedError  # pragma: no cover


class SentryReporter(Reporter):
    async def report(
        self, success: bool, job: "RunningJob", config: Dict[str, Any]
    ) -> None:
        config = config["sentry"]
        if config["dsn"]["value"]:
            dsn = config["dsn"]["value"]
        elif config["dsn"]["fromFile"]:
            with open(config["dsn"]["fromFile"], "rt") as dsn_file:
                dsn = dsn_file.read().strip()
        elif config["dsn"]["fromEnvVar"]:
            dsn = os.environ[config["dsn"]["fromEnvVar"]]
        else:
            return  # sentry disabled: early return

        template = jinja2.Template(config["body"])
        body = template.render(job.template_vars)

        fingerprint = []
        for line in config["fingerprint"]:
            fingerprint.append(jinja2.Template(line).render(job.template_vars))

        kwargs = {}
        if config.get("maxStringLength"):
            sentry_sdk.utils.MAX_STRING_LENGTH = (  # type:ignore
                config["maxStringLength"]
            )
        if config.get("environment"):
            kwargs["environment"] = config["environment"]
        sentry_sdk.init(dsn=dsn, **kwargs)
        extra = {
            "job": job.config.name,
            "exit_code": job.retcode,
            "command": job.config.command,
            "shell": job.config.shell,
            "success": success,
        }
        extra.update(config.get("extra", {}))
        logger.debug(
            "sentry: fingerprint=%r; extra=%r' body:\n%s",
            fingerprint,
            extra,
            body,
        )
        with sentry_sdk.push_scope() as scope:
            for key, val in extra.items():
                scope.set_extra(key, val)
            scope.fingerprint = fingerprint
            sentry_sdk.capture_message(
                body, level=config.get("level", "error")
            )


class MailReporter(Reporter):
    async def report(
        self, success: bool, job: "RunningJob", config: Dict[str, Any]
    ) -> None:
        mail = config["mail"]
        if not (mail["to"] and mail["from"]):
            return  # email reporting disabled
        smtp_host = mail["smtpHost"]
        smtp_port = mail["smtpPort"]

        password = None  # type: Optional[str]
        username = None  # type: Optional[str]

        if mail["password"]["value"]:
            password = mail["password"]["value"]
        elif mail["password"]["fromFile"]:
            with open(mail["password"]["fromFile"], "rt") as pass_file:
                password = pass_file.read().strip()
        elif mail["password"]["fromEnvVar"]:
            password = os.environ[mail["password"]["fromEnvVar"]]
        else:
            password = None
        username = mail.get("username")

        tmpl_vars = job.template_vars
        body_tmpl = jinja2.Template(mail["body"])
        body = body_tmpl.render(tmpl_vars)
        if success and not body.strip():
            logger.debug("body is empty, not sending email")
            return
        subject_tmpl = jinja2.Template(mail["subject"])
        subject = subject_tmpl.render(tmpl_vars)

        logger.debug("smtp: host=%r, port=%r", smtp_host, smtp_port)
        message = EmailMessage()
        message["From"] = mail["from"]
        message["To"] = mail["to"].strip()
        message["Subject"] = subject.strip()
        message["Date"] = datetime.now(timezone.utc).isoformat()
        if mail["html"]:
            message.set_payload(body)
            message.add_header("Content-Type", "text/html")
        else:
            message.set_content(body)
        smtp = aiosmtplib.SMTP(
            hostname=smtp_host,
            port=smtp_port,
            use_tls=mail["tls"],
            validate_certs=mail["validate_certs"],
        )
        await smtp.connect()
        if mail["starttls"]:
            await smtp.starttls()
        if username and password:
            await smtp.login(username=username, password=password)

        await smtp.send_message(message)


class ShellReporter(Reporter):
    async def report(
        self, success: bool, job: "RunningJob", config: Dict[str, Any]
    ) -> None:
        shell_config = config["shell"]

        if shell_config["command"] is None:
            return

        if isinstance(shell_config["command"], list):
            create = asyncio.create_subprocess_exec  # type: Any
            cmd = shell_config["command"]
        else:
            if shell_config["shell"]:
                create = asyncio.create_subprocess_exec
                cmd = [shell_config["shell"], "-c", shell_config["command"]]
            else:
                create = asyncio.create_subprocess_shell
                cmd = [shell_config["command"]]

        # pass the necessary information as env variables

        # We have to be a bit careful because job.stderr and job.stdout
        # can potentially be very large. On Linux there are limits
        # both on the individual as well as combined length of the arguments.
        std_err_str = job.stderr if job.stderr is not None else ""
        std_out_str = job.stdout if job.stdout is not None else ""
        # this is an arbitrary safe lower limit
        max_length_arg = 1024 * 16
        args_too_long = (
            len(std_err_str) > max_length_arg
            or len(std_out_str) > max_length_arg
            or len(std_err_str) + len(std_out_str) > max_length_arg
        )
        std_err_str_safe = (
            std_err_str if not args_too_long else std_err_str[:max_length_arg]
        )
        std_out_str_safe = (
            std_out_str if not args_too_long else std_out_str[:max_length_arg]
        )

        env = {
            **os.environ,
            "YACRON_FAIL_REASON": (
                job.fail_reason if job.fail_reason is not None else ""
            ),
            "YACRON_JOB_NAME": job.config.name,
            "YACRON_JOB_COMMAND": (
                job.config.command
                if not isinstance(job.config.command, list)
                else " ".join(job.config.command)
            ),
            "YACRON_JOB_SCHEDULE": job.config.schedule_unparsed,
            "YACRON_FAILED": "1" if job.failed else "0",
            "YACRON_RETCODE": str(job.retcode),
            "YACRON_STDERR": std_err_str_safe,
            "YACRON_STDOUT": std_out_str_safe,
            "YACRON_STDERR_TRUNCATED": (
                "1" if len(std_err_str_safe) != len(std_err_str) else "0"
            ),
            "YACRON_STDOUT_TRUNCATED": (
                "1" if len(std_out_str_safe) != len(std_out_str) else "0"
            ),
        }

        logger.debug("Executing shell report cmd: %s", cmd)
        try:
            proc = await create(*cmd, env=env)
        except subprocess.SubprocessError:
            logger.exception(
                "Error executing shell reporter of job %s", job.config.name
            )
            return

        retcode = await proc.wait()
        if retcode != 0:
            logger.exception(
                "Error executing shell reporter of job %s with return code %s",
                job.config.name,
                retcode,
            )


class JobRetryState:
    def __init__(
        self, initial_delay: float, multiplier: float, max_delay: float
    ) -> None:
        self.multiplier = multiplier
        self.max_delay = max_delay
        self.delay = initial_delay
        self.count = 0  # number of times retried
        self.task = None  # type: Optional[asyncio.Task]
        self.cancelled = False

    def next_delay(self) -> float:
        delay = self.delay
        self.delay = min(delay * self.multiplier, self.max_delay)
        self.count += 1
        return delay


class RunningJob:
    REPORTERS = [
        SentryReporter(),
        MailReporter(),
        ShellReporter(),
    ]  # type: List[Reporter]

    def __init__(
        self, config: JobConfig, retry_state: Optional[JobRetryState]
    ) -> None:
        self.config = config
        self.proc = None  # type: Optional[asyncio.subprocess.Process]
        self.retcode = None  # type: Optional[int]
        self._stderr_reader = None  # type: Optional[StreamReader]
        self._stdout_reader = None  # type: Optional[StreamReader]
        self.stderr = None  # type: Optional[str]
        self.stdout = None  # type: Optional[str]
        self.stderr_discarded = 0
        self.stdout_discarded = 0
        self.execution_deadline = None  # type: Optional[float]
        self.retry_state = retry_state
        self.env = None  # type: Optional[Dict[str, str]]

        statsd_config = self.config.statsd
        if statsd_config is not None:
            self.statsd_writer = StatsdJobMetricWriter(
                host=statsd_config["host"],
                port=statsd_config["port"],
                prefix=statsd_config["prefix"],
                job=self,
            )  # type: Optional[StatsdJobMetricWriter]
        else:
            self.statsd_writer = None

    async def _on_start(self) -> None:
        if self.statsd_writer:
            await self.statsd_writer.job_started()

    async def _on_stop(self) -> None:
        if self.statsd_writer:
            await self.statsd_writer.job_stopped()

    async def start(self) -> None:
        if self.proc is not None:
            raise RuntimeError("process already running")
        kwargs = {}  # type: Dict[str, Any]
        if isinstance(self.config.command, list):
            create = asyncio.create_subprocess_exec  # type: Any
            cmd = self.config.command
        else:
            if self.config.shell:
                create = asyncio.create_subprocess_exec
                cmd = [self.config.shell, "-c", self.config.command]
            else:
                create = asyncio.create_subprocess_shell
                cmd = [self.config.command]
        if self.config.environment:
            env = dict(os.environ)
            fixup_pyinstaller_env(env)
            for envvar in self.config.environment:
                env[envvar["key"]] = envvar["value"]
                self.env = env
            kwargs["env"] = env
        if self.config.uid is not None or self.config.gid is not None:
            kwargs["preexec_fn"] = self._demote
        logger.debug("%s: will execute argv %r", self.config.name, cmd)
        if self.config.captureStderr:
            kwargs["stderr"] = asyncio.subprocess.PIPE
        if self.config.captureStdout:
            kwargs["stdout"] = asyncio.subprocess.PIPE
        if self.config.executionTimeout:
            self.execution_deadline = (
                time.perf_counter() + self.config.executionTimeout
            )
        if self.config.captureStderr or self.config.captureStdout:
            kwargs["limit"] = self.config.maxLineLength

        try:
            args = [c.encode() for c in cmd]
            logger.debug("subprocess: args=%r, kwargs=%r", args, kwargs)
            self.proc = await create(*args, **kwargs)
        except (
            subprocess.SubprocessError,
            UnicodeEncodeError,
            FileNotFoundError,
        ):
            logger.exception(
                "Error launching subprocess of job %s, cmd=%r, kwargs=%s "
                "(system encoding: %s)",
                self.config.name,
                cmd,
                kwargs,
                sys.getdefaultencoding(),
            )
            return

        await self._on_start()

        if self.config.captureStderr:
            assert self.proc.stderr is not None
            self._stderr_reader = StreamReader(
                self.config.name,
                "stderr",
                self.proc.stderr,
                self.config.streamPrefix,
                self.config.saveLimit,
            )
        if self.config.captureStdout:
            assert self.proc.stdout is not None
            self._stdout_reader = StreamReader(
                self.config.name,
                "stdout",
                self.proc.stdout,
                self.config.streamPrefix,
                self.config.saveLimit,
            )

    def _demote(self):
        if self.config.gid is not None:
            logger.debug("Changing to gid %r ...", self.config.gid)
            try:
                os.setgid(self.config.gid)
            except OSError as ex:
                raise RuntimeError("setgid: {}".format(ex)) from ex
        if self.config.uid is not None:
            logger.debug("Changing to uid %r ...", self.config.uid)
            try:
                os.setuid(self.config.uid)
            except OSError as ex:
                raise RuntimeError("setuid: {}".format(ex)) from ex

    async def wait(self) -> None:
        if self.proc is None:
            raise RuntimeError("process is not running")
        if self.execution_deadline is None:
            self.retcode = await self.proc.wait()
            await self._on_stop()
        else:
            timeout = self.execution_deadline - time.perf_counter()
            try:
                if timeout > 0:
                    self.retcode = await asyncio.wait_for(
                        self.proc.wait(), timeout
                    )
                    await self._on_stop()
                else:
                    raise asyncio.TimeoutError
            except asyncio.TimeoutError:
                logger.info(
                    "Job %s exceeded its executionTimeout of "
                    "%.1f seconds, cancelling it...",
                    self.config.name,
                    self.config.executionTimeout,
                )
                self.retcode = -100
                await self.cancel()
        await self._read_job_streams()

    async def _read_job_streams(self):
        if self._stderr_reader:
            (
                self.stderr,
                self.stderr_discarded,
            ) = await self._stderr_reader.join()
        if self._stdout_reader:
            (
                self.stdout,
                self.stdout_discarded,
            ) = await self._stdout_reader.join()

    @property
    def failed(self) -> bool:
        return self.fail_reason is not None

    @property
    def fail_reason(self) -> Optional[str]:
        if self.config.failsWhen["always"]:
            return "failsWhen=always"
        if self.config.failsWhen["nonzeroReturn"] and self.retcode != 0:
            return "failsWhen=nonzeroReturn and retcode={}".format(
                self.retcode
            )
        if self.config.failsWhen["producesStdout"] and (
            self.stdout or self.stdout_discarded
        ):
            return "failsWhen=producesStdout and stdout is not empty"
        if self.config.failsWhen["producesStderr"] and (
            self.stderr or self.stderr_discarded
        ):
            return "failsWhen=producesStderr and stderr is not empty"
        return None

    async def cancel(self) -> None:
        if self.proc is None:
            raise RuntimeError("process is not running")
        if self.proc.returncode is None:
            try:
                self.proc.terminate()
            except ProcessLookupError:
                pass
        try:
            await asyncio.wait_for(self.proc.wait(), self.config.killTimeout)
        except asyncio.TimeoutError:
            logger.warning(
                "Job %s did not gracefully terminate after "
                "%.1f seconds, killing it...",
                self.config.name,
                self.config.killTimeout,
            )
            self.proc.kill()
        await self._on_stop()

    async def report_failure(self):
        logger.info("Cron job %s: reporting failure", self.config.name)
        await self._report_common(self.config.onFailure["report"], False)

    async def report_permanent_failure(self):
        logger.info(
            "Cron job %s: reporting permanent failure", self.config.name
        )
        await self._report_common(
            self.config.onPermanentFailure["report"], False
        )

    async def report_success(self):
        logger.info("Cron job %s: reporting success", self.config.name)
        await self._report_common(self.config.onSuccess["report"], True)

    async def _report_common(self, report_config: dict, success: bool) -> None:
        results = await asyncio.gather(
            *[
                reporter.report(success, self, report_config)
                for reporter in self.REPORTERS
            ],
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, Exception):
                logger.error(
                    "Problem reporting job %s failure: %s",
                    self.config.name,
                    result,
                    exc_info=result,
                )

    @property
    def template_vars(self) -> dict:
        fail_reason = self.fail_reason
        return {
            "name": self.config.name,
            "success": fail_reason is None,
            "fail_reason": fail_reason,
            "stdout": self.stdout,
            "stderr": self.stderr,
            "exit_code": self.retcode,
            "command": self.config.command,
            "shell": self.config.shell,
            "environment": self.env,
        }
