"""Seed pre-upgrade rows atomically, even when the local test DB is already migrated."""

from contextlib import contextmanager


@contextmanager
def historical_tasks(admin):
    # The table lock prevents another connection observing a temporarily relaxed constraint.
    with admin.transaction():
        exists = admin.execute(
            "select exists(select 1 from pg_constraint where conrelid='atp.memory_tasks'::regclass "
            "and conname='memory_tasks_learning_only')"
        ).fetchone()[0]
        if exists:
            admin.execute("alter table atp.memory_tasks drop constraint memory_tasks_learning_only")
        yield
        if exists:
            admin.execute(
                "alter table atp.memory_tasks add constraint memory_tasks_learning_only check(kind='learning') not valid"
            )
