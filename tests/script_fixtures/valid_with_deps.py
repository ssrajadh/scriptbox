META = {
    "name": "Script With Deps",
    "description": "A script that depends on another",
    "schedule": "*/5 * * * *",
    "depends_on": ["valid_basic"],
    "outputs": ["report.json"],
    "sandbox": {"network": False},
}


async def run(ctx):
    return "done"
