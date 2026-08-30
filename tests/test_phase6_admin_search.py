from __future__ import annotations

import tempfile
import unittest
from datetime import UTC, datetime
from pathlib import Path
from unittest.mock import MagicMock

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from admin_license_bridge import AdminLicenseBridge
from admin_licensing import AdminDatabase, AdminLicenseService


class Phase6AdminSearchTests(unittest.TestCase):
    RECORDS = 1_500

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp = tempfile.TemporaryDirectory()
        cls.root = Path(cls.temp.name)
        cls.database = AdminDatabase(cls.root / "large-admin.db")
        cls.private_key = Ed25519PrivateKey.generate()
        cls._seed_large_database()
        cls.database = AdminDatabase(cls.database.path)
        cls.service = AdminLicenseService(
            cls.database,
            key_id="license-test-2026",
            private_key_provider=lambda: cls.private_key,
        )
        cls.now = datetime(2026, 8, 30, 12, 0, tzinfo=UTC)

    @classmethod
    def tearDownClass(cls) -> None:
        cls.temp.cleanup()

    @classmethod
    def _seed_large_database(cls) -> None:
        issued_at = "2026-06-01T12:00:00Z"
        customers = []
        licenses = []
        features = []
        devices = []
        leases = []
        for index in range(cls.RECORDS):
            customer_id = f"CUS-SYN-{index:06d}"
            name = "Pessoa Repetida" if index % 2 == 0 else f"Cliente Distinto {index:04d}"
            normalized = name.casefold()
            customers.append(
                (
                    customer_id,
                    name,
                    normalized,
                    f"pessoa{index}@example.test",
                    f"8299{index:07d}",
                    f"CPF{index:011d}",
                    f"PED-{index:06d}",
                    issued_at,
                )
            )
            license_id = f"LIC-2026-{index + 1:06d}"
            category = index % 4
            status = "active" if category < 2 else ("suspended" if category == 2 else "revoked")
            expires_at = "2026-08-20T12:00:00Z" if category == 0 else "2026-09-03T12:00:00Z"
            validation_mode = "hybrid" if index % 3 else "offline"
            licenses.append(
                (
                    license_id,
                    customer_id,
                    "license-test-2026",
                    status,
                    issued_at,
                    issued_at,
                    expires_at,
                    3,
                    validation_mode,
                    7 if validation_mode == "hybrid" else 0,
                    f"CUST-{index:012d}",
                    f"VENDA-{index:06d}",
                )
            )
            features.extend((license_id, feature) for feature in ("converter", "ocr", "reader"))
            machine_hex = f"{index:016X}"
            machine_id = "NXJ2-" + "-".join(machine_hex[position : position + 4] for position in range(0, 16, 4))
            device_id = f"DEV-SYN-{index:06d}"
            devices.append((device_id, license_id, machine_id, issued_at))
            if index % 10 == 1:
                leases.append(
                    (
                        f"LEA-SYN-{index:06d}",
                        license_id,
                        device_id,
                        "2026-08-29T12:00:00Z",
                        "2026-09-02T12:00:00Z",
                        "2026-08-30T10:00:00Z",
                    )
                )
        with cls.database.transaction() as connection:
            connection.executemany(
                """
                INSERT INTO customers(
                    customer_id, name, normalized_name, email, phone, tax_id,
                    commercial_reference, created_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                customers,
            )
            connection.executemany(
                """
                INSERT INTO licenses(
                    license_id, customer_id, key_id, status, issued_at, not_before,
                    expires_at, term_months, validation_mode, max_offline_days,
                    customer_reference, commercial_reference
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                licenses,
            )
            connection.executemany(
                "INSERT INTO license_features(license_id, feature) VALUES (?, ?)", features
            )
            connection.executemany(
                """
                INSERT INTO licensed_devices(device_id, license_id, machine_id, bound_at, active)
                VALUES (?, ?, ?, ?, 1)
                """,
                devices,
            )
            connection.executemany(
                """
                INSERT INTO online_leases(
                    lease_id, license_id, device_id, issued_at, expires_at, last_seen_at, status
                ) VALUES (?, ?, ?, ?, ?, ?, 'active')
                """,
                leases,
            )

    def test_consulta_vazia_nao_carrega_tabela_geral(self) -> None:
        result = self.service.search_licenses("", now=self.now)
        self.assertEqual(result["total"], 0)
        self.assertEqual(result["items"], [])

    def test_nome_repetido_e_paginado_sem_perder_identidade(self) -> None:
        first = self.service.search_licenses('cliente:"Pessoa Repetida"', page=1, page_size=25, now=self.now)
        second = self.service.search_licenses('cliente:"Pessoa Repetida"', page=2, page_size=25, now=self.now)
        self.assertEqual(first["total"], 750)
        self.assertEqual(len(first["items"]), 25)
        self.assertEqual(len(second["items"]), 25)
        first_ids = {item["license_id"] for item in first["items"]}
        second_ids = {item["license_id"] for item in second["items"]}
        self.assertFalse(first_ids & second_ids)
        self.assertTrue(all(item["customer_name"] == "Pessoa Repetida" for item in first["items"]))

    def test_busca_fts_e_campos_indexados(self) -> None:
        by_license = self.service.search_licenses("LIC-2026-000124", now=self.now)
        by_email = self.service.search_licenses("pessoa123@example.test", now=self.now)
        by_machine = self.service.search_licenses("maquina:NXJ2-0000-0000-0000-007B", now=self.now)
        by_commercial_reference = self.service.search_licenses("VENDA-000123", now=self.now)
        self.assertEqual(by_license["items"][0]["license_id"], "LIC-2026-000124")
        self.assertEqual(by_email["items"][0]["license_id"], "LIC-2026-000124")
        self.assertEqual(by_machine["items"][0]["license_id"], "LIC-2026-000124")
        self.assertEqual(by_commercial_reference["items"][0]["license_id"], "LIC-2026-000124")
        with self.database.read() as connection:
            plan = connection.execute(
                "EXPLAIN QUERY PLAN SELECT license_id FROM license_search WHERE license_search MATCH ?",
                ('"pessoa123"*',),
            ).fetchall()
        self.assertIn("VIRTUAL TABLE INDEX", " ".join(str(row[3]) for row in plan).upper())

    def test_filtros_de_status_validade_e_offline(self) -> None:
        expired = self.service.search_licenses("status:expirada", page_size=100, now=self.now)
        expiring = self.service.search_licenses("expira:7d", page_size=100, now=self.now)
        offline = self.service.search_licenses("offline:7d", page_size=100, now=self.now)
        revoked = self.service.search_licenses("status:revogada", page_size=100, now=self.now)
        self.assertEqual(expired["total"], 375)
        self.assertEqual(expiring["total"], 375)
        self.assertEqual(offline["total"], 75)
        self.assertEqual(revoked["total"], 375)

    def test_dashboard_e_detalhe_exibem_conexao_prazo_e_acoes(self) -> None:
        dashboard = self.service.dashboard(now=self.now)
        self.assertEqual(dashboard["counts"]["expiring"], 375)
        self.assertEqual(dashboard["counts"]["suspended"], 375)
        self.assertEqual(dashboard["counts"]["revoked"], 375)
        self.assertGreater(dashboard["counts"]["awaiting_connection"], 0)
        detail = self.service.license_detail("LIC-2026-000002", now=self.now)
        self.assertEqual(detail["last_connection"], "2026-08-30T10:00:00Z")
        self.assertEqual(detail["offline_until"], "2026-09-02T12:00:00Z")
        self.assertIn("replace_device", detail["available_actions"])

    def test_bridge_preserva_paginacao_e_confirmacoes_do_nucleo(self) -> None:
        bridge = AdminLicenseBridge(self.service)
        result = bridge.search_licenses("status:revogada", 2, 25)
        self.assertTrue(result["ok"])
        self.assertEqual(result["page"], 2)
        invalid = bridge.revoke_license("LIC-2026-000002", "teste", "sim")
        self.assertFalse(invalid["ok"])
        self.assertIn("REVOGAR:LIC-2026-000002", invalid["error"])
        bridge.set_window(MagicMock())

    def test_emissao_da_interface_valida_tudo_antes_de_criar_cliente(self) -> None:
        bridge = AdminLicenseBridge(self.service)
        with self.database.read() as connection:
            before = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        invalid = bridge.create_customer_license(
            {
                "name": "Novo cliente",
                "term_months": 3,
                "features": ["converter"],
                "validation_mode": "offline",
                "max_offline_days": 0,
                "machine_id": "NXJ2-INVALIDO",
            }
        )
        with self.database.read() as connection:
            after = connection.execute("SELECT COUNT(*) FROM customers").fetchone()[0]
        self.assertFalse(invalid["ok"])
        self.assertEqual(before, after)

        valid = bridge.create_customer_license(
            {
                "name": "Novo cliente",
                "term_months": 6,
                "features": ["converter", "reader"],
                "validation_mode": "offline",
                "max_offline_days": 0,
                "machine_id": "NXJ2-AAAA-BBBB-CCCC-DDDD",
            }
        )
        self.assertTrue(valid["ok"])
        detail = self.service.license_detail(valid["license_id"])
        self.assertEqual(detail["active_device"]["machine_id"], "NXJ2-AAAA-BBBB-CCCC-DDDD")


if __name__ == "__main__":
    unittest.main()
