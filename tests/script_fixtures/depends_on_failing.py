META = {
    "name": "Depends On Failing",
    "depends_on": ["failing_script"],
}


async def run(ctx):
    return {"should": "never run"}
