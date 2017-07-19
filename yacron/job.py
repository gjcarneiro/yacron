import sys
import os
import logging
import asyncio
import asyncio.subprocess
from email.mime.text import MIMEText

from raven import Client
from raven_aiohttp import AioHttpTransport
import aiosmtplib

from yacron.config import JobConfig


logger = logging.getLogger('yacron')


class StreamReader:

    def __init__(self, job, stream_name, stream):
        self.save_top = []
        self.save_bottom = []
        self.job = job
        self.stream_name = stream_name
        self._reader = asyncio.Task(self._read(stream))
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

    async def join(self):
        await self._reader
        if self.save_bottom:
            middle = (["   [.... {} lines discarded ...]\n"
                       .format(self.discarded_lines)]
                      if self.discarded_lines else [])
            return ''.join(self.save_top + middle + self.save_bottom)
        else:
            return ''.join(self.save_top)


class RunningJob:

    def __init__(self, config: JobConfig) -> None:
        self.config = config
        self.proc = None
        self.retcode = None
        self._stderr_reader = None
        self._stdout_reader = None
        self.stderr = None
        self.stdout = None

    async def start(self) -> None:
        kwargs = {}
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

    async def wait(self) -> True:
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
        self.proc.terminate()
        # TODO: check that it exits after a while, if not send it SIGKILL

    async def report_failure(self):
        logger.info("Cron job %s: reporting failure", self.config.name)
        await self._report_common(self.config.onFailure['report'])

    async def report_permanent_failure(self):
        logger.info("Cron job %s: reporting permanent failure",
                    self.config.name)
        await self._report_common(self.config.onPermanentFailure['report'])

    async def report_success(self):
        logger.info("Cron job %s: reporting success", self.config.name)
        await self._report_common(self.config.success['report'])

    async def _report_common(self, report_config):
        results = await asyncio.gather(
            self._report_sentry(report_config['sentry']),
            self._report_mail(report_config['mail']),
            return_exceptions=True
        )
        for result in results:
            if isinstance(result, Exception):
                logger.error("Problem reporting job %s failure: %s",
                             self.config.name, result)

    async def _report_sentry(self, config):
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
        client = Client(transport=AioHttpTransport,
                        dsn=dsn,
                        string_max_length=4096)
        extra = {
            'job': self.config.name,
            'exit_code': self.retcode,
            'command': self.config.command,
            'shell': self.config.shell,
        }
        logger.debug("sentry body: %r", body)
        client.captureMessage(
            body,
            extra=extra,
        )

    async def _report_mail(self, mail):
        if not (mail['smtp_host'] and mail['to'] and mail['from']):
            return  # email reporting disabled
        if self.stdout and self.stderr:
            body = ("STDOUT:\n---\n{}\n---\nSTDERR:\n{}"
                    .format(self.stdout, self.stderr))
        else:
            body = self.stdout or self.stderr or '(no output was captured)'
        logger.debug("smtp: host=%r, port=%r",
                     mail['smtp_host'], mail['smtp_port'])
        smtp = aiosmtplib.SMTP(hostname=mail['smtp_host'],
                               port=mail['smtp_port'])
        await smtp.connect()
        message = MIMEText(body)
        message['From'] = mail['from']
        message['To'] = mail['from']
        message['Subject'] = 'Cron job {!r} failed'.format(self.config.name)
        await smtp.send_message(message)
