import yacron.job
import yacron.config
import asyncio
import pytest
import aiosmtplib
import sentry_sdk
from unittest.mock import Mock, patch


@pytest.mark.parametrize(
    "save_limit, input_lines, output, expected_failure",
    [
        (
            10,
            b"line1\nline2\nline3\nline4\n",
            "line1\nline2\nline3\nline4\n",
            True,
        ),
        (
            1,
            b"line1\nline2\nline3\nline4\n",
            "   [.... 3 lines discarded ...]\nline4\n",
            True,
        ),
        (
            2,
            b"line1\nline2\nline3\nline4\n",
            "line1\n   [.... 2 lines discarded ...]\nline4\n",
            True,
        ),
        (0, b"line1\nline2\nline3\nline4\n", "", True),
        (0, b"", "", False),
    ],
)
def test_stream_reader(save_limit, input_lines, output, expected_failure):
    loop = asyncio.get_event_loop()
    fake_stream = asyncio.StreamReader()
    reader = yacron.job.StreamReader(
        "cronjob-1", "stderr", fake_stream, save_limit
    )

    config, _ = yacron.config.parse_config_string(
        """
jobs:
  - name: test
    command: foo
    schedule: "* * * * *"
    captureStderr: true
"""
    )
    job_config = config[0]
    job = yacron.job.RunningJob(job_config, None)

    async def producer(fake_stream):
        fake_stream.feed_data(input_lines)
        fake_stream.feed_eof()

    job._stderr_reader = reader
    job.retcode = 0
    loop.run_until_complete(
        asyncio.gather(producer(fake_stream), job._read_job_streams())
    )
    out = job.stderr

    assert (out, job.failed) == (output, expected_failure)


A_JOB = """
jobs:
  - name: test
    command: ls
    schedule: "* * * * *"
    onSuccess:
      report:
        mail:
          from: example@foo.com
          to: example@bar.com
          smtpHost: smtp1
          smtpPort: 1025
          subject: "Cron job '{{name}}' {% if success %}completed{% else %}failed{% endif %}"
          body: |
            {% if stdout and stderr -%}
            STDOUT:
            ---
            {{stdout}}
            ---
            STDERR:
            {{stderr}}
            {% elif stdout -%}
            {{stdout}}
            {% elif stderr -%}
            {{stderr}}
            {% else -%}
            (no output was captured)
            {% endif %}
"""


@pytest.mark.parametrize(
    "success, stdout, stderr, subject, body",
    [
        (
            True,
            "out",
            "err",
            "Cron job 'test' completed",
            "STDOUT:\n---\nout\n---\nSTDERR:\nerr\n",
        ),
        (
            False,
            "out",
            "err",
            "Cron job 'test' failed",
            "STDOUT:\n---\nout\n---\nSTDERR:\nerr\n",
        ),
        (
            False,
            None,
            None,
            "Cron job 'test' failed",
            "(no output was captured)\n",
        ),
        (False, None, "err", "Cron job 'test' failed", "err\n"),
        (False, "out", None, "Cron job 'test' failed", "out\n"),
    ],
)
def test_report_mail(success, stdout, stderr, subject, body):
    config, _ = yacron.config.parse_config_string(A_JOB)
    job_config = config[0]
    print(job_config.onSuccess["report"])
    job = Mock(
        config=job_config,
        stdout=stdout,
        stderr=stderr,
        template_vars={
            "name": job_config.name,
            "success": success,
            "stdout": stdout,
            "stderr": stderr,
        },
    )

    mail = yacron.job.MailReporter()
    loop = asyncio.get_event_loop()

    connect_calls = []
    messages_sent = []

    async def connect(self):
        connect_calls.append(self)

    async def send_message(self, message):
        messages_sent.append(message)

    real_init = aiosmtplib.SMTP.__init__
    smtp_init_args = None

    def init(self, *args, **kwargs):
        nonlocal smtp_init_args
        smtp_init_args = args, kwargs
        real_init(self, *args, **kwargs)

    with patch("aiosmtplib.SMTP.__init__", init), patch(
        "aiosmtplib.SMTP.connect", connect
    ), patch("aiosmtplib.SMTP.send_message", send_message):
        loop.run_until_complete(
            mail.report(success, job, job_config.onSuccess["report"])
        )

    assert smtp_init_args == ((), {"hostname": "smtp1", "port": 1025})
    assert len(connect_calls) == 1
    assert len(messages_sent) == 1
    message = messages_sent[0]
    assert message["From"] == "example@foo.com"
    assert message["To"] == "example@bar.com"
    assert message["Subject"] == subject
    assert message.get_payload() == body


