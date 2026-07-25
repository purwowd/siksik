from __future__ import annotations

from typing import NoReturn

from fastapi import HTTPException

from app.acquisition.agent_client import AgentClient, AgentClientConfig
from app.acquisition.errors import AcquisitionError, ErrorCategory
from app.acquisition.runtime import agent_runtime_registry
from app.core.config import settings
from app.core.db import db
from app.core.request_context import current_request_id
from app.selection.contracts import (
    CandidateConfirmationResponse,
    CandidateListResponse,
    CandidateMutationResponse,
    HumanOverride,
    SelectionRunV1,
)
from app.selection.repository import selection_repository
from app.services.auth import AuthUser, Role


class SelectionReviewService:
    async def crawl(self, session_id: str, user: AuthUser) -> SelectionRunV1:
        await self._authorize_owner(session_id, user)
        return self._run_model(await selection_repository.get_run_for_session(session_id))

    async def list_candidates(
        self,
        session_id: str,
        user: AuthUser,
        *,
        page: int,
        page_size: int,
        source_kind: str | None,
        selected: bool | None,
        minimum_score: float | None,
    ) -> CandidateListResponse:
        await self._authorize_owner(session_id, user)
        run, items, total = await selection_repository.list_candidates(
            session_id,
            page=page,
            page_size=page_size,
            source_kind=source_kind,
            selected=selected,
            minimum_score=minimum_score,
        )
        pages = max(1, (total + page_size - 1) // page_size)
        bounded_page = min(page, pages)
        if bounded_page != page:
            run, items, total = await selection_repository.list_candidates(
                session_id,
                page=bounded_page,
                page_size=page_size,
                source_kind=source_kind,
                selected=selected,
                minimum_score=minimum_score,
            )
        return CandidateListResponse(
            session_id=session_id,
            crawl_id=run["crawl_id"],
            state=run["state"],
            revision=run["selection_revision"],
            selection_fingerprint=run["selection_fingerprint"],
            policy_version=run["policy_version"],
            policy_fingerprint=run["policy_fingerprint"],
            items=items,
            page=bounded_page,
            page_size=page_size,
            total=total,
            pages=pages,
        )

    async def mutate_candidate(
        self,
        session_id: str,
        record_id: str,
        user: AuthUser,
        *,
        expected_revision: int,
        override: HumanOverride,
    ) -> CandidateMutationResponse:
        session = await self._authorize_owner(session_id, user)
        run = await selection_repository.get_run_for_session(session_id)
        if run["selection_confirmed"]:
            raise HTTPException(
                status_code=409,
                detail="Selection yang dikonfirmasi tidak dapat diubah.",
            )
        if session["status"] != "awaiting_review" or run["state"] != "awaiting_review":
            raise HTTPException(status_code=409, detail="Selection belum siap direview.")
        client = await self._client(session_id)
        try:
            mutation = (
                await client.mutate_selection_candidate(
                    session_id,
                    run["crawl_id"],
                    record_id,
                    expected_revision=expected_revision,
                    override=override,
                    operator_id=user.id,
                    request_id=current_request_id(),
                )
            ).body
        except AcquisitionError as exc:
            self._raise_agent_error(exc)
        await selection_repository.sync_mutation(mutation.run, mutation.candidate)
        return CandidateMutationResponse(
            state=mutation.run.state,
            revision=mutation.run.revision,
            selection_fingerprint=self._required_fingerprint(mutation.run),
            candidate=mutation.candidate,
        )

    async def confirm(
        self,
        session_id: str,
        user: AuthUser,
        *,
        expected_revision: int,
    ) -> CandidateConfirmationResponse:
        session = await self._authorize_owner(session_id, user)
        stored = await selection_repository.get_run_for_session(session_id)
        if stored["selection_revision"] != expected_revision:
            raise HTTPException(status_code=409, detail="Revision selection telah berubah.")
        if stored["selection_confirmed"]:
            fingerprint = stored["selection_fingerprint"]
            confirmed_at = stored["confirmed_at"]
            if fingerprint is None or confirmed_at is None:
                raise HTTPException(status_code=500, detail="Snapshot selection tidak valid.")
            return CandidateConfirmationResponse(
                state="confirmed",
                revision=stored["selection_revision"],
                selection_fingerprint=fingerprint,
                confirmed_at=confirmed_at,
            )
        if session["status"] != "awaiting_review" or stored["state"] != "awaiting_review":
            raise HTTPException(status_code=409, detail="Selection belum siap dikonfirmasi.")
        client = await self._client(session_id)
        try:
            run = (
                await client.confirm_selection(
                    session_id,
                    stored["crawl_id"],
                    expected_revision=expected_revision,
                    request_id=current_request_id(),
                )
            ).body
        except AcquisitionError as exc:
            self._raise_agent_error(exc)
        await selection_repository.mark_confirmed(run)
        return CandidateConfirmationResponse(
            state="confirmed",
            revision=run.revision,
            selection_fingerprint=self._required_fingerprint(run),
            confirmed_at=self._required_confirmed_at(run),
        )

    async def _authorize_owner(self, session_id: str, user: AuthUser):
        session = await db.fetchone(
            "SELECT id, status, created_by FROM sessions WHERE id = ?",
            (session_id,),
        )
        if session is None:
            raise HTTPException(status_code=404, detail="Session not found")
        if user.role != Role.ADMIN and session["created_by"] != user.id:
            raise HTTPException(status_code=403, detail="Akses candidate sesi ditolak.")
        return session

    async def _client(self, session_id: str) -> AgentClient:
        try:
            runtime = await agent_runtime_registry.get(session_id)
        except AcquisitionError as exc:
            self._raise_agent_error(exc)
        return AgentClient(
            runtime.forward_host_port,
            runtime.token,
            config=AgentClientConfig(
                timeout_seconds=settings.android_agent_request_timeout_s,
                max_attempts=settings.android_agent_request_attempts,
                max_response_bytes=settings.android_agent_max_response_mb * 1024 * 1024,
            ),
        )

    @staticmethod
    def _run_model(value: dict) -> SelectionRunV1:
        return SelectionRunV1.model_validate(
            {
                "schema_version": 1,
                "crawl_id": value["crawl_id"],
                "siksik_session_id": value["session_id"],
                "state": value["state"],
                "policy_version": value["policy_version"],
                "policy_fingerprint": value["policy_fingerprint"],
                "revision": value["selection_revision"],
                "selection_fingerprint": value["selection_fingerprint"],
                "review_candidates": value["review_candidates"],
                "totals": value["totals"],
                "started_at": value["started_at"],
                "updated_at": value["updated_at"],
                "frozen_at": value["frozen_at"],
                "confirmed_at": value["confirmed_at"],
                "failure_reason": value["failure_reason"],
            }
        )

    @staticmethod
    def _required_fingerprint(run: SelectionRunV1) -> str:
        if run.selection_fingerprint is None:
            raise HTTPException(status_code=502, detail="Fingerprint selection tidak tersedia.")
        return run.selection_fingerprint

    @staticmethod
    def _required_confirmed_at(run: SelectionRunV1) -> str:
        if run.confirmed_at is None:
            raise HTTPException(status_code=502, detail="Waktu konfirmasi tidak tersedia.")
        return run.confirmed_at

    @staticmethod
    def _raise_agent_error(error: AcquisitionError) -> NoReturn:
        status = {
            ErrorCategory.NOT_FOUND: 404,
            ErrorCategory.VALIDATION_ERROR: 422,
            ErrorCategory.CONFLICT: 409,
            ErrorCategory.AGENT_AUTH_INVALID: 401,
            ErrorCategory.AGENT_SESSION_MISMATCH: 409,
        }.get(error.category, 502)
        raise HTTPException(status_code=status, detail=error.public_message) from error


selection_review_service = SelectionReviewService()
