"""Inspect native NC witness data. Candidate status never proves finished geometry."""

import base64
from collections import Counter
from datetime import datetime
from decimal import Decimal, InvalidOperation
import hashlib
import json
from pathlib import Path
import re
from uuid import UUID

from .native_facts import build_native_facts


def _issue(code, message):
    return {"code": code, "message": message}


def _number(token):
    match = re.fullmatch(r"([+-]?\d+(?:\.\d+)?)([a-z]?)", token)
    if not match:
        raise ValueError(f"Unsupported NC number: {token}")
    value = Decimal(match[1])
    return {"text": match[1], "modifier": match[2],
            "resolution": str(Decimal(10) ** value.as_tuple().exponent)}


def _geometry_rows(block, issues):
    rows, face = [], None
    expected = 5 if block["kind"] == "KA" else 7
    for line in block["lines"]:
        tokens = line["text"].split()
        if block["kind"] != "KA" and tokens and tokens[0] in ("v", "h", "o", "u"):
            face = tokens.pop(0)
        if len(tokens) != expected or (block["kind"] != "KA" and face is None):
            issues.append(_issue("NC_ROW_UNSUPPORTED", f"Unexpected row at line {line['lineNumber']}"))
            continue
        try:
            fields = [_number(token) for token in tokens]
        except ValueError as exc:
            issues.append(_issue("NC_ROW_UNSUPPORTED", str(exc)))
            continue
        allowed = {0: {"", "s", "u", "o"}}
        if block["kind"] == "BO":
            allowed[3] = {"", "l"}
        if any(field["modifier"] not in allowed.get(index, {""}) for index, field in enumerate(fields)):
            issues.append(_issue("NC_MODIFIER_UNSUPPORTED", f"Unknown modifier at line {line['lineNumber']}"))
        if block["kind"] in ("AK", "IK") and any(Decimal(f["text"]) != 0 for f in fields[3:]):
            issues.append(_issue("NC_MACHINING_FIELDS_UNSUPPORTED", "Nonzero bevel/extra machining fields retained"))
        rows.append({"lineNumber": line["lineNumber"], "face": face, "fields": fields})
    if not rows:
        issues.append(_issue("NC_EMPTY_GEOMETRY", f"No readable rows in {block['kind']}"))
    if block["kind"] in ("AK", "IK") and rows:
        first = (rows[0]["face"], *(Decimal(f["text"]) for f in rows[0]["fields"][:2]))
        last = (rows[-1]["face"], *(Decimal(f["text"]) for f in rows[-1]["fields"][:2]))
        if len(rows) < 3 or first != last:
            issues.append(_issue("NC_LOOP_NOT_CLOSED", f"{block['kind']} has no explicit closed contour"))
    return rows


def parse_nc(text):
    """Preserve blocks and decimal lexemes; do not convert BO coordinates to hole centers."""
    if not isinstance(text, str):
        raise ValueError("NC text required")
    blocks, issues = [], []
    for number, line in enumerate(text.splitlines(), 1):
        stripped = line.strip()
        if not stripped or stripped.startswith("**"):
            continue
        if re.fullmatch(r"[A-Z]{2}", stripped):
            blocks.append({"kind": stripped, "lineNumber": number, "lines": []})
        elif blocks:
            blocks[-1]["lines"].append({"lineNumber": number, "text": line})
        else:
            issues.append(_issue("NC_OUTSIDE_BLOCK", f"Content before ST at line {number}"))
    kinds = [b["kind"] for b in blocks]
    if not kinds or kinds[0] != "ST" or kinds[-1] != "EN" or kinds.count("ST") != 1 or kinds.count("EN") != 1:
        issues.append(_issue("NC_BLOCK_STRUCTURE_INVALID", "Exactly one leading ST and trailing EN required"))
    if kinds.count("AK") != 1:
        issues.append(_issue("NC_OUTLINE_UNSUPPORTED", "This inspector requires one AK outline"))
    header = blocks[0]["lines"] if blocks and blocks[0]["kind"] == "ST" else []
    position = header[3]["text"].strip() if len(header) >= 22 else None
    if position is None or header[7]["text"].strip() != "B":
        issues.append(_issue("NC_HEADER_UNSUPPORTED", "Plate ST header with piece identification required"))
    resolutions = []
    for block in blocks:
        kind = block["kind"]
        if kind in ("AK", "IK", "BO", "KA"):
            block["rows"] = _geometry_rows(block, issues)
            resolutions.extend(Decimal(field["resolution"]) for row in block["rows"] for field in row["fields"])
        elif kind not in ("ST", "EN"):
            issues.append(_issue("NC_BLOCK_UNSUPPORTED", f"Uninterpreted block {kind} retained"))
        if kind == "EN" and block["lines"]:
            issues.append(_issue("NC_TRAILING_DATA", "Unexpected content after EN"))
    return {"partPosition": position, "blocks": blocks, "blockCounts": dict(Counter(kinds)),
            "geometryRepresentation": "UNFOLDED" if "KA" in kinds else "NON_UNFOLDED",
            "coarsestResolution": str(max(resolutions)) if resolutions else None,
            "coordinateMeaning": "NC_FIELDS_NOT_MODEL_COORDINATES_OR_HOLE_CENTERS", "issues": issues}


