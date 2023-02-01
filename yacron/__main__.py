import argparse
import asyncio
import asyncio.subprocess
import logging
import logging.config
import signal
import sys
import os

import ruamel.yaml

from yacron.cron import Cron, ConfigError
import yacron.version

CONFIG_DEFAULT = "/etc/yacron.d"


def main_loop(loop):
    parser = argparse.ArgumentParser(prog="yacron")
    parser.add_argument(
        "-c",
        "--config",
        default=CONFIG_DEFAULT,
        metavar="FILE-OR-DIR",
        help="configuration file, or directory containing configuration files",
    )
    parser.add_argument("-l", "--log-level", default="INFO")
    parser.add_argument(
        "--log-config",
        required=False,
        help="logging configuration file in format as of: "
             "https://docs.python.org/3/library/logging.config.html#dictionary-schema-details"  # noqa: E501
    )
    parser.add_argument(
        "-v", "--validate-config", default=False, action="store_true"
    )
    parser.add_argument("--version", default=False, action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    logger = logging.getLogger("yacron")

    if args.version:
        print(yacron.version.version)
        sys.exit(0)

    if args.log_config:
        with open(args.log_config, "rt", encoding="utf-8") as stream:
            try:
                log_config = ruamel.yaml.safe_load(stream)
                logging.config.dictConfig(log_config)
            except ruamel.yaml.YAMLError as err:
                print("logging configuration error: " + str(err), file=sys.stderr)
                sys.exit(1)

    if args.config == CONFIG_DEFAULT and not os.path.exists(args.config):
        print(
            "yacron error: configuration file not found, please provide one "
            "with the --config option",
            file=sys.stderr,
        )
        parser.print_help(sys.stderr)
        sys.exit(1)

    try:
        cron = Cron(args.config)
    except ConfigError as err:
        logger.error("Configuration error: %s", str(err))
        sys.exit(1)

    if args.validate_config:
        logger.info("Configuration is valid.")
        sys.exit(0)

    loop.add_signal_handler(signal.SIGINT, cron.signal_shutdown)
    loop.add_signal_handler(signal.SIGTERM, cron.signal_shutdown)
    try:
        loop.run_until_complete(cron.run())
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        loop.remove_signal_handler(signal.SIGTERM)


def main():  # pragma: no cover
    if sys.platform == "win32":
        _loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(_loop)
    else:
        _loop = asyncio.get_event_loop()
    try:
        main_loop(_loop)
    finally:
        _loop.close()


if __name__ == "__main__":  # pragma: no cover
    main()