@pytest.mark.parametrize(
    "success, dsn_from, body, extra, expected_dsn, fingerprint, "
    "level_in, level_out",
    [
        (
            True,
            "value",
            "Cron job 'test' completed\n\nSTDOUT:\n---\nout\n---\nSTDERR:\nerr\n",
            {
                "job": "test",
                "exit_code": 0,
                "command": "ls",
                "shell": "/bin/sh",
                "success": True,
            },
            "http://xxx:yyy@sentry/1",
            ["test"],
            "warning",
            "warning",
        ),
        (
            False,
            "file",
            "Cron job 'test' failed\n\nSTDOUT:\n---\nout\n---\nSTDERR:\nerr\n",
            {
                "job": "test",
                "exit_code": 0,
                "command": "ls",
                "shell": "/bin/sh",
                "success": False,
            },
            "http://xxx:yyy@sentry/2",
            ["test"],
            None,
            "error",
        ),
        (
            False,
            "envvar",
            "Cron job 'test' failed\n\nSTDOUT:\n---\nout\n---\nSTDERR:\nerr\n",
            {
                "job": "test",
                "exit_code": 0,
                "command": "ls",
                "shell": "/bin/sh",
                "success": False,
            },
            "http://xxx:yyy@sentry/3",
            ["test"],
            None,
            "error",
        ),
    ],
)
def test_report_sentry(
    success,
    dsn_from,
    body,
    extra,
    expected_dsn,
    fingerprint,
    level_in,
    level_out,
    tmpdir,
    monkeypatch,
):
    config, _ = yacron.config.parse_config_string(A_JOB)
    job_config = config[0]

    p = tmpdir.join("sentry-secret-dsn")
    p.write("http://xxx:yyy@sentry/2")

    monkeypatch.setenv("TEST_SENTRY_DSN", "http://xxx:yyy@sentry/3")

    if dsn_from == "value":
        job_config.onSuccess["report"]["sentry"] = {
            "dsn": {
                "value": "http://xxx:yyy@sentry/1",
                "fromFile": None,
                "fromEnvVar": None,
            }
        }
    elif dsn_from == "file":
        job_config.onSuccess["report"]["sentry"] = {
            "dsn": {"value": None, "fromFile": str(p), "fromEnvVar": None}
        }
    elif dsn_from == "envvar":
        job_config.onSuccess["report"]["sentry"] = {
            "dsn": {
                "value": None,
                "fromFile": None,
                "fromEnvVar": "TEST_SENTRY_DSN",
            }
        }
    else:
        raise AssertionError

    job_config.onSuccess["report"]["sentry"][
        "body"
    ] = yacron.config.DEFAULT_CONFIG["onFailure"]["report"]["sentry"]["body"]

    job_config.onSuccess["report"]["sentry"]["fingerprint"] = ["{{ name }}"]

    if level_in is not None:
        job_config.onSuccess["report"]["sentry"]["level"] = level_in

    job = Mock(
        config=job_config,
        stdout="out",
        stderr="err",
        retcode=0,
        template_vars={
            "name": job_config.name,
            "success": success,
            "stdout": "out",
            "stderr": "err",
        },
    )
    loop = asyncio.get_event_loop()

    transports = []

    class FakeSentryTransport:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs
            self.messages_sent = []

        def capture_event(self, event_opt):
            self.messages_sent.append(event_opt)

        def kill(self):
            pass

        def flush(self, *args, **kwargs):
            pass

    def make_transport(*args, **kwargs):
        transport = FakeSentryTransport(*args, **kwargs)
        transports.append(transport)
        return transport

    monkeypatch.setattr("sentry_sdk.client.make_transport", make_transport)

    sentry = yacron.job.SentryReporter()
    loop.run_until_complete(
        sentry.report(success, job, job_config.onSuccess["report"])
    )
    for transport in transports:
        assert transport.args[0].get("dsn") == expected_dsn

    messages_sent = [
        msg for transport in transports for msg in transport.messages_sent
    ]

    assert len(messages_sent) == 1
    msg = messages_sent[0]
    msg1 = {
        key: msg[key] for key in {"message", "level", "fingerprint", "extra"}
    }
    msg1["extra"].pop("sys.argv", "")

    assert msg1 == {
        "message": body,
        "level": level_out,
        "fingerprint": fingerprint,
        "extra": extra,
    }


