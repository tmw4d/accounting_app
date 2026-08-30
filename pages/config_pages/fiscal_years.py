import database as db
import streamlit as st


def render():
    st.subheader("Manage Fiscal Years")
    
    # Form to add new FY
    with st.expander("Add New Fiscal Year"):
        with st.form("fy_form"):
            col1, col2 = st.columns(2)
            name = col1.text_input("Name (e.g., FY2027)")
            active = col2.checkbox("Set as Active Fiscal Year", value=False)
            start = st.date_input("Start Date")
            end = st.date_input("End Date")
            
            if st.form_submit_button("Save Fiscal Year"):
                if not name.strip():
                    st.error("Fiscal Year name is required.")
                else:
                    with db.get_connection() as conn:
                        if active:
                            conn.execute("UPDATE fy SET active_ind = 0")
                        cursor = conn.execute(
                            "INSERT INTO fy (name, start_date, end_date, active_ind) VALUES (?, ?, ?, ?)",
                            (name.strip(), str(start), str(end), 1 if active else 0)
                        )
                        new_id = cursor.lastrowid
                        if active:
                            st.session_state.selected_fy = new_id
                    st.success(f"Fiscal Year '{name}' added successfully!")
                    st.rerun()

    # List, Set Active, and Delete
    st.write("### Configured Fiscal Years")
    with db.get_connection() as conn:
        fys = conn.execute(
            "SELECT fiscal_year_id, name, start_date, end_date, active_ind FROM fy ORDER BY start_date DESC"
        ).fetchall()
        
        # Count transactions associated with each fiscal year
        counts = dict(
            conn.execute(
                "SELECT fiscal_year_id, COUNT(*) FROM ledger GROUP BY fiscal_year_id"
            ).fetchall()
        )

    if not fys:
        st.info("No Fiscal Years configured yet.")
        return

    for fy in fys:
        fy_id, name, start_date, end_date, active_ind = fy
        txn_count = counts.get(fy_id, 0)
        
        with st.container():
            col1, col2, col3 = st.columns([4, 2, 1])
            
            status_badge = "🟢 **(Active)**" if active_ind else ""
            txn_info = f"({txn_count} txns)" if txn_count > 0 else "(0 txns)"
            col1.write(f"**{name}** | {start_date} to {end_date} {status_badge} `{txn_info}`")
            
            if not active_ind:
                if col2.button("Set Active", key=f"set_active_{fy_id}"):
                    with db.get_connection() as conn:
                        conn.execute("UPDATE fy SET active_ind = 0")
                        conn.execute("UPDATE fy SET active_ind = 1 WHERE fiscal_year_id = ?", (fy_id,))
                    st.session_state.selected_fy = fy_id
                    st.success(f"Set '{name}' as the active Fiscal Year.")
                    st.rerun()
            else:
                col2.caption("Currently Active")

            if col3.button("Delete", key=f"del_fy_{fy_id}"):
                if active_ind:
                    st.error(f"Cannot delete '{name}' because it is currently set as the active Fiscal Year. Set another Fiscal Year as active first.")
                elif txn_count > 0:
                    st.error(f"Cannot delete '{name}' because it has {txn_count} transaction(s) associated with it. Reassign or remove those transactions first.")
                else:
                    with db.get_connection() as conn:
                        conn.execute("DELETE FROM fy WHERE fiscal_year_id = ?", (fy_id,))
                    st.success(f"Fiscal Year '{name}' deleted.")
                    st.rerun()
