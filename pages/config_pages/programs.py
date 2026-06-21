import streamlit as st
import sqlite3

def render():
    st.subheader("Program Management")
    
    # Add New Program
    with st.expander("Add New Program"):
        with st.form("new_program_form"):
            col1, col2 = st.columns(2)
            name = col1.text_input("Program Name")
            code = col2.text_input("Program Code (e.g., PROG001)")
            desc = st.text_area("Description")
            active = st.checkbox("Active", value=True)
            
            if st.form_submit_button("Save Program"):
                try:
                    with sqlite3.connect("data/ledger.db") as conn:
                        conn.execute(
                            "INSERT INTO programs (name, code, description, active_ind) VALUES (?, ?, ?, ?)",
                            (name, code, desc, 1 if active else 0)
                        )
                    st.success(f"Program '{name}' added!")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Program name or code already exists.")

    # List and Delete
    st.write("### Active Programs")
    with sqlite3.connect("data/ledger.db") as conn:
        programs = conn.execute("SELECT * FROM programs").fetchall()
        
    for prog in programs:
        pid, name, code, desc, active = prog
        col1, col2 = st.columns([3, 1])
        col1.button(f"**[{code}] {name}**", width="stretch", help=desc if desc else "No description provided")
        if col2.button("Delete", key=f"del_prog_{pid}", type="primary"):
            with sqlite3.connect("data/ledger.db") as conn:
                conn.execute("DELETE FROM programs WHERE id = ?", (pid,))
            st.rerun()