@pytest.mark.parametrize(
    "shell, command, expected_type, expected_args",
    [
        ("", "Civ 6", "shell", ("Civ 6",)),
        ("", ["echo", "hello"], "exec", ("echo", "hello")),
        ("bash", 'echo "hello"', "exec", ("bash", "-c", 'echo "hello"')),
    ],
)
def test_job_run(monkeypatch, shell, command, expected_type, expected_args):

    shell_commands = []
    exec_commands = []

    async def create_subprocess_common(*args, **kwargs):
        stdout = asyncio.StreamReader()
        stderr = asyncio.StreamReader()
        stdout.feed_data(b"out\n")
        stdout.feed_eof()
        stderr.feed_data(b"err\n")
        stderr.feed_eof()
        proc = Mock(stdout=stdout, stderr=stderr)

        async def wait():
            return

        proc.wait = wait
        return proc

    async def create_subprocess_shell(*args, **kwargs):
        shell_commands.append((args, kwargs))
        return await create_subprocess_common(*args, **kwargs)

    async def create_subprocess_exec(*args, **kwargs):
        exec_commands.append((args, kwargs))
        return await create_subprocess_common(*args, **kwargs)

    monkeypatch.setattr(
        "asyncio.create_subprocess_exec", create_subprocess_exec
    )
    monkeypatch.setattr(
        "asyncio.create_subprocess_shell", create_subprocess_shell
    )

    if isinstance(command, list):
        command_snippet = "\n".join(
            ["    command:"] + ["      - " + arg for arg in command]
        )
    else:
        command_snippet = "    command: " + command

    config, _ = yacron.config.parse_config_string(
        """
jobs:
  - name: test
{command}
    schedule: "* * * * *"
    shell: {shell}
    captureStderr: true
    captureStdout: true
    environment:
      - key: FOO
        value: bar
""".format(
            command=command_snippet, shell=shell
        )
    )
    job_config = config[0]

    job = yacron.job.RunningJob(job_config, None)

    async def run(job):
        await job.start()
        await job.wait()

    loop = asyncio.get_event_loop()
    loop.run_until_complete(run(job))

    if shell_commands:
        run_type = "shell"
        assert len(shell_commands) == 1
        args, kwargs = shell_commands[0]
    elif exec_commands:
        run_type = "exec"
        assert len(exec_commands) == 1
        args, kwargs = exec_commands[0]
    else:
        raise AssertionError

    assert kwargs["env"]["FOO"] == "bar"
    assert run_type == expected_type
    assert args == expected_args


def test_execution_timeout():
    config, _ = yacron.config.parse_config_string(
        """
jobs:
  - name: test
    command: |
        echo "hello"
        sleep 1
        echo "world"
    executionTimeout: 0.25
    schedule: "* * * * *"
    captureStderr: false
    captureStdout: true
"""
    )
    job_config = config[0]

    async def test(job):
        await job.start()
        await job.wait()
        return job.stdout

    job = yacron.job.RunningJob(job_config, None)
    loop = asyncio.get_event_loop()
    stdout = loop.run_until_complete(test(job))
    assert stdout == "hello\n"


def test_error1():
    config, _ = yacron.config.parse_config_string(
        """
jobs:
  - name: test
    command: echo "hello"
    schedule: "* * * * *"
"""
    )
    job_config = config[0]
    job = yacron.job.RunningJob(job_config, None)

    loop = asyncio.get_event_loop()
    loop.run_until_complete(job.start())
    with pytest.raises(RuntimeError):
        loop.run_until_complete(job.start())


def test_error2():
    config, _ = yacron.config.parse_config_string(
        """
jobs:
  - name: test
    command: echo "hello"
    schedule: "* * * * *"
"""
    )
    job_config = config[0]
    job = yacron.job.RunningJob(job_config, None)

    loop = asyncio.get_event_loop()
    with pytest.raises(RuntimeError):
        loop.run_until_complete(job.wait())


def test_error3():
    config, _ = yacron.config.parse_config_string(
        """
jobs:
  - name: test
    command: echo "hello"
    schedule: "* * * * *"
"""
    )
    job_config = config[0]
    job = yacron.job.RunningJob(job_config, None)

    loop = asyncio.get_event_loop()
    with pytest.raises(RuntimeError):
        loop.run_until_complete(job.cancel())


@pytest.mark.parametrize("command", ['echo "hello"', "exit 1"])
def test_statsd(command):
    loop = asyncio.get_event_loop()
    received = []

    async def run():
        class UDPServerProtocol:
            def connection_made(self, transport):
                self.transport = transport

            def datagram_received(self, data, addr):
                print("Statsd UDP packet received:", data)
                message = data.decode()
                received.extend(m for m in message.split("\n") if m)

            def connection_lost(*_):
                pass

        listen = loop.create_datagram_endpoint(
            UDPServerProtocol, local_addr=("127.0.0.1", 0)
        )
        transport, protocol = await listen

        host, port = transport.get_extra_info("sockname")
        print("Listening UDP on %s:%s" % (host, port))

        config, _ = yacron.config.parse_config_string(
            """
jobs:
  - name: test
    command: {command}
    schedule: "* * * * *"
    statsd:
      host: 127.0.0.1
      port: {port}
      prefix: the.prefix
""".format(
                port=port, command=command
            )
        )
        job_config = config[0]

        job = yacron.job.RunningJob(job_config, None)

        await job.start()
        await job.wait()
        await asyncio.sleep(0.05)
        transport.close()
        await asyncio.sleep(0.05)
        return job

    job = loop.run_until_complete(run())

    assert received
    assert len(received) == 4
    assert "the.prefix.start" in received[0]
    assert any("the.prefix.stop" in r for r in received[1:])
    success = 0 if job.failed else 1
    assert any("the.prefix.success:%i" % success in r for r in received[1:])
    assert any("the.prefix.duration" in r for r in received[1:])
