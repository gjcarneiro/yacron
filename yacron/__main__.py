import argparse
import asyncio
import asyncio.subprocess
import logging
import signal
import sys

from yacron.cron import Cron, ConfigError
import yacron.version


def main_loop(loop):
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "-c", "--config", default="/etc/yacron.d", metavar="FILE-OR-DIR"
    )
    parser.add_argument("-l", "--log-level", default="INFO")
    parser.add_argument("-v", "--validate-config", default=False)
    parser.add_argument("--version", default=False, action="store_true")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    # logging.getLogger("asyncio").setLevel(logging.WARNING)
    logger = logging.getLogger("yacron")

    if args.version:
        print(yacron.version.version)
        sys.exit(0)

    try:
        cron = Cron(args.config)
    except ConfigError as err:
        logger.error("Configuration error: %s", str(err))
        sys.exit(1)

    if args.validate_config is True:
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
