"""Common organisation capacity and fenced heartbeat for both job kinds."""

import threading


def active_organization_jobs(db, org_id, *, exclude_job=None, exclude_memory=None):
    return db.execute(
        "select (select count(*) from atp.jobs where org_id=%s and status='running' "
        "and lease_until>now() and (%s::uuid is null or id<>%s::uuid)) + "
        "(select count(*) from atp.memory_tasks where org_id=%s and status='running' "
        "and lease_until>now() and (%s::uuid is null or id<>%s::uuid)) as n",
        (org_id, exclude_job, exclude_job, org_id, exclude_memory, exclude_memory),
    ).fetchone()["n"]


class LeaseHeartbeat:
    def __init__(self, renew, lease_seconds):
        self.renew = renew
        self.interval = min(10, lease_seconds / 3)
        self.lost = threading.Event()
        self.stopped = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stopped.wait(self.interval):
            try:
                if self.renew():
                    continue
            except Exception:
                pass
            self.lost.set()
            return

    def start(self):
        self.thread.start()

    def close(self):
        self.stopped.set()
        self.thread.join(timeout=1)
