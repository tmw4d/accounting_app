import sqlite3
from datetime import date

import pandas as pd
import streamlit as st


DB_PATH = "data/ledger.db"


def _format_currency(value):
    if pd.isna(value):
        return ""
    return f"${value:,.2f}"


def _load_categories():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
            SELECT id, name, flow
            FROM categories
            WHERE active_ind = 1
            ORDER BY flow, name
        """).fetchall()


def _load_allocation_methods():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
            SELECT id, name
            FROM allocation_methods
            ORDER BY name
        """).fetchall()


def _load_fiscal_years():
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
            SELECT fiscal_year_id, start_date, end_date
            FROM fy
        """).fetchall()


def _date_from_value(value):
    try:
        return date.fromisoformat(value)
    except (TypeError, ValueError):
        return date.today()


def _fiscal_year_for_date(date_value, fiscal_years, fallback_fy_id):
    date_str = date_value.isoformat()
    for fy_id, start_date, end_date in fiscal_years:
        if start_date <= date_str <= end_date:
            return fy_id
    return fallback_fy_id


def _load_transactions(fy_id, search_text, category_id):
    query = """
        SELECT
            l.id,
            l.transaction_date,
            l.description,
            l.amount,
            l.transaction_type,
            l.check_number,
            l.source_indicator,
            l.category_id,
            l.allocation_method_id,
            c.name AS category,
            c.flow AS flow,
            am.name AS allocation_method,
            l.notes,
            l.more_notes,
            CASE WHEN l.transaction_date = 'pending' THEN 1 ELSE 0 END AS is_pending
        FROM ledger l
        LEFT JOIN categories c ON l.category_id = c.id
        LEFT JOIN allocation_methods am ON l.allocation_method_id = am.id
        WHERE l.fiscal_year_id = ?
            AND l.is_deleted = 0
    """
    params = [fy_id]

    if search_text:
        query += """
            AND (
                l.description LIKE ?
                OR l.notes LIKE ?
                OR l.more_notes LIKE ?
                OR l.check_number LIKE ?
            )
        """
        like_term = f"%{search_text}%"
        params.extend([like_term, like_term, like_term, like_term])

    if category_id is not None:
        query += " AND l.category_id = ?"
        params.append(category_id)

    query += """
        ORDER BY
            CASE WHEN l.transaction_date = 'pending' THEN 0 ELSE 1 END,
            CASE WHEN l.transaction_date = 'pending' THEN l.id ELSE NULL END DESC,
            l.transaction_date DESC,
            l.id DESC
    """

    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn, params=params)


def render():
    st.title("Transactions")
    st.write("View pending activity and recent posted ledger transactions for the selected fiscal year.")

    fy_id = st.session_state.selected_fy
    categories = _load_categories()
    allocation_methods = _load_allocation_methods()
    fiscal_years = _load_fiscal_years()
    category_map = {
        f"{flow} - {name}" if flow else name: category_id
        for category_id, name, flow in categories
    }
    category_reverse_map = {category_id: label for label, category_id in category_map.items()}
    allocation_map = {name: method_id for method_id, name in allocation_methods}
    allocation_reverse_map = {method_id: name for name, method_id in allocation_map.items()}

    controls = st.columns([2, 1])
    with controls[0]:
        search_text = st.text_input(
            "Search transactions",
            value="",
            placeholder="Description, notes, or check number",
        ).strip()
    with controls[1]:
        selected_category = st.selectbox(
            "Category",
            ["All Categories"] + list(category_map.keys()),
        )

    category_id = None
    if selected_category != "All Categories":
        category_id = category_map[selected_category]

    txns = _load_transactions(fy_id, search_text, category_id)

    if txns.empty:
        st.info("No transactions found for this fiscal year.")
        return

    pending_count = int(txns["is_pending"].sum())
    posted_count = len(txns) - pending_count
    pending_total = txns.loc[txns["is_pending"] == 1, "amount"].sum()
    displayed_total = txns["amount"].sum()

    m1, m2, m3, m4 = st.columns(4)
    m1.metric("Pending", f"{pending_count} txns", _format_currency(pending_total))
    m2.metric("Posted", f"{posted_count} txns")
    m3.metric("Displayed Total", _format_currency(displayed_total))
    m4.metric("Rows", f"{len(txns):,}")

    display_df = txns.copy()
    display_df["Status"] = display_df["is_pending"].map({1: "Pending", 0: "Posted"})
    display_df["Date"] = display_df["transaction_date"].replace({"pending": "Pending"})
    display_df["Amount"] = display_df["amount"].apply(_format_currency)
    display_df["Category"] = display_df["category"].fillna("Uncategorized")
    display_df["Allocation"] = display_df["allocation_method"].fillna("Unallocated")
    display_df["Check #"] = display_df["check_number"].fillna("")
    display_df["Source"] = display_df["source_indicator"].fillna("")
    display_df["Type"] = display_df["transaction_type"].fillna("")
    display_df["Notes"] = display_df[["notes", "more_notes"]].fillna("").agg(
        lambda row: " | ".join(value for value in row if value),
        axis=1,
    )

    table_df = display_df[
        [
            "Status",
            "Date",
            "description",
            "Amount",
            "Type",
            "Category",
            "Allocation",
            "Check #",
            "Source",
            "Notes",
        ]
    ].rename(columns={"description": "Description"})

    st.dataframe(
        table_df,
        hide_index=True,
        use_container_width=True,
        column_config={
            "Description": st.column_config.TextColumn(width="large"),
            "Notes": st.column_config.TextColumn(width="large"),
        },
    )

    st.divider()
    st.write("### Edit Transaction")

    edit_options = {}
    for row in txns.itertuples(index=False):
        date_label = "Pending" if row.transaction_date == "pending" else row.transaction_date
        edit_options[f"{row.id} | {date_label} | {_format_currency(row.amount)} | {row.description}"] = row.id

    selected_edit_label = st.selectbox(
        "Select transaction to edit",
        list(edit_options.keys()),
    )
    selected_transaction_id = edit_options[selected_edit_label]
    selected_txn = txns.loc[txns["id"] == selected_transaction_id].iloc[0]

    is_pending = selected_txn["transaction_date"] == "pending"
    current_category = category_reverse_map.get(selected_txn["category_id"], "Uncategorized")
    current_allocation = allocation_reverse_map.get(selected_txn["allocation_method_id"], "None")

    with st.form(f"edit_transaction_{selected_transaction_id}"):
        c1, c2 = st.columns(2)

        new_is_pending = c1.checkbox("Pending transaction", value=is_pending)
        new_date = c1.date_input(
            "Transaction Date",
            value=_date_from_value(selected_txn["transaction_date"]),
            disabled=new_is_pending,
        )
        new_description = c1.text_input(
            "Description",
            value=selected_txn["description"] or "",
        )
        new_amount = c1.number_input(
            "Amount",
            value=float(selected_txn["amount"]),
            step=1.0,
            format="%.2f",
            help="Use negative amounts for expenses and positive amounts for income.",
        )
        new_transaction_type = c1.selectbox(
            "Transaction Type",
            ["debit", "credit"],
            index=0 if (selected_txn["transaction_type"] or "debit").lower() == "debit" else 1,
        )
        new_check_number = c1.text_input(
            "Check / Serial Number",
            value=selected_txn["check_number"] or "",
        )

        category_options = ["Uncategorized"] + list(category_map.keys())
        category_index = category_options.index(current_category) if current_category in category_options else 0
        new_category = c2.selectbox(
            "Category",
            category_options,
            index=category_index,
        )

        allocation_options = ["None"] + list(allocation_map.keys())
        allocation_index = allocation_options.index(current_allocation) if current_allocation in allocation_options else 0
        new_allocation = c2.selectbox(
            "Allocation Method",
            allocation_options,
            index=allocation_index,
        )
        new_source = c2.text_input(
            "Source",
            value=selected_txn["source_indicator"] or "",
        )
        new_notes = c2.text_input(
            "Primary Note",
            value=selected_txn["notes"] or "",
        )
        new_more_notes = c2.text_area(
            "Extended Notes",
            value=selected_txn["more_notes"] or "",
            height=96,
        )

        save_edit = st.form_submit_button("Save Transaction", type="primary")

    if save_edit:
        if not new_description.strip():
            st.error("Description is required.")
            return

        new_date_value = "pending" if new_is_pending else new_date.isoformat()
        new_fy_id = fy_id if new_is_pending else _fiscal_year_for_date(new_date, fiscal_years, fy_id)
        new_category_id = category_map[new_category] if new_category != "Uncategorized" else None
        new_allocation_id = allocation_map[new_allocation] if new_allocation != "None" else None

        with sqlite3.connect(DB_PATH) as conn:
            conn.execute("""
                UPDATE ledger
                SET transaction_date = ?,
                    description = ?,
                    amount = ?,
                    transaction_type = ?,
                    check_number = ?,
                    source_indicator = ?,
                    category_id = ?,
                    fiscal_year_id = ?,
                    allocation_method_id = ?,
                    notes = ?,
                    more_notes = ?
                WHERE id = ?
                    AND is_deleted = 0
            """, (
                new_date_value,
                new_description.strip(),
                float(new_amount),
                new_transaction_type,
                new_check_number.strip() or None,
                new_source.strip() or "Manual",
                new_category_id,
                new_fy_id,
                new_allocation_id,
                new_notes.strip() or None,
                new_more_notes.strip() or None,
                int(selected_transaction_id),
            ))
            conn.commit()

        st.success("Transaction updated.")
        st.rerun()