def _bytes(record):
    if not isinstance(record, dict):
        raise ValueError("File evidence object required")
    try:
        data = base64.b64decode(record["bytesBase64"], validate=True)
    except (KeyError, TypeError, ValueError) as exc:
        raise ValueError("Invalid base64 file evidence") from exc
    if hashlib.sha256(data).hexdigest() != record.get("sha256"):
        raise ValueError("File evidence digest mismatch")
    return data


def _read(node):
    return node.get("value") if isinstance(node, dict) and node.get("status") == "READ" else None


def _guid(text):
    try:
        value = UUID(text)
        return str(value) if value.int else None
    except (ValueError, TypeError, AttributeError):
        return None


def _time(text):
    if not isinstance(text, str):
        raise ValueError("Timestamp required")
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Capture timestamp must include time zone")
    return value


def _capture_checks(capture, before, after, facts_before, facts_after):
    issues = []
    if (capture.get("captureStatus") != "CAPTURED" or capture.get("apiReturn") is not True
            or capture.get("beforeExitCode") != 0 or capture.get("afterExitCode") != 0
            or capture.get("captureError") or capture.get("restoreError")):
        issues.append(_issue("CAPTURE_INCOMPLETE", "Capture/API/readback did not complete"))
    if facts_before["readStatus"] != "COMPLETE" or facts_after["readStatus"] != "COMPLETE":
        issues.append(_issue("RAW_READ_INCOMPLETE", "Before/after native evidence incomplete"))
    if before.get("model") != after.get("model"):
        issues.append(_issue("MODEL_CHANGED", "Model identity changed during export"))
    ids = capture.get("requestedIds")
    if not isinstance(ids, list) or not ids or any(type(i) is not int for i in ids) or len(set(ids)) != len(ids):
        raise ValueError("Unique numeric requestedIds required")
    for raw in (before, after):
        if sorted(p["id"] for p in raw["parts"]) != sorted(ids):
            issues.append(_issue("CAPTURE_PART_SET_MISMATCH", "Native evidence differs from requested IDs"))
    if capture.get("selectionForExport") is None or sorted(capture["selectionForExport"]) != sorted(ids):
        issues.append(_issue("EXPORT_SELECTION_MISMATCH", "Export did not select exactly requested parts"))
    if (capture.get("selectionRestored") is not True or "selectionBefore" not in capture
            or "selectionAfter" not in capture or sorted(capture["selectionBefore"]) != sorted(capture["selectionAfter"])):
        issues.append(_issue("SELECTION_NOT_RESTORED", "Original selection restoration not confirmed"))
    numbering = capture.get("numbering", [])
    if sorted(n.get("id", -1) for n in numbering) != sorted(ids) or any(n.get("upToDate") is not True for n in numbering):
        issues.append(_issue("NUMBERING_UNCONFIRMED", "Current numbering required for every requested part"))
    try:
        timeline = [capture["startedAtUtc"], before["startedAtUtc"], before["completedAtUtc"],
                    capture["exportStartedAtUtc"], capture["exportCompletedAtUtc"],
                    after["startedAtUtc"], after["completedAtUtc"], capture["completedAtUtc"]]
        times = [_time(t) for t in timeline]
        if times != sorted(times):
            raise ValueError("Capture window ordering invalid")
    except (KeyError, ValueError, TypeError):
        issues.append(_issue("CAPTURE_TIMELINE_INVALID", "Before/export/after time window not confirmed"))
    return issues


