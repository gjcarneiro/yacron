import argparse
import asyncio
import asyncio.subprocess
import logging
import signal
import sys

from yacron.cron import Cron


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('-c', "--config", default="/etc/yacron.d",
                        metavar="FILE-OR-DIR")
    parser.add_argument('-l', "--log-level", default="INFO")
    args = parser.parse_args()

    logging.basicConfig(level=getattr(logging, args.log_level))
    # logging.getLogger("asyncio").setLevel(logging.WARNING)

    cron = Cron(args.config)

    if sys.platform == "win32":
        loop = asyncio.ProactorEventLoop()
        asyncio.set_event_loop(loop)
    else:
        loop = asyncio.get_event_loop()

    loop.add_signal_handler(signal.SIGINT, cron.signal_shutdown)
    loop.add_signal_handler(signal.SIGTERM, cron.signal_shutdown)
    try:
        loop.run_until_complete(cron.run())
    finally:
        loop.close()


if __name__ == '__main__':
    main()
