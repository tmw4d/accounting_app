import csv
from io import StringIO

import database as db
import pandas as pd
import streamlit as st


REQUIRED_EXPORT_COLUMNS = {
    "transfer_timestamp",
    "item_type",
    "type",
    "product_name",
    "amount",
    "identifier",
}


def _read_export(uploaded_file):
    text = uploaded_file.getvalue().decode("utf-8-sig", errors="ignore")
    reader = csv.DictReader(StringIO(text))
    fieldnames = set(reader.fieldnames or [])
    missing = REQUIRED_EXPORT_COLUMNS - fieldnames
    if missing:
        raise ValueError(f"The CSV is missing required columns: {', '.join(sorted(missing))}.")

    rows = []
    for row in reader:
        try:
            amount = float((row.get("amount") or "0").strip() or 0)
        except ValueError as exc:
            raise ValueError(f"Invalid amount in export: {row.get('amount')!r}.") from exc
        rows.append((
            (row.get("transfer_timestamp") or "").strip(),
            (row.get("transfer_id") or "").strip(),
            (row.get("item_type") or "").strip(),
            (row.get("type") or "").strip(),
            (row.get("product_name") or "").strip(),
            (row.get("applied_currency") or "").strip(),
            amount,
            (row.get("identifier") or "").strip(),
        ))

    if not rows:
        raise ValueError("The CSV contains no data rows.")
    return rows


