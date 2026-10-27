import base64
import copy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from test_native_facts import fixture, read
from zijinhua_tekla.cli import main
from zijinhua_tekla.part_drawing.native_facts import build_native_facts
from zijinhua_tekla.part_drawing.nc_witness import audit_nc_capture, parse_nc


GUID = "01234567-1234-5678-9abc-0123456789ab"


def timestamp(second):
    return f"2026-10-17T00:00:{second:02d}+00:00"


def file_record(name, data, second=5):
    if isinstance(data, str):
        data = data.encode("utf8")
    return {"name": name, "bytesBase64": base64.b64encode(data).decode(),
            "sha256": hashlib.sha256(data).hexdigest(), "lastWriteUtc": timestamp(second)}


def nc_text():
    header = ["order", "drawing", "phase", "TEST-1", "steel", "99", "PL10", "B"] + ["10.00"] * 14
    return "ST\n** TEST-1.nc1\n" + "\n".join(header) + "\nAK\n" + (
        "v 0.00u 0.00 0.00 0.00 0.00 0.00 0.00\n"
        "20.00 0.00 -10.00 0.00 0.00 0.00 0.00\n"
        "0.00 0.00 0.00 0.00 0.00 0.00 0.00\nEN\n")


def native_evidence():
    raw = fixture()
    raw["readerVersion"] = "native-reader.r2a"
    raw["startedAtUtc"], raw["completedAtUtc"] = timestamp(1), timestamp(2)
    part = raw["parts"][0]
    stamp = {"value": "2026-10-16T01:02:03", "kind": "Unspecified"}
    part.update(guid=read(GUID), modificationTime=read(stamp),
                identityAfterRead=read({"guid": GUID, "modificationTime": copy.deepcopy(stamp)}),
                dstvCoordinateSystem=copy.deepcopy(part["coordinateSystem"]))
    return raw


def capture_fixture():
    before = native_evidence()
    after = copy.deepcopy(before)
    after["startedAtUtc"], after["completedAtUtc"] = timestamp(6), timestamp(7)
    log = f"Guid: ID{GUID} Part: TEST-1 => TEST-1.nc1 NC Settings: test\n"
    return {"schemaVersion": "teklaNcCapture.v1", "startedAtUtc": timestamp(0), "completedAtUtc": timestamp(8),
            "before": file_record("before.json", json.dumps(before)), "after": file_record("after.json", json.dumps(after)),
            "beforeExitCode": 0, "afterExitCode": 0, "requestedIds": [10], "templateName": "test",
            "settingsBefore": file_record("test.ncfs", "synthetic settings"),
            "settingsAfter": read(file_record("test.ncfs", "synthetic settings")),
            "settingsBinding": "UNCONFIRMED", "captureStatus": "CAPTURED", "apiReturn": True,
            "selectionBefore": [], "selectionAfter": [], "selectionForExport": [10], "selectionRestored": True,
            "numbering": [{"id": 10, "upToDate": True}],
            "exportStartedAtUtc": timestamp(3), "exportCompletedAtUtc": timestamp(5),
            "exportLogBefore": read(None), "exportLog": read(file_record("dstv_nc.log", log)),
            "files": [file_record("TEST-1.nc1", nc_text())]}


class NativeIdentityTests(unittest.TestCase):
    def test_r2a_identity_is_read_consistent_not_geometry_proof(self):
        report = build_native_facts(native_evidence())
        self.assertEqual("READ_CONSISTENT", report["parts"][0]["sourceIdentity"]["status"])
        self.assertEqual("BLOCKED", report["manufacturingStatus"])

    def test_missing_identity_timestamp_or_frame_blocks(self):
        for field in ("guid", "modificationTime", "dstvCoordinateSystem", "identityAfterRead"):
            raw = native_evidence()
            del raw["parts"][0][field]
            with self.subTest(field=field):
                self.assertEqual("PARTIAL", build_native_facts(raw)["readStatus"])

    def test_change_during_read_detected(self):
        raw = native_evidence()
        raw["parts"][0]["identityAfterRead"]["value"]["guid"] = "other"
        self.assertEqual("PARTIAL", build_native_facts(raw)["readStatus"])

    def test_degenerate_frame_rejected_without_rewriting_axes(self):
        raw = native_evidence()
        raw["parts"][0]["dstvCoordinateSystem"]["value"]["axisY"] = [2, 0, 0]
        original = copy.deepcopy(raw)
        self.assertEqual("PARTIAL", build_native_facts(raw)["readStatus"])
        self.assertEqual(original, raw)

    def test_legacy_r1_does_not_gain_identity_proof(self):
        report = build_native_facts(fixture())
        self.assertEqual("UNAVAILABLE", report["parts"][0]["sourceIdentity"]["status"])

    def test_overflowing_frame_cannot_pass_consistency(self):
        raw = native_evidence()
        frame = raw["parts"][0]["dstvCoordinateSystem"]["value"]
        frame.update(axisX=[1e308, 0, 0], axisY=[0, 1e308, 0])
        self.assertEqual("BLOCKED", build_native_facts(raw)["parts"][0]["sourceIdentity"]["status"])


