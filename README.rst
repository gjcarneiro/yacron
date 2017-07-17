================
Yet Another Cron
================


A modern Cron replacement that is Docker-friendly


* Free software: MIT license


Features
--------

* "Crontab" is in YAML format
* Builtin sending of Sentry and Mail outputs
* Flexible configation: you decide how to determine of a cron job failed or not
* Designed for running in Docker, Kubernetes, or 12 factor environments:
  * Runs in the foreground
  * Logs everything to stdout/stderr
