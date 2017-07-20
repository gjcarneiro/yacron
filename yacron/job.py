import asyncio
import asyncio.subprocess
import logging
import os
import sys
from email.mime.text import MIMEText
from typing import Any, Awaitable, Dict, List, Optional  # noqa
from enum import Enum

from raven import Client
from raven_aiohttp import AioHttpTransport

import aiosmtplib
from yacron.config import JobConfig

logger = logging.getLogger('yacron')


def create_task(coro: Awaitable) -> asyncio.Task:
    return asyncio.get_event_loop().create_task(coro)


class ReportType(Enum):
    FAILURE = 1
    SUCCESS = 2


class StreamReader:

    def __init__(self, job, stream_name, stream):
        self.save_top = []
        self.save_bottom = []
        self.job = job
        self.stream_name = stream_name
        self._reader = create_task(self._read(stream))
        self.discarded_lines = 0

    async def _read(self, stream):
        prefix = "[{} {}] ".format(self.job.config.name, self.stream_name)
        limit = self.job.config.saveLimit // 2
        while True:
            line = (await stream.readline()).decode("utf-8")
            if not line:
                return
            sys.stdout.write(prefix + line)
            sys.stdout.flush()
            if len(self.save_top) < limit:
                self.save_top.append(line)
            else:
                if len(self.save_bottom) == limit:
                    del self.save_bottom[0]
                    self.discarded_lines += 1
                self.save_bottom.append(line)

    async def join(self) -> str:
        await self._reader
        if self.save_bottom:
            middle = (["   [.... {} lines discarded ...]\n"
                       .format(self.discarded_lines)]
                      if self.discarded_lines else [])
            output = ''.join(self.save_top + middle + self.save_bottom)
        else:
            output = ''.join(self.save_top)
        return output


