"""Independent final-solid provider; legacy audits are never invoked."""

import hashlib
import json
from pathlib import Path

from ..expression_requirements import _requirements, _pitch_candidates, GEOMETRY_TOLERANCE
from ..native_expressions import _facts
from ..section_plan import section_requirements
from .shell import read_shell, plane_regions, EvidenceError, require
from .features import interpret
from .supplements import (
    supplement_curves,
    supplement_holes,
    supplement_folded_holes,
    supplement_stepped_holes,
    shape_polygon,
)
from .steps import step_sections
from .profiles import supplement_profile, profile_expressions

VERSION = "final-solid-trial.r4"


def _part(part):
    reports = part.get("reports", {})
    position = reports.get("PART_POS", {}).get("value") or f"id:{part.get('id')}"
    result = dict(
        id=part.get("id"),
        partPosition=position,
        status="BLOCKED",
        shellStatus="BLOCKED",
        featureStatus="BLOCKED",
        partType="UNRESOLVED_SOLID",
        issues=[],
        runtimeType=part.get("runtimeType"),
        profile=part.get("profile", {}).get("value"),
        rawFinalSolid=part.get("finalSolid"),
    )
    stage = "shell"
    try:
        require(
            part.get("readStatus", "COMPLETE") == "COMPLETE",
            "PART_READ_INCOMPLETE",
            "Part read did not complete",
        )
        shell = read_shell(part)
        result.update(shellStatus="MATCHED", finalSolid=shell)
        regions = plane_regions(shell)
        result["regions"] = regions
        stage = "features"
        feature = interpret(part, shell, regions)
        result.update(feature)
        require(
            len(feature["faceRoles"]) == len(shell["faces"])
            and len({r["faceIndex"] for r in feature["faceRoles"]}) == len(shell["faces"]),
            "FACE_COVERAGE_INCOMPLETE",
            "Every source face must have exactly one role",
        )
        if feature["partType"] == "FLAT_PLATE":
            supplement_curves(part, result, shell, 0.1)
            supplement_holes(part, result)
        elif feature["partType"] == "STEPPED_PLATE":
            supplement_stepped_holes(part, result)
        elif feature["partType"] == "STRAIGHT_PROFILE":
            supplement_profile(part, result, shell)
        else:
            supplement_folded_holes(part, result)
        result.update(status="READY", featureStatus="MATCHED")
    except (ValueError, KeyError, TypeError, IndexError) as error:
        issue = (
            error.issue
            if isinstance(error, EvidenceError)
            else dict(code="EVIDENCE_INVALID", message=str(error))
        )
        result["issues"].append(dict(issue, stage=stage))
    return result


