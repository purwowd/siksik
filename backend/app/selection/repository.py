from __future__ import annotations

import json
from typing import Any

from app.core.db import db
from app.selection.contracts import (
    SelectionCandidateV1,
    SelectionRunV1,
)


class SelectionRepository:
    async def begin_snapshot(self, run: SelectionRunV1) -> None:
        async with db.transaction(immediate=True) as conn:
            current = await (
                await conn.execute(
                    "SELECT selection_confirmed FROM crawl_runs WHERE crawl_id = ?",
                    (run.crawl_id,),
                )
            ).fetchone()
            if current is not None and bool(current["selection_confirmed"]):
                raise RuntimeError("confirmed selection snapshot is immutable")
            await conn.execute(
                """
                INSERT INTO crawl_runs (
                    crawl_id, session_id, state, policy_version, policy_fingerprint,
                    selection_revision, selection_fingerprint, review_candidates,
                    selection_confirmed, totals_json, started_at, updated_at,
                    frozen_at, confirmed_at, failure_reason
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(crawl_id) DO UPDATE SET
                    state = excluded.state,
                    policy_version = excluded.policy_version,
                    policy_fingerprint = excluded.policy_fingerprint,
                    selection_revision = excluded.selection_revision,
                    selection_fingerprint = excluded.selection_fingerprint,
                    review_candidates = excluded.review_candidates,
                    selection_confirmed = excluded.selection_confirmed,
                    totals_json = excluded.totals_json,
                    updated_at = excluded.updated_at,
                    frozen_at = excluded.frozen_at,
                    confirmed_at = excluded.confirmed_at,
                    failure_reason = excluded.failure_reason
                """,
                self._run_values(run),
            )
            await conn.execute(
                "DELETE FROM selection_candidates WHERE crawl_id = ?",
                (run.crawl_id,),
            )

    async def append_candidates(
        self,
        session_id: str,
        crawl_id: str,
        candidates: list[SelectionCandidateV1],
    ) -> None:
        if not candidates:
            return
        rows = [self._candidate_values(session_id, crawl_id, item) for item in candidates]
        async with db.transaction() as conn:
            await conn.executemany(
                """
                INSERT INTO selection_candidates (
                    crawl_id, session_id, record_id, source_kind, source_app,
                    evidence_text, score, threshold, auto_selected, selected,
                    matched_keywords_json, matched_rules_json, model_signals_json,
                    reasons_json, human_override, operator_id, decided_at,
                    duplicate_group_id, representative_record_id, size_bytes,
                    thumbnail_available
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                rows,
            )

    async def finish_snapshot(self, run: SelectionRunV1) -> None:
        await db.execute(
            """
            UPDATE crawl_runs SET state = ?, selection_revision = ?,
                selection_fingerprint = ?, selection_confirmed = ?, totals_json = ?,
                updated_at = ?, frozen_at = ?, confirmed_at = ?, failure_reason = ?
            WHERE crawl_id = ? AND session_id = ?
            """,
            (
                run.state,
                run.revision,
                run.selection_fingerprint,
                int(run.state == "confirmed"),
                json.dumps(run.totals.model_dump(mode="json"), separators=(",", ":")),
                run.updated_at,
                run.frozen_at,
                run.confirmed_at,
                run.failure_reason,
                run.crawl_id,
                run.siksik_session_id,
            ),
        )

    async def get_run_for_session(self, session_id: str) -> dict[str, Any]:
        row = await db.fetchone(
            "SELECT * FROM crawl_runs WHERE session_id = ?",
            (session_id,),
        )
        if row is None:
            raise KeyError("selection run not found")
        return self._run_row(row)

    async def list_candidates(
        self,
        session_id: str,
        *,
        page: int,
        page_size: int,
        source_kind: str | None,
        selected: bool | None,
        minimum_score: float | None,
    ) -> tuple[dict[str, Any], list[SelectionCandidateV1], int]:
        run = await self.get_run_for_session(session_id)
        predicates = ["session_id = ?", "crawl_id = ?"]
        parameters: list[object] = [session_id, run["crawl_id"]]
        if source_kind is not None:
            predicates.append("source_kind = ?")
            parameters.append(source_kind)
        if selected is not None:
            predicates.append("selected = ?")
            parameters.append(int(selected))
        if minimum_score is not None:
            predicates.append("score >= ?")
            parameters.append(minimum_score)
        where = " AND ".join(predicates)
        total_row = await db.fetchone(
            f"SELECT COUNT(*) AS c FROM selection_candidates WHERE {where}",
            tuple(parameters),
        )
        total = int(total_row["c"]) if total_row else 0
        offset = (page - 1) * page_size
        rows = await db.fetchall(
            f"SELECT * FROM selection_candidates WHERE {where} "
            "ORDER BY score DESC, record_id LIMIT ? OFFSET ?",
            (*parameters, page_size, offset),
        )
        return run, [self._candidate_row(row, run["selection_revision"]) for row in rows], total

    async def sync_mutation(
        self,
        run: SelectionRunV1,
        candidate: SelectionCandidateV1,
    ) -> None:
        async with db.transaction(immediate=True) as conn:
            current = await (
                await conn.execute(
                    "SELECT selection_confirmed FROM crawl_runs WHERE crawl_id = ?",
                    (run.crawl_id,),
                )
            ).fetchone()
            if current is None or bool(current["selection_confirmed"]):
                raise RuntimeError("selection snapshot is not mutable")
            await conn.execute(
                """
                UPDATE crawl_runs SET state = ?, selection_revision = ?,
                    selection_fingerprint = ?, totals_json = ?, updated_at = ?
                WHERE crawl_id = ? AND session_id = ?
                """,
                (
                    run.state,
                    run.revision,
                    run.selection_fingerprint,
                    json.dumps(run.totals.model_dump(mode="json"), separators=(",", ":")),
                    run.updated_at,
                    run.crawl_id,
                    run.siksik_session_id,
                ),
            )
            values = self._candidate_values(run.siksik_session_id, run.crawl_id, candidate)
            await conn.execute(
                """
                INSERT OR REPLACE INTO selection_candidates (
                    crawl_id, session_id, record_id, source_kind, source_app,
                    evidence_text, score, threshold, auto_selected, selected,
                    matched_keywords_json, matched_rules_json, model_signals_json,
                    reasons_json, human_override, operator_id, decided_at,
                    duplicate_group_id, representative_record_id, size_bytes,
                    thumbnail_available
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                values,
            )

    async def mark_confirmed(self, run: SelectionRunV1) -> None:
        if run.state != "confirmed" or run.selection_fingerprint is None:
            raise ValueError("confirmed selection run is invalid")
        await db.execute(
            """
            UPDATE crawl_runs SET state = 'confirmed', selection_revision = ?,
                selection_fingerprint = ?, selection_confirmed = 1, totals_json = ?,
                updated_at = ?, confirmed_at = ?
            WHERE crawl_id = ? AND session_id = ?
            """,
            (
                run.revision,
                run.selection_fingerprint,
                json.dumps(run.totals.model_dump(mode="json"), separators=(",", ":")),
                run.updated_at,
                run.confirmed_at,
                run.crawl_id,
                run.siksik_session_id,
            ),
        )

    async def candidate(
        self,
        session_id: str,
        record_id: str,
    ) -> SelectionCandidateV1:
        run = await self.get_run_for_session(session_id)
        row = await db.fetchone(
            "SELECT * FROM selection_candidates WHERE session_id = ? AND crawl_id = ? "
            "AND record_id = ?",
            (session_id, run["crawl_id"], record_id),
        )
        if row is None:
            raise KeyError("candidate not found")
        return self._candidate_row(row, run["selection_revision"])

    @staticmethod
    def _run_values(run: SelectionRunV1) -> tuple[object, ...]:
        return (
            run.crawl_id,
            run.siksik_session_id,
            run.state,
            run.policy_version,
            run.policy_fingerprint,
            run.revision,
            run.selection_fingerprint,
            int(run.review_candidates),
            int(run.state == "confirmed"),
            json.dumps(run.totals.model_dump(mode="json"), separators=(",", ":")),
            run.started_at,
            run.updated_at,
            run.frozen_at,
            run.confirmed_at,
            run.failure_reason,
        )

    @staticmethod
    def _candidate_values(
        session_id: str,
        crawl_id: str,
        item: SelectionCandidateV1,
    ) -> tuple[object, ...]:
        return (
            crawl_id,
            session_id,
            item.record_id,
            item.source_kind,
            item.source_app,
            item.evidence_text,
            item.score,
            item.threshold,
            int(item.auto_selected),
            int(item.selected),
            json.dumps(item.matched_keywords, ensure_ascii=False, separators=(",", ":")),
            json.dumps(item.matched_rules, ensure_ascii=False, separators=(",", ":")),
            json.dumps(
                [signal.model_dump(mode="json") for signal in item.model_signals],
                ensure_ascii=False,
                separators=(",", ":"),
            ),
            json.dumps(item.reasons, ensure_ascii=False, separators=(",", ":")),
            item.human_override,
            item.operator_id,
            item.decided_at,
            item.duplicate_group_id,
            item.representative_record_id,
            item.size_bytes,
            int(item.thumbnail_available),
        )

    @staticmethod
    def _run_row(row: Any) -> dict[str, Any]:
        return {
            "crawl_id": row["crawl_id"],
            "session_id": row["session_id"],
            "state": row["state"],
            "policy_version": row["policy_version"],
            "policy_fingerprint": row["policy_fingerprint"],
            "selection_revision": int(row["selection_revision"]),
            "selection_fingerprint": row["selection_fingerprint"],
            "review_candidates": bool(row["review_candidates"]),
            "selection_confirmed": bool(row["selection_confirmed"]),
            "totals": json.loads(row["totals_json"]),
            "started_at": row["started_at"],
            "updated_at": row["updated_at"],
            "frozen_at": row["frozen_at"],
            "confirmed_at": row["confirmed_at"],
            "failure_reason": row["failure_reason"],
        }

    @staticmethod
    def _candidate_row(row: Any, revision: int) -> SelectionCandidateV1:
        return SelectionCandidateV1.model_validate(
            {
                "record_id": row["record_id"],
                "source_kind": row["source_kind"],
                "source_app": row["source_app"],
                "evidence_text": row["evidence_text"],
                "score": float(row["score"]),
                "threshold": float(row["threshold"]),
                "auto_selected": bool(row["auto_selected"]),
                "selected": bool(row["selected"]),
                "matched_keywords": json.loads(row["matched_keywords_json"]),
                "matched_rules": json.loads(row["matched_rules_json"]),
                "model_signals": json.loads(row["model_signals_json"]),
                "reasons": json.loads(row["reasons_json"]),
                "human_override": row["human_override"],
                "operator_id": row["operator_id"],
                "revision": revision,
                "decided_at": row["decided_at"],
                "duplicate_group_id": row["duplicate_group_id"],
                "representative_record_id": row["representative_record_id"],
                "size_bytes": row["size_bytes"],
                "thumbnail_available": bool(row["thumbnail_available"]),
            }
        )


selection_repository = SelectionRepository()
