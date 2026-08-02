import os
import unittest
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

        # Test session state override
        st.session_state["read_only_mode"] = True
        self.assertTrue(app.is_read_only(), "Expected is_read_only() to return True when session_state['read_only_mode'] is True")

        # Test session state false
        st.session_state["read_only_mode"] = False
        os.environ.pop("READ_ONLY", None)
        self.assertFalse(app.is_read_only(), "Expected is_read_only() to return False when disabled")

        # Test environment variable override
        os.environ["READ_ONLY"] = "true"
        self.assertTrue(app.is_read_only(), "Expected is_read_only() to return True when READ_ONLY env var is 'true'")
        os.environ.pop("READ_ONLY", None)


if __name__ == "__main__":
    unittest.main()
