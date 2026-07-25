import streamlit as st
import sqlite3
from datetime import datetime

import database as db

def render():
    st.title("✍️ Manual Transaction Entry")
    st.write("Record a new manual transaction directly into the ledger. The system will automatically classify its fiscal year and type.")

    # Fetch active categories and fiscal years for dropdowns
    with db.get_connection() as conn:

        categories = conn.execute("SELECT id, name, flow FROM categories WHERE active_ind = 1 ORDER BY flow, name").fetchall()
        cat_map = {f"{c[2]} - {c[1]}": c[0] for c in categories}
        
        fy_rows = conn.execute("SELECT fiscal_year_id, name, start_date, end_date FROM fy").fetchall()

    st.write("### 📝 Enter Transaction Details")
    
    with st.form("manual_entry_form", clear_on_submit=True):
        col1, col2 = st.columns(2)
        
        with col1:
            is_pending = st.checkbox("Pending transaction", value=False)
            tx_date = st.date_input(
                "Transaction Date",
                value=datetime.today(),
                disabled=is_pending,
                help="Pending transactions are saved without a posted date.",
            )
            desc = st.text_input("Description / Payee", placeholder="e.g. Office Depot, Donation from John")
            amount = st.number_input("Amount ($)", min_value=0.0, step=1.0, format="%.2f")
            tx_flow = st.selectbox("Transaction Flow", ["Expense (Debit)", "Income (Credit)"])
            
        with col2:
            sel_cat = st.selectbox("Category", ["Uncategorized"] + list(cat_map.keys()))
            check_num = st.text_input("Check / Serial Number", placeholder="e.g. 1024 (leave blank if none)")
            notes = st.text_input("Primary Note", placeholder="Short summary")
            more_notes = st.text_area("Extended Notes", placeholder="Detailed explanation or breakdown", height=68)

        submitted = st.form_submit_button("💾 Save Transaction to Ledger", type="primary")

        if submitted:
            if not desc:
                st.error("Please enter a description for the transaction.")
                return
            if amount <= 0:
                st.error("Please enter a positive transaction amount.")
                return

            # Format fields
            date_str = "pending" if is_pending else tx_date.strftime("%Y-%m-%d")
            
            # Map debit/credit amount and type
            if tx_flow == "Expense (Debit)":
                tx_amt = -amount
                tx_type = "debit"
            else:
                tx_amt = amount
                tx_type = "credit"
                
            tx_check = check_num.strip() or None
            tx_cat = cat_map[sel_cat] if sel_cat != "Uncategorized" else None
            
            active_fy_id = st.session_state.selected_fy

            if is_pending:
                matched_fy_id = active_fy_id
            else:
                # Dynamically determine correct fiscal year based on date
                matched_fy_id = None
                for fy_id, fy_name, start, end in fy_rows:
                    if date_str >= start and date_str <= end:
                        matched_fy_id = fy_id
                        break
                        
                if not matched_fy_id:
                    # If date is out of range of any fiscal year, warn and fallback
                    matched_fy_id = active_fy_id
                    st.warning(f"Warning: Selected date {date_str} does not fall within any configured fiscal year. It has been associated with the active year.")
            
            # Save to database
            try:
                with db.get_connection() as conn:

                    conn.execute("""
                        INSERT INTO ledger (
                            transaction_date, description, amount, transaction_type, check_number,
                            category_id, fiscal_year_id, source_indicator, notes, more_notes, is_deleted
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, 'Manual', ?, ?, 0)
                    """, (date_str, desc, tx_amt, tx_type, tx_check, tx_cat, matched_fy_id, notes or None, more_notes or None))
                    db.recalculate_running_balances(conn)
                    conn.commit()
                status_label = "pending " if is_pending else ""
                st.success(f"Successfully recorded manual {status_label}{tx_flow.lower()} of ${amount:,.2f} for '{desc}'!")
                st.balloons()
            except Exception as e:
                st.error(f"Error saving transaction: {str(e)}")
