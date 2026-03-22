META = {"name": "Failing Script"}


async def run(ctx):
    raise RuntimeError("intentional failure")
