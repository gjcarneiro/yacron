import os.path
from typing import List

import yaml
from crontab import CronTab


BUILTIN_DEFAULTS = {
    'shell': None,
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
        'report': {
            'sentry': {
                'dsn': {
                    'value': None,
                    'fromFile': None,
                    'fromEnvVar': None,
                },
            },
            'mail': {
                'sender': None,
                'recipient': None,
                'smtp_host': None,
                'smtp_port': 25,
            },
        },
    },
    'environment': [],
}


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
        self.name = config['name']  # type: str
        self.command = config['command']  # type: Union[str, List[str]]
        self.schedule_unparsed = config.pop('schedule')  # type: str
        self.schedule = CronTab(self.schedule_unparsed)  # type: CronTab
        self.shell = config.pop('shell')
        self.concurrencyPolicy = config.pop('concurrencyPolicy')
        self.captureStderr = config.pop('captureStderr')
        self.captureStdout = config.pop('captureStdout')
        self.saveLimit = config.pop('saveLimit')
        self.failsWhen = config.pop('failsWhen')
        self.onFailure = config.pop('onFailure')
        self.environment = config.pop('environment')

    def get_sentry_dsn(self):
        dsn_dict = self.onFailure['report']['sentry']['dsn']
        if dsn_dict['value']:
            return dsn_dict['value']
        elif dsn_dict['fromFile']:
            with open(dsn_dict['fromFile'], "rt") as dsn_file:
                return dsn_file.read().strip()
        elif dsn_dict['fromEnvVar']:
            return os.environ[dsn_dict['fromEnvVar']]
        return None


def parse_config_file(path: str) -> List[JobConfig]:
    with open(path, "rb") as stream:
        doc = yaml.load(stream)
    defaults = doc['defaults']
    jobs = []
    for config_job in doc['jobs']:
        job_dict = dict(mergedicts(BUILTIN_DEFAULTS, defaults))
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
