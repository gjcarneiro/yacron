================
Yet Another Cron
================


A modern Cron replacement that is Docker-friendly


* Free software: MIT license


Features
--------

* "Crontab" is in YAML format
* Builtin sending of Sentry and Mail outputs when cron jobs fail
* Flexible configation: you decide how to determine of a cron job failed or not
* Designed for running in Docker, Kubernetes, or 12 factor environments:

  * Runs in the foreground
  * Logs everything to stdout/stderr

* Option to automatically retry failing cron jobs, with exponential backoff
