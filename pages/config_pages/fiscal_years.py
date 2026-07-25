import database as db
import streamlit as st


def render():
    st.subheader("Manage Fiscal Years")
    
    # Form to add
    with st.expander("Add New Fiscal Year"):
        with st.form("fy_form"):
            col1, col2 = st.columns(2)
            name = col1.text_input("Name (e.g., FY2026)")
            active = col2.checkbox("Set Active", value=False)
            start = st.date_input("Start Date")
            end = st.date_input("End Date")
            
            if st.form_submit_button("Save FY"):
                with db.get_connection() as conn:
                    if active:
                        conn.execute("UPDATE fy SET active_ind = 0")
                    conn.execute(
                        "INSERT INTO fy (name, start_date, end_date, active_ind) VALUES (?, ?, ?, ?)",
                        (name, str(start), str(end), 1 if active else 0)
                    )
                st.success("Added!")
                st.rerun()

    # List and Delete
    with db.get_connection() as conn:
        fys = conn.execute("SELECT fiscal_year_id, name, start_date, end_date, active_ind FROM fy").fetchall()
        
    for fy in fys:
        col1, col2 = st.columns([3, 1])
        col1.write(f"**{fy[1]}** | {fy[2]} to {fy[3]} {'(Active)' if fy[4] else ''}")
        if col2.button("Delete", key=f"del_fy_{fy[0]}"):
            with db.get_connection() as conn:
                conn.execute("DELETE FROM fy WHERE fiscal_year_id = ?", (fy[0],))
            st.rerun()
