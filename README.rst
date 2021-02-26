================
Yet Another Cron
================


A modern Cron replacement that is Docker-friendly


* Free software: MIT license

.. image:: https://travis-ci.org/gjcarneiro/yacron.svg?branch=master&maxAge=600
    :target: https://travis-ci.org/gjcarneiro/yacron
.. image:: https://coveralls.io/repos/github/gjcarneiro/yacron/badge.svg?branch=master&maxAge=600
    :target: https://coveralls.io/github/gjcarneiro/yacron?branch=master


Features
--------

* "Crontab" is in YAML format;
* Builtin sending of Sentry and Mail outputs when cron jobs fail;
* Flexible configuration: you decide how to determine if a cron job fails or not;
* Designed for running in Docker, Kubernetes, or 12 factor environments:

  * Runs in the foreground;
  * Logs everything to stdout/stderr [1]_;

* Option to automatically retry failing cron jobs, with exponential backoff;
* Optional HTTP REST API, to fetch status and start jobs on demand;
* Arbitrary timezone support;


.. [1] Whereas vixie cron only logs to syslog, requiring a syslog daemon to be running in the background or else you don't get logs!

Status
--------------

The project is in beta stage: essential features are complete, and the focus is
finding and fixing bugs before the first stable release.

Installation
------------

Install using pip
+++++++++++++++++

yacron requires Python >= 3.5.3.  It is advisable to install it in a Python
virtual environment, for example:

.. code-block:: shell

    python3 -m venv yacronenv
    . yacronenv/bin/activate
    pip install yacron

Install using pipx
++++++++++++++++++

pipx_ automates creating a virtualenv and installing a python program in the
newly created virtualenv.  It is as simple as:

.. code-block:: shell

    pipx install yacron

.. _pipx: https://github.com/pipxproject/pipx

Install using binary
++++++++++++++++++++

Alternatively, a self-contained binary can be downloaded
from github: https://github.com/gjcarneiro/yacron/releases. This binary should
work on any Linux 64-bit system post glibc 2.23 (e.g. Ubuntu:16.04).  Python is not required on the target system (it is embedded in the executable).

Usage
-----

Configuration is in YAML format.  To start yacron, give it a configuration file
or directory path as the ``-c`` argument.  For example::

    yacron -c /tmp/my-crontab.yaml

This starts yacron (always in the foreground!), reading
``/tmp/my-crontab.yaml`` as configuration file.  If the path is a directory,
any ``*.yaml`` or ``*.yml`` files inside this directory are taken as
configuration files.

Configuration basics
++++++++++++++++++++

This configuration runs a command every 5 minutes:

.. code-block:: yaml

    jobs:
      - name: test-01
        command: echo "foobar"
        shell: /bin/bash
        schedule: "*/5 * * * *"

The command can be a string or a list of strings.  If command is a string,
yacron runs it through a shell, which is ``/bin/bash`` in the above example, but
is ``/bin/sh`` by default.

If the command is a list of strings, the command is executed directly, without a
shell.  The ARGV of the command to execute is extracted directly from the
configuration:

.. code-block:: yaml

    jobs:
      - name: test-01
        command:
          - echo
          - foobar
        schedule: "*/5 * * * *"


The `schedule` option can be a string in the traditional crontab format
(including @reboot, which will only run the job when yacron is initially
executed), or can be an object with properties.  The following configuration
runs a command every 5 minutes, but only on the specific date 2017-07-19, and
doesn't run it in any other date:

.. code-block:: yaml

    jobs:
      - name: test-01
        command: echo "foobar"
        schedule:
          minute: "*/5"
          dayOfMonth: 19
          month: 7
          year: 2017
          dayOfWeek: "*"

Important: by default all time is interpreted to be in UTC, but you can
request to use local time instead.  For instance, the cron job below runs
every day at 19h27 *local time* because of the ``utc: false`` option:

.. code-block:: yaml

  jobs:
    - name: test-01
      command: echo "hello"
      schedule: "27 19 * * *"
      utc: false
      captureStdout: true

Since Yacron version 0.11, you can also request that the schedule be
interpreted in an arbitrary timezone, using the ``timezone`` attribute:

.. code-block:: yaml

  jobs:
    - name: test-01
      command: echo "hello"
      schedule: "27 19 * * *"
      timezone: America/Los_Angeles
      captureStdout: true


