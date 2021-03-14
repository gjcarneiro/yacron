from pwd import getpwnam
from grp import getgrnam
import logging
import os.path
from typing import Union  # noqa
from typing import List, Optional, Any, Dict, NewType, Tuple
import datetime
import pytz

import strictyaml
from strictyaml import Optional as Opt, EmptyDict
from strictyaml import (
    Bool,
    EmptyNone,
    Enum,
    Float,
    Int,
    Map,
    Seq,
    Str,
    MapPattern,
)
from ruamel.yaml.error import YAMLError

from crontab import CronTab

logger = logging.getLogger("yacron.config")
WebConfig = NewType("WebConfig", Dict[str, Any])


class ConfigError(Exception):
    pass


DEFAULT_BODY_TEMPLATE = """
{% if fail_reason -%}
(job failed because {{fail_reason}})
{% endif %}
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

DEFAULT_SUBJECT_TEMPLATE = (
    "Cron job '{{name}}' {% if success %}completed"
    "{% else %}failed{% endif %}"
)

_REPORT_DEFAULTS = {
    "sentry": {
        "dsn": {"value": None, "fromFile": None, "fromEnvVar": None},
        "body": DEFAULT_SUBJECT_TEMPLATE + "\n" + DEFAULT_BODY_TEMPLATE,
        "fingerprint": ["yacron", "{{ environment.HOSTNAME }}", "{{ name }}"],
    },
    "mail": {
        "from": None,
        "to": None,
        "smtpHost": None,
        "smtpPort": 25,
        "tls": False,
        "starttls": False,
        "subject": DEFAULT_SUBJECT_TEMPLATE,
        "body": DEFAULT_BODY_TEMPLATE,
        "username": None,
        "password": {"value": None, "fromFile": None, "fromEnvVar": None},
    },
}


DEFAULT_CONFIG = {
    "shell": "/bin/sh",
    "concurrencyPolicy": "Allow",
    "captureStderr": True,
    "captureStdout": False,
    "saveLimit": 4096,
    "utc": True,
    "timezone": None,
    "failsWhen": {
        "producesStdout": False,
        "producesStderr": True,
        "nonzeroReturn": True,
        "always": False,
    },
    "onFailure": {
        "retry": {
            "maximumRetries": 0,
            "initialDelay": 1,
            "maximumDelay": 300,
            "backoffMultiplier": 2,
        },
        "report": _REPORT_DEFAULTS,
    },
    "onPermanentFailure": {"report": _REPORT_DEFAULTS},
    "onSuccess": {"report": _REPORT_DEFAULTS},
    "environment": [],
    "env_file": None,
    "executionTimeout": None,
    "killTimeout": 30,
    "statsd": None,
}


_report_schema = Map(
    {
        Opt("sentry"): Map(
            {
                Opt("dsn"): Map(
                    {
                        Opt("value"): EmptyNone() | Str(),
                        Opt("fromFile"): EmptyNone() | Str(),
                        Opt("fromEnvVar"): EmptyNone() | Str(),
                    }
                ),
                Opt("fingerprint"): Seq(Str()),
                Opt("level"): Str(),
                Opt("extra"): MapPattern(Str(), Str() | Int() | Bool()),
                Opt("body"): Str(),
            }
        ),
        Opt("mail"): Map(
            {
                "from": EmptyNone() | Str(),
                "to": EmptyNone() | Str(),
                Opt("smtpHost"): Str(),
                Opt("smtpPort"): Int(),
                Opt("subject"): Str(),
                Opt("body"): Str(),
                Opt("username"): Str(),
                Opt("password"): Map(
                    {
                        Opt("value"): EmptyNone() | Str(),
                        Opt("fromFile"): EmptyNone() | Str(),
                        Opt("fromEnvVar"): EmptyNone() | Str(),
                    }
                ),
                Opt("tls"): Bool(),
                Opt("starttls"): Bool(),
            }
        ),
    }
)

_job_defaults_common = {
    Opt("shell"): Str(),
    Opt("concurrencyPolicy"): Enum(["Allow", "Forbid", "Replace"]),
    Opt("captureStderr"): Bool(),
    Opt("captureStdout"): Bool(),
    Opt("saveLimit"): Int(),
    Opt("utc"): Bool(),
    Opt("timezone"): Str(),
    Opt("failsWhen"): Map(
        {
            "producesStdout": Bool(),
            Opt("producesStderr"): Bool(),
            Opt("nonzeroReturn"): Bool(),
            Opt("always"): Bool(),
        }
    ),
    Opt("onFailure"): Map(
        {
            Opt("retry"): Map(
                {
                    "maximumRetries": Int(),
                    "initialDelay": Float(),
                    "maximumDelay": Float(),
                    "backoffMultiplier": Float(),
                }
            ),
            Opt("report"): _report_schema,
        }
    ),
    Opt("onPermanentFailure"): Map({Opt("report"): _report_schema}),
    Opt("onSuccess"): Map({Opt("report"): _report_schema}),
    Opt("environment"): Seq(Map({"key": Str(), "value": Str()})),
    Opt("env_file"): Str(),
    Opt("executionTimeout"): Float(),
    Opt("killTimeout"): Float(),
    Opt("statsd"): Map({"prefix": Str(), "host": Str(), "port": Int()}),
    Opt("user"): Str() | Int(),
    Opt("group"): Str() | Int(),
}

_job_schema_dict = dict(_job_defaults_common)
_job_schema_dict.update(
    {
        "name": Str(),
        "command": Str() | Seq(Str()),
        "schedule": Str()
        | Map(
            {
                Opt("minute"): Str(),
                Opt("hour"): Str(),
                Opt("dayOfMonth"): Str(),
                Opt("month"): Str(),
                Opt("year"): Str(),
                Opt("dayOfWeek"): Str(),
            }
        ),
    }
)

CONFIG_SCHEMA = EmptyDict() | Map(
    {
        Opt("defaults"): Map(_job_defaults_common),
        Opt("jobs"): Seq(Map(_job_schema_dict)),
        Opt("web"): Map({"listen": Seq(Str())}),
    }
)


# Slightly modified version of https://stackoverflow.com/a/7205672/2211825
def mergedicts(dict1, dict2):
    for k in set(dict1.keys()).union(dict2.keys()):
        if k in dict1 and k in dict2:
            v1 = dict1[k]
            v2 = dict2[k]
            if isinstance(v1, dict) and isinstance(v2, dict):
                yield (k, dict(mergedicts(v1, v2)))
            elif isinstance(v1, dict) and v2 is None:  # modification
                yield (k, dict(mergedicts(v1, {})))
            elif isinstance(v1, list) and isinstance(v2, list):  # merge lists
                yield (k, v1 + v2)
            else:
                yield (k, v2)
        elif k in dict1:
            yield (k, dict1[k])
        else:
            yield (k, dict2[k])


class JobConfig:
    def __init__(self, config: dict) -> None:
        self.name = config["name"]  # type: str
        self.command = config["command"]  # type: Union[str, List[str]]
        self.schedule_unparsed = config.pop("schedule")
        if isinstance(self.schedule_unparsed, str):
            if self.schedule_unparsed in {"@reboot"}:
                self.schedule = (
                    self.schedule_unparsed
                )  # type: Union[CronTab, str]
            else:
                self.schedule = CronTab(self.schedule_unparsed)
        elif isinstance(self.schedule_unparsed, dict):
            minute = self.schedule_unparsed.get("minute", "*")
            hour = self.schedule_unparsed.get("hour", "*")
            day = self.schedule_unparsed.get("dayOfMonth", "*")
            month = self.schedule_unparsed.get("month", "*")
            dow = self.schedule_unparsed.get("dayOfWeek", "*")
            tab = "{} {} {} {} {}".format(minute, hour, day, month, dow)
            logger.debug("Converted schedule to %r", tab)
            self.schedule = CronTab(tab)
        else:
            raise ConfigError("invalid schedule: %r", self.schedule_unparsed)
        self.shell = config.pop("shell")
        self.concurrencyPolicy = config.pop("concurrencyPolicy")
        self.captureStderr = config.pop("captureStderr")
        self.captureStdout = config.pop("captureStdout")
        self.saveLimit = config.pop("saveLimit")
        self.utc = config.pop("utc")
        self.timezone = None  # type: Optional[datetime.tzinfo]
        if config["timezone"] is not None:
            try:
                self.timezone = pytz.timezone(config["timezone"])
            except pytz.UnknownTimeZoneError as err:
                raise ConfigError("unknown timezone: " + str(err))
        elif self.utc:
            self.timezone = datetime.timezone.utc

        self.failsWhen = config.pop("failsWhen")
        self.onFailure = config.pop("onFailure")
        self.onPermanentFailure = config.pop("onPermanentFailure")
        self.onSuccess = config.pop("onSuccess")

        self.env_file = config.pop("env_file")
        self.environment = config.pop("environment")
        if self.env_file is not None:
            try:
                file_environs = parse_environment_file(self.env_file)
            except OSError as e:
                raise ConfigError("Could not load env_file: {}".format(e))
            else:
                # unpack variables in dictionaries
                config_environs = {
                    env["key"]: env["value"] for env in self.environment
                }
                # update file values with config ones
                file_environs.update(config_environs)
                # replace environment
                self.environment = [
                    {"key": key, "value": value}
                    for key, value in file_environs.items()
                ]

        self.executionTimeout = config.pop("executionTimeout")
        self.killTimeout = config.pop("killTimeout")
        self.statsd = config.pop("statsd")

        self.uid = None
        self.gid = None

        user = config.pop("user", None)
        if user is not None:
            if isinstance(user, int):
                self.uid = user
            else:
                try:
                    pw = getpwnam(user)
                    self.uid = pw.pw_uid
                    self.gid = pw.pw_gid
                except KeyError:
                    raise ConfigError("User not found: {!r}".format(user))

        group = config.pop("group", None)
        if group is not None:
            if isinstance(group, int):
                self.gid = group
            else:
                try:
                    self.gid = getgrnam(group).gr_gid
                except KeyError:
                    raise ConfigError("Group not found: {!r}".format(group))

        if self.uid is not None or self.gid is not None:
            if os.geteuid() != 0:
                raise ConfigError(
                    "Job {} wants to change user or group, "
                    "but yacron is not running as superuser".format(self.name)
                )


def parse_config_file(
    path: str,
) -> Tuple[List[JobConfig], Optional[WebConfig]]:
    with open(path, "rt", encoding="utf-8") as stream:
        data = stream.read()
    return parse_config_string(data, path)


def parse_environment_file(path: str) -> Dict[str, str]:
    """
    Parse environment variables from file.

    Handles comments (lines starting with ``#``) and blank lines.
    Variables must be specified in ``VARIABLE_NAME=CONTENT`` format.

    :param path: Path to the environment file.
    :raise ConfigError: If a line in the file is not parsable
        (the ``=`` key-value separation character is missing).
    :raise OSError: If an error occurred while opening the file at ``path``.
    :return: key-value map of environment variables.
    """
    environ: Dict[str, str] = {}

    with open(path, "r") as env_file:
        # file parsing
        # you may want to use the `dotenv` library to do the job
        for line in env_file.readlines():
            line = line.strip(" ").rstrip("\n")
            if line.startswith("#") or not line:
                continue
            if "=" not in line:
                raise ConfigError(
                    "Invalid line in env_file: '{}'".format(line)
                )
            key, value = line.split("=", 1)
            key = key.strip(" ")
            value = value.strip(" ")
            environ[key] = value

    return environ


def parse_config_string(
    data: str, path: Optional[str] = None
) -> Tuple[List[JobConfig], Optional[WebConfig]]:
    try:
        doc = strictyaml.load(data, CONFIG_SCHEMA, label=path).data
    except YAMLError as ex:
        raise ConfigError(str(ex))

    defaults = doc.get("defaults", {})
    jobs = []
    for config_job in doc.get("jobs", []):
        job_dict = dict(mergedicts(DEFAULT_CONFIG, defaults))
        job_dict = dict(mergedicts(job_dict, config_job))
        jobs.append(JobConfig(job_dict))
    webconf = WebConfig(doc["web"]) if "web" in doc else None
    return jobs, webconf


def parse_config(
    config_arg: str,
) -> Tuple[List[JobConfig], Optional[WebConfig]]:
    jobs = []
    config_errors = {}
    web_config = None
    web_config_source_fname = None
    if os.path.isdir(config_arg):
        for direntry in os.scandir(config_arg):
            _, ext = os.path.splitext(direntry.name)
            if ext in {".yml", ".yaml"}:
                try:
                    config, webconf = parse_config_file(direntry.path)
                except ConfigError as err:
                    config_errors[direntry.path] = str(err)
                except OSError as ex:
                    config_errors[config_arg] = str(ex)
                else:
                    jobs.extend(config)
                    if webconf is not None:
                        if web_config is None:
                            web_config = webconf
                            web_config_source_fname = direntry.path
                        else:
                            raise ConfigError(
                                "Multiple 'web' configurations found: "
                                "first in {}, now in {}".format(
                                    web_config_source_fname, direntry.path
                                )
                            )
    else:
        try:
            config, web_config = parse_config_file(config_arg)
        except OSError as ex:
            config_errors[config_arg] = str(ex)
        else:
            jobs.extend(config)
    if config_errors:
        raise ConfigError("\n---".join(config_errors.values()))
    return jobs, web_config
