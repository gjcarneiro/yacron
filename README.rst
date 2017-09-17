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

* Option to automatically retry failing cron jobs, with exponential backoff.

.. [1] Whereas vixie cron only logs to syslog, requiring a syslog daemon to be running in the background or else you don't get logs!

Status
--------------

The project is in beta stage: essential features are complete, and the focus is
finding and fixing bugs before the first stable release.

Installation
------------
yacron requires Python >= 3.5.  It is advisable to install it in a Python virtual environment, for example:

.. code-block:: shell

    virtualenv -p python3 yacronenv
    . yacronenv/bin/activate
    pip install yacron

Usage
-----

Configuration is in YAML format.  To start yacron, give it a configuration file
or directory path as the ``-c`` argument.  For example::

    yacron -c /tmp/my-crontab.yaml

This starts yacron (always in the foreground!), reading ``/tmp/my-crontab.yaml``
as configuration file.  If the path is a directory, any ``*.yaml`` or ``*.yml`` files inside this directory are taken as configuration files.

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


The `schedule` option can be a string in the traditional crontab format, or can
be an object with properties.  The following configuration runs a command every
5 minutes, but only on the specific date 2017-07-19, and doesn't run it in any
other date:

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
        mail:
          from: example@foo.com
          to: example@bar.com
          smtp_host: 127.0.0.1

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
          smtp_host: 127.0.0.1

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
          smtp_host: 127.0.0.1
          subject: Cron job '{{name}}' {% if success %}completed{% else %}failed{% endif %}
          body: |
            {{stderr}}
            (exit code: {{exit_code}})


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

producesStdout
    If true, any captured standard output causes yacron to consider the job
    as failed.  This is false by default.

producesStderr
    If true, any captured standard error causes yacron to consider the job
    as failed.  This is true by default.

nonzeroReturn
    If true, if the job process returns a code other than zero causes yacron
    to consider the job as failed.  This is true by default.

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
          smtp_host: 127.0.0.1
      retry:
        maximumRetries: 10
        initialDelay: 1
        maximumDelay: 30
        backoffMultiplier: 2

The above settings tell yacron to retry the job up to 10 times, with the delay
between retries defined by an exponential backoff process: initially 1 second,
doubling for every retry up to a maximum of 30 seconds.

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
          smtp_host: 127.0.0.1

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