You can ask for environment variables to be defined for command execution:

.. code-block:: yaml

    jobs:
      - name: test-01
        command: echo "foobar"
        shell: /bin/bash
        schedule: "*/5 * * * *"
        environment:
          - key: PATH
            value: /bin:/usr/bin

You can also provide an environment file to define environments for command execution:

.. code-block:: yaml

    jobs:
      - name: test-01
        command: echo "foobar"
        shell: /bin/bash
        schedule: "*/5 * * * *"
        env_file: .env

The env file must be a list of ``KEY=VALUE`` pairs. Empty lines and lines starting with ``#`` will be ignored.

Variables declared in the ``environment`` option will override those found in the ``env_file``.


Specifying defaults
+++++++++++++++++++


There can be a special ``defaults`` section in the config.  Any attributes
defined in this section provide default values for cron jobs to inherit.
Although cron jobs can still override the defaults, as needed:

.. code-block:: yaml

    defaults:
        environment:
          - key: PATH
            value: /bin:/usr/bin
        shell: /bin/bash
        utc: false
    jobs:
      - name: test-01
        command: echo "foobar"  # runs with /bin/bash as shell
        schedule: "*/5 * * * *"
      - name: test-02  # runs with /bin/sh as shell
        command: echo "zbr"
        shell: /bin/sh
        schedule: "*/5 * * * *"

Note: if the configuration option is a directory and there are multiple configuration files in that directory, then the ``defaults`` section in each configuration file provides default options only for cron jobs inside that same file; the defaults have no effect beyond any individual YAML file.

Reporting
+++++++++

Yacron has builtin support for reporting jobs failure (more on that below) by
email and Sentry (additional reporting methods might be added in the future):

.. code-block:: yaml

  - name: test-01
    command: |
      echo "hello" 1>&2
      sleep 1
      exit 10
    schedule:
      minute: "*/2"
    captureStderr: true
    onFailure:
      report:
        sentry:
          dsn:
            value: example
            # Alternatively:
            # fromFile: /etc/secrets/my-secret-dsn
            # fromEnvVar: SENTRY_DSN
          fingerprint:  # optional, since yacron 0.6
            - yacron
            - "{{ environment.HOSTNAME }}"
            - "{{ name }}"
          extra:
            foo: bar
            zbr: 123
          level: warning
        mail:
          from: example@foo.com
          to: example@bar.com
          smtpHost: 127.0.0.1
          # optional fields:
          username: "username1"  # set username and password to enable login
          pasword:
            value: example
            # Alternatively:
            # fromFile: /etc/secrets/my-secret-password
            # fromEnvVar: MAIL_PASSWORD
          tls: false  # set to true to enable TLS
          starttls: false  # set to true to enable StartTLS

Here, the ``onFailure`` object indicates that what to do when a job failure
is detected.  In this case we ask for it to be reported both to sentry and by
sending an email.

The ``captureStderr: true`` part instructs yacron to capture output from the the
program's `standard error`, so that it can be included in the report.  We could
also turn on `standard output` capturing via the ``captureStdout: true`` option.
By default, yacron captures only standard error.  If a cron job's standard error
or standard output capturing is not enabled, these streams will simply write to
the same standard output and standard error as yacron itself.

It is possible also to report job success, as well as failure, via the
``onSuccess`` option.

.. code-block:: yaml

  - name: test-01
    command: echo "hello world"
    schedule:
      minute: "*/2"
    captureStdout: true
    onSuccess:
      report:
        mail:
          from: example@foo.com
          to: example@bar.com
          smtpHost: 127.0.0.1

Since yacron 0.5, it is possible to customise the format of the report. For
``mail`` reporting, the option ``subject`` indicates what is the subject of the
email, while ``body`` formats the email body.  For Sentry reporting, there is
only ``body``.  In all cases, the values of those options are strings that are
processed by the jinja2_ templating engine.  The following variables are
available in templating:

* name(str): name of the cron job
* success(bool): whether or not the cron job succeeded
* stdout(str): standard output of the process
* stderr(str): standard error of the process
* exit_code(int): process exit code
* command(str): cron job command
* shell(str): cron job shell
* environment(dict): subprocess environment variables

.. _jinja2: http://jinja.pocoo.org/

Example:

