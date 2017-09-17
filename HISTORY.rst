=======
History
=======

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
