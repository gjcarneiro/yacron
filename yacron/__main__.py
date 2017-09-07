import argparse
import asyncio
import asyncio.subprocess
import logging
import signal
import sys

from yacron.cron import Cron, ConfigError


def main_loop(loop):
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', "--config", default="/etc/yacron.d",
                        metavar="FILE-OR-DIR")
    parser.add_argument('-l', "--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    # logging.getLogger("asyncio").setLevel(logging.WARNING)
    logger = logging.getLogger("yacron")

    try:
        cron = Cron(args.config)
    except ConfigError as err:
        logger.error("Configuration error: %s", str(err))
        sys.exit(1)

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


if __name__ == '__main__':  # pragma: no cover
    main()