def _files_and_log(capture, issues):
    settings_before = _bytes(capture.get("settingsBefore"))
    settings_after = _bytes(_read(capture.get("settingsAfter")))
    if settings_before != settings_after:
        issues.append(_issue("SETTINGS_CHANGED", "Observed settings file changed during export"))
    if capture.get("settingsBefore", {}).get("name") != capture.get("templateName", "") + ".ncfs":
        issues.append(_issue("SETTINGS_NAME_MISMATCH", "Observed settings file does not match template name"))
    record = _read(capture.get("exportLog"))
    log_bytes = _bytes(record)
    prior = capture.get("exportLogBefore")
    if not isinstance(prior, dict) or prior.get("status") != "READ":
        issues.append(_issue("EXPORT_LOG_BASELINE_MISSING", "Pre-export log baseline unavailable"))
    elif prior.get("value") is not None:
        prior_bytes = _bytes(prior["value"])
        if log_bytes.startswith(prior_bytes):
            log_bytes = log_bytes[len(prior_bytes):]
        else:
            # Overwrite is not assumed: a bounded new log must be independently established.
            issues.append(_issue("EXPORT_LOG_REPLACED", "Export log is not an append of its captured baseline"))
    log = log_bytes.decode("utf-8-sig")
    try:
        if not (_time(capture["exportStartedAtUtc"]) <= _time(record["lastWriteUtc"])
                <= _time(capture["completedAtUtc"])):
            raise ValueError("Stale export log")
    except (KeyError, ValueError):
        issues.append(_issue("EXPORT_LOG_STALE", "Log modification time outside this capture"))
    entries = []
    pattern = r"Guid:\s+ID([0-9A-Fa-f-]+)\s+Part:\s+(.+?)\s+=>\s+(.+?)\s+NC Settings:\s+(.+?)\s*$"
    for line in log.splitlines():
        match = re.search(pattern, line)
        if match:
            entries.append({"guid": _guid(match[1]), "partPosition": match[2], "filename": match[3], "template": match[4]})
        elif "Guid:" in line or "=>" in line:
            issues.append(_issue("NC_LOG_LINE_UNSUPPORTED", "Unparsed export record retained in raw log"))
    files = {}
    for record in capture.get("files", []):
        name = record.get("name")
        if not isinstance(name, str) or not name.endswith(".nc1") or name in files or "/" in name or "\\" in name:
            raise ValueError("Expected unique NC filenames")
        files[name] = {"sha256": record["sha256"], "parsed": parse_nc(_bytes(record).decode("utf-8-sig"))}
    return files, entries


