import asyncio


def run_async(coro):
    """Runs a coroutine to completion on a private event loop (for worker threads) and cleans up."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
