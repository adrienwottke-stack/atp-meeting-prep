"""Authenticated FastAPI application and the small ATP web interface."""

import asyncio
import logging
from pathlib import Path
from urllib.parse import urlencode
from uuid import uuid4

import httpx
from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, ConfigDict, Field

from .budget import Budget
from .config import Settings
from .models import (
    BriefingResult,
    Clarification,
    ClarificationAnswer,
    Decision,
    MeetingInput,
    RecommendationValue,
)
from .report_notes import report_notes


class Payload(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Login(Payload):
    email: str = Field(max_length=320)
    password: str = Field(min_length=1, max_length=1024)


class Parse(Payload):
    text: str = Field(min_length=1, max_length=20000)


class Answers(Payload):
    expected_version: int = Field(ge=1)
    answers: dict[str, str | ClarificationAnswer]


class DecisionInput(Payload):
    confirmed_decision: RecommendationValue
    override_reason: str = Field(default="", max_length=2000)
    run_id: str


def create_app(settings=None):
    settings = settings or Settings()
    app = FastAPI(title="ATP Meeting-Vorbereitung", version="0.1.0", docs_url=None, redoc_url=None)
    app.state.settings = settings
    app.state.repo = None
    app.state.auth = None
    app.state.storage = None
    base = Path(__file__).parent
    (base / "static").mkdir(exist_ok=True)
    app.mount("/static", StaticFiles(directory=base / "static"), name="static")
    templates = Jinja2Templates(directory=base / "templates")

    def services():
        if settings.setup_issues():
            raise HTTPException(
                503,
                detail={
                    "code": "CONFIGURATION_REQUIRED",
                    "message": "Die lokale Einrichtung ist noch nicht vollständig. Hinweise stehen in der Betriebsanleitung.",
                },
            )
        if app.state.repo is None:
            from .auth import Auth
            from .repository import Repository
            from .storage import Storage

            app.state.repo = Repository(settings)
            app.state.auth = Auth(settings, app.state.repo)
            app.state.storage = Storage(settings)
        return app.state.repo, app.state.auth, app.state.storage

    async def actor(request: Request):
        _, auth, _ = services()
        token = request.cookies.get("atp_access", "")
        authorization = request.headers.get("authorization", "")
        if authorization.startswith("Bearer "):
            token = authorization[7:]
        if not token:
            raise HTTPException(401, detail={"code": "AUTH_REQUIRED", "message": "Bitte anmelden."})
        try:
            return await auth.authenticate(token)
        except HTTPException:
            raise
        except ValueError as exc:
            if str(exc) == "UNAUTHENTICATED":
                raise HTTPException(
                    401,
                    detail={
                        "code": "AUTH_EXPIRED",
                        "message": "Die Anmeldung ist ungültig oder abgelaufen. Bitte erneut anmelden.",
                    },
                ) from None
            raise HTTPException(
                503,
                detail={
                    "code": "AUTH_UNAVAILABLE",
                    "message": "Der Anmeldedienst ist vorübergehend nicht erreichbar. Bitte erneut versuchen.",
                },
            ) from None
        except Exception:
            raise HTTPException(
                503,
                detail={
                    "code": "AUTH_UNAVAILABLE",
                    "message": "Der Anmeldedienst ist vorübergehend nicht erreichbar. Bitte erneut versuchen.",
                },
            ) from None

    def missing():
        raise HTTPException(
            404, detail={"code": "NOT_FOUND", "message": "Die Vorbereitung ist nicht verfügbar."}
        )

    def error_response(request, detail, status):
        request_id = getattr(request.state, "request_id", str(uuid4()))
        return JSONResponse(
            {**detail, "request_id": request_id}, status, headers={"X-Request-ID": request_id}
        )

    @app.middleware("http")
    async def safety(request, call_next):
        request.state.request_id = str(uuid4())
        if request.method in ("POST", "PUT", "PATCH", "DELETE"):
            origin = request.headers.get("origin")
            # Cookie-based mutations and login must originate at this application.
            if origin is not None and origin.rstrip("/") != settings.app_base_url.rstrip("/"):
                return error_response(
                    request, {"code": "ORIGIN_REJECTED", "message": "Dieser Aufruf ist nicht zulässig."}, 403
                )
            if request.cookies.get("atp_access") and not request.headers.get("authorization") and not origin:
                return error_response(
                    request, {"code": "ORIGIN_REQUIRED", "message": "Die Herkunft des Aufrufs fehlt."}, 403
                )
            try:
                length = int(request.headers.get("content-length", 0))
            except ValueError:
                length = 250001
            if length > 250000:
                return error_response(
                    request, {"code": "INPUT_TOO_LARGE", "message": "Die Eingabe ist zu groß."}, 413
                )
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "same-origin"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers.setdefault(
            "Content-Security-Policy",
            (
                "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
            ),
        )
        if not request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        # Document navigation needs the login form; API clients keep their 401.
        if (
            exc.status_code == 401
            and request.method in ("GET", "HEAD")
            and request.url.path == "/help"
            and "text/html" in request.headers.get("accept", "")
        ):
            target = request.url.path
            if request.url.query:
                target += "?" + request.url.query
            response = RedirectResponse("/login?" + urlencode({"next": target}), status_code=303)
            response.delete_cookie("atp_access", path="/")
            return response
        detail = (
            exc.detail
            if isinstance(exc.detail, dict)
            else {"code": "REQUEST_FAILED", "message": str(exc.detail)}
        )
        detail = {**detail, "request_id": request.state.request_id}
        return error_response(request, detail, exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        fields = [".".join(map(str, e["loc"][1:])) for e in exc.errors()]
        return error_response(
            request,
            {
                "code": "VALIDATION_ERROR",
                "message": "Bitte die markierten Eingaben prüfen.",
                "fields": fields,
                "request_id": request.state.request_id,
            },
            422,
        )

    @app.exception_handler(ValueError)
    async def domain_error(request, exc):
        code = str(exc)
        status = {
            "IDEMPOTENCY_CONFLICT": 409,
            "VERSION_CONFLICT": 409,
            "RATE_LIMIT": 429,
            "MEMORY_CHANGED": 409,
            "MEMORY_DISABLED": 404,
            "NOT_FOUND": 404,
            "FORBIDDEN": 404,
        }.get(code, 422)
        messages = {
            "INVALID_ANSWER": "Bitte die Antworten prüfen. Bei Auswahlfragen eine angebotene Antwort wählen; unbekannte Angaben ausdrücklich ungeklärt lassen.",
            "INPUT_CORRECTION_REQUIRED": "Bitte die ursprüngliche Eingabe korrigieren und die Vorschau erneut bestätigen.",
            "VERSION_CONFLICT": "Der Stand wurde inzwischen geändert. Bitte neu laden.",
            "IDEMPOTENCY_CONFLICT": "Diese Startkennung wurde bereits für eine andere Eingabe verwendet.",
            "RATE_LIMIT": "Eine Vorbereitung wird bereits bearbeitet. Bitte anschließend erneut starten.",
            "MEMORY_CHANGED": "Das ausgewählte Wissen wurde geändert. Bitte die Vorbereitung mit dem aktuellen Stand neu starten.",
            "MEMORY_DISABLED": "Wissen & Learnings ist für diesen Zugang nicht aktiviert.",
        }
        safe_code = code if code in messages or code in ("NOT_FOUND", "FORBIDDEN") else "INVALID_INPUT"
        return error_response(
            request,
            {
                "code": safe_code,
                "request_id": request.state.request_id,
                "message": messages.get(code, "Der Vorgang ist mit diesen Angaben nicht möglich."),
            },
            status,
        )

    @app.exception_handler(Exception)
    async def unexpected_error(request, exc):
        logging.getLogger(__name__).error(
            "http_failed request_id=%s code=INTERNAL_ERROR type=%s",
            request.state.request_id,
            type(exc).__name__,
        )
        # Do not expose SQL/connection strings, provider errors, inputs or stack traces.
        return error_response(
            request,
            {
                "code": "SERVICE_UNAVAILABLE",
                "message": "Der Dienst ist vorübergehend nicht verfügbar. Bitte den Status prüfen und später erneut versuchen.",
            },
            503,
        )

    @app.get("/health")
    async def health():
        return {
            "status": "configuration_required" if settings.setup_issues() else "ok",
            "fixture_mode": settings.fixture_mode,
        }

    @app.get("/help")
    def help_page(user=Depends(actor)):
        from .handbook import render_handbook

        return Response(
            render_handbook(
                Path(__file__).resolve().parents[2] / "docs/handbook",
                operator=user.role == "admin",
                memory_enabled=settings.memory_available(user),
            ),
            media_type="text/html",
        )

    @app.get("/v1/handbook/pdf")
    def handbook_pdf(user=Depends(actor)):
        if user.role != "admin":
            missing()
        root = Path(__file__).resolve().parents[2]
        path = root / "handbook/ATP-Handbuch.pdf"
        if not path.exists():
            path = root / "output/handbook/ATP-Handbuch.pdf"
        if not path.exists():
            raise HTTPException(
                409,
                detail={
                    "code": "HANDBOOK_NOT_BUILT",
                    "message": "Das PDF-Handbuch wurde für diese Fassung noch nicht erzeugt.",
                },
            )
        return Response(
            path.read_bytes(),
            media_type="application/pdf",
            headers={"Content-Disposition": 'attachment; filename="ATP-Handbuch.pdf"'},
        )

    @app.get("/projects")
    def retired_projects():
        from fastapi.responses import RedirectResponse

        return RedirectResponse("/knowledge", status_code=307)

    @app.get("/")
    @app.get("/login")
    @app.get("/history")
    @app.get("/runs/{path_id}")
    @app.get("/briefings/{path_id}")
    @app.get("/knowledge")
    @app.get("/knowledge/{path_id}")
    @app.get("/memory-tasks/{path_id}")
    async def shell(request: Request, path_id: str = ""):
        return templates.TemplateResponse(
            request=request,
            name="index.html",
            context={
                "config": {
                    "fixture_mode": settings.fixture_mode,
                    "configured": not settings.setup_issues(),
                    "setup_issues": settings.setup_issues(),
                    "app_base_url": settings.app_base_url,
                    "memory_enabled": settings.memory_enabled,
                }
            },
        )

    @app.post("/v1/auth/login")
    async def login(payload: Login):
        _, auth, _ = services()
        try:
            session = await auth.login(payload.email, payload.password)
        except Exception:
            raise HTTPException(
                401,
                detail={
                    "code": "LOGIN_FAILED",
                    "message": "Die Anmeldung ist fehlgeschlagen. Bitte Zugangsdaten prüfen.",
                },
            ) from None
        response = JSONResponse({"authenticated": True})
        response.set_cookie(
            "atp_access",
            session["access_token"],
            httponly=True,
            secure=settings.app_mode == "hosted",
            samesite="strict",
            max_age=session.get("expires_in", 3600),
            path="/",
        )
        return response

    @app.post("/v1/auth/logout")
    async def logout(request: Request):
        token = request.cookies.get("atp_access")
        if token:
            try:
                async with httpx.AsyncClient(timeout=10) as client:
                    await client.post(
                        settings.supabase_url.rstrip("/") + "/auth/v1/logout?scope=local",
                        headers={"apikey": settings.supabase_anon_key, "Authorization": f"Bearer {token}"},
                    )
            except httpx.HTTPError:
                pass
        response = JSONResponse({"authenticated": False})
        response.delete_cookie("atp_access", path="/")
        return response

    @app.get("/v1/auth/me")
    def me(user=Depends(actor)):
        return {"user_id": user.user_id, "role": user.role}

    @app.post("/v1/input/parse")
    async def parse(payload: Parse, user=Depends(actor)):
        from .providers import FixtureProvider, Provider

        repo, _, _ = services()
        budget = Budget(settings)
        provider = FixtureProvider(settings) if settings.fixture_mode else Provider(settings)
        result = await asyncio.wait_for(provider.parse(payload.text, budget), timeout=budget.timeout(60))
        preview = MeetingInput.model_validate(result["preview"])
        preview.confirmed = False
        preview.public_identifiers_confirmed = False
        draft_id = await asyncio.to_thread(
            repo.save_draft,
            user,
            preview.model_dump(mode="json"),
            result.get("warnings", []),
            budget.snapshot(),
        )
        return {
            "draft_id": draft_id,
            "preview": preview.model_dump(mode="json"),
            "warnings": result.get("warnings", []),
        }

    def check_input(data):
        from .rules import clarify_input

        questions = clarify_input(data)
        blocking = [q for q in questions if q.code == "K01"]
        if blocking:
            raise HTTPException(
                422,
                detail={
                    "code": "K01",
                    "message": "Bitte den Eingabekern vervollständigen.",
                    "clarifications": [q.model_dump(mode="json") for q in blocking],
                },
            )
        if not data.confirmed or not data.public_identifiers_confirmed:
            raise HTTPException(
                422,
                detail={
                    "code": "CONFIRMATION_REQUIRED",
                    "message": "Bitte Vorschau und öffentliche Rechercheangaben bestätigen.",
                },
            )

    @app.post("/v1/preparations", status_code=202)
    def prepare(payload: MeetingInput, request: Request, user=Depends(actor)):
        from .manage import active_profile

        check_input(payload)
        key = request.headers.get("idempotency-key", "")
        if not key or len(key) > 200:
            raise HTTPException(
                422,
                detail={
                    "code": "IDEMPOTENCY_KEY_REQUIRED",
                    "message": "Die Startkennung fehlt. Bitte erneut starten.",
                },
            )
        repo, _, _ = services()
        parse_usage = None
        if draft_id := request.headers.get("x-draft-id"):
            draft = repo.get_draft(user, draft_id)
            if draft is None:
                missing()
            parse_usage = draft["usage"]
        return repo.create_preparation(
            user, payload.model_dump(mode="json"), active_profile(repo, user), key, parse_usage=parse_usage
        )

    @app.get("/v1/meetings")
    def meetings(q: str = "", user=Depends(actor)):
        return {"items": services()[0].list_meetings(user, q[:500])}

    @app.get("/v1/runs/{run_id}")
    def run(run_id: str, user=Depends(actor)):
        result = services()[0].get_run(user, run_id)
        if result is None:
            missing()
        # Deliberately expose only UI fields; not the entire internal checkpoint.
        allowed = (
            "run_id",
            "meeting_id",
            "status",
            "phase",
            "version",
            "clarifications",
            "usage",
            "briefing_id",
            "pdf_status",
            "error",
            "input",
            "result_version",
            "created_at",
            "independent_result",
        )
        response = {k: result[k] for k in allowed if k in result}
        if response.get("clarifications"):
            response["clarifications"] = [
                Clarification.model_validate(q).model_dump(mode="json")
                for q in response["clarifications"]
                if q.get("code") != "K06"
            ]
        if response.get("independent_result"):
            preview = response["independent_result"]
            response["independent_result"] = {**preview, "report_notes": report_notes(preview)}
        return response

    @app.post("/v1/runs/{run_id}/answers")
    def answer(run_id: str, payload: Answers, user=Depends(actor)):
        return services()[0].answer(user, run_id, payload.expected_version, payload.answers)

    @app.get("/v1/briefings/{briefing_id}")
    def briefing(briefing_id: str, user=Depends(actor)):
        result = services()[0].get_briefing(user, briefing_id)
        if result is None:
            missing()
        from .briefing_content import attention_notes, presentation_overview, reader_result
        from .models import BriefingResult

        view = reader_result(
            BriefingResult.model_validate(
                {k: v for k, v in result.items() if k in BriefingResult.model_fields}
            )
        )
        return {
            **result,
            "sources": [source.model_dump(mode="json") for source in view.sources],
            "report_notes": report_notes(result),
            "reader_view": {
                "overview": [item.model_dump(mode="json") for item in presentation_overview(view)],
                "attention": attention_notes(view),
            },
        }

    @app.get("/v1/briefings/{briefing_id}/pdf")
    def pdf(briefing_id: str, compact: bool = False, user=Depends(actor)):
        repo, _, storage = services()
        record = repo.get_briefing(user, briefing_id)
        if record is None:
            missing()
        if compact:
            from .pdf import render_pdf

            result = BriefingResult.model_validate(
                {k: v for k, v in record.items() if k in BriefingResult.model_fields}
            )
            return Response(
                render_pdf(result, compact=True),
                media_type="application/pdf",
                headers={"Content-Disposition": f'attachment; filename="ATP-Kurzbriefing-{briefing_id}.pdf"'},
            )
        path = record.get("pdf_path")
        if not path:
            raise HTTPException(
                409,
                detail={
                    "code": "PDF_NOT_READY",
                    "message": "Das PDF ist noch nicht verfügbar. Bitte Export erneut versuchen.",
                },
            )
        content = storage.get_pdf(path)
        return Response(
            content,
            media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="ATP-Briefing-{briefing_id}.pdf"'},
        )

    @app.get("/v1/briefings/{briefing_id}/html")
    def html_download(briefing_id: str, user=Depends(actor)):
        from .html_export import render_html

        record = services()[0].get_briefing(user, briefing_id)
        if record is None:
            missing()
        result = BriefingResult.model_validate(
            {k: v for k, v in record.items() if k in BriefingResult.model_fields}
        )
        return Response(
            render_html(result),
            media_type="text/html; charset=utf-8",
            headers={
                "Content-Disposition": f'attachment; filename="ATP-Briefing-{briefing_id}.html"',
                "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; font-src data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'",
            },
        )

    @app.post("/v1/briefings/{briefing_id}/pdf/retry")
    def retry_pdf(briefing_id: str, user=Depends(actor)):
        from .pdf import render_pdf

        repo, _, storage = services()
        record = repo.get_briefing(user, briefing_id)
        if record is None:
            missing()
        if not repo.can_edit(user, record["meeting_id"]):
            missing()
        result = BriefingResult.model_validate(
            {k: v for k, v in record.items() if k in BriefingResult.model_fields}
        )
        try:
            content = render_pdf(result)

            def publish():
                with repo.storage_lock(result.meeting_id):
                    fresh = repo.get_briefing(user, briefing_id)
                    if fresh is None or not repo.can_edit(user, result.meeting_id):
                        raise ValueError("NOT_FOUND")
                    path = f"{user.org_id}/{result.meeting_id}/{result.run_id}/result.pdf"
                    try:
                        path = storage.put_pdf(user.org_id, result.meeting_id, result.run_id, content)
                        repo.retry_pdf(user, briefing_id, path, None)
                    except Exception:
                        try:
                            storage.delete_pdf(path)
                        except Exception:
                            repo.enqueue_storage_cleanup(path, user.org_id, result.meeting_id, result.run_id)
                        # Publish both success and error while holding the same lock.
                        # A late error from this retry cannot overwrite a newer success.
                        repo.retry_pdf(user, briefing_id, None, "PDF_EXPORT_FAILED")
                        raise

            publish()
        except Exception:
            raise HTTPException(
                503,
                detail={
                    "code": "PDF_EXPORT_FAILED",
                    "message": "Der PDF-Export ist fehlgeschlagen. Das Briefing bleibt verfügbar.",
                },
            ) from None
        return {"pdf_status": "ready"}

    @app.post("/v1/meetings/{meeting_id}/runs", status_code=202)
    def new_run(meeting_id: str, payload: MeetingInput, request: Request, user=Depends(actor)):
        from .manage import active_profile

        check_input(payload)
        repo = services()[0]
        key = request.headers.get("idempotency-key", "")
        if not key.strip() or len(key) > 200:
            raise HTTPException(
                422,
                detail={
                    "code": "IDEMPOTENCY_KEY_REQUIRED",
                    "message": "Die Startkennung fehlt. Bitte erneut starten.",
                },
            )
        return repo.new_run(
            user, meeting_id, payload.model_dump(mode="json"), active_profile(repo, user), key
        )

    @app.post("/v1/meetings/{meeting_id}/decision")
    def decide(meeting_id: str, payload: DecisionInput, user=Depends(actor)):
        repo, _, _ = services()
        run = repo.get_run(user, payload.run_id)
        if run is None or run["meeting_id"] != meeting_id or not run.get("briefing_id"):
            missing()
        record = repo.get_briefing(user, run["briefing_id"])
        original = record["recommendation"]["value"]
        if payload.confirmed_decision != original and not payload.override_reason.strip():
            raise HTTPException(
                422,
                detail={
                    "code": "OVERRIDE_REASON_REQUIRED",
                    "message": "Bitte die abweichende Entscheidung begründen.",
                },
            )
        decision = Decision(
            original_recommendation=original,
            confirmed_decision=payload.confirmed_decision,
            decided_by=user.user_id,
            override_reason=payload.override_reason,
        )
        repo.save_decision(user, meeting_id, {**decision.model_dump(mode="json"), "run_id": payload.run_id})
        return decision

    @app.delete("/v1/meetings/{meeting_id}", status_code=202)
    def delete(meeting_id: str, user=Depends(actor)):
        services()[0].delete_meeting(user, meeting_id)
        return {"status": "deletion_requested"}

    @app.post("/v1/hooks/booking/{source}")
    def hook(source: str):
        # Intentionally closed until a real source and signature protocol are approved.
        raise HTTPException(
            503,
            detail={
                "code": "INTEGRATION_NOT_ACTIVE",
                "message": "Dieser Anschluss ist noch nicht aktiviert.",
            },
        )

    from .memory_web import install_memory_routes

    install_memory_routes(app, settings, services, actor)
    return app


app = create_app()
