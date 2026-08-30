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

        with patch.object(st, "user", mock_user, create=True), patch.object(st, "stop", mock_stop):
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

            # 3. Authenticated standard user from @yula-ulti.org -> read_only_mode = True
            session_dict = {}
            with patch.object(st, "session_state", session_dict):
                mock_user.is_logged_in = True
                mock_user.email = "member@yula-ulti.org"
                app.check_auth_and_permissions()
                self.assertTrue(session_dict.get("read_only_mode"), "Expected standard domain user to be read-only")

            # 4. Authenticated treasurer from @yula-ulti.org -> read_only_mode = False
            session_dict = {}
            with patch.object(st, "session_state", session_dict):
                mock_user.is_logged_in = True
                mock_user.email = "treasurer@yula-ulti.org"
                app.check_auth_and_permissions()
                self.assertFalse(session_dict.get("read_only_mode"), "Expected treasurer to have full edit access (read_only_mode=False)")


if __name__ == "__main__":
    unittest.main()