def _replace_topscore_export(rows, filename):
    with db.get_connection() as conn:
        conn.execute("DELETE FROM topscore_transfer_items")
        conn.executemany("""
            INSERT INTO topscore_transfer_items (
                source_filename, source_row_number, transfer_timestamp, transfer_id,
                item_type, type, product_name, applied_currency, amount, identifier
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, [
            (filename, row_number, *row)
            for row_number, row in enumerate(rows, start=2)
        ])


def _load_groupings():
    with db.get_connection() as conn:
        return [
            row[0]
            for row in conn.execute(
                "SELECT name FROM registration_groupings ORDER BY name"
            ).fetchall()
        ]


def _load_product_summary():
    with db.get_connection() as conn:
        return pd.read_sql_query("""
            SELECT
                t.product_name,
                COALESCE(m.grouping, 'Unmapped') AS grouping,
                COALESCE(m.primary_registration_ind, 0) AS primary_registration_ind,
                COUNT(DISTINCT CASE
                    WHEN lower(t.item_type) = 'payment'
                        AND lower(t.type) = 'payment'
                        AND t.amount > 0
                        AND trim(COALESCE(t.identifier, '')) != ''
                    THEN t.identifier
                END) AS registrations,
                SUM(CASE
                    WHEN lower(t.item_type) = 'payment'
                        AND lower(t.type) = 'payment'
                        AND t.amount > 0
                    THEN t.amount ELSE 0
                END) AS gross_payments,
                SUM(CASE
                    WHEN lower(t.item_type) = 'refund' AND lower(t.type) = 'refund'
                    THEN t.amount ELSE 0
                END) AS refunds,
                SUM(CASE WHEN lower(t.item_type) = 'network_fee' THEN t.amount ELSE 0 END) AS network_fees,
                SUM(CASE WHEN lower(t.item_type) = 'cc_fee' THEN t.amount ELSE 0 END) AS cc_fees,
                SUM(CASE WHEN lower(t.item_type) = 'topscore_fee' THEN t.amount ELSE 0 END) AS topscore_fees,
                SUM(t.amount) AS net_dollars
            FROM topscore_transfer_items t
            LEFT JOIN topscore_product_mappings m ON m.product_name = t.product_name
            WHERE trim(COALESCE(t.product_name, '')) != ''
            GROUP BY t.product_name, m.grouping, m.primary_registration_ind
            ORDER BY grouping = 'Unmapped' DESC, grouping, t.product_name
        """, conn)


def _save_mappings(original, edited):
    changed = 0
    with db.get_connection() as conn:
        for old, new in zip(original.itertuples(index=False), edited.itertuples(index=False)):
            if old.grouping == new.grouping and bool(old.primary_registration_ind) == bool(new.primary_registration_ind):
                continue
            if new.grouping == "Unmapped":
                conn.execute(
                    "DELETE FROM topscore_product_mappings WHERE product_name = ?",
                    (new.product_name,),
                )
            else:
                conn.execute("""
                    INSERT INTO topscore_product_mappings (
                        product_name, grouping, primary_registration_ind
                    ) VALUES (?, ?, ?)
                    ON CONFLICT(product_name) DO UPDATE SET
                        grouping = excluded.grouping,
                        primary_registration_ind = excluded.primary_registration_ind
                """, (new.product_name, new.grouping, int(bool(new.primary_registration_ind))))
            changed += 1
    return changed


def _current_import_details():
    with db.get_connection() as conn:
        return conn.execute("""
            SELECT source_filename, COUNT(*), MIN(transfer_timestamp), MAX(transfer_timestamp)
            FROM topscore_transfer_items
        """).fetchone()


def render(read_only=False):
    st.title("Registrations")
    st.write("Import the current TopScore export, then assign each product to a registration grouping.")

    st.subheader("Import TopScore export")
    uploaded_file = st.file_uploader(
        "TopScore Transfer CSV",
        type=["csv"],
        disabled=read_only,
        help="Importing a file replaces the current TopScore export. Product groupings are retained.",
    )
    if uploaded_file:
        try:
            parsed_rows = _read_export(uploaded_file)
            st.caption(f"Ready to replace the current export with {len(parsed_rows):,} rows from {uploaded_file.name}.")
            if st.button("Replace current TopScore data", type="primary", disabled=read_only):
                _replace_topscore_export(parsed_rows, uploaded_file.name)
                st.success(f"Imported {len(parsed_rows):,} rows. The prior TopScore export was replaced.")
                st.rerun()
        except ValueError as exc:
            st.error(str(exc))

    source_filename, row_count, first_timestamp, last_timestamp = _current_import_details()
    if not row_count:
        st.info("No TopScore export is currently loaded.")
        return
    st.caption(
        f"Current export: {source_filename} · {row_count:,} rows · "
        f"{first_timestamp or 'unknown date'} to {last_timestamp or 'unknown date'}"
    )

    summary = _load_product_summary()
    st.subheader("Products")
    st.caption("Registration counts are distinct identifiers on positive payment rows. Amounts use the signed values supplied by TopScore.")

    groupings = ["Unmapped"] + _load_groupings()
    editor_df = summary.copy()
    editor_df["primary_registration_ind"] = editor_df["primary_registration_ind"].astype(bool)
    edited_df = st.data_editor(
        editor_df,
        hide_index=True,
        width="stretch",
        disabled=read_only,
        column_config={
            "product_name": st.column_config.TextColumn("Product", disabled=True, width="large"),
            "grouping": st.column_config.SelectboxColumn("Grouping", options=groupings, required=True),
            "primary_registration_ind": st.column_config.CheckboxColumn("Primary registration product"),
            "registrations": st.column_config.NumberColumn("Registrations", format="%d", disabled=True),
            "gross_payments": st.column_config.NumberColumn("Gross payments", format="$%.2f", disabled=True),
            "refunds": st.column_config.NumberColumn("Refunds", format="$%.2f", disabled=True),
            "network_fees": st.column_config.NumberColumn("Network fees", format="$%.2f", disabled=True),
            "cc_fees": st.column_config.NumberColumn("CC fees", format="$%.2f", disabled=True),
            "topscore_fees": st.column_config.NumberColumn("TopScore fees", format="$%.2f", disabled=True),
            "net_dollars": st.column_config.NumberColumn("Net dollars", format="$%.2f", disabled=True),
        },
        column_order=[
            "product_name", "grouping", "primary_registration_ind", "registrations",
            "gross_payments", "refunds", "network_fees", "cc_fees", "topscore_fees", "net_dollars",
        ],
    )

    unmapped_count = int((summary["grouping"] == "Unmapped").sum())
    if unmapped_count:
        st.warning(f"{unmapped_count:,} product(s) still need a grouping.")
    else:
        st.success("Every product in the current export has a grouping.")
    if st.button("Save product mappings", disabled=read_only):
        invalid_primary = edited_df[
            (edited_df["grouping"] == "Unmapped")
            & edited_df["primary_registration_ind"]
        ]
        if not invalid_primary.empty:
            st.error("Assign a grouping before marking a product as a primary registration product.")
            return
        changed = _save_mappings(summary, edited_df)
        if changed:
            st.success(f"Saved {changed:,} product mapping(s).")
            st.rerun()
        st.info("No mapping changes to save.")
