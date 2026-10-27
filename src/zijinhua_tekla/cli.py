import argparse
import json
from pathlib import Path

from .part_drawing.pipeline import run_part_drawing_batch
from .part_drawing.native_geometry import DEFAULT_MODEL_TOLERANCE_MM


def build_parser():
    parser = argparse.ArgumentParser(description="Tekla 2017 native part drawings.")
    sub = parser.add_subparsers(dest="command", required=True)
    draw_parts_parser = sub.add_parser("draw-parts")
    draw_parts_parser.add_argument("--snapshot-root", required=True)
    draw_parts_parser.add_argument("--out", default="outputs/part-drawings")
    draw_parts_parser.add_argument("--cjk-font", default="")
    expression_parser = sub.add_parser("plan-expressions", help="Generate flat-plate manufacturing expression requirements and audit coverage.")
    expression_parser.add_argument("--snapshot", required=True)
    expression_parser.add_argument("--context", default="")
    expression_parser.add_argument("--observations", default="")
    expression_parser.add_argument("--out", required=True)
    native_parser = sub.add_parser("read-native-facts", help="Catalog native Tekla evidence; does not authorize drawing.")
    native_parser.add_argument("--evidence", required=True)
    native_parser.add_argument("--out", required=True)
    nc_parser = sub.add_parser("audit-nc-witness", help="Inspect an NC capture; never authorizes manufacturing or drawing.")
    nc_parser.add_argument("--capture", required=True)
    nc_parser.add_argument("--max-resolution-mm", required=True)
    nc_parser.add_argument("--out", required=True)
    geometry_parser = sub.add_parser("audit-native-geometry", help="Check analytic candidates against native/NC evidence; not manufacturing approval.")
    geometry_input = geometry_parser.add_mutually_exclusive_group(required=True)
    geometry_input.add_argument("--capture")
    geometry_input.add_argument("--evidence")
    geometry_parser.add_argument("--model-tolerance-mm", default=DEFAULT_MODEL_TOLERANCE_MM,
                                 help="Model comparison tolerance in mm, inclusive (default: 0.1); preserves raw coordinates.")
    geometry_parser.add_argument("--nc-tolerance-mm")
    geometry_parser.add_argument("--max-resolution-mm")
    geometry_parser.add_argument("--hole-observations", help="Independent nativeHoleObservations.v1 JSON; not annotation plans.")
    geometry_parser.add_argument("--out", required=True)
    native_expression_parser = sub.add_parser("plan-native-expressions", help="Build requirements directly from audited native final material.")
    native_expression_input = native_expression_parser.add_mutually_exclusive_group(required=True)
    native_expression_input.add_argument("--evidence")
    native_expression_input.add_argument("--capture")
    native_expression_parser.add_argument("--model-tolerance-mm", default=DEFAULT_MODEL_TOLERANCE_MM)
    native_expression_parser.add_argument("--nc-tolerance-mm")
    native_expression_parser.add_argument("--max-resolution-mm")
    native_expression_parser.add_argument("--hole-observations")
    native_expression_parser.add_argument("--specifications", help="Explicit specifications/requiredSpecifications JSON; no evidence override.")
    native_expression_parser.add_argument("--out", required=True)
    native_drawing_parser = sub.add_parser('draw-native-parts', help='Plan, write, reopen and audit supported simple plates in Tekla 2017.')
    native_drawing_parser.add_argument('--ids', help='Authorized comma-separated model IDs; otherwise read current selection.')
    native_drawing_parser.add_argument('--reader-exe')
    native_drawing_parser.add_argument('--writer-exe')
    native_drawing_parser.add_argument('--ownership', help='Verified drawing ownership manifest; required to update existing owned sheets.')
    native_drawing_parser.add_argument('--drawing-attributes', help='Tekla attribute file for new owned drawings; existing drawings retain their template.')
    native_drawing_parser.add_argument('--nc-settings-file', help='Explicit existing NC settings file for fresh target-specific special-hole evidence.')
    native_drawing_parser.add_argument('--out', required=True)
    return parser


