"""Run one OCR model evaluation in the background; called by the worker task.

The task carries only the evaluation id; data is loaded from the database and object storage here
(as with document processing). Claimed by status: anything not queued is skipped; running means a
worker died mid-run (the task is redelivered), so it is marked failed for the user to restart."""

from __future__ import annotations

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.orm import Session, sessionmaker

from jacara.core.i18n import is_stored, stored
from jacara.core.storage import ObjectStorage
from jacara.eval.ocr import EvalArm, EvalEngine, default_engine, lines_jsonl, run
from jacara.modules.hub import datasets as dataset_files
from jacara.modules.hub.models import Dataset, OcrEvalRun

log = logging.getLogger(__name__)


def _arm(raw: dict[str, object]) -> EvalArm:
    sha = raw.get("sha256")
    return EvalArm(
        name=str(raw["name"]),
        label=str(raw["label"]),
        det=str(raw["det"]) if raw.get("det") else None,
        rec=str(raw["rec"]) if raw.get("rec") else None,
        dict_path=str(raw["dict"]) if raw.get("dict") else None,
        sha256={str(k): str(v) for k, v in sha.items()} if isinstance(sha, dict) else {},
    )


def run_eval(
    run_id: uuid.UUID,
    factory: sessionmaker[Session],
    storage: ObjectStorage,
    engines: Callable[[EvalArm], EvalEngine] = default_engine,
) -> str:
    with factory() as session:
        found = session.get(OcrEvalRun, run_id)
        if found is None:
            return "missing"
        if found.status == "running":
            found.status = "failed"
            found.error = stored("上次评测在运行中被中断（worker 已停止），请重新开始")
            found.finished_at = datetime.now(UTC)
            session.commit()
            return "interrupted"
        if found.status != "queued":
            return "skipped"
        found.status = "running"
        found.started_at = datetime.now(UTC)
        session.commit()
        dataset = session.get_one(Dataset, found.dataset_id)
        key, kind, split, team = dataset.storage_key, dataset.kind, found.split, found.team_id
        arms = (_arm(found.arms[0]), _arm(found.arms[1]))
        label = f"{dataset.name} v{dataset.version}"

    def progress(text: str) -> None:
        with factory() as s:
            current = s.get_one(OcrEvalRun, run_id)
            current.progress = text[:64]
            s.commit()

    try:
        data = storage.get(key)
        parsed = dataset_files.parse(data, kind, verify_images=False)
        result = run(data, parsed, split, arms, engines, progress)
        lines_key = f"ocr-evals/{team}/{run_id}.jsonl"
        storage.put(lines_key, lines_jsonl(result), "application/x-ndjson")
    except Exception as err:  # A failed evaluation does not affect the worker; record the cause.
        log.exception("OCR 评测 %s 出错", run_id)
        with factory() as session:
            failed = session.get_one(OcrEvalRun, run_id)
            failed.status = "failed"
            message = str(err)
            failed.error = stored(
                "{kind}: {detail}",
                kind=type(err).__name__,
                detail=message if is_stored(message) else message[:300],
            )
            failed.finished_at = datetime.now(UTC)
            session.commit()
        return "failed"
    with factory() as session:
        done = session.get_one(OcrEvalRun, run_id)
        done.status = "done"
        done.summary = result.summary | {"dataset": label}
        done.lines_key = lines_key
        done.progress = ""
        done.finished_at = datetime.now(UTC)
        session.commit()
    return "done"