def audit_nc_capture(capture, max_resolution_mm):
    """Audit witness attribution and supported NC syntax, not final geometry accuracy."""
    if not isinstance(capture, dict) or capture.get("schemaVersion") != "teklaNcCapture.v1":
        raise ValueError("Expected teklaNcCapture.v1")
    try:
        limit = Decimal(str(max_resolution_mm))
        if not limit.is_finite() or limit <= 0:
            raise ValueError("Positive finite resolution limit required")
    except InvalidOperation as exc:
        raise ValueError("Invalid resolution limit") from exc
    before = json.loads(_bytes(capture.get("before")).decode("utf-8-sig"))
    after = json.loads(_bytes(capture.get("after")).decode("utf-8-sig"))
    facts_before, facts_after = build_native_facts(before), build_native_facts(after)
    global_issues = _capture_checks(capture, before, after, facts_before, facts_after)
    files, entries = _files_and_log(capture, global_issues)
    expected_files = {_read(p.get("reports", {}).get("PART_POS")) + ".nc1" for p in before["parts"]
                      if isinstance(_read(p.get("reports", {}).get("PART_POS")), str)}
    if set(files) != expected_files or {e["filename"] for e in entries} != expected_files:
        global_issues.append(_issue("NC_FILE_SET_MISMATCH", "Export files/log differ from requested part positions"))
    after_by_id = {p["id"]: p for p in after["parts"]}
    parts = []
    for index, part in enumerate(before["parts"]):
        issues = list(global_issues)
        identity = facts_before["parts"][index]["sourceIdentity"]
        if identity["status"] != "READ_CONSISTENT":
            issues.append(_issue("INSTANCE_IDENTITY_UNCONFIRMED", "GUID, version and DSTV frame required"))
        if part != after_by_id.get(part["id"]):
            issues.append(_issue("MODEL_PART_CHANGED", "Raw part evidence changed across export; no stale witness reuse"))
        position = _read(part.get("reports", {}).get("PART_POS"))
        filename = str(position) + ".nc1"
        file = files.get(filename)
        log_entries = [entry for entry in entries if entry["filename"] == filename]
        if not log_entries:
            issues.append(_issue("NC_LOG_BINDING_MISSING", "At least one current export record per numbered part required"))
        for entry in log_entries:
            if not entry["guid"]:
                issues.append(_issue("NC_LOG_GUID_INVALID", "Export representative GUID is invalid"))
            if entry["partPosition"] != position or entry["template"] != capture.get("templateName"):
                issues.append(_issue("NC_LOG_IDENTITY_MISMATCH", "Part position/template differs from capture"))
        if file:
            parsed = file["parsed"]
            issues.extend(parsed["issues"])
            if parsed["partPosition"] != position:
                issues.append(_issue("NC_PART_POSITION_MISMATCH", "ST piece identification differs from exact PART_POS"))
            resolution = parsed["coarsestResolution"]
            if resolution is None or Decimal(resolution) > limit:
                issues.append(_issue("NC_RESOLUTION_INSUFFICIENT", "Output resolution exceeds the explicit witness comparison budget"))
        else:
            issues.append(_issue("NC_FILE_MISSING", "No NC file for this part"))
        parts.append({"id": part["id"], "partPosition": position,
                      "status": "BLOCKED" if issues else "CANDIDATE", "manufacturingStatus": "BLOCKED",
                      "associationBasis": "EXACT_PART_POSITION",
                      "instanceRelation": ("UNRESOLVED" if not log_entries or any(not e['guid'] for e in log_entries)
                                           else "SAME_INSTANCE" if all(e['guid'] == _guid(_read(part.get('guid')))
                                                                      for e in log_entries)
                                           else "NUMBERED_GROUP_REPRESENTATIVE"),
                      "geometryRepresentation": file['parsed']['geometryRepresentation'] if file else None,
                      "modelGuid": _read(part.get("guid")), "exportLogEntries": log_entries,
                      "ncFile": file, "issues": issues})
    return {"schemaVersion": "ncWitnessAudit.v1", "parts": parts, "issues": global_issues,
            "candidateCount": sum(p["status"] == "CANDIDATE" for p in parts),
            "blockedCount": sum(p["status"] == "BLOCKED" for p in parts), "manufacturingStatus": "BLOCKED",
            "settingsBinding": "UNCONFIRMED", "geometryStatus": "NOT_PROVEN",
            "maxWitnessResolutionMm": str(limit), "precisionMeaning": "Comparison budget, not manufacturing tolerance",
            "captureDigest": hashlib.sha256(json.dumps(capture, sort_keys=True, allow_nan=False).encode()).hexdigest(),
            "pendingProofs": ["effective_settings_binding", "analytic_boundary_and_thickness", "actual_hole_effect_and_orientation",
                              "operation_coverage", "instance_recheck_before_drawing"]}


def load_nc_audit(path, max_resolution_mm):
    return audit_nc_capture(json.loads(Path(path).read_text(encoding="utf-8-sig")), max_resolution_mm)
