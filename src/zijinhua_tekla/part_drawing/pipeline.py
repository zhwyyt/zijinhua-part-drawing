from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .contracts import DrawingIssue, DrawingStatus, IssueCode
from .dimension_generator import generate_dimension_intents
from .dimension_optimizer import optimize_dimension_layout
from .dimension_planner import plan_dimensions
from .drawing_output import (
    PartDrawingRenderError,
    build_part_drawing_document,
    render_part_drawing,
)
from .feature_recognizer import recognize_plate_features
from .quality import PartPositionGroup, group_part_snapshots
from .snapshot_input import load_part_snapshot, safe_part_position


@dataclass(frozen=True)
class PartDrawingItemResult:
    part_position: str
    status: DrawingStatus
    part_ids: tuple[str, ...]
    issues: tuple[DrawingIssue, ...]
    paths: dict[str, str]


@dataclass(frozen=True)
class PartDrawingBatchResult:
    items: tuple[PartDrawingItemResult, ...]
    load_issues: tuple[dict[str, str], ...]

    @property
    def ok_count(self) -> int:
        return sum(item.status == DrawingStatus.OK for item in self.items)

    @property
    def review_count(self) -> int:
        return sum(item.status == DrawingStatus.REVIEW_REQUIRED for item in self.items)

    @property
    def rejected_count(self) -> int:
        return sum(item.status == DrawingStatus.REJECTED for item in self.items) + len(self.load_issues)


def run_part_drawing_batch(
    snapshot_root: Path,
    output_root: Path,
    cjk_font_path: Path | None = None,
) -> PartDrawingBatchResult:
    loaded = []
    load_issues: list[dict[str, str]] = []
    for path in sorted(snapshot_root.glob("*.json")):
        try:
            loaded.append(load_part_snapshot(path))
        except (OSError, ValueError, json.JSONDecodeError) as exc:
            load_issues.append({
                "code": IssueCode.SNAPSHOT_INVALID.value,
                "source_path": str(path),
                "message": str(exc),
            })

    results = [_process_group(group, output_root, cjk_font_path) for group in group_part_snapshots(loaded)]
    batch = PartDrawingBatchResult(tuple(results), tuple(load_issues))
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "part-drawing-batch-summary.json").write_text(
        json.dumps(_batch_to_jsonable(batch), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return batch


def _process_group(
    group: PartPositionGroup,
    output_root: Path,
    cjk_font_path: Path | None,
) -> PartDrawingItemResult:
    if group.status == DrawingStatus.REJECTED:
        path = _write_issue_evidence(group, output_root, "conflict")
        return PartDrawingItemResult(
            group.part_position,
            DrawingStatus.REJECTED,
            tuple(snapshot.part_id for snapshot in group.snapshots),
            group.issues,
            {"evidence": str(path)},
        )

    feature_result = recognize_plate_features(group.representative, group.geometry)
    dimension_result = generate_dimension_intents(
        group.representative,
        group.geometry,
        feature_result.features,
    )
    plan = plan_dimensions(
        group.geometry,
        feature_result.features,
        dimension_result.intents,
    )
    layout = optimize_dimension_layout(
        group.representative,
        group.geometry,
        plan,
        group.quantity,
    )
    status = _aggregate_status(
        group.status,
        feature_result.status,
        dimension_result.status,
        plan.status,
        layout.status,
    )
    layout_issues = _layout_issues(group, layout)
    issues = (
        group.issues
        + feature_result.issues
        + dimension_result.issues
        + plan.issues
        + layout_issues
    )
    document = build_part_drawing_document(
        group.representative,
        group.geometry,
        feature_result.features,
        layout,
        group.quantity,
        status,
        issues,
        tuple(snapshot.part_id for snapshot in group.snapshots),
    )
    try:
        paths = render_part_drawing(
            document,
            output_root / "parts" / safe_part_position(group.part_position),
            cjk_font_path=cjk_font_path,
        )
    except (OSError, ValueError, PartDrawingRenderError) as exc:
        issue = DrawingIssue(
            IssueCode.RENDER_FAILED,
            str(exc),
            True,
            tuple(snapshot.part_id for snapshot in group.snapshots),
        )
        failed_group = PartPositionGroup(
            group.part_position,
            group.snapshots,
            group.representative,
            group.geometry,
            group.quantity,
            DrawingStatus.REJECTED,
            issues + (issue,),
        )
        evidence = _write_issue_evidence(failed_group, output_root, "render-error")
        return PartDrawingItemResult(
            group.part_position,
            DrawingStatus.REJECTED,
            tuple(snapshot.part_id for snapshot in group.snapshots),
            issues + (issue,),
            {"evidence": str(evidence)},
        )
    return PartDrawingItemResult(
        group.part_position,
        status,
        tuple(snapshot.part_id for snapshot in group.snapshots),
        issues,
        {"dxf": str(paths.dxf_path), "pdf": str(paths.pdf_path), "json": str(paths.json_path)},
    )


def _aggregate_status(*statuses: DrawingStatus) -> DrawingStatus:
    if DrawingStatus.REJECTED in statuses:
        return DrawingStatus.REJECTED
    if DrawingStatus.REVIEW_REQUIRED in statuses:
        return DrawingStatus.REVIEW_REQUIRED
    return DrawingStatus.OK


def _layout_issues(group: PartPositionGroup, layout) -> tuple[DrawingIssue, ...]:
    if not layout.unplaced_intents and not layout.collision_count:
        return ()
    return (
        DrawingIssue(
            IssueCode.LAYOUT_OVERFLOW,
            f"{len(layout.unplaced_intents)} annotations unplaced; {layout.collision_count} collisions",
            False,
            tuple(snapshot.part_id for snapshot in group.snapshots),
            tuple(_planned_item_id(item) for item in layout.unplaced_intents),
        ),
    )


def _planned_item_id(item) -> str:
    for name in ("segment_id", "callout_id", "intent_id"):
        value = getattr(item, name, None)
        if value:
            return str(value)
    return type(item).__name__


def _write_issue_evidence(group: PartPositionGroup, output_root: Path, suffix: str) -> Path:
    directory = output_root / "conflicts"
    directory.mkdir(parents=True, exist_ok=True)
    stem = safe_part_position(group.part_position)
    path = directory / f"{stem}.{suffix}.json"
    if suffix == "conflict":
        path = directory / f"{stem}.conflict.json"
    path.write_text(
        json.dumps(
            {
                "part_position": group.part_position,
                "status": DrawingStatus.REJECTED.value,
                "part_ids": [snapshot.part_id for snapshot in group.snapshots],
                "issues": [_issue_to_jsonable(issue) for issue in group.issues],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def _batch_to_jsonable(batch: PartDrawingBatchResult) -> dict[str, Any]:
    return {
        "status_counts": {
            DrawingStatus.OK.value: batch.ok_count,
            DrawingStatus.REVIEW_REQUIRED.value: batch.review_count,
            DrawingStatus.REJECTED.value: batch.rejected_count,
        },
        "items": [
            {
                "part_position": item.part_position,
                "status": item.status.value,
                "part_ids": list(item.part_ids),
                "issues": [_issue_to_jsonable(issue) for issue in item.issues],
                "paths": item.paths,
            }
            for item in batch.items
        ],
        "load_issues": list(batch.load_issues),
    }


def _issue_to_jsonable(issue: DrawingIssue) -> dict[str, Any]:
    return {
        "code": issue.code.value,
        "message": issue.message,
        "blocking": issue.blocking,
        "part_ids": list(issue.part_ids),
        "evidence": list(issue.evidence),
    }
