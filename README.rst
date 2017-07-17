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
* Logs everything to stdout/stderr, making it suitable to run inside Docker,
  Kubernetes, or 12 factor environments.
