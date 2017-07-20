import os.path
from typing import List
import logging
from typing import Union  # noqa

import yaml
import jsonschema

from crontab import CronTab

logger = logging.getLogger('yacron.config')


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
}

CONFIG_SCHEMA = yaml.load('''
type: object
properties:
  shell: {type: string}
  name: {type: string}
  concurrencyPolicy:
    type: string
    enum: ['Allow', 'Forbid', 'Replace']
  command:
    oneOf:
      - type: string
      - type: array
        items:
          type: string
  captureStderr: {type: boolean}
  captureStdout: {type: boolean}
  saveLimit:
    type: integer
    minimum: 1
    maximum: 100000
  failsWhen:
    type: object
    properties:
      producesStdout: {type: boolean}
      producesStderr: {type: boolean}
      nonzeroReturn: {type: boolean}
    additionalProperties: false
  onFailure:
    type: object
    properties:
      retry:
        type: object
        properties:
          maximumRetries:
            type: integer
          initialDelay:
            type: number
          maximumDelay:
            type: number
          backoffMultiplier:
            type: number
      report: {"$ref": "#/definitions/report"}
  onPermanentFailure:
    type: object
    properties:
      report: {"$ref": "#/definitions/report"}
  onSuccess:
    type: object
    properties:
      report: {"$ref": "#/definitions/report"}
  schedule:
    oneOf:
      - type: string
      - type: object
        properties:
          minute: {oneOf: [{type: string}, {type: integer}]}
          hour: {oneOf: [{type: string}, {type: integer}]}
          dayOfMonth: {oneOf: [{type: string}, {type: integer}]}
          month: {oneOf: [{type: string}, {type: integer}]}
          year: {oneOf: [{type: string}, {type: integer}]}
          dayOfWeek: {oneOf: [{type: string}, {type: integer}]}
        additionalProperties: false
  environment:
    type: array
    items:
      type: object
      properties:
        key: {type: string}
        value: {type: string}
required:
  - name
  - command
additionalProperties: false
definitions:
  report:
    type: object
    properties:
      sentry:
        type: object
        properties:
          dsn:
            type: object
            properties:
              value: {oneOf: [{type: string}, {type: "null"}]}
              fromFile: {oneOf: [{type: string}, {type: "null"}]}
              fromEnvVar: {oneOf: [{type: string}, {type: "null"}]}
            additionalProperties: false
      mail:
        type: object
        properties:
          from: {oneOf: [{type: string, format: email}, {type: "null"}]}
          to: {oneOf: [{type: string, format: email}, {type: "null"}]}
          smtp_host: {oneOf: [{type: string}, {type: "null"}]}
          smtp_port: {oneOf: [{type: integer}, {type: "null"}]}
          smtpHost: {oneOf: [{type: string}, {type: "null"}]}
          smtpPort: {oneOf: [{type: integer}, {type: "null"}]}
        additionalProperties: false
''')


# Check that the default config passes the schema validation
tmp = dict(DEFAULT_CONFIG)
tmp['name'] = 'foo'
tmp['command'] = 'true'
tmp['schedule'] = '* * * * *'
jsonschema.validate(tmp, CONFIG_SCHEMA)
del tmp


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

    def __init__(self, config) -> None:
        jsonschema.validate(config, CONFIG_SCHEMA)
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


def parse_config_file(path: str) -> List[JobConfig]:
    with open(path, "rb") as stream:
        doc = yaml.load(stream)
    defaults = doc['defaults']
    jobs = []
    for config_job in doc['jobs']:
        job_dict = dict(mergedicts(DEFAULT_CONFIG, defaults))
        job_dict = dict(mergedicts(job_dict, config_job))
        jobs.append(JobConfig(job_dict))
    return jobs


def parse_config(config_arg: str) -> List[JobConfig]:
    jobs = []
    if os.path.isdir(config_arg):
        for direntry in os.scandir(config_arg):
            _, ext = os.path.splitext(direntry.name)
            if ext in {'.yml', '.yaml'}:
                config = parse_config_file(direntry.path)
                jobs.extend(config)
    else:
        config = parse_config_file(config_arg)
        jobs.extend(config)
    return jobs
