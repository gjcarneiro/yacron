=======
History
=======

0.12.0 (2021-04-22)
-------------------

* web: don't crash when receiving a web request without Accept header (#45)
* add env_file configuration option (Alessandro Romani, #43)
* email: add missing Date header (#39)


0.11.2 (2020-11-29)
-------------------

* Add back a self contained binary, this time based on PyInstaller

0.11.1 (2020-07-29)
-------------------

* Fix email reporting when multiple recipients given


0.11.0 (2020-07-20)
-------------------

* reporting: add a failure reason line at the top of sentry/email (#36)
* mail: new tls, startls, username, and password options (#21)
* allow jobs to run as a different user (#18)
* Support timezone schedule (#26)


0.10.1 (2020-06-02)
-------------------

* Minor bugfixes


0.10.0 (2019-11-03)
-------------------

* HTTP remote interface, allowing to get job status and start jobs on demand
* Simple Linux binary including all dependencies (built using PyOxidizer)

0.10.0b2 (2019-10-26)
---------------------

* Build Linux binary inside Docker Ubuntu 16.04, so that it is compatible with
  older glibc systems

0.10.0b1 (2019-10-13)
---------------------
* Build a standalone Linux binary, using PyOxidizer
* Switch from raven to sentry-sdk

0.9.0 (2019-04-03)
------------------
* Added an option to just check if the yaml file is valid without running the scheduler.
* Fix missing `body` in the schema for sentry config


0.8.1 (2018-10-16)
------------------
* Fix a bug handling ``@reboot`` in schedule (#22)

0.8.0 (2018-05-14)
------------------
* Sentry: add new ``extra`` and ``level`` options.


0.7.0 (2018-03-21)
------------------

* Added the ``utc`` option and document that times are utc by default (#17);
* If an email body is empty, skip sending it;
* Added docker and k8s example.


0.6.0 (2017-11-24)
------------------
* Add custom Sentry fingerprint support
* Ability to send job metrics to statsd (thanks bofm)
* ``always`` flag to consider any cron job that exits to be failed
  (thanks evanjardineskinner)
* `maximumRetries` can now be ``-1`` to never stop retrying (evanjardineskinner)
* ``schedule`` can be the string ``@reboot`` to always run that cron job on startup
  (evanjardineskinner)
* ``saveLimit`` can be set to zero (evanjardineskinner)

0.5.0
------------------
* Templating support for reports
* Remove deprecated smtp_host/smtp_port

0.4.3 (2017-09-13)
------------------
* Bug fixes

0.4.2 (2017-09-07)
------------------
* Bug fixes

0.4.1 (2017-08-03)
------------------

* More polished handling of configuration errors;
* Unit tests;
* Bug fixes.

0.4.0 (2017-07-24)
------------------

* New option ``executionTimeout``, to terminate jobs that get stuck;
* If a job doesn't terminate gracefully kill it.  New option ``killTimeout``
  controls how much time to wait for graceful termination before killing it;
* Switch parsing to strictyaml, for more user friendly parsing validation error
  messages.
