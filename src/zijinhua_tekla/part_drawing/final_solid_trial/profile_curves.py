"""Explicit rounded-cutter surfaces backing oblique profile relief boundaries."""

import math
import numpy as np

from ..analytic_geometry import project, frame_axes, parameter
from .shell import read


def profile_curve_evidence(part, wall, shell, tolerance=0.1):
    from .supplements import _contour
    from ..native_geometry import _arc_spans

    candidates = []
    for i, operation in enumerate(part.get("operations", {}).get("value") or []):
        if operation.get("type", {}).get("value", {}).get("name") != "BOOLEAN_CUT":
            continue
        geometry = operation.get("operativeGeometry", {}).get("value")
        if not geometry:
            continue
        ref = f"parts/{part['id']}/operations/value/{i}/operativeGeometry"
        try:
            frame = read(geometry["coordinateSystem"])
        except (ValueError, KeyError, TypeError):
            continue
        arcs = []
        try:
            arcs = [s for s in _contour(geometry, frame, ref + "/modelingContour") if s["kind"] == "ARC"]
        except (ValueError, KeyError, TypeError):
            pass
        if not arcs:
            # A fillet can consume its entire neighboring edge. The existing
            # contour constructor rejects that limit; the cutter's cap still
            # gives a complete measured arc with the approved chord budget.
            try:
                for face in read(read(geometry["rawSolid"])["faces"]):
                    if abs(np.dot(read(face["normal"]), frame_axes(frame)[2])) < 1 - 1e-7:
                        continue
                    for loop in read(face["loops"]):
                        pts = [project(p, frame)[:2] for p in read(loop["vertices"])]
                        arcs.extend(
                            accepted[0] for _, _, accepted in _arc_spans(pts + pts[:1], tolerance)
                        )
            except (ValueError, KeyError, TypeError):
                pass
        candidates.extend((frame, arc, ref, operation["id"]) for arc in arcs)
    records = []
    for patch in wall["patches"]:
        face = shell["faces"][patch["faceIndex"]]
        edges = [
            e
            for e in shell["edges"]
            if face["index"] in e["faces"] and e["apiType"] not in (None, "EDGE_TYPE_NORMAL")
        ]
        if not edges:
            continue
        for frame, arc, ref, operation_id in candidates:
            if face.get("originPartId") != operation_id:
                continue
            pts = [project(p, frame)[:2] for ring in face["loops"] for p in ring]
            if not all(parameter(p, arc, tolerance) is not None for p in pts):
                continue
            radial = max(abs(math.dist(p, arc["center"]) - arc["radius"]) for p in pts)
            # Every boundary chord, including extrusion seams, must remain
            # within the same established 0.1 mm surface budget.
            chord = max(
                abs(math.dist([(a[0] + b[0]) / 2, (a[1] + b[1]) / 2], arc["center"]) - arc["radius"])
                for ring in face["loops"]
                for a, b in zip(
                    [project(p, frame)[:2] for p in ring],
                    [project(p, frame)[:2] for p in ring[1:] + ring[:1]],
                )
            )
            if max(radial, chord) > tolerance:
                continue
            axes = frame_axes(frame)
            center = (
                np.array(frame["origin"])
                + arc["center"][0] * np.array(axes[0])
                + arc["center"][1] * np.array(axes[1])
            ).tolist()
            records.append(
                dict(
                    faceIndex=face["index"],
                    wallId=wall["id"],
                    operationId=operation_id,
                    kind="CYLINDRICAL_RELIEF",
                    radius=arc["radius"],
                    axis=list(axes[2]),
                    axisPoint=center,
                    cutterFrame=frame,
                    sourceArc=arc,
                    sourceRef=ref,
                    maxRadialResidualMm=radial,
                    maxChordDeviationMm=chord,
                    observedLoops=face["loops"],
                    projection=(
                        "CIRCULAR_CYLINDER_INTERSECTION"
                        if abs(np.dot(axes[2], frame_axes(wall["frame"])[2])) > 1 - 1e-7
                        else "OBLIQUE_CYLINDER_INTERSECTION_NOT_A_CIRCULAR_ARC"
                    ),
                )
            )
            break
    proven = {r["faceIndex"] for r in records}
    filtered = dict(
        shell,
        edges=[
            dict(e, apiType="EDGE_TYPE_NORMAL") if proven.intersection(e["faces"]) else e
            for e in shell["edges"]
        ],
    )
    return records, filtered
