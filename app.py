import os
import streamlit as st
import database as db
import cloud_sync


def is_read_only():
    """Returns True if the application is running in read-only mode."""
    if st.session_state.get("read_only_mode", False):
        return True
    try:
        if hasattr(st, "secrets") and st.secrets.get("READ_ONLY", False):
            return True
    except Exception:
        pass
    env_val = os.getenv("READ_ONLY", "").lower()
    return env_val in ("true", "1", "yes")


def fiscal_year_selector():
    with db.get_connection() as conn:
        years = conn.execute("SELECT fiscal_year_id, name FROM fy ORDER BY name DESC").fetchall()
    
    if not years:
        st.sidebar.info("No Fiscal Years configured.")
        st.session_state.selected_fy = None
        return None

    with db.get_connection() as conn:
        active_year = conn.execute("SELECT fiscal_year_id FROM fy WHERE active_ind = 1").fetchone()
    default_id = active_year[0] if active_year else years[0][0]

    year_map = {y[1]: y[0] for y in years}

    if "selected_fy" not in st.session_state or st.session_state.selected_fy not in year_map.values():
        st.session_state.selected_fy = default_id

    current_name = next((name for name, fy_id in year_map.items() if fy_id == st.session_state.selected_fy), None)
    default_index = list(year_map.keys()).index(current_name) if current_name in year_map else 0

    selected_name = st.sidebar.selectbox(
        "Select Fiscal Year", 
        list(year_map.keys()), 
        index=default_index
    )
    
    st.session_state.selected_fy = year_map.get(selected_name)
    return st.session_state.selected_fy


def get_dashboard_summary():
    def as_float(value):
        return float(value) if value is not None else 0.0

    fy_id = st.session_state.selected_fy
    with db.get_connection() as conn:
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
        
        pending_sum = conn.execute("""
            SELECT SUM(amount) 
            FROM ledger 
            WHERE transaction_date = 'pending'
            AND fiscal_year_id = ?
            AND is_deleted = 0
        """, (fy_id,)).fetchone()

        latest_posted_val = as_float(latest_posted[0]) if latest_posted else 0.0
        pending_sum_val = as_float(pending_sum[0]) if pending_sum else 0.0
        total_balance = latest_posted_val + pending_sum_val
        
        return latest_posted_val, pending_sum_val, total_balance


def main():
    st.set_page_config(page_title="Accounting System", layout="wide")

    # Ensure data directory exists, attempt startup S3 download if missing, and initialize schema
    db.ensure_data_dir()
    if not db.DB_PATH.exists():
        cloud_sync.auto_download_on_startup(st.secrets)
    db.init_db()

    # Initialize session state immediately when the app starts
    if "selected_fy" not in st.session_state:
        with db.get_connection() as conn:
            active_year = conn.execute("SELECT fiscal_year_id FROM fy WHERE active_ind = 1").fetchone()
            st.session_state.selected_fy = active_year[0] if active_year else None

    st.markdown("""
        <style>
            [data-testid="stSidebarNav"] {
                display: none;
            }
        </style>
    """, unsafe_allow_html=True)

    fiscal_year_selector()

    st.sidebar.title("Accounting System")
    if is_read_only():
        st.sidebar.info("🔒 Read-Only Mode")
        nav_options = [
            "Dashboard",
            "Transactions",
            "Reports",
            "Cloud Sync",
        ]
    else:
        nav_options = [
            "Dashboard", 
            "Transactions",
            "Reports",
            "Cloud Sync",
            "Reconcile Expenses", 
            "Manual Entry", 
            "Import Bank Files", 
            "Configuration"
        ]


    # 1. Check if the user is authenticated
#    if not st.user.is_logged_in:
#        # Prompt the user to log in if they haven't already
#        st.write("Please log in to access the application.")
    if st.button("Log in with Google"):
        st.login("google")
#        st.stop()  # Stop executing the rest of the page for unauthenticated users

    # 2. If logged in, display the welcome message using st.user attributes
    st.title(f"Welcome, {st.user}!")
    st.write(f"Logged in as: {st.user}")

    # 3. Provide a logout option
    if st.button("Log out"):
        st.logout()


    page = st.sidebar.radio("Navigate to:", nav_options)

    if page == "Dashboard":
        st.title("Welcome")
        st.write("Use the sidebar to manage your accounts.")
        
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


if __name__ == "__main__":
    main()
