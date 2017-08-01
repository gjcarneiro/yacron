import yacron.job
import yacron.config
import asyncio
import pytest
import aiosmtplib
from unittest.mock import Mock, patch


@pytest.mark.parametrize("save_limit, output", [
    (10, 'line1\nline2\nline3\nline4\n'),
    (1, '   [.... 3 lines discarded ...]\nline4\n'),
    (2, 'line1\n   [.... 2 lines discarded ...]\nline4\n'),
])
def test_stream_reader(save_limit, output):
    loop = asyncio.get_event_loop()
    fake_stream = asyncio.StreamReader()
    reader = yacron.job.StreamReader("cronjob-1", "stderr", fake_stream,
                                     save_limit)

    async def producer(fake_stream):
        fake_stream.feed_data(b"line1\nline2\nline3\nline4\n")
        fake_stream.feed_eof()

    _, out = loop.run_until_complete(asyncio.gather(
        producer(fake_stream),
        reader.join()))

    assert out == output


A_JOB = '''
jobs:
  - name: test
    command: |
      echo "foobar"
      echo "error" 1>&2
    schedule: "* * * * *"
    onSuccess:
      report:
        mail:
          from: example@foo.com
          to: example@bar.com
          smtpHost: smtp1
          smtpPort: 1025
'''


@pytest.mark.parametrize("report_type, stdout, stderr, subject, body", [
    (yacron.job.ReportType.SUCCESS, "out", "err",
     "Cron job 'test' completed",
     'STDOUT:\n---\nout\n---\nSTDERR:\nerr'),

    (yacron.job.ReportType.FAILURE, "out", "err",
     "Cron job 'test' failed",
     'STDOUT:\n---\nout\n---\nSTDERR:\nerr'),

    (yacron.job.ReportType.FAILURE, None, None,
     "Cron job 'test' failed",
     "(no output was captured)"),

    (yacron.job.ReportType.FAILURE, None, "err",
     "Cron job 'test' failed",
     'err'),

    (yacron.job.ReportType.FAILURE, "out", None,
     "Cron job 'test' failed",
     'out'),
])
def test_report_mail(report_type, stdout, stderr, subject, body):
    job_config = yacron.config.parse_config_string(A_JOB)[0]
    job = Mock(config=job_config, stdout=stdout, stderr=stderr)
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

    with patch("aiosmtplib.SMTP.__init__", init), \
         patch("aiosmtplib.SMTP.connect", connect), \
         patch("aiosmtplib.SMTP.send_message", send_message):
        loop.run_until_complete(mail.report(report_type,
                                            job,
                                            job_config.onSuccess['report']))

    assert smtp_init_args == ((), {'hostname': 'smtp1', 'port': 1025})
    assert len(connect_calls) == 1
    assert len(messages_sent) == 1
    message = messages_sent[0]
    assert message['From'] == "example@foo.com"
    assert message['To'] == "example@bar.com"
    assert message['Subject'] == subject
    assert message.get_payload() == body
