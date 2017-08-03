import logging
import os.path
from typing import Union  # noqa
from typing import List, Optional

import strictyaml
from strictyaml import Optional as Opt
from strictyaml import Bool, EmptyNone, Enum, Float, Int, Map, Seq, Str
from strictyaml.exceptions import StrictYAMLError
from ruamel.yaml.error import YAMLError

from crontab import CronTab

logger = logging.getLogger('yacron.config')


class ConfigError(Exception):
    pass


_REPORT_DEFAULTS = {
    'sentry': {
        'dsn': {
            'value': None,
            'fromFile': None,
            'fromEnvVar': None,
        },
    },
    'mail': {
        'from': None,
        'to': None,
        'smtp_host': None,  # deprecated
        'smtp_port': 25,  # deprecated
        'smtpHost': None,
        'smtpPort': 25,
    },
}


DEFAULT_CONFIG = {
    'shell': '/bin/sh',
    'concurrencyPolicy': 'Allow',
    'captureStderr': True,
    'captureStdout': False,
    'saveLimit': 4096,
    'failsWhen': {
        'producesStdout': False,
        'producesStderr': True,
        'nonzeroReturn': True,
    },
    'onFailure': {
        'retry': {
            'maximumRetries': 0,
            'initialDelay': 1,
            'maximumDelay': 300,
            'backoffMultiplier': 2,
        },
        'report': _REPORT_DEFAULTS,
    },
    'onPermanentFailure': {
        'report': _REPORT_DEFAULTS,
    },
    'onSuccess': {
        'report': _REPORT_DEFAULTS,
    },
    'environment': [],
    'executionTimeout': None,
    'killTimeout': 30,
}


_report_schema = Map({
    "sentry": Map({
        Opt("dsn"): Map({
            Opt("value"): Str() | EmptyNone(),
            Opt("fromFile"): Str() | EmptyNone(),
            Opt("fromEnvVar"): Str() | EmptyNone(),
        }),
    }),
    Opt("mail"): Map({
        "from": EmptyNone() | Str(),
        "to": EmptyNone() | Str(),
        Opt("smtp_host"): Str(),
        Opt("smtp_port"): Int(),
        Opt("smtpHost"): Str(),
        Opt("smtpPort"): Int(),
    })
})

_job_defaults_common = {
    Opt("shell"): Str(),
    Opt("concurrencyPolicy"): Enum(['Allow', 'Forbid', 'Replace']),
    Opt("captureStderr"): Bool(),
    Opt("captureStdout"): Bool(),
    Opt("saveLimit"): Int(),
    Opt("failsWhen"): Map({
        "producesStdout": Bool(),
        "producesStderr": Bool(),
        "nonzeroReturn": Bool(),
    }),
    Opt("onFailure"): Map({
        Opt("retry"): Map({
            "maximumRetries": Int(),
            "initialDelay": Float(),
            "maximumDelay": Float(),
            "backoffMultiplier": Float(),
        }),
        Opt("report"): _report_schema,
    }),
    Opt("onPermanentFailure"): Map({
        Opt("report"): _report_schema,
    }),
    Opt("onSuccess"): Map({
        Opt("report"): _report_schema,
    }),
    Opt("environment"): Seq(Map({
        "key": Str(),
        "value": Str(),
    })),
    Opt("executionTimeout"): Float(),
    Opt("killTimeout"): Float(),
}

_job_schema_dict = dict(_job_defaults_common)
_job_schema_dict.update({
    "name": Str(),
    "command": Str() | Seq(Str()),
    "schedule": Str() | Map({
        Opt("minute"): Str(),
        Opt("hour"): Str(),
        Opt("dayOfMonth"): Str(),
        Opt("month"): Str(),
        Opt("year"): Str(),
        Opt("dayOfWeek"): Str(),
    }),
})

CONFIG_SCHEMA = Map({
    Opt("defaults"): Map(_job_defaults_common),
    "jobs": Seq(Map(_job_schema_dict)),
})


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
        self.name = config['name']  # type: str
        self.command = config['command']  # type: Union[str, List[str]]
        self.schedule_unparsed = config.pop('schedule')
        if isinstance(self.schedule_unparsed, str):
            self.schedule = CronTab(self.schedule_unparsed)  # type: CronTab
        elif isinstance(self.schedule_unparsed, dict):
            minute = self.schedule_unparsed.get("minute", "*")
            hour = self.schedule_unparsed.get("hour", "*")
            day = self.schedule_unparsed.get("dayOfMonth", "*")
            month = self.schedule_unparsed.get("month", "*")
            dow = self.schedule_unparsed.get("dayOfWeek", "*")
            tab = '{} {} {} {} {}'.format(minute, hour, day, month, dow)
            logger.debug("Converted schedule to %r", tab)
            self.schedule = CronTab(tab)
        else:
            raise ValueError("invalid schedule: %r", self.schedule_unparsed)
        self.shell = config.pop('shell')
        self.concurrencyPolicy = config.pop('concurrencyPolicy')
        self.captureStderr = config.pop('captureStderr')
        self.captureStdout = config.pop('captureStdout')
        self.saveLimit = config.pop('saveLimit')
        self.failsWhen = config.pop('failsWhen')
        self.onFailure = config.pop('onFailure')
        self.onPermanentFailure = config.pop('onPermanentFailure')
        self.onSuccess = config.pop('onSuccess')
        self.environment = config.pop('environment')
        self.executionTimeout = config.pop('executionTimeout')
        self.killTimeout = config.pop('killTimeout')


def parse_config_file(path: str) -> List[JobConfig]:
    with open(path, "rt", encoding='utf-8') as stream:
        data = stream.read()
    return parse_config_string(data, path)


def parse_config_string(data: str, path: Optional[str] = None,
                        ) -> List[JobConfig]:
    try:
        doc = strictyaml.load(data, CONFIG_SCHEMA).data
    except StrictYAMLError as ex:
        if ex.context_mark is not None:
            ex.context_mark.name = path
        if ex.problem_mark is not None:
            ex.problem_mark.name = path
        raise ConfigError(str(ex))
    except YAMLError as ex:
        raise ConfigError(str(ex))

    defaults = doc.get('defaults', {})
    jobs = []
    for config_job in doc['jobs']:
        job_dict = dict(mergedicts(DEFAULT_CONFIG, defaults))
        job_dict = dict(mergedicts(job_dict, config_job))
        jobs.append(JobConfig(job_dict))
    return jobs


def parse_config(config_arg: str) -> List[JobConfig]:
    jobs = []
    config_errors = {}
    if os.path.isdir(config_arg):
        for direntry in os.scandir(config_arg):
            _, ext = os.path.splitext(direntry.name)
            if ext in {'.yml', '.yaml'}:
                try:
                    config = parse_config_file(direntry.path)
                except ConfigError as err:
                    config_errors[direntry.path] = str(err)
                except OSError as ex:
                    config_errors[config_arg] = str(ex)
                else:
                    jobs.extend(config)
    else:
        try:
            config = parse_config_file(config_arg)
        except OSError as ex:
            config_errors[config_arg] = str(ex)
        else:
            jobs.extend(config)
    if config_errors:
        raise ConfigError("\n---".join(config_errors.values()))
    return jobs
