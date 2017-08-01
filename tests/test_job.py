import yacron.job
import asyncio
import pytest

@pytest.mark.parametrize("save_limit, output", [
    (10, 'line1\nline2\nline3\nline4\n'),
    (1, '   [.... 3 lines discarded ...]\nline4\n'),
    (2, 'line1\n   [.... 2 lines discarded ...]\nline4\n'),
])
def test_stream_reader(save_limit, output):
    loop = asyncio.get_event_loop()
    fake_stream = asyncio.StreamReader()
    reader = yacron.job.StreamReader("cronjob-1", "stderr", fake_stream,
                                     save_limit)

    async def producer(fake_stream):
        fake_stream.feed_data(b"line1\nline2\nline3\nline4\n")
        fake_stream.feed_eof()

    _, out = loop.run_until_complete(asyncio.gather(
        producer(fake_stream),
        reader.join()))

    assert out == output