class RunningJob:

    def __init__(self, config: JobConfig) -> None:
        self.config = config
        self.proc = None  # type: Optional[asyncio.subprocess.Process]
        self.retcode = None  # type: Optional[int]
        self._stderr_reader = None  # type: Optional[StreamReader]
        self._stdout_reader = None  # type: Optional[StreamReader]
        self.stderr = None  # type: Optional[str]
        self.stdout = None  # type: Optional[str]

    async def start(self) -> None:
        kwargs = {}  # type: Dict[str, Any]
        if isinstance(self.config.command, list):
            create = asyncio.create_subprocess_exec
            cmd = self.config.command
        else:
            if self.config.shell:
                create = asyncio.create_subprocess_exec
                cmd = [self.config.shell, '-c', self.config.command]
            else:
                create = asyncio.create_subprocess_shell
                cmd = [self.config.command]
        if self.config.environment:
            env = dict(os.environ)
            for envvar in self.config.environment:
                env[envvar['key']] = envvar['value']
            kwargs['env'] = env
        logger.debug("%s: will execute argv %r", self.config.name, cmd)
        if self.config.captureStderr:
            kwargs['stderr'] = asyncio.subprocess.PIPE
        if self.config.captureStdout:
            kwargs['stdout'] = asyncio.subprocess.PIPE
        self.proc = await create(*cmd, **kwargs)
        if self.config.captureStderr:
            self._stderr_reader = \
                StreamReader(self, 'stderr', self.proc.stderr)
        if self.config.captureStdout:
            self._stdout_reader = \
                StreamReader(self, 'stdout', self.proc.stdout)

    async def wait(self) -> None:
        if self.proc is None:
            raise RuntimeError("process is not running")
        self.retcode = await self.proc.wait()
        if self._stderr_reader:
            self.stderr = await self._stderr_reader.join()
        if self._stdout_reader:
            self.stdout = await self._stdout_reader.join()

    @property
    def failed(self) -> bool:
        if self.config.failsWhen['nonzeroReturn'] and self.retcode != 0:
            return True
        if self.config.failsWhen['producesStdout'] and self.stdout:
            return True
        if self.config.failsWhen['producesStderr'] and self.stderr:
            return True
        return False

    async def cancel(self) -> None:
        if self.proc is None:
            raise RuntimeError("process is not running")
        self.proc.terminate()
        # TODO: check that it exits after a while, if not send it SIGKILL

    async def report_failure(self):
        logger.info("Cron job %s: reporting failure", self.config.name)
        await self._report_common(self.config.onFailure['report'],
                                  ReportType.FAILURE)

    async def report_permanent_failure(self):
        logger.info("Cron job %s: reporting permanent failure",
                    self.config.name)
        await self._report_common(self.config.onPermanentFailure['report'],
                                  ReportType.FAILURE)

    async def report_success(self):
        logger.info("Cron job %s: reporting success", self.config.name)
        await self._report_common(self.config.onSuccess['report'],
                                  ReportType.SUCCESS)

    async def _report_common(self, report_config: dict,
                             report_type: ReportType) -> None:
        results = await asyncio.gather(
            self._report_sentry(report_config['sentry'], report_type),
            self._report_mail(report_config['mail'], report_type),
            return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.error("Problem reporting job %s failure: %s",
                             self.config.name, result)

    async def _report_sentry(self, config, report_type: ReportType):
        if config['dsn']['value']:
            dsn = config['dsn']['value']
        elif config['dsn']['fromFile']:
            with open(config['dsn']['fromFile'], "rt") as dsn_file:
                dsn = dsn_file.read().strip()
        elif config['dsn']['fromEnvVar']:
            dsn = os.environ[config['dsn']['fromEnvVar']]
        else:
            return  # sentry disabled: early return
        if self.stdout and self.stderr:
            body = ("STDOUT:\n---\n{}\n---\nSTDERR:\n{}"
                    .format(self.stdout, self.stderr))
        else:
            body = self.stdout or self.stderr or '(no output was captured)'

        if report_type == ReportType.SUCCESS:
            headline = ('Cron job {!r} completed'
                        .format(self.config.name))
        elif report_type == ReportType.FAILURE:
            headline = ('Cron job {!r} failed'
                        .format(self.config.name))
        body = "{}\n\n{}".format(headline, body)

        client = Client(transport=AioHttpTransport,
                        dsn=dsn,
                        string_max_length=4096)
        extra = {
            'job': self.config.name,
            'exit_code': self.retcode,
            'command': self.config.command,
            'shell': self.config.shell,
            'success': True if report_type == ReportType.SUCCESS else False,
        }
        logger.debug("sentry body: %r", body)
        client.captureMessage(
            body,
            extra=extra,
        )

    async def _report_mail(self, mail, report_type: ReportType):
        if not ((mail['smtpHost'] or mail['smtp_host']) and
                mail['to'] and mail['from']):
            return  # email reporting disabled

        if self.stdout and self.stderr:
            body = ("STDOUT:\n---\n{}\n---\nSTDERR:\n{}"
                    .format(self.stdout, self.stderr))
        else:
            body = self.stdout or self.stderr or '(no output was captured)'

        if mail['smtpHost']:
            smtp_host = mail['smtpHost']
        else:
            logger.warning("smtp_host is deprecated, was renamed to smtpHost")
            smtp_host = mail['smtp_host']
        if mail['smtpPort']:
            smtp_port = mail['smtpPort']
        else:
            logger.warning("smtp_port is deprecated, was renamed to smtpPort")
            smtp_port = mail['smtp_port']

        logger.debug("smtp: host=%r, port=%r", smtp_host, smtp_port)
        smtp = aiosmtplib.SMTP(hostname=smtp_host, port=smtp_port)
        await smtp.connect()
        message = MIMEText(body)
        message['From'] = mail['from']
        message['To'] = mail['to']
        if report_type == ReportType.SUCCESS:
            message['Subject'] = ('Cron job {!r} completed'
                                  .format(self.config.name))
        elif report_type == ReportType.FAILURE:
            message['Subject'] = ('Cron job {!r} failed'
                                  .format(self.config.name))
        else:
            raise AssertionError
        await smtp.send_message(message)
