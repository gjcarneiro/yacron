=======
History
=======

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
