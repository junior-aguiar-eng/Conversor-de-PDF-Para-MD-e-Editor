"""Regressões da Fase 4 de confiabilidade do armazenamento persistente."""

from __future__ import annotations

import os
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import app_storage
import web_api
from constants import user_data_root
from library_db import LibraryDatabase


class StorageResilienceTests(unittest.TestCase):
    def test_user_data_is_independent_from_executable_location(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            env = dict(os.environ)
            env.pop("NEXOJURIS_DATA_DIR", None)
            env["LOCALAPPDATA"] = temp_dir
            with patch.dict(os.environ, env, clear=True):
                self.assertEqual(user_data_root(), Path(temp_dir).resolve() / "NexoJuris" / "Conversor")

    def test_legacy_database_and_license_are_migrated_without_removing_source(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            legacy = root / "legacy"
            current = root / "current"
            legacy.mkdir()
            legacy_db = legacy / "nexojuris_acervo.db"
            with closing(sqlite3.connect(legacy_db)) as conn:
                conn.execute("CREATE TABLE marker (value TEXT)")
                conn.execute("INSERT INTO marker VALUES ('preservado')")
                conn.commit()
            (legacy / "license.sig").write_text("machine:key", encoding="utf-8")
            (legacy / "conversion-journal.json").write_text('{"state":"interrupted"}', encoding="utf-8")

            with (
                patch.object(app_storage, "legacy_data_directory", return_value=legacy),
                patch.object(app_storage, "data_directory", return_value=current),
                patch.object(app_storage, "library_database_path", return_value=current / "nexojuris_acervo.db"),
                patch.object(app_storage, "license_backup_path", return_value=current / "license.sig"),
            ):
                app_storage.migrate_legacy_user_data()

            with closing(sqlite3.connect(current / "nexojuris_acervo.db")) as conn:
                self.assertEqual(conn.execute("SELECT value FROM marker").fetchone()[0], "preservado")
            self.assertEqual((current / "license.sig").read_text(encoding="utf-8"), "machine:key")
            self.assertEqual(
                (current / "conversion-journal.json").read_text(encoding="utf-8"), '{"state":"interrupted"}'
            )
            self.assertTrue(legacy_db.is_file())
            self.assertTrue((legacy / "license.sig").is_file())

    def test_corrupt_database_is_quarantined_and_restored_from_valid_backup(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "acervo.db"
            database = LibraryDatabase(db_path)
            document = Path(temp_dir) / "precedente.pdf"
            document.write_bytes(b"pdf")
            database.index_document_metadata_only(document, 12, 3)
            backup = database.create_backup()
            db_path.write_bytes(b"not-a-sqlite-database")

            recovered = LibraryDatabase(db_path)

            self.assertEqual(recovered.recovery_status["state"], "restored")
            self.assertEqual(recovered.recovery_status["recovered_from"], str(backup))
            self.assertTrue(Path(recovered.recovery_status["quarantined_path"]).is_file())
            self.assertTrue(recovered.get_session_state(str(document))["found"])

    def test_corrupt_database_without_backup_is_quarantined_and_rebuilt(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "acervo.db"
            db_path.write_bytes(b"corrupt")

            rebuilt = LibraryDatabase(db_path)

            self.assertEqual(rebuilt.recovery_status["state"], "rebuilt")
            self.assertTrue(Path(rebuilt.recovery_status["quarantined_path"]).is_file())
            self.assertEqual(rebuilt.get_recent_documents(), [])

    def test_access_error_does_not_quarantine_a_healthy_database(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "acervo.db"
            LibraryDatabase(db_path)
            original = db_path.read_bytes()

            for error in (
                sqlite3.OperationalError("database is locked"),
                sqlite3.OperationalError("unable to open database file"),
            ):
                with self.subTest(error=str(error)):
                    with (
                        patch("library_db.sqlite3.connect", side_effect=error),
                        self.assertRaisesRegex(sqlite3.OperationalError, str(error)),
                    ):
                        LibraryDatabase(db_path)

                    self.assertEqual(db_path.read_bytes(), original)
                    self.assertEqual(list(db_path.parent.glob("acervo.corrupt-*.db")), [])

    def test_backup_rotation_keeps_only_five_valid_copies(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            database = LibraryDatabase(Path(temp_dir) / "acervo.db")
            for _ in range(7):
                database.create_backup()
            backups = database._backup_candidates()
            self.assertEqual(len(backups), 5)
            self.assertTrue(all(database._integrity_ok(item) for item in backups))

    def test_bridge_opens_with_temporary_library_when_persistent_path_fails(self) -> None:
        real_database = LibraryDatabase

        def factory(db_path=None):
            if db_path is None:
                raise PermissionError("pasta persistente somente leitura")
            return real_database(db_path)

        with patch.object(web_api, "LibraryDatabase", side_effect=factory):
            api = web_api.BridgeApi()

        status = api.get_app_info()["library_storage"]
        self.assertEqual(status["state"], "degraded")
        self.assertFalse(status["persistent"])
        self.assertIn("somente leitura", status["error"])

    def test_read_only_default_output_falls_back_to_user_profile(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            root = Path(temp_dir)
            blocked_output = root / "blocked-output"
            blocked_output.write_text("not a directory", encoding="utf-8")
            profile = root / "profile"
            database = LibraryDatabase(root / "acervo.db")
            with (
                patch.object(web_api, "DEFAULT_OUTPUT_DIR", blocked_output),
                patch.object(web_api, "user_data_root", return_value=profile),
                patch.object(web_api, "LibraryDatabase", return_value=database),
            ):
                api = web_api.BridgeApi(conversion_journal_path=root / "journal.json")

            self.assertEqual(api.get_app_info()["default_output_dir"], str(profile / "PDFs Convertidos"))
            self.assertTrue((profile / "PDFs Convertidos").is_dir())


if __name__ == "__main__":
    unittest.main()