.. code-block:: yaml

  - name: test-01
    command: |
      echo "hello" 1>&2
      sleep 1
      exit 10
    schedule:
      minute: "*/2"
    captureStderr: true
    onFailure:
      report:
        mail:
          from: example@foo.com
          to: example@bar.com
          smtpHost: 127.0.0.1
          subject: Cron job '{{name}}' {% if success %}completed{% else %}failed{% endif %}
          body: |
            {{stderr}}
            (exit code: {{exit_code}})


Metrics
+++++++++

Yacron has builtin support for writing job metrics to Statsd_:

.. _Statsd: https://github.com/etsy/statsd

.. code-block:: yaml

    jobs:
      - name: test01
        command: echo "hello"
        schedule: "* * * * *"
        statsd:
          host: my-statsd.exemple.com
          port: 8125
          prefix: my.cron.jobs.prefix.test01

With this config Yacron will write the following metrics over UDP
to the Statsd listening on ``my-statsd.exemple.com:8125``:

.. code-block::

  my.cron.jobs.prefix.test01.start:1|g  # this one is sent when the job starts
  my.cron.jobs.prefix.test01.stop:1|g   # the rest are sent when the job stops
  my.cron.jobs.prefix.test01.success:1|g
  my.cron.jobs.prefix.test01.duration:3|ms|@0.1


Handling failure
++++++++++++++++

By default, yacron considers that a job has `failed` if either the process
returns a non-zero code or if it generates output to `standard error` (and
standard error capturing is enabled, of course).

You can instruct yacron how to determine if a job has failed or not via the
``failsWhen`` option:

.. code-block:: yaml

  failsWhen:
    producesStdout: false
    producesStderr: true
    nonzeroReturn: true
    always: false

producesStdout
    If true, any captured standard output causes yacron to consider the job
    as failed.  This is false by default.

producesStderr
    If true, any captured standard error causes yacron to consider the job
    as failed.  This is true by default.

nonzeroReturn
    If true, if the job process returns a code other than zero causes yacron
    to consider the job as failed.  This is true by default.

always
    If true, if the job process exits that causes yacron to consider the job as
    failed.  This is false by default.

It is possible to instruct yacron to retry failing cron jobs by adding a
``retry`` option inside ``onFailure``:

.. code-block:: yaml

  - name: test-01
    command: |
      echo "hello" 1>&2
      sleep 1
      exit 10
    schedule:
      minute: "*/10"
    captureStderr: true
    onFailure:
      report:
        mail:
          from: example@foo.com
          to: example@bar.com
          smtpHost: 127.0.0.1
      retry:
        maximumRetries: 10
        initialDelay: 1
        maximumDelay: 30
        backoffMultiplier: 2

The above settings tell yacron to retry the job up to 10 times, with the delay
between retries defined by an exponential backoff process: initially 1 second,
doubling for every retry up to a maximum of 30 seconds. A value of -1 for
maximumRetries will mean yacron will keep retrying forever, this is mostly
useful with a schedule of "@reboot" to restart a long running process when it
has failed.

If the cron job is expected to fail sometimes, you may wish to report only in
the case the cron job ultimately fails after all retries and we give up on it.
For that situation, you can use the ``onPermanentFailure`` option:

.. code-block:: yaml

  - name: test-01
    command: |
      echo "hello" 1>&2
      sleep 1
      exit 10
    schedule:
      minute: "*/10"
    captureStderr: true
    onFailure:
      retry:
        maximumRetries: 10
        initialDelay: 1
        maximumDelay: 30
        backoffMultiplier: 2
    onPermanentFailure:
      report:
        mail:
          from: example@foo.com
          to: example@bar.com
          smtpHost: 127.0.0.1

Concurrency
+++++++++++
Sometimes it may happen that a cron job takes so long to execute that when the moment its next scheduled execution is reached a previous instance may still be running.  How yacron handles this situation is controlled by the option ``concurrencyPolicy``, which takes one of the following values:

Allow
    allows concurrently running jobs (default)
Forbid
    forbids concurrent runs, skipping next run if previous hasn’t finished yet
Replace
    cancels currently running job and replaces it with a new one

Execution timeout
+++++++++++++++++

(new in version 0.4)

If you have a cron job that may possibly hang sometimes, you can instruct yacron
to terminate the process after N seconds if it's still running by then, via the
``executionTimeout`` option.  For example, the following cron job takes 2
seconds to complete, yacron will terminate it after 1 second:

