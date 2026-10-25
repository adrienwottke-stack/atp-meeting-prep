"""Run with python -m atp.worker; no browser or HTTP request is required."""

import argparse
import asyncio
import logging
import socket
from uuid import uuid4

from .config import Settings
from .repository import Repository
from .storage import Storage
from .workflow import Workflow


async def work(once=False):
    settings = Settings()
    issues = settings.setup_issues()
    if issues:
        raise SystemExit("Konfiguration unvollständig: " + " ".join(issues))
    repo = Repository(settings)
    workflow = Workflow(settings, repo, Storage(settings))
    from .memory_workflow import MemoryWorkflow

    memory_workflow = MemoryWorkflow(settings, repo, Storage(settings))
    worker_id = f"{socket.gethostname()}-{uuid4()}"
    logging.info("worker_started fixture=%s", settings.fixture_mode)
    while True:
        try:
            workflow.cleanup_once()
            workflow.purge_once()
            memory_job = memory_workflow.claim()
            job = None if memory_job else repo.claim_job(worker_id)
            if memory_job:
                await memory_workflow.process(memory_job)
            elif job:
                await workflow.process(job)
            elif not once:
                await asyncio.sleep(1)
        except Exception as exc:
            logging.warning("worker_poll_failed type=%s", type(exc).__name__)
            if once:
                raise
            await asyncio.sleep(3)
        if once:
            return


def main():
    parser = argparse.ArgumentParser(description="ATP persistenter Recherche-Worker")
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(work(args.once))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
