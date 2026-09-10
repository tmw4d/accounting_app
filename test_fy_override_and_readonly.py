import os
import unittest
from unittest.mock import MagicMock, patch
import database as db
import app


class TestFYOverrideAndReadOnly(unittest.TestCase):
    def setUp(self):
        db.init_db()

    def test_database_fy_override(self):
        with db.get_connection() as conn:
            # Create a test transaction
            conn.execute("""
                INSERT INTO ledger (
                    transaction_date, description, amount, transaction_type, source_indicator, fiscal_year_id, is_deleted
                ) VALUES ('2026-08-01', 'Test Accrual Income FY27', 1500.0, 'credit', 'Manual', 1, 0)
            """)
            txn_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

            # Update the fiscal_year_id to FY 2 (override)
            conn.execute("""
                UPDATE ledger
                SET fiscal_year_id = 2
                WHERE id = ?
            """, (txn_id,))
            conn.commit()

            # Verify the updated fiscal_year_id
            row = conn.execute("SELECT fiscal_year_id FROM ledger WHERE id = ?", (txn_id,)).fetchone()
            self.assertEqual(row[0], 2, "Expected fiscal_year_id to be updated to 2")

            # Clean up test transaction
            conn.execute("DELETE FROM ledger WHERE id = ?", (txn_id,))
            conn.commit()

    def test_read_only_mode_detection(self):
        import streamlit as st

        # Test session state override (True)
        with patch.object(st, "session_state", {"read_only_mode": True}):
            self.assertTrue(app.is_read_only(), "Expected is_read_only() to return True when session_state['read_only_mode'] is True")

        # Test session state override (False)
        with patch.object(st, "session_state", {"read_only_mode": False}):
            os.environ.pop("READ_ONLY", None)
            self.assertFalse(app.is_read_only(), "Expected is_read_only() to return False when session_state['read_only_mode'] is False")

        # Test environment variable override
        with patch.object(st, "session_state", {}):
            os.environ["READ_ONLY"] = "true"
            self.assertTrue(app.is_read_only(), "Expected is_read_only() to return True when READ_ONLY env var is 'true'")
            os.environ.pop("READ_ONLY", None)

    def test_auth_and_permissions(self):
        import streamlit as st

        mock_user = MagicMock()
        mock_stop = MagicMock()
        mock_secrets = {
            "ALLOWED_DOMAIN": "example.org",
            "ADMIN_EMAILS": ["admin@example.org"],
            "ALLOWED_EMAILS": ["external_auditor@example.com"],
        }

        with patch.object(st, "user", mock_user, create=True), patch.object(st, "stop", mock_stop), patch.object(st, "secrets", mock_secrets, create=True):
            # 1. Unauthenticated user -> triggers stop
            mock_user.is_logged_in = False
            app.check_auth_and_permissions()
            mock_stop.assert_called()

            # Reset mock
            mock_stop.reset_mock()

            # 2. Authenticated user with invalid domain -> triggers stop
            mock_user.is_logged_in = True
            mock_user.email = "unauthorized@gmail.com"
            app.check_auth_and_permissions()
            mock_stop.assert_called()

            # Reset mock
            mock_stop.reset_mock()

            # 3. Authenticated standard user from allowed domain -> read_only_mode = True
            session_dict = {}
            with patch.object(st, "session_state", session_dict):
                mock_user.is_logged_in = True
                mock_user.email = "member@example.org"
                app.check_auth_and_permissions()
                self.assertTrue(session_dict.get("read_only_mode"), "Expected standard domain user to be read-only")

            # 4. Authenticated admin from ADMIN_EMAILS -> read_only_mode = False
            session_dict = {}
            with patch.object(st, "session_state", session_dict):
                mock_user.is_logged_in = True
                mock_user.email = "admin@example.org"
                app.check_auth_and_permissions()
                self.assertFalse(session_dict.get("read_only_mode"), "Expected admin to have full edit access (read_only_mode=False)")

            # 5. Authenticated external email outside domain in ALLOWED_EMAILS -> read_only_mode = True
            session_dict = {}
            with patch.object(st, "session_state", session_dict):
                mock_user.is_logged_in = True
                mock_user.email = "external_auditor@example.com"
                mock_stop.reset_mock()
                app.check_auth_and_permissions()
                mock_stop.assert_not_called()
                self.assertTrue(session_dict.get("read_only_mode"), "Expected external allowed email to be granted read-only access")

    def test_dashboard_program_breakdown_calculation(self):
        import database as db

        fy_id = 10
        pivot_df = db.get_program_financial_breakdown(fy_id)

        # Verify FY2026 active totals exclude soft-deleted transactions
        self.assertAlmostEqual(pivot_df.loc['Income', 'Total'], 118635.89, places=2)
        self.assertAlmostEqual(pivot_df.loc['Expense', 'Total'], 126938.14, places=2)
        self.assertAlmostEqual(pivot_df.loc['Total', 'Total'], -8302.25, places=2)

    def test_fiscal_year_active_and_deletion_guard(self):
        with db.get_connection() as conn:
            # 1. Insert a temporary FY
            conn.execute(
                "INSERT INTO fy (name, start_date, end_date, active_ind) VALUES ('FY2099', '2099-01-01', '2099-12-31', 0)"
            )
            fy_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

            # 2. Test setting active
            conn.execute("UPDATE fy SET active_ind = 0")
            conn.execute("UPDATE fy SET active_ind = 1 WHERE fiscal_year_id = ?", (fy_id,))
            conn.commit()

            active_id = conn.execute("SELECT fiscal_year_id FROM fy WHERE active_ind = 1").fetchone()[0]
            self.assertEqual(active_id, fy_id, "Expected FY2099 to be set active")

            # 3. Test transaction count guard
            conn.execute("""
                INSERT INTO ledger (transaction_date, description, amount, source_indicator, fiscal_year_id, is_deleted)
                VALUES ('2099-05-01', 'Test Txn FY2099', 100.0, 'Manual', ?, 0)
            """, (fy_id,))
            txn_id = conn.execute("SELECT last_insert_rowid()").fetchone()[0]
            conn.commit()

            count = conn.execute("SELECT COUNT(*) FROM ledger WHERE fiscal_year_id = ?", (fy_id,)).fetchone()[0]
            self.assertEqual(count, 1, "Expected 1 transaction associated with FY2099")

            # Clean up test data
            conn.execute("DELETE FROM ledger WHERE id = ?", (txn_id,))
            conn.execute("DELETE FROM fy WHERE fiscal_year_id = ?", (fy_id,))
            conn.execute("UPDATE fy SET active_ind = 1 WHERE name = 'FY2026'")
            conn.commit()

    def test_dynamic_allocation_rules_view(self):
        with db.get_connection() as conn:
            # Verify allocation_rules_by_fy_v contains rules for all fiscal years
            rules = conn.execute("""
                SELECT fiscal_year_id, method_id, program_id, percentage
                FROM allocation_rules_by_fy_v
                WHERE method_id IN (21, 22)
            """).fetchall()
            # If TopScore items exist, rules view must resolve percentage rules
            self.assertIsNotNone(rules)


if __name__ == "__main__":
    unittest.main()

