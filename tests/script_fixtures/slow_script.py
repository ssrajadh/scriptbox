import asyncio

META = {
    "name": "Slow Script",
    "sandbox": {"timeout": 1},
}


async def run(ctx):
    await asyncio.sleep(5)
    return {"done": True}
