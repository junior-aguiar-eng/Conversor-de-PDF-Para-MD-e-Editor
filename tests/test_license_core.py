"""Gate do protocolo criptográfico ACT4."""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from license_core import (
    LICENSE_FILE_FORMAT,
    LICENSE_SCHEMA,
    LicenseExpiredError,
    LicenseFormatError,
    LicenseNotYetValidError,
    LicensePayload,
    MachineMismatchError,
    PublicKeyRing,
    SignatureVerificationError,
    UnknownKeyError,
    UnsupportedSchemaError,
    canonicalize_payload,
    issue_license,
    load_license_file,
    parse_license,
    verify_license,
)


class Act4ProtocolTests(unittest.TestCase):
    def setUp(self) -> None:
        self.private_key = Ed25519PrivateKey.generate()
        self.key_id = "license-main-2026-01"
        self.key_ring = PublicKeyRing({self.key_id: self.private_key.public_key()})
        self.payload = LicensePayload(
            key_id=self.key_id,
            license_id="LIC-2026-000001",
            revision=1,
            machine_id="NXJ2-1111-2222-3333-4444",
            issued_at="2026-09-01T00:00:00Z",
            not_before="2026-09-01T00:00:00Z",
            expires_at="2027-09-01T00:00:00Z",
            features=("converter", "ocr", "reader"),
            customer_reference="CLI-000001",
        )
        self.valid_at = datetime(2026, 10, 1, tzinfo=UTC)

    def issue(self, payload: LicensePayload | None = None) -> bytes:
        return issue_license(payload or self.payload, self.private_key)

    def test_canonical_payload_is_independent_of_input_key_order(self) -> None:
        mapping = self.payload.to_mapping()
        reverse_mapping = dict(reversed(mapping.items()))
        self.assertEqual(canonicalize_payload(mapping), canonicalize_payload(reverse_mapping))
        self.assertEqual(canonicalize_payload(mapping), canonicalize_payload(self.payload))
        self.assertNotIn(b" ", canonicalize_payload(mapping))

    def test_issue_parse_and_verify_roundtrip(self) -> None:
        document = self.issue()
        parsed_payload, signature = parse_license(document)
        self.assertEqual(parsed_payload, self.payload)
        self.assertEqual(len(signature), 64)
        self.assertEqual(
            verify_license(
                document,
                self.key_ring,
                expected_machine_id=self.payload.machine_id,
                at=self.valid_at,
            ),
            self.payload,
        )
        self.assertTrue(document.endswith(b"\n"))

    def test_rejects_payload_tampering(self) -> None:
        value = json.loads(self.issue())
        value["payload"]["expires_at"] = "2028-09-01T00:00:00Z"
        tampered = json.dumps(value, separators=(",", ":")).encode()
        with self.assertRaises(SignatureVerificationError):
            verify_license(tampered, self.key_ring, at=self.valid_at)

    def test_rejects_signature_tampering(self) -> None:
        value = json.loads(self.issue())
        signature = value["signature"]
        value["signature"] = ("A" if signature[0] != "A" else "B") + signature[1:]
        with self.assertRaises(SignatureVerificationError):
            verify_license(json.dumps(value), self.key_ring, at=self.valid_at)

    def test_rejects_machine_mismatch(self) -> None:
        with self.assertRaises(MachineMismatchError):
            verify_license(
                self.issue(),
                self.key_ring,
                expected_machine_id="NXJ2-AAAA-BBBB-CCCC-DDDD",
                at=self.valid_at,
            )

    def test_date_boundaries_are_inclusive_then_exclusive(self) -> None:
        verify_license(self.issue(), self.key_ring, at=datetime(2026, 9, 1, tzinfo=UTC))
        with self.assertRaises(LicenseNotYetValidError):
            verify_license(self.issue(), self.key_ring, at=datetime(2026, 8, 31, 23, 59, 59, tzinfo=UTC))
        with self.assertRaises(LicenseExpiredError):
            verify_license(self.issue(), self.key_ring, at=datetime(2027, 9, 1, tzinfo=UTC))

    def test_rejects_naive_validation_time(self) -> None:
        with self.assertRaisesRegex(ValueError, "fuso horário"):
            verify_license(self.issue(), self.key_ring, at=datetime(2026, 10, 1))

    def test_rejects_invalid_date_order_and_format(self) -> None:
        values = self.payload.to_mapping()
        values["not_before"] = "2027-09-01T00:00:00Z"
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)
        values = self.payload.to_mapping()
        values["expires_at"] = "2027-02-30T00:00:00Z"
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)

    def test_rejects_unsupported_schema_and_envelope_version(self) -> None:
        values = self.payload.to_mapping()
        values["schema"] = "nexojuris-license/v5"
        with self.assertRaises(UnsupportedSchemaError):
            LicensePayload.from_mapping(values)

        document = json.loads(self.issue())
        document["format"] = "nexojuris-license-file/v2"
        with self.assertRaises(UnsupportedSchemaError):
            parse_license(json.dumps(document))

    def test_key_rotation_accepts_each_registered_key(self) -> None:
        next_private_key = Ed25519PrivateKey.generate()
        next_payload = LicensePayload.from_mapping(
            {**self.payload.to_mapping(), "key_id": "license-main-2027-01", "license_id": "LIC-2027-000002"}
        )
        rotating_ring = PublicKeyRing(
            {
                self.key_id: self.private_key.public_key(),
                next_payload.key_id: next_private_key.public_key(),
            }
        )
        verify_license(self.issue(), rotating_ring, at=self.valid_at)
        next_document = issue_license(next_payload, next_private_key)
        self.assertEqual(verify_license(next_document, rotating_ring, at=self.valid_at), next_payload)

    def test_unknown_key_id_is_rejected(self) -> None:
        unrelated_key = Ed25519PrivateKey.generate()
        with self.assertRaises(UnknownKeyError):
            verify_license(self.issue(), PublicKeyRing({"other-main-2026": unrelated_key.public_key()}), at=self.valid_at)

    def test_lease_key_id_cannot_identify_a_full_license(self) -> None:
        values = self.payload.to_mapping()
        values["key_id"] = "lease-online-2026-01"
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)

    def test_rejects_missing_extra_and_duplicate_fields(self) -> None:
        value = json.loads(self.issue())
        del value["payload"]["license_id"]
        with self.assertRaises(LicenseFormatError):
            parse_license(json.dumps(value))

        value = json.loads(self.issue())
        value["payload"]["unexpected"] = True
        with self.assertRaises(LicenseFormatError):
            parse_license(json.dumps(value))

        duplicate = self.issue().decode().replace(
            f'"format":"{LICENSE_FILE_FORMAT}"',
            f'"format":"{LICENSE_FILE_FORMAT}","format":"{LICENSE_FILE_FORMAT}"',
        )
        with self.assertRaisesRegex(LicenseFormatError, "duplicado"):
            parse_license(duplicate)

    def test_rejects_noncanonical_features(self) -> None:
        values = self.payload.to_mapping()
        values["features"] = ["reader", "converter"]
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)
        values["features"] = ["converter", "telemetry"]
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)

    def test_revision_is_positive_and_hybrid_fields_are_rejected(self) -> None:
        values = self.payload.to_mapping()
        values["revision"] = 0
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)
        values = self.payload.to_mapping()
        values["revision"] = True
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)
        values = self.payload.to_mapping()
        values["validation_mode"] = "hybrid"
        with self.assertRaises(LicenseFormatError):
            LicensePayload.from_mapping(values)

    def test_file_extension_and_size_are_enforced(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_dir:
            root = Path(temporary_dir)
            valid_path = root / "cliente.nxjlic"
            valid_path.write_bytes(self.issue())
            self.assertEqual(load_license_file(valid_path, self.key_ring, at=self.valid_at), self.payload)
            invalid_path = root / "cliente.json"
            invalid_path.write_bytes(self.issue())
            with self.assertRaises(LicenseFormatError):
                load_license_file(invalid_path, self.key_ring, at=self.valid_at)
        with self.assertRaises(LicenseFormatError):
            parse_license(b"{" + b" " * (64 * 1024))

    def test_legacy_tokens_are_not_license_documents(self) -> None:
        with self.assertRaises(LicenseFormatError):
            parse_license("ACT3-01-documento-obsoleto")

    def test_constants_match_documented_versions(self) -> None:
        self.assertEqual(LICENSE_SCHEMA, "nexojuris-license/v4")
        self.assertEqual(LICENSE_FILE_FORMAT, "nexojuris-license-file/v1")


if __name__ == "__main__":
    unittest.main()
