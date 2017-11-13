import logging
import asyncio


logger = logging.getLogger('statsd')


class StatsdClientProtocol:

    def __init__(self, message, loop):
        self.message = message
        self.loop = loop
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport
        self.transport.sendto(self.message.encode())
        self.transport.close()

    def datagram_received(self, data, addr):
        pass

    def error_received(self, exc):
        logger.error('UDP error received:', exc)

    def connection_lost(self, exc):
        pass


async def send_to_statsd(host, port, message):
    loop = asyncio.get_event_loop()
    connect = loop.create_datagram_endpoint(
        lambda: StatsdClientProtocol(message, loop),
        remote_addr=(host, port))
    await connect
