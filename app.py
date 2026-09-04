import os
from datetime import date

import streamlit as st
import database as db
import cloud_sync


def is_read_only():
    """Returns True if the application is running in read-only mode."""
    try:
        if "read_only_mode" in st.session_state:
            return bool(st.session_state["read_only_mode"])
    except Exception:
        pass
    try:
        if hasattr(st, "secrets") and st.secrets.get("READ_ONLY", False):
            return True
    except Exception:
        pass
    env_val = os.getenv("READ_ONLY", "").lower()
    return env_val in ("true", "1", "yes")


def check_auth_and_permissions():
    """Validates user authentication, domain restriction, allowed emails list, and sets read-only permissions based on st.secrets."""
    if not hasattr(st, "user") or not st.user.is_logged_in:
        st.title("🔐 Authentication Required")
        st.write("Please log in with your Google account to access the Accounting System.")
        if hasattr(st, "login") and "auth" in st.secrets and "google" in st.secrets.auth:
            st.button("Log in with Google", on_click=st.login("google"))
        else:
            # Fallback for Community Cloud default injection
            st.button("Log in with Google", on_click=st.login)
#        if hasattr(st, "login"):
#            st.button("Log in with Google", on_click=st.login("google"))
#        else:
#            st.button("Log in with Google")
        st.stop()  # Stop execution until authenticated

    # Get user email
    user_email = (getattr(st.user, "email", "") or str(st.user)).strip().lower()

    # Configuration loaded strictly from st.secrets
    allowed_domain = ""
    admin_emails = []
    allowed_emails = []

    try:
        if hasattr(st, "secrets"):
            if "ALLOWED_DOMAIN" in st.secrets:
                allowed_domain = str(st.secrets["ALLOWED_DOMAIN"])
            if "ADMIN_EMAILS" in st.secrets:
                raw_admins = st.secrets["ADMIN_EMAILS"]
                if isinstance(raw_admins, str):
                    admin_emails = [e.strip() for e in raw_admins.split(",")]
                elif isinstance(raw_admins, (list, tuple)):
                    admin_emails = list(raw_admins)
            if "ALLOWED_EMAILS" in st.secrets:
                raw_allowed = st.secrets["ALLOWED_EMAILS"]
                if isinstance(raw_allowed, str):
                    allowed_emails = [e.strip() for e in raw_allowed.split(",")]
                elif isinstance(raw_allowed, (list, tuple)):
                    allowed_emails = list(raw_allowed)
    except Exception:
        pass

    allowed_domain = allowed_domain.strip().lower()
    admin_emails = [e.strip().lower() for e in admin_emails if e.strip()]
    allowed_emails = [e.strip().lower() for e in allowed_emails if e.strip()]

    # Check authorization:
    # 1. Admin emails -> Edit access
    # 2. Allowed domain OR Allowed emails list -> Read-Only access
    is_admin = user_email in admin_emails
    is_domain_allowed = bool(allowed_domain and user_email.endswith(f"@{allowed_domain}"))
    is_email_allowed = user_email in allowed_emails

    if not (is_admin or is_domain_allowed or is_email_allowed):
        st.title("🚫 Access Denied")
        st.error(
            f"Your account ({user_email}) is not authorized to access this application. "
            "Please contact an administrator if you require access."
        )
        if st.button("Log out"):
            st.logout()
        st.stop()

    # Determine read-only mode based on user email
    if is_admin:
        st.session_state["read_only_mode"] = False
    else:
        st.session_state["read_only_mode"] = True


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


def get_dashboard_data_through_dates():
    """Return the latest active bank and imported registration data dates."""
    with db.get_connection() as conn:
        dates = conn.execute("""
            SELECT
                (
                    SELECT MAX(date(transaction_date))
                    FROM ledger
                    WHERE source_indicator = 'Bank'
                        AND is_deleted = 0
                        AND transaction_date != 'pending'
                ) AS bank_data_through,
                (
                    SELECT MAX(date(transfer_timestamp))
                    FROM topscore_transfer_items
                ) AS registration_data_through
        """).fetchone()

    return dates[0], dates[1]


def format_data_through_date(value):
    if not value:
        return "not available"
    parsed_date = date.fromisoformat(value)
    return f"{parsed_date.strftime('%b')} {parsed_date.day}, {parsed_date.year}"


def main():
    st.set_page_config(page_title="Accounting System", layout="wide")

    # Gatekeeper authentication and permissions
    check_auth_and_permissions()

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

    # User info and role display
    user_email = (getattr(st.user, "email", "") or str(st.user)).strip()
    st.sidebar.caption(f"Logged in as: **{user_email}**")

    if is_read_only():
        st.sidebar.info("🔒 Read-Only Mode")
        nav_options = [
            "Dashboard",
            "Registrations",
            "Transactions",
            "Reports",
            "Cloud Sync",
        ]
    else:
        st.sidebar.success("✏️ Admin Access")
        nav_options = [
            "Dashboard", 
            "Registrations",
            "Transactions",
            "Reports",
            "Cloud Sync",
            "Reconcile Expenses", 
            "Manual Entry", 
            "Import Bank Files", 
            "Configuration"
        ]

    if st.sidebar.button("Log out"):
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

        bank_date, registration_date = get_dashboard_data_through_dates()
        st.caption(
            f"Bank data through {format_data_through_date(bank_date)} · "
            f"Registration data through {format_data_through_date(registration_date)}"
        )
        
        from pages import dashboard
        dashboard.render_dashboard_table()

    elif page == "Transactions":
        from pages import transactions
        transactions.render()
    elif page == "Registrations":
        from pages import registrations
        registrations.render(read_only=is_read_only())
    elif page == "Reports":
        from pages import reports
        reports.render()
    elif page == "Cloud Sync":
        from pages import cloud_sync as cloud_sync_page
        cloud_sync_page.render()
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