def _expressions(fact):
    issues = list(fact["issues"])
    result = dict(
        schemaVersion="expressionRequirements.v1",
        rulesVersion=VERSION,
        partPosition=fact["partPosition"],
        source=dict(partId=str(fact["id"]), partPosition=fact["partPosition"]),
        status="BLOCKED",
        issues=issues,
        facts={},
        requirements=[],
        groups=[],
        pitchCandidates=[],
        modelToleranceMm=0.1,
        tolerance=GEOMETRY_TOLERANCE,
        displayDecimals=1,
        scope="GEOMETRIC_EXPRESSION_ONLY",
        remainingGates=["INDEPENDENT_REVIEW", "PRODUCTION_ROUTE_NOT_CONNECTED"],
    )
    if fact["status"] != "READY":
        return result
    try:
        if fact["partType"] in ("FLAT_PLATE", "STEPPED_PLATE"):
            # Preserve the established callout convention for the complete
            # cutter-proof path. The measured material still records Bolt=False;
            # this adapter changes only the legacy expression origin label.
            expression_material = dict(
                fact["material"],
                holes=[
                    (
                        {k: v for k, v in h.items() if k != "fastener"}
                        if h.get("proof") == "HOLE_ONLY_CUTTER_CONFIRMED"
                        else h
                    )
                    for h in fact["material"]["holes"]
                ],
            )
            facts = _facts(expression_material)
            requirements, groups = _requirements(fact["partPosition"], fact["thickness"], facts, {})
            result.update(
                facts=facts,
                requirements=requirements,
                groups=groups,
                pitchCandidates=_pitch_candidates(facts, groups),
                materialEvidence=dict(status="MATCHED", basis=fact["material"]["basis"]),
            )
            if fact["partType"] == "STEPPED_PLATE":
                for requirement in requirements:
                    if requirement["feature"] == "plate" and requirement["aspect"] == "thickness":
                        requirement.update(aspect="maximum_thickness", rule="FINAL-STEP-01")
                for zone in fact["thicknessRegions"]:
                    for aspect, expected in (
                        (
                            "shape_and_location",
                            dict(
                                polygon=zone["polygon"], interiors=zone["interiors"], frame=fact["frame"]
                            ),
                        ),
                        ("remaining_thickness", zone["thickness"]),
                        ("cut_depth", zone["depth"]),
                        ("cut_side", zone["side"]),
                    ):
                        requirements.append(
                            dict(
                                id=zone["id"] + "/" + aspect,
                                feature=zone["id"],
                                aspect=aspect,
                                expected=expected,
                                rule="FINAL-STEP-01",
                                alternatives=["dimension", "section"],
                                sourceRefs=zone["sourceRefs"],
                            )
                        )
                for step in fact["steps"]:
                    requirements.append(
                        dict(
                            id=step["id"] + "/boundary_height",
                            feature=step["id"],
                            aspect="boundary_height",
                            expected=step,
                            rule="FINAL-STEP-01",
                            alternatives=["section"],
                            sourceRefs=step["sourceRefs"],
                        )
                    )
                for hole in fact["material"]["holes"]:
                    requirements.append(
                        dict(
                            id=hole["id"] + "/local_thickness",
                            feature=hole["id"],
                            aspect="local_thickness",
                            expected=dict(
                                region=hole["thicknessRegion"], thickness=hole["localThickness"]
                            ),
                            rule="FINAL-STEP-01",
                            alternatives=["section", "note"],
                        )
                    )
                sections = step_sections(
                    fact, [shape_polygon(h["segments"]) for h in fact["material"]["holes"]]
                )
                result.update(
                    schemaVersion="steppedExpressionRequirements.v1",
                    thicknessRegions=fact["thicknessRegions"],
                    steps=fact["steps"],
                    sectionRequirements=sections,
                    requiredViews=["main"] + [s["id"] for s in sections],
                )
            elif fact["sectionGeometry"]["bevels"]:
                sections = section_requirements(
                    fact["sectionGeometry"], fact["frame"], fact["material"]["holes"]
                )
                require(bool(sections), "BEVEL_EXPRESSION_MISSING", "Bevel requires a section")
                result.update(
                    schemaVersion="expressionRequirements.v2",
                    sectionRequirements=sections,
                    requiredViews=["main"] + [s["id"] for s in sections],
                )
        elif fact["partType"] == "STRAIGHT_PROFILE":
            result.update(profile_expressions(fact))
        else:
            requirements = []

            def add(feature, aspect, expected):
                requirements.append(
                    dict(
                        id=feature + "/" + aspect,
                        feature=feature,
                        aspect=aspect,
                        expected=expected,
                        rule="FINAL-FOLD-01",
                        alternatives=["dimension", "view", "note"],
                    )
                )

            add("plate", "identity", fact["partPosition"])
            add("plate", "thickness", fact["thickness"])
            for segment in fact["segments"]:
                add(segment["id"], "shape", dict(polygon=segment["polygon"], frame=segment["frame"]))
                add(segment["id"], "length", segment["length"])
                add(segment["id"], "width", segment["width"])
            for bend in fact["bends"]:
                for aspect in ("angle", "direction", "radius", "segments"):
                    add(bend["id"], aspect, bend[aspect])
            for end in fact["endFaces"]:
                add("end:" + end["id"], "shape", dict(loops=end["loops"], normal=end["normal"]))
            for end in fact["endCuts"]:
                for aspect in ("segment", "elevationAngle", "planAngle"):
                    add("end:" + end["id"], aspect, end[aspect])
            for hole in fact["holes"]:
                for aspect in (
                    "kind",
                    "diameter",
                    "extension",
                    "direction",
                    "center",
                    "segment",
                    "unfoldedCenter",
                ):
                    add("hole:" + hole["id"], aspect, hole[aspect])
            for aspect in ("sizeX", "sizeY", "segmentPolygons"):
                add("unfolding", aspect, fact["unfolding"][aspect])
            add("unfolding", "manufacturing_allowance", fact["unfolding"])
            result.update(
                schemaVersion="foldedExpressionRequirements.v1",
                requirements=requirements,
                manufacturingStatus="NOT_SPECIFIED",
                segmentFacts=fact["segments"],
                holes=fact["holes"],
            )
        result["status"] = "READY"
    except (ValueError, KeyError, TypeError) as error:
        result["issues"].append(
            error.issue
            if isinstance(error, EvidenceError)
            else dict(code="EXPRESSION_UNRESOLVED", message=str(error))
        )
    return result


def analyze_snapshot(snapshot):
    digest = hashlib.sha256(
        json.dumps(snapshot, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    facts = [_part(part) for part in snapshot.get("parts", [])]
    source_issues = []
    if snapshot.get("readStatus", "COMPLETE") != "COMPLETE" or snapshot.get("errors"):
        source_issues.append(dict(code="SNAPSHOT_READ_INCOMPLETE", message="Snapshot read is incomplete"))
    ids = [f["id"] for f in facts]
    if len(set(ids)) != len(ids):
        source_issues.append(dict(code="DUPLICATE_PART", message="Snapshot contains duplicate part IDs"))
    if source_issues:
        for fact in facts:
            fact["issues"].extend(source_issues)
            fact["status"] = "BLOCKED"
    expressions = [_expressions(fact) for fact in facts]
    ready = sum(e["status"] == "READY" for e in expressions)
    return dict(
        schemaVersion="finalSolidTrial.v2",
        algorithmVersion=VERSION,
        status="READY" if facts and ready == len(facts) else "PARTIAL",
        source=dict(
            schemaVersion=snapshot.get("schemaVersion"),
            inputDigest=digest,
            readerVersion=snapshot.get("readerVersion"),
            model=snapshot.get("model", {}).get("value"),
        ),
        geometryFacts=facts,
        expressionRequirements=expressions,
        summary=dict(
            parts=len(facts),
            shellMatched=sum(f["shellStatus"] == "MATCHED" for f in facts),
            geometryReady=sum(f["status"] == "READY" for f in facts),
            expressionReady=ready,
            blocked=len(facts) - ready,
        ),
    )


def write_trial(input_path, out_dir):
    input_path, out_dir = Path(input_path), Path(out_dir)
    if out_dir.exists():
        raise FileExistsError(f"output directory already exists: {out_dir}")
    snapshot = json.loads(input_path.read_text(encoding="utf-8-sig"))
    report = analyze_snapshot(snapshot)
    out_dir.mkdir(parents=True)
    for filename, key in (
        ("result.json", None),
        ("facts.json", "geometryFacts"),
        ("expressions.json", "expressionRequirements"),
    ):
        payload = (
            report
            if key is None
            else dict(schemaVersion=report["schemaVersion"], source=report["source"], parts=report[key])
        )
        (out_dir / filename).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
