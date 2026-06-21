import streamlit as st
import database as db

import sqlite3

st.set_page_config(page_title="Accounting System", layout="wide")

# Initialize session state immediately when the app starts
if "selected_fy" not in st.session_state:
    with sqlite3.connect("data/ledger.db") as conn:
        # Default to the active fiscal year
        active_year = conn.execute("SELECT fiscal_year_id FROM fy WHERE active_ind = 1").fetchone()
        st.session_state.selected_fy = active_year[0] if active_year else 1

def fiscal_year_selector():
    # 1. Fetch available years
    with sqlite3.connect("data/ledger.db") as conn:
        years = conn.execute("SELECT fiscal_year_id, name FROM fy ORDER BY name DESC").fetchall()
    
    # 2. Determine default (active) year
    active_year = conn.execute("SELECT fiscal_year_id FROM fy WHERE active_ind = 1").fetchone()
    default_id = active_year[0] if active_year else (years[0][0] if years else None)

    # 3. Initialize session state
    if "selected_fy" not in st.session_state:
        st.session_state.selected_fy = default_id

    # 4. Display Selector
    year_map = {y[1]: y[0] for y in years}
    
    # Pre-select based on session
    current_name = next((name for name, id in year_map.items() if id == st.session_state.selected_fy), None)
    
    selected_name = st.sidebar.selectbox(
        "Select Fiscal Year", 
        list(year_map.keys()), 
        index=list(year_map.keys()).index(current_name) if current_name else 0
    )
    
    # Update state
    st.session_state.selected_fy = year_map[selected_name]
    return st.session_state.selected_fy

def get_dashboard_summary():
    def as_float(value):
        return float(value) if value is not None else 0.0

    fy_id = st.session_state.selected_fy
    with sqlite3.connect("data/ledger.db") as conn:
        # Example: Filter balance by the selected Fiscal Year
        # we don't use a f-string to prevent SQL injection
        latest_posted = conn.execute("""
            SELECT daily_posted_balance 
            FROM ledger 
            WHERE transaction_date != 'pending' 
            AND fiscal_year_id = ?
            AND is_deleted = 0
            AND daily_posted_balance IS NOT NULL
            ORDER BY transaction_date DESC, id DESC
            LIMIT 1
        """, (fy_id,)).fetchone()
        
        # ... apply same filter to pending sum query

        # 2. Sum of pending balances
        # Assuming pending transactions have an 'amount' field
        pending_sum = conn.execute("""
            SELECT SUM(amount) 
            FROM ledger 
            WHERE transaction_date = 'pending'
            AND fiscal_year_id = ?
            AND is_deleted = 0
        """, (fy_id,)).fetchone()

        latest_posted_val = as_float(latest_posted[0]) if latest_posted else 0.0
        pending_sum_val = as_float(pending_sum[0]) if pending_sum else 0.0
        
        # 3. Total including pending
        total_balance = latest_posted_val + pending_sum_val
        
        return latest_posted_val, pending_sum_val, total_balance
    
# Initialize the system
db.init_db()

st.markdown("""
    <style>
        [data-testid="stSidebarNav"] {
            display: none;
        }
    </style>
""", unsafe_allow_html=True)

fiscal_year_selector()

st.sidebar.title("Accounting System")
page = st.sidebar.radio("Navigate to:", [
    "Dashboard", 
    "Transactions",
    "Reports",
    "Cloud Sync",
    "Reconcile Expenses", 
    "Manual Entry", 
    "Import Bank Files", 
    "Configuration"
])

if page == "Dashboard":
    st.title("Welcome")
    st.write("Use the sidebar to manage your accounts.")
    
    # ... inside your render function
    posted, pending, total = get_dashboard_summary()

    col1, col2, col3 = st.columns(3)
    col1.metric("Latest Posted Balance", f"${posted:,.2f}")
    col2.metric("Pending Transactions", f"${pending:,.2f}")
    col3.metric("Projected Total", f"${total:,.2f}")
    
    from pages import dashboard
    dashboard.render_dashboard_table()

elif page == "Transactions":
    from pages import transactions
    transactions.render()
elif page == "Reports":
    from pages import reports
    reports.render()
elif page == "Cloud Sync":
    from pages import cloud_sync
    cloud_sync.render()
elif page == "Reconcile Expenses":
    from pages import reconcile
    reconcile.render_reconciliation()
elif page == "Manual Entry":
    from pages import manual_inputs
    manual_inputs.render()
elif page == "Import Bank Files":
    from pages import imports
    imports.render()
elif page == "Configuration":
    from pages import config
    config.render()
