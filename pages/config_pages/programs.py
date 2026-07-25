import database as db
import streamlit as st


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
                    with db.get_connection() as conn:
                        conn.execute(
                            "INSERT INTO programs (name, code, description, active_ind) VALUES (?, ?, ?, ?)",
                            (name, code, desc, 1 if active else 0)
                        )
                    st.success(f"Program '{name}' added!")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Error saving program: {exc}")

    # List and Delete
    st.write("### Active Programs")
    with db.get_connection() as conn:
        programs = conn.execute("SELECT id, name, code, description, active_ind FROM programs").fetchall()
        
    for prog in programs:
        pid, name, code, desc, active = prog
        col1, col2 = st.columns([3, 1])
        col1.button(f"**[{code}] {name}**", width="stretch", help=desc if desc else "No description provided")
        if col2.button("Delete", key=f"del_prog_{pid}", type="primary"):
            with db.get_connection() as conn:
                conn.execute("DELETE FROM programs WHERE id = ?", (pid,))
            st.rerun()
