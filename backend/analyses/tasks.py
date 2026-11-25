"""The django-q entry point for analyses."""

from analyses import runner


def run_call(run_id, position):
    return runner.execute(run_id, position)