def _run_native_expressions(args):
    from .part_drawing.native_expressions import build_native_expression_requirements

    source, destination = Path(args.capture or args.evidence), Path(args.out)
    if destination.exists() or destination.resolve() == source.resolve():
        print('Native expressions: choose a new output path; evidence is preserved.')
        return 2
    try:
        read = lambda p: json.loads(Path(p).read_text(encoding='utf-8-sig')) if p else None
        report = build_native_expression_requirements(read(source), args.model_tolerance_mm,
                    args.nc_tolerance_mm, args.max_resolution_mm, read(args.hole_observations), read(args.specifications))
        code = 1 if report['blockedCount'] else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as exc:
        report = {'schemaVersion': 'nativeExpressionBatch.v1', 'status': 'INVALID',
                  'issues': [{'code': 'INPUT_INVALID', 'message': str(exc)}]}
        code = 2
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as stream:
        json.dump(report, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(f'Native expressions: {destination} (exit={code})')
    return code


def _run_expressions(args) -> int:
    from .part_drawing.expression_coverage import audit_expression_coverage
    from .part_drawing.expression_requirements import build_expression_requirements
    from .part_drawing.snapshot_input import load_part_snapshot

    try:
        snapshot = load_part_snapshot(Path(args.snapshot))
        context = json.loads(Path(args.context).read_text(encoding="utf-8")) if args.context else None
        observations = json.loads(Path(args.observations).read_text(encoding="utf-8")) if args.observations else None
        if observations is not None and not isinstance(observations, dict):
            raise ValueError("observations must be an object")
        plan = build_expression_requirements(snapshot, context)
        coverage = audit_expression_coverage(plan, observations)
        output = {"requirements": plan, "audit": coverage}
        code = 2 if plan["status"] == "BLOCKED" else (1 if coverage["status"] == "FAIL" else 0)
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        output = {"status": "BLOCKED", "issues": [{"code": "INPUT_INVALID", "message": str(exc)}]}
        code = 2
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(output, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    print(f"Expression report: {destination} (exit={code})")
    return code


def _run_native_facts(args) -> int:
    from .part_drawing.native_facts import load_native_facts

    destination = Path(args.out)
    if destination.resolve() == Path(args.evidence).resolve() or destination.exists():
        print("Native facts: choose a new output path; source and existing reports are preserved.")
        return 2
    try:
        output = load_native_facts(args.evidence)
        code = 0 if output["readStatus"] == "COMPLETE" else 1
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        output = {"schemaVersion": "nativePartFacts.v1", "readStatus": "INVALID", "manufacturingStatus": "BLOCKED",
                  "issues": [{"code": "INPUT_INVALID", "message": str(exc)}]}
        code = 2
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"Native evidence catalog: {destination} (exit={code}; manufacturing=BLOCKED)")
    return code


def _run_nc_witness(args) -> int:
    from .part_drawing.nc_witness import load_nc_audit

    destination = Path(args.out)
    if destination.exists() or destination.resolve() == Path(args.capture).resolve():
        print("NC witness: choose a new output path; existing evidence is preserved.")
        return 2
    try:
        output = load_nc_audit(args.capture, args.max_resolution_mm)
        code = 1 if output["blockedCount"] else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError) as exc:
        output = {"schemaVersion": "ncWitnessAudit.v1", "status": "INVALID", "manufacturingStatus": "BLOCKED",
                  "issues": [{"code": "INPUT_INVALID", "message": str(exc)}]}
        code = 2
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"NC witness audit: {destination} (exit={code}; manufacturing=BLOCKED)")
    return code


def _run_native_geometry(args) -> int:
    from .part_drawing.native_geometry import load_geometry_audit

    destination = Path(args.out)
    source = args.capture or args.evidence
    if destination.exists() or destination.resolve() == Path(source).resolve():
        print("Native geometry: choose a new output path; evidence is preserved.")
        return 2
    try:
        output = load_geometry_audit(source, args.model_tolerance_mm, args.nc_tolerance_mm,
                                     args.max_resolution_mm, args.hole_observations)
        code = 1 if output["unresolvedCount"] else 0
    except (OSError, ValueError, TypeError, KeyError, AttributeError, IndexError, OverflowError) as exc:
        output = {"schemaVersion": "nativeGeometryAudit.v1", "status": "INVALID", "manufacturingStatus": "BLOCKED",
                  "issues": [{"code": "INPUT_INVALID", "message": str(exc)}]}
        code = 2
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(output, stream, ensure_ascii=False, indent=2, allow_nan=False)
    print(f"Native geometry audit: {destination} (exit={code}; manufacturing=BLOCKED)")
    return code


def _run_draw_parts(args) -> int:
    result = run_part_drawing_batch(
        Path(args.snapshot_root),
        Path(args.out),
        Path(args.cjk_font) if args.cjk_font else None,
    )
    print(
        f"Part drawings: OK={result.ok_count} "
        f"REVIEW_REQUIRED={result.review_count} REJECTED={result.rejected_count}"
    )
    return 1 if result.rejected_count else 0


def main(argv=None):
    args = build_parser().parse_args(argv)
    if args.command == "draw-parts":
        return _run_draw_parts(args)
    if args.command == "plan-expressions":
        return _run_expressions(args)
    if args.command == "read-native-facts":
        return _run_native_facts(args)
    if args.command == "audit-nc-witness":
        return _run_nc_witness(args)
    if args.command == "audit-native-geometry":
        return _run_native_geometry(args)
    if args.command == "plan-native-expressions":
        return _run_native_expressions(args)
    if args.command == 'draw-native-parts':
        from .part_drawing.native_drawing import run_native_drawing_batch
        try:
            ids=[int(i) for i in args.ids.split(',')] if args.ids else None
            if ids is not None and (not ids or any(i<=0 for i in ids)):raise ValueError('IDs must be positive integers')
            report=run_native_drawing_batch(args.out,ids,args.reader_exe,args.writer_exe,ownership=args.ownership,drawing_attributes=args.drawing_attributes,nc_settings_file=args.nc_settings_file)
            print(f"Native drawings: PASS={report['passed']} FAIL/BLOCKED={report['failed']}")
            return 1 if report['failed'] else 0
        except (OSError,ValueError,RuntimeError) as error:
            print('Native drawings: '+str(error))
            return 2
    raise SystemExit('Unknown command')


if __name__ == "__main__":
    raise SystemExit(main())

