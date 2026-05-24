import streamlit as st
import sqlite3

def render():
    st.header("Manage Categories")
    st.subheader("Category Management")
    
    # --- Add New Category ---
    with st.expander("Add New Category"):
        with st.form("new_category_form"):
            col1, col2 = st.columns(2)
            with col1:
                name = st.text_input("Category Name")
                flow = st.selectbox("Flow", ["Income", "Expense"])
            with col2:
                desc = st.text_area("Description")
                active = st.checkbox("Active", value=True)
            
            submitted = st.form_submit_button("Add Category")
            
            if submitted and name:
                try:
                    with sqlite3.connect("data/ledger.db") as conn:
                        conn.execute(
                            "INSERT INTO categories (flow, name, description, active_ind) VALUES (?, ?, ?, ?)", 
                            (flow, name, desc, 1 if active else 0)
                        )
                    st.success(f"Category '{name}' added!")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.error("Category name already exists.")

    # --- View/Manage Categories ---
    st.subheader("Existing Categories")
    with sqlite3.connect("data/ledger.db") as conn:
        categories = conn.execute("SELECT id, flow, name, description, active_ind FROM categories").fetchall()
        
    if categories:
        for cat in categories:
            cid, flow, name, desc, active = cat
            # Display row with status indicator
            status = "🟢" if active else "🔴"
            st.write(f"{status} **{name}** ({flow}) - {desc}")
            
            # Simple toggle for active status or delete
            c1, c2 = st.columns([1, 1])
            if c1.button("Toggle Active", key=f"tog_{cid}"):
                with sqlite3.connect("data/ledger.db") as conn:
                    conn.execute("UPDATE categories SET active_ind = NOT active_ind WHERE id = ?", (cid,))
                st.rerun()
            if c2.button("Delete", key=f"del_{cid}"):
                with sqlite3.connect("data/ledger.db") as conn:
                    conn.execute("DELETE FROM categories WHERE id = ?", (cid,))
                st.rerun()
            st.divider()
    else:
        st.info("No categories defined yet.")