class NcWitnessTests(unittest.TestCase):
    def setUp(self):
        self.capture = capture_fixture()

    def report(self, limit="0.01"):
        return audit_nc_capture(self.capture, limit)

    def codes(self, limit="0.01"):
        return {i["code"] for p in self.report(limit)["parts"] for i in p["issues"]}

    def replace_nc(self, text):
        self.capture["files"] = [file_record("TEST-1.nc1", text)]

    def edit_raw(self, which, change):
        raw = json.loads(base64.b64decode(self.capture[which]["bytesBase64"]))
        change(raw)
        self.capture[which] = file_record(which + ".json", json.dumps(raw))

    def test_candidate_never_manufacturing_ready(self):
        original = copy.deepcopy(self.capture)
        report = self.report()
        self.assertEqual(1, report["candidateCount"])
        self.assertEqual("BLOCKED", report["manufacturingStatus"])
        self.assertEqual("NOT_PROVEN", report["geometryStatus"])
        self.assertEqual("UNCONFIRMED", report["settingsBinding"])
        self.assertEqual(original, self.capture)

    def test_signed_radius_and_decimal_lexemes_preserved(self):
        parsed = parse_nc(nc_text())
        field = parsed["blocks"][1]["rows"][1]["fields"][2]
        self.assertEqual({"text": "-10.00", "modifier": "", "resolution": "0.01"}, field)
        self.assertEqual("TEST-1", parsed["partPosition"])

    def test_same_position_different_instance_is_numbered_group_representative(self):
        other = GUID.replace("01234567-", "11234567-")
        log = f"Guid: ID{other} Part: TEST-1 => TEST-1.nc1 NC Settings: test\n"
        self.capture["exportLog"] = read(file_record("dstv_nc.log", log))
        part = self.report()['parts'][0]
        self.assertEqual('CANDIDATE', part['status'])
        self.assertEqual('EXACT_PART_POSITION', part['associationBasis'])
        self.assertEqual('NUMBERED_GROUP_REPRESENTATIVE', part['instanceRelation'])
        self.assertEqual(GUID, part['modelGuid'])
        self.assertEqual(other, part['exportLogEntries'][0]['guid'])

    def test_unfolding_and_unknown_blocks_not_discarded(self):
        self.replace_nc(nc_text().replace("EN\n", "KA\n1.00 2.00 3.00 4.00 -12.29\nZZ\nunknown\nEN\n"))
        self.assertIn('NC_BLOCK_UNSUPPORTED', self.codes())
        self.assertNotIn('NC_UNFOLDING_PRESENT', self.codes())
        self.assertEqual('UNFOLDED', self.report()['parts'][0]['geometryRepresentation'])
        self.assertEqual(1, self.report()["parts"][0]["ncFile"]["parsed"]["blockCounts"]["ZZ"])

    def test_raw_change_across_export_rejected(self):
        self.edit_raw("after", lambda raw: raw["parts"][0]["reports"].update(PART_POS=read("OTHER")))
        self.assertIn("MODEL_PART_CHANGED", self.codes())

    def test_model_change_rejected(self):
        self.edit_raw("after", lambda raw: raw.update(model=read({"name": "other", "path": "elsewhere"})))
        self.assertIn("MODEL_CHANGED", self.codes())

    def test_coarser_than_explicit_budget_rejected(self):
        self.assertIn("NC_RESOLUTION_INSUFFICIENT", self.codes("0.001"))
        self.assertEqual(1, self.report("0.01")["candidateCount"])

    def test_invalid_budget_rejected(self):
        for value in ("NaN", "Infinity", "-1", "0", "garbage"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                self.report(value)

    def test_digest_tampering_rejected(self):
        self.capture["files"][0]["bytesBase64"] = base64.b64encode(b"other").decode()
        with self.assertRaises(ValueError):
            self.report()

    def test_settings_change_rejected(self):
        self.capture["settingsAfter"] = read(file_record("test.ncfs", "different"))
        self.assertIn("SETTINGS_CHANGED", self.codes())

    def test_appended_log_uses_captured_prefix_only(self):
        log = base64.b64decode(self.capture["exportLog"]["value"]["bytesBase64"])
        self.capture["exportLogBefore"] = read(file_record("dstv_nc.log", log, 0))
        self.capture["exportLog"] = read(file_record("dstv_nc.log", log + log))
        self.assertEqual(1, self.report()["candidateCount"])

    def test_log_replacement_and_absent_baseline_blocked(self):
        self.capture["exportLogBefore"] = read(file_record("dstv_nc.log", "unrelated history"))
        self.assertIn("EXPORT_LOG_REPLACED", self.codes())
        del self.capture["exportLogBefore"]
        self.assertIn("EXPORT_LOG_BASELINE_MISSING", self.codes())

    def test_consistent_duplicate_log_records_are_preserved(self):
        log = base64.b64decode(self.capture["exportLog"]["value"]["bytesBase64"])
        self.capture["exportLog"] = read(file_record("dstv_nc.log", log + log))
        part = self.report()['parts'][0]
        self.assertEqual('CANDIDATE', part['status'])
        self.assertEqual(2, len(part['exportLogEntries']))

    def test_unchanged_log_cannot_reuse_old_success(self):
        self.capture["exportLogBefore"] = copy.deepcopy(self.capture["exportLog"])
        self.assertTrue({"NC_FILE_SET_MISMATCH", "NC_LOG_BINDING_MISSING"} <= self.codes())

    def test_multiple_instances_can_share_one_numbered_nc_file(self):
        other = GUID.replace('01234567-', '11234567-')
        def change(raw):
            second = copy.deepcopy(raw['parts'][0])
            second['id'] = 20
            second['guid'] = read(other)
            second['identityAfterRead']['value']['guid'] = other
            raw['parts'].append(second)
            raw['selection'].append({'requestedId': 20, 'resolved': {'id': 20}})
        for which in ('before', 'after'):
            self.edit_raw(which, change)
        self.capture.update(requestedIds=[10, 20], selectionForExport=[10, 20],
                            numbering=[{'id': i, 'upToDate': True} for i in (10, 20)])
        report = self.report()
        self.assertEqual(2, report['candidateCount'])
        self.assertEqual(1, len(self.capture['files']))
        self.assertEqual(['SAME_INSTANCE', 'NUMBERED_GROUP_REPRESENTATIVE'],
                         [p['instanceRelation'] for p in report['parts']])
        self.assertEqual(report['parts'][0]['ncFile']['sha256'], report['parts'][1]['ncFile']['sha256'])

    def test_multiple_representative_logs_require_every_record_to_agree(self):
        original = base64.b64decode(self.capture['exportLog']['value']['bytesBase64']).decode()
        second = original.replace(GUID, GUID.replace('01234567-', '11234567-'))
        self.capture['exportLog'] = read(file_record('dstv_nc.log', original+second))
        self.assertEqual(1, self.report()['candidateCount'])
        for changed in (second.replace('Part: TEST-1', 'Part: OTHER'), second.replace('Settings: test', 'Settings: other')):
            self.capture['exportLog'] = read(file_record('dstv_nc.log', original+changed))
            self.assertIn('NC_LOG_IDENTITY_MISMATCH', self.codes())

    def test_same_number_does_not_bypass_stale_numbering_or_invalid_guid(self):
        original = base64.b64decode(self.capture['exportLog']['value']['bytesBase64']).decode()
        self.capture['exportLog'] = read(file_record('dstv_nc.log', original.replace(GUID,
            '11234567-1234-5678-9abc-0123456789ab')))
        self.capture['numbering'][0]['upToDate'] = False
        self.assertIn('NUMBERING_UNCONFIRMED', self.codes())
        self.capture['numbering'][0]['upToDate'] = True
        self.capture['exportLog'] = read(file_record('dstv_nc.log', original.replace(GUID,
            '00000000-0000-0000-0000-000000000000')))
        self.assertIn('NC_LOG_GUID_INVALID', self.codes())

    def test_unfolded_nc_is_valid_manufacturing_representation(self):
        self.replace_nc(nc_text().replace('EN\n', 'KA\n1.00 2.00 3.00 4.00 -12.29\nEN\n'))
        part = self.report()['parts'][0]
        self.assertEqual('CANDIDATE', part['status'])
        self.assertEqual('UNFOLDED', part['geometryRepresentation'])

    def test_stale_log_and_capture_order_rejected(self):
        self.capture["exportLog"]["value"]["lastWriteUtc"] = timestamp(0)
        self.assertIn("EXPORT_LOG_STALE", self.codes())
        self.capture["exportStartedAtUtc"] = timestamp(7)
        self.assertIn("CAPTURE_TIMELINE_INVALID", self.codes())

    def test_selection_numbering_and_incomplete_capture_rejected(self):
        self.capture.update(selectionRestored=False, selectionForExport=[11], captureStatus="PARTIAL")
        self.capture["numbering"][0]["upToDate"] = False
        self.assertTrue({"SELECTION_NOT_RESTORED", "EXPORT_SELECTION_MISMATCH", "CAPTURE_INCOMPLETE",
                         "NUMBERING_UNCONFIRMED"} <= self.codes())

    def test_inner_contour_and_slot_rows_retained_without_center_guess(self):
        more = ("IK\nv 10.00s 20.00 5.00 0.00 0.00 0.00 0.00\n"
                "10.00 10.00 5.00 0.00 0.00 0.00 0.00\n10.00 20.00 0.00 0.00 0.00 0.00 0.00\n"
                "BO\nv 47.49s 49.99 22.00 0.00l 59.00 0.00 0.00\nEN\n")
        self.replace_nc(nc_text().replace("EN\n", more))
        parsed = self.report()["parts"][0]["ncFile"]["parsed"]
        self.assertEqual(1, parsed["blockCounts"]["IK"])
        row = parsed["blocks"][3]["rows"][0]
        self.assertEqual("47.49", row["fields"][0]["text"])
        self.assertNotIn("center", row)
        self.assertEqual(1, self.report()["candidateCount"])

    def test_header_filename_missing_or_extra_file_rejected(self):
        self.replace_nc(nc_text().replace("TEST-1", "OTHER"))
        self.assertIn("NC_PART_POSITION_MISMATCH", self.codes())
        self.capture["files"] = []
        self.assertTrue({"NC_FILE_MISSING", "NC_FILE_SET_MISMATCH"} <= self.codes())

    def test_malformed_structure_row_modifier_and_bevel_fail_closed(self):
        cases = [nc_text().replace("EN\n", ""), nc_text().replace("0.00u", "NaN"),
                 nc_text().replace("0.00u", "0.00q"), nc_text().replace("20.00 0.00 -10.00 0.00", "20.00 0.00 -10.00 1.00")]
        for text in cases:
            with self.subTest(text=text):
                self.replace_nc(text)
                self.assertEqual(1, self.report()["blockedCount"])

    def test_cli_preserves_input_and_existing_output(self):
        with tempfile.TemporaryDirectory() as directory:
            source, out = Path(directory)/"capture.json", Path(directory)/"audit.json"
            source.write_text(json.dumps(self.capture), encoding="utf8")
            args = ["audit-nc-witness", "--capture", str(source), "--max-resolution-mm", "0.01", "--out", str(out)]
            self.assertEqual(0, main(args))
            self.assertEqual("BLOCKED", json.loads(out.read_text("utf8"))["manufacturingStatus"])
            self.assertEqual(2, main(args))

    def test_cli_blocked_and_invalid_capture_exit_codes(self):
        with tempfile.TemporaryDirectory() as directory:
            source, out = Path(directory)/"capture.json", Path(directory)/"blocked.json"
            self.capture["selectionRestored"] = False
            source.write_text(json.dumps(self.capture), encoding="utf8")
            args = ["audit-nc-witness", "--capture", str(source), "--max-resolution-mm", "0.01", "--out", str(out)]
            self.assertEqual(1, main(args))
            self.assertEqual(1, json.loads(out.read_text("utf8"))["blockedCount"])
            source.write_text("{}", encoding="utf8")
            args[-1] = str(Path(directory)/"invalid.json")
            self.assertEqual(2, main(args))


if __name__ == "__main__":
    unittest.main()
