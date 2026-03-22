META = {
    "name": "Depends On Pass",
    "depends_on": ["pass_through"],
}


async def run(ctx):
    value = ctx.inputs["pass_through"]["data"]
    return {"received": value}