.. code-block:: yaml

  - name: test-03
    command: |
      echo "starting..."
      sleep 2
      echo "all done."
    schedule:
      minute: "*"
    captureStderr: true
    executionTimeout: 1  # in seconds

When terminating a job, it is always a good idea to give that job process some
time to terminate properly.  For example, it may have opened a file, and even if
you tell it to shutdown, the process may need a few seconds to flush buffers and
avoid losing data.

On the other hand, there are times when programs are buggy and simply get stuck,
refusing to terminate nicely no matter what.  For this reason, yacron always
checks if a process exited some time after being asked to do so. If it hasn't,
it tries to forcefully kill the process.  The option ``killTimeout`` option
indicates how many seconds to wait for the process to gracefully terminate
before killing it more forcefully.  In Unix systems, we first send a SIGTERM,
but if the process doesn't exit after ``killTimeout`` seconds (30 by default)
then we send SIGKILL.  For example, this cron job ignores SIGTERM, and so yacron
will send it a SIGKILL after half a second:

.. code-block:: yaml

  - name: test-03
    command: |
      trap "echo '(ignoring SIGTERM)'" TERM
      echo "starting..."
      sleep 10
      echo "all done."
    schedule:
      minute: "*"
    captureStderr: true
    executionTimeout: 1
    killTimeout: 0.5

Change to another user/group
++++++++++++++++++++++++++++

(new in version 0.11)

You can request that Yacron change to another user and/or group for a specific
cron job.  The field ``user`` indicates the user (uid or userame) under which
the subprocess must be executed.  The field ``group`` (gid or group name)
indicates the group id.  If only ``user`` is given, the group defaults to the
main group of that user.  Example:

.. code-block:: yaml

  - name: test-03
    command: id
    schedule:
      minute: "*"
    captureStderr: true
    user: www-data

Naturally, yacron must be running as root in order to have permissions to
change to another user.


Remote web/HTTP interface
+++++++++++++++++++++++++

(new in version 0.10)

If you wish to remotely control yacron, you can optionally enable an HTTP REST
interface, with the following configuration (example):

.. code-block:: yaml

  web:
    listen:
       - http://127.0.0.1:8080
       - unix:///tmp/yacron.sock

Now you have the following options to control it (using HTTPie as example):

Get the version of yacron:
##########################

.. code-block:: shell

  $ http get http://127.0.0.1:8080/version
  HTTP/1.1 200 OK
  Content-Length: 22
  Content-Type: text/plain; charset=utf-8
  Date: Sun, 03 Nov 2019 19:48:15 GMT
  Server: Python/3.7 aiohttp/3.6.2

  0.10.0b3.dev7+g45bc4ce

Get the status of cron jobs:
############################

.. code-block:: shell

  $ http get http://127.0.0.1:8080/status
  HTTP/1.1 200 OK
  Content-Length: 104
  Content-Type: text/plain; charset=utf-8
  Date: Sun, 03 Nov 2019 19:44:45 GMT
  Server: Python/3.7 aiohttp/3.6.2

  test-01: scheduled (in 14 seconds)
  test-02: scheduled (in 74 seconds)
  test-03: scheduled (in 14 seconds)

You may also get status info in json format:

.. code-block:: shell

  $ http get http://127.0.0.1:8080/status Accept:application/json
  HTTP/1.1 200 OK
  Content-Length: 206
  Content-Type: application/json; charset=utf-8
  Date: Sun, 03 Nov 2019 19:45:53 GMT
  Server: Python/3.7 aiohttp/3.6.2

  [
      {
          "job": "test-01",
          "scheduled_in": 6.16588,
          "status": "scheduled"
      },
      {
          "job": "test-02",
          "scheduled_in": 6.165787,
          "status": "scheduled"
      },
      {
          "job": "test-03",
          "scheduled_in": 6.165757,
          "status": "scheduled"
      }
  ]

Start a job right now:
######################

Sometimes it's useful to start a cron job right now, even if it's not
scheduled to run yet, for example for testing:

.. code-block:: shell

  $ http post http://127.0.0.1:8080/jobs/test-02/start
  HTTP/1.1 200 OK
  Content-Length: 0
  Content-Type: application/octet-stream
  Date: Sun, 03 Nov 2019 19:50:20 GMT
  Server: Python/3.7 aiohttp/3.6.2
