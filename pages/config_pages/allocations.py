import streamlit as st
import sqlite3

def render():
    st.subheader("Allocation Management")

    # Initialize session state for method selection
    if "sel_method_id" not in st.session_state:
        st.session_state.sel_method_id = None

    # Inject CSS for Red Warning Button
    st.markdown("""
        <style>
        div.stButton > button[kind="secondary"][key*="del_meth_"] {
            background-color: #ff4b4b;
            color: white;
        }
        </style>
    """, unsafe_allow_html=True)

    # 1. List and Delete Existing Methods
    st.write("### Existing Methods")
    with sqlite3.connect("data/ledger.db") as conn:
        methods = conn.execute("SELECT id, name, description FROM allocation_methods ORDER BY name ASC").fetchall()

    if methods:
        for mid, name, desc in methods:
            col1, col2 = st.columns([3, 1])
            
            # Select button (updates session state)
            if col1.button(f"**{name}**", width="stretch", help=desc or "No description"):
                st.session_state.sel_method_id = mid
                st.rerun()
            
            # Delete button (custom red style)
            if col2.button("Delete Method", key=f"del_meth_{mid}", type="primary"):
                with sqlite3.connect("data/ledger.db") as conn:
                    conn.execute("DELETE FROM allocation_rules WHERE method_id = ?", (mid,))
                    conn.execute("DELETE FROM allocation_methods WHERE id = ?", (mid,))
                if st.session_state.sel_method_id == mid:
                    st.session_state.sel_method_id = None
                st.rerun()
    else:
        st.info("No allocation methods defined yet.")

    st.divider()

    # 2. Create New Method
    st.write("### New Allocation Methods")
    with st.expander("Create New Allocation Method"):
        with st.form("new_method_form"):
            name = st.text_input("Method Name")
            desc = st.text_area("Description")
            if st.form_submit_button("Save Method"):
                with sqlite3.connect("data/ledger.db") as conn:
                    conn.execute("INSERT INTO allocation_methods (name, description) VALUES (?, ?)", (name, desc))
                st.rerun()

    # 3. Manage Rules
    st.write("### Define Percentages")
    with sqlite3.connect("data/ledger.db") as conn:
        all_methods = conn.execute("SELECT id, name FROM allocation_methods").fetchall()
        programs = conn.execute("SELECT id, name FROM programs WHERE active_ind = 1").fetchall()

    if all_methods:
        method_map = {m[1]: m[0] for m in all_methods}
        # Use session state to set the index of the selectbox
        selected_idx = 0
        if st.session_state.sel_method_id:
            try:
                selected_idx = list(method_map.values()).index(st.session_state.sel_method_id)
            except ValueError: pass

        sel_method = st.selectbox("Select Method to edit", list(method_map.keys()), index=selected_idx)
        m_id = method_map[sel_method]
        st.session_state.sel_method_id = m_id # Keep session in sync


        # Fetch current rules
        with sqlite3.connect("data/ledger.db") as conn:
            rules = conn.execute("""
                SELECT r.id, p.name, r.percentage, r.program_id 
                FROM allocation_rules r
                JOIN programs p ON r.program_id = p.id
                WHERE r.method_id = ?
            """, (m_id,)).fetchall()

        # Display Rules Table
        total_pct = sum([r[2] for r in rules])
        
        # Total line with conditional red color
        if total_pct != 1.0:
            st.markdown(f"### Total: :red[{total_pct*100:.0f}%]")
        else:
            st.markdown(f"### Total: {total_pct*100:.0f}%")

        # Edit/Delete List
        for r in rules:
            rid, pname, pct, pid = r
            cols = st.columns([2, 1, 1, 1])
            cols[0].write(f"**{pname}**")
            
            # Inline edit for percentage
            new_pct = cols[1].number_input("Pct", value=pct, key=f"edit_{rid}", label_visibility="collapsed")
            
            if cols[2].button("Update", key=f"upd_{rid}"):
                with sqlite3.connect("data/ledger.db") as conn:
                    conn.execute("UPDATE allocation_rules SET percentage = ? WHERE id = ?", (new_pct, rid))
                st.rerun()
                
            if cols[3].button("Delete", key=f"del_{rid}"):
                with sqlite3.connect("data/ledger.db") as conn:
                    conn.execute("DELETE FROM allocation_rules WHERE id = ?", (rid,))
                st.rerun()

        # Form to add a rule to the selected method
        with st.form("add_rule_form"):
            c1, c2 = st.columns(2)
            prog_map = {p[1]: p[0] for p in programs}
            sel_prog = c1.selectbox("Program", list(prog_map.keys()))
            pct = c2.number_input("Percentage (0-1)", min_value=0.0, max_value=1.0, step=0.05)
            
            if st.form_submit_button("Add Rule to Method"):
                with sqlite3.connect("data/ledger.db") as conn:
                    conn.execute(
                        "INSERT INTO allocation_rules (method_id, program_id, percentage) VALUES (?, ?, ?)",
                        (m_id, prog_map[sel_prog], pct)
                    )
                st.rerun()

    else:
        st.info("Create a method above to start defining rules.")
