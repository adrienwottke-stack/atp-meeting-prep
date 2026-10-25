"""Authenticated v1 API; no provider or privileged key is exposed to the client."""

from uuid import UUID

from fastapi import Depends, HTTPException, Request

from .memory_models import (
    BriefingFeedbackInput,
    ContextInput,
    EntryInput,
    FeedbackInput,
    ImportInput,
    ProposalInput,
    ShareInput,
)


def install_memory_routes(app, settings, services, actor):
    def store(user):
        if not settings.memory_available(user):
            raise HTTPException(
                404,
                detail={
                    "code": "MEMORY_DISABLED",
                    "message": "Wissen & Learnings ist für diesen Zugang nicht aktiviert.",
                },
            )
        return services()[0].memory()

    @app.get("/v1/memory/status")
    def status(user=Depends(actor)):
        return {"enabled": settings.memory_available(user)}

    @app.get("/v1/contexts")
    def contexts(user=Depends(actor)):
        return {"items": store(user).contexts(user)}

    @app.post("/v1/contexts", status_code=201)
    def add_context(value: ContextInput, user=Depends(actor)):
        return store(user).save_context(user, value)

    @app.get("/v1/contexts/{context_id}")
    def context(context_id: UUID, q: str = "", user=Depends(actor)):
        return store(user).context(user, str(context_id), query=q[:500])

    @app.patch("/v1/contexts/{context_id}")
    def edit_context(context_id: UUID, value: ContextInput, user=Depends(actor)):
        return store(user).save_context(user, value, str(context_id))

    @app.delete("/v1/contexts/{context_id}", status_code=202)
    def delete_context(context_id: UUID, user=Depends(actor)):
        store(user).invalidate(user, str(context_id))
        return {"status": "deletion_requested"}

    @app.post("/v1/contexts/{context_id}/shares")
    def share_context(context_id: UUID, value: ShareInput, user=Depends(actor)):
        store(user).share(user, str(context_id), str(value.user_id), value.permission)
        return {"status": "shared"}

    @app.delete("/v1/contexts/{context_id}/shares/{user_id}")
    def revoke_context(context_id: UUID, user_id: UUID, user=Depends(actor)):
        store(user).share(user, str(context_id), str(user_id))
        return {"status": "revoked"}

    @app.post("/v1/contexts/{context_id}/entries", status_code=201)
    def add_entry(context_id: UUID, value: EntryInput, user=Depends(actor)):
        return store(user).save_entry(user, str(context_id), value)

    @app.patch("/v1/contexts/{context_id}/entries/{entry_id}")
    def edit_entry(context_id: UUID, entry_id: UUID, value: EntryInput, user=Depends(actor)):
        return store(user).save_entry(user, str(context_id), value, str(entry_id))

    @app.delete("/v1/contexts/{context_id}/entries/{entry_id}", status_code=202)
    def delete_entry(context_id: UUID, entry_id: UUID, user=Depends(actor)):
        store(user).invalidate(user, str(context_id), str(entry_id))
        return {"status": "deletion_requested"}

    @app.get("/v1/memory/preview")
    def preview(
        context_ids: str = "", meeting_type: str = "setup", company_website: str = "", user=Depends(actor)
    ):
        ids = [str(UUID(x)) for x in context_ids.split(",") if x]
        if len(ids) > 2:
            raise ValueError("INVALID_INPUT")
        return store(user).snapshot(user, ids, meeting_type, company={"website": company_website[:2048]})

    def briefing_feedback_store(user):
        from .briefing_feedback import BriefingFeedbackStore

        store(user)
        return BriefingFeedbackStore(services()[0])

    @app.get("/v1/briefings/{briefing_id}/feedback")
    def briefing_feedback(briefing_id: UUID, user=Depends(actor)):
        return briefing_feedback_store(user).list(user, str(briefing_id))

    @app.post("/v1/briefings/{briefing_id}/feedback", status_code=201)
    def save_briefing_feedback(briefing_id: UUID, value: BriefingFeedbackInput, user=Depends(actor)):
        return briefing_feedback_store(user).save(user, str(briefing_id), value)

    @app.post("/v1/briefings/{briefing_id}/feedback/{feedback_id}/remember")
    def remember_briefing_feedback(briefing_id: UUID, feedback_id: UUID, user=Depends(actor)):
        return briefing_feedback_store(user).remember(user, str(briefing_id), str(feedback_id))

    @app.post("/v1/memory/feedback", status_code=201)
    def feedback(value: FeedbackInput, user=Depends(actor)):
        return store(user).feedback(user, value)

    @app.post("/v1/memory/proposals", status_code=202)
    def proposal(value: ProposalInput, request: Request, user=Depends(actor)):
        return store(user).start_task(
            user, "learning", value.model_dump(mode="json"), request.headers.get("idempotency-key")
        )

    @app.get("/v1/memory/tasks")
    def tasks(user=Depends(actor)):
        return {"items": store(user).tasks(user)}

    @app.get("/v1/memory/tasks/{task_id}")
    def task(task_id: UUID, user=Depends(actor)):
        row = store(user).tasks(user, str(task_id))
        return {
            k: v
            for k, v in row.items()
            if k not in ("lease_token", "checkpoint", "payload_hash", "idempotency_key", "pdf_path", "queue")
        }

    @app.post("/v1/memory/tasks/{task_id}/confirm")
    def confirm(task_id: UUID, value: EntryInput, user=Depends(actor)):
        return store(user).confirm_learning(user, str(task_id), value)

    @app.get("/v1/memory/export")
    def export(context_ids: str, user=Depends(actor)):
        ids = [str(UUID(x)) for x in context_ids.split(",") if x]
        if not ids or len(ids) > 20:
            raise ValueError("INVALID_INPUT")
        return store(user).export(user, ids)

    @app.post("/v1/memory/import")
    def import_memory(value: ImportInput, user=Depends(actor)):
        return store(user).import_package(
            user, value.package, {str(k): str(v) for k, v in value.mapping.items()}, value.apply
        )
