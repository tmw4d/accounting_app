import csv
import os
import sqlite3
from io import StringIO

import pandas as pd
import streamlit as st


DB_PATH = "data/ledger.db"
IMPORT_FOLDER = "bank_import"
GROUPINGS = [
    "High School",
    "Middle School",
    "Winter",
    "Donation",
    "Fundraising",
    "Merchandise",
    "Tournament Bids",
    "Other",
]


def _ensure_table():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topscore_product_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL UNIQUE,
                grouping TEXT NOT NULL,
                category_id INTEGER,
                allocation_method_id INTEGER,
                active_ind INTEGER DEFAULT 1,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                FOREIGN KEY(category_id) REFERENCES categories(id),
                FOREIGN KEY(allocation_method_id) REFERENCES allocation_methods(id)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS topscore_transfer_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                source_filename TEXT NOT NULL,
                source_row_number INTEGER NOT NULL,
                transfer_timestamp TEXT,
                transfer_id TEXT,
                item_type TEXT,
                type TEXT,
                product_name TEXT,
                applied_currency TEXT,
                amount REAL,
                identifier TEXT,
                imported_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(source_filename, source_row_number)
            )
        """)


def _load_reference_data():
    with sqlite3.connect(DB_PATH) as conn:
        categories = conn.execute("""
            SELECT id, flow, name
            FROM categories
            WHERE active_ind = 1
            ORDER BY flow, name
        """).fetchall()
        allocation_methods = conn.execute("""
            SELECT id, name
            FROM allocation_methods
            ORDER BY name
        """).fetchall()

    category_options = {"Unmapped": None}
    for category_id, flow, name in categories:
        category_options[f"{flow} - {name}"] = category_id

    allocation_options = {"Unmapped": None}
    for method_id, name in allocation_methods:
        allocation_options[name] = method_id

    return category_options, allocation_options


def _reverse_lookup(options, selected_id):
    for label, option_id in options.items():
        if option_id == selected_id:
            return label
    return "Unmapped"


def _load_mapping_for_edit(mapping_id):
    with sqlite3.connect(DB_PATH) as conn:
        return conn.execute("""
            SELECT
                id,
                product_name,
                grouping,
                category_id,
                allocation_method_id,
                active_ind,
                notes
            FROM topscore_product_mappings
            WHERE id = ?
        """, (mapping_id,)).fetchone()


def _load_mappings(search_text="", grouping_filter="All", active_filter="Active"):
    query = """
        SELECT
            m.id,
            m.product_name,
            m.grouping,
            c.flow || ' - ' || c.name AS category,
            am.name AS allocation_method,
            m.active_ind,
            m.notes
        FROM topscore_product_mappings m
        LEFT JOIN categories c ON m.category_id = c.id
        LEFT JOIN allocation_methods am ON m.allocation_method_id = am.id
        WHERE 1 = 1
    """
    params = []

    if search_text:
        query += " AND m.product_name LIKE ?"
        params.append(f"%{search_text}%")

    if grouping_filter != "All":
        query += " AND m.grouping = ?"
        params.append(grouping_filter)

    if active_filter == "Active":
        query += " AND m.active_ind = 1"
    elif active_filter == "Inactive":
        query += " AND m.active_ind = 0"

    query += " ORDER BY m.grouping, m.product_name"

    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query(query, conn, params=params)


def _parse_mapping_text(raw_text):
    rows = []
    if not raw_text.strip():
        return rows

    reader = csv.reader(StringIO(raw_text.strip()), delimiter="\t")
    parsed_rows = list(reader)
    if not parsed_rows:
        return rows

    header = [value.strip().lower() for value in parsed_rows[0]]
    has_header = "product_name" in header or "product name" in header
    data_rows = parsed_rows[1:] if has_header else parsed_rows

    if has_header:
        product_idx = header.index("product_name") if "product_name" in header else header.index("product name")
        grouping_idx = 1
        for candidate in ["grouping", "group", "other"]:
            if candidate in header:
                grouping_idx = header.index(candidate)
                break
    else:
        product_idx = 0
        grouping_idx = 1

    for row in data_rows:
        if len(row) <= max(product_idx, grouping_idx):
            continue
        product_name = row[product_idx].strip()
        grouping = row[grouping_idx].strip()
        if product_name and grouping:
            rows.append((product_name, grouping))
    return rows


def _default_refs_for_grouping(grouping, category_options, allocation_options):
    category_name = "Unmapped"
    allocation_name = "Unmapped"

    if grouping in ("High School", "Middle School", "Winter"):
        category_name = "Income - Registration"
    elif grouping == "Donation":
        category_name = "Income - Donation"
    elif grouping == "Fundraising":
        category_name = "Income - Fundraising"
    elif grouping == "Merchandise":
        category_name = "Income - Merchandise Sales"
    elif grouping == "Tournament Bids":
        category_name = "Income - YULA Invite Bids"
    elif grouping == "Other":
        category_name = "Income - Other Income"

    if grouping == "High School":
        allocation_name = "High School Season Split"
    elif grouping == "Middle School":
        allocation_name = "2026 Player Distribution"
    elif grouping == "Winter":
        allocation_name = "High School Winter"

    if category_name not in category_options:
        category_name = "Unmapped"
    if allocation_name not in allocation_options:
        allocation_name = "Unmapped"

    return category_options[category_name], allocation_options[allocation_name]


def _upsert_mapping(product_name, grouping, category_id, allocation_method_id, active_ind, notes):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            INSERT INTO topscore_product_mappings (
                product_name,
                grouping,
                category_id,
                allocation_method_id,
                active_ind,
                notes,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
            ON CONFLICT(product_name) DO UPDATE SET
                grouping = excluded.grouping,
                category_id = excluded.category_id,
                allocation_method_id = excluded.allocation_method_id,
                active_ind = excluded.active_ind,
                notes = excluded.notes,
                updated_at = CURRENT_TIMESTAMP
        """, (product_name, grouping, category_id, allocation_method_id, active_ind, notes))


def _update_mapping(mapping_id, product_name, grouping, category_id, allocation_method_id, active_ind, notes):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            UPDATE topscore_product_mappings
            SET product_name = ?,
                grouping = ?,
                category_id = ?,
                allocation_method_id = ?,
                active_ind = ?,
                notes = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
        """, (
            product_name,
            grouping,
            category_id,
            allocation_method_id,
            active_ind,
            notes,
            mapping_id,
        ))


def _bulk_import_mappings(rows, apply_defaults, category_options, allocation_options):
    imported = 0
    with sqlite3.connect(DB_PATH) as conn:
        for product_name, grouping in rows:
            if apply_defaults:
                category_id, allocation_method_id = _default_refs_for_grouping(
                    grouping,
                    category_options,
                    allocation_options,
                )
            else:
                category_id = None
                allocation_method_id = None

            conn.execute("""
                INSERT INTO topscore_product_mappings (
                    product_name,
                    grouping,
                    category_id,
                    allocation_method_id,
                    active_ind,
                    updated_at
                )
                VALUES (?, ?, ?, ?, 1, CURRENT_TIMESTAMP)
                ON CONFLICT(product_name) DO UPDATE SET
                    grouping = excluded.grouping,
                    category_id = COALESCE(excluded.category_id, topscore_product_mappings.category_id),
                    allocation_method_id = COALESCE(excluded.allocation_method_id, topscore_product_mappings.allocation_method_id),
                    active_ind = 1,
                    updated_at = CURRENT_TIMESTAMP
            """, (product_name, grouping, category_id, allocation_method_id))
            imported += 1
    return imported


def _topscore_files():
    if not os.path.exists(IMPORT_FOLDER):
        return []
    return sorted(
        [
            filename
            for filename in os.listdir(IMPORT_FOLDER)
            if filename.lower().endswith(".csv") and "transfer" in filename.lower()
        ],
        reverse=True,
    )


def _scan_topscore_products(filename):
    path = os.path.join(IMPORT_FOLDER, filename)
    rows = []
    with open(path, newline="", encoding="utf-8-sig") as csv_file:
        for row in csv.DictReader(csv_file):
            product_name = (row.get("product_name") or "").strip()
            if not product_name:
                continue
            try:
                amount = float(row.get("amount") or 0)
            except ValueError:
                amount = 0.0
            rows.append({
                "product_name": product_name,
                "rows": 1,
                "net_amount": amount,
            })

    if not rows:
        return pd.DataFrame()

    products_df = pd.DataFrame(rows).groupby("product_name", as_index=False).agg({
        "rows": "sum",
        "net_amount": "sum",
    })

    mappings = _load_mappings(active_filter="Active")[["product_name", "grouping", "category", "allocation_method"]]
    return products_df.merge(mappings, on="product_name", how="left")


def _import_topscore_export(filename):
    path = os.path.join(IMPORT_FOLDER, filename)
    inserted = 0
    seen = 0

    with sqlite3.connect(DB_PATH) as conn:
        with open(path, newline="", encoding="utf-8-sig") as csv_file:
            for source_row_number, row in enumerate(csv.DictReader(csv_file), start=2):
                seen += 1
                try:
                    amount = float(row.get("amount") or 0)
                except ValueError:
                    amount = 0.0

                cursor = conn.execute("""
                    INSERT OR IGNORE INTO topscore_transfer_items (
                        source_filename,
                        source_row_number,
                        transfer_timestamp,
                        transfer_id,
                        item_type,
                        type,
                        product_name,
                        applied_currency,
                        amount,
                        identifier
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    filename,
                    source_row_number,
                    (row.get("transfer_timestamp") or "").strip(),
                    (row.get("transfer_id") or "").strip(),
                    (row.get("item_type") or "").strip(),
                    (row.get("type") or "").strip(),
                    (row.get("product_name") or "").strip(),
                    (row.get("applied_currency") or "").strip(),
                    amount,
                    (row.get("identifier") or "").strip(),
                ))
                inserted += cursor.rowcount

    return seen, inserted


def _load_imported_files():
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("""
            SELECT
                source_filename,
                COUNT(*) AS rows,
                COUNT(DISTINCT transfer_id) AS transfers,
                MIN(transfer_timestamp) AS first_timestamp,
                MAX(transfer_timestamp) AS last_timestamp,
                SUM(amount) AS net_amount
            FROM topscore_transfer_items
            GROUP BY source_filename
            ORDER BY last_timestamp DESC
        """, conn)


def _load_player_counts():
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("""
            SELECT
                m.grouping,
                CASE
                    WHEN m.grouping = 'Winter' THEN 'High School Winter'
                    WHEN m.grouping = 'High School' AND t.product_name LIKE '%Spring%' THEN 'High School Spring'
                    WHEN m.grouping = 'High School' AND t.product_name LIKE '%Fall%' THEN 'High School Fall'
                    WHEN m.grouping = 'High School' THEN 'High School Other'
                    WHEN m.grouping = 'Middle School' AND t.product_name LIKE '%Spring%' THEN 'Middle School Spring'
                    WHEN m.grouping = 'Middle School' AND t.product_name LIKE '%Fall%' THEN 'Middle School Fall'
                    WHEN m.grouping = 'Middle School' THEN 'Middle School Other'
                    ELSE ifnull(m.grouping, 'Unmapped')
                END AS season,
                t.product_name,
                COUNT(*) AS payment_rows,
                COUNT(DISTINCT t.identifier) AS players,
                SUM(t.amount) AS gross_amount
            FROM topscore_transfer_items t
            LEFT JOIN topscore_product_mappings m
                ON t.product_name = m.product_name
                AND m.active_ind = 1
            WHERE lower(t.item_type) = 'payment'
                AND lower(t.type) = 'payment'
                AND t.amount > 0
                AND ifnull(m.grouping, 'Unmapped') IN ('High School', 'Middle School', 'Winter')
            GROUP BY m.grouping, t.product_name
            ORDER BY
                CASE season
                    WHEN 'High School Fall' THEN 0
                    WHEN 'High School Winter' THEN 1
                    WHEN 'High School Spring' THEN 2
                    WHEN 'Middle School Fall' THEN 3
                    WHEN 'Middle School Spring' THEN 4
                    ELSE 5
                END,
                t.product_name
        """, conn)


def _load_grouping_summary():
    with sqlite3.connect(DB_PATH) as conn:
        return pd.read_sql_query("""
            SELECT
                ifnull(m.grouping, 'Unmapped') AS grouping,
                COUNT(*) AS rows,
                COUNT(DISTINCT CASE
                    WHEN lower(t.item_type) = 'payment' AND lower(t.type) = 'payment' AND t.amount > 0
                    THEN t.identifier
                END) AS positive_payment_count,
                SUM(CASE WHEN lower(t.item_type) = 'payment' THEN t.amount ELSE 0 END) AS payments,
                SUM(CASE WHEN lower(t.item_type) = 'refund' THEN t.amount ELSE 0 END) AS refunds,
                SUM(CASE WHEN lower(t.item_type) IN ('cc_fee', 'network_fee', 'topscore_fee') THEN t.amount ELSE 0 END) AS fees,
                SUM(t.amount) AS net_amount
            FROM topscore_transfer_items t
            LEFT JOIN topscore_product_mappings m
                ON t.product_name = m.product_name
                AND m.active_ind = 1
            GROUP BY ifnull(m.grouping, 'Unmapped')
            ORDER BY net_amount DESC
        """, conn)


def render():
    _ensure_table()

    st.header("TopScore Product Mappings")
    st.write("Map TopScore product names to accounting groupings before using the export to segment ledger deposits.")

    category_options, allocation_options = _load_reference_data()

    with st.expander("Paste Mapping From Google Sheet", expanded=False):
        st.write("Paste two columns: `product_name` and grouping. Tab-separated rows copied from Google Sheets work best.")
        raw_text = st.text_area("Mapping rows", height=180, placeholder="product_name\tGrouping")
        apply_defaults = st.checkbox(
            "Apply default category/allocation suggestions",
            value=True,
            help="Uses grouping to prefill likely category and allocation method where possible.",
        )
        parsed_rows = _parse_mapping_text(raw_text)
        if parsed_rows:
            st.caption(f"Parsed {len(parsed_rows)} mapping rows.")
            st.dataframe(
                pd.DataFrame(parsed_rows[:25], columns=["Product Name", "Grouping"]),
                hide_index=True,
                use_container_width=True,
            )
        if st.button("Import Pasted Mappings", disabled=not parsed_rows):
            count = _bulk_import_mappings(parsed_rows, apply_defaults, category_options, allocation_options)
            st.success(f"Imported or updated {count} TopScore product mappings.")
            st.rerun()

    st.write("### Add or Update Mapping")
    with st.form("topscore_mapping_form"):
        product_name = st.text_input("Product Name")
        grouping = st.selectbox("Grouping", GROUPINGS)
        c1, c2 = st.columns(2)
        category_name = c1.selectbox("Category", list(category_options.keys()))
        allocation_name = c2.selectbox("Allocation Method", list(allocation_options.keys()))
        active_ind = st.checkbox("Active", value=True)
        notes = st.text_area("Notes", height=68)

        if st.form_submit_button("Save Mapping"):
            if not product_name.strip():
                st.error("Product Name is required.")
            else:
                _upsert_mapping(
                    product_name.strip(),
                    grouping,
                    category_options[category_name],
                    allocation_options[allocation_name],
                    1 if active_ind else 0,
                    notes.strip() or None,
                )
                st.success("TopScore product mapping saved.")
                st.rerun()

    st.write("### Edit Existing Mapping")
    edit_mappings_df = _load_mappings(active_filter="All")
    if edit_mappings_df.empty:
        st.info("No mappings available to edit yet.")
    else:
        edit_options = {
            f"{row.grouping} | {row.product_name}": int(row.id)
            for row in edit_mappings_df.itertuples(index=False)
        }
        selected_edit_label = st.selectbox("Select Product Mapping", list(edit_options.keys()))
        selected_mapping = _load_mapping_for_edit(edit_options[selected_edit_label])

        if selected_mapping:
            (
                mapping_id,
                edit_product_name,
                edit_grouping,
                edit_category_id,
                edit_allocation_method_id,
                edit_active_ind,
                edit_notes,
            ) = selected_mapping

            with st.form(f"edit_topscore_mapping_{mapping_id}"):
                updated_product_name = st.text_input("Product Name", value=edit_product_name)
                grouping_index = GROUPINGS.index(edit_grouping) if edit_grouping in GROUPINGS else 0
                updated_grouping = st.selectbox("Grouping", GROUPINGS, index=grouping_index)

                e1, e2 = st.columns(2)
                category_labels = list(category_options.keys())
                current_category = _reverse_lookup(category_options, edit_category_id)
                category_index = category_labels.index(current_category) if current_category in category_labels else 0
                updated_category = e1.selectbox("Category", category_labels, index=category_index)

                allocation_labels = list(allocation_options.keys())
                current_allocation = _reverse_lookup(allocation_options, edit_allocation_method_id)
                allocation_index = allocation_labels.index(current_allocation) if current_allocation in allocation_labels else 0
                updated_allocation = e2.selectbox("Allocation Method", allocation_labels, index=allocation_index)

                updated_active = st.checkbox("Active", value=bool(edit_active_ind))
                updated_notes = st.text_area("Notes", value=edit_notes or "", height=68)

                if st.form_submit_button("Update Mapping"):
                    if not updated_product_name.strip():
                        st.error("Product Name is required.")
                    else:
                        try:
                            _update_mapping(
                                mapping_id,
                                updated_product_name.strip(),
                                updated_grouping,
                                category_options[updated_category],
                                allocation_options[updated_allocation],
                                1 if updated_active else 0,
                                updated_notes.strip() or None,
                            )
                            st.success("TopScore product mapping updated.")
                            st.rerun()
                        except sqlite3.IntegrityError:
                            st.error("Another mapping already uses that product name.")

    st.write("### Existing Mappings")
    f1, f2, f3 = st.columns([2, 1, 1])
    search_text = f1.text_input("Search Product", value="")
    grouping_filter = f2.selectbox("Grouping Filter", ["All"] + GROUPINGS)
    active_filter = f3.selectbox("Status", ["Active", "Inactive", "All"])

    mappings_df = _load_mappings(search_text, grouping_filter, active_filter)
    st.metric("Mappings", f"{len(mappings_df):,}")
    if mappings_df.empty:
        st.info("No mappings found.")
    else:
        display_df = mappings_df.rename(columns={
            "product_name": "Product Name",
            "grouping": "Grouping",
            "category": "Category",
            "allocation_method": "Allocation Method",
            "active_ind": "Active",
            "notes": "Notes",
        })
        display_df["Active"] = display_df["Active"].map({1: "Yes", 0: "No"})
        st.dataframe(
            display_df[["Product Name", "Grouping", "Category", "Allocation Method", "Active", "Notes"]],
            hide_index=True,
            use_container_width=True,
        )

    st.write("### Scan TopScore Export")
    files = _topscore_files()
    if not files:
        st.info("No TopScore transfer CSV files found in the bank_import folder.")
        return

    selected_file = st.selectbox("TopScore Export File", files)
    imported_files = _load_imported_files()
    imported_match = imported_files[imported_files["source_filename"] == selected_file]
    if imported_match.empty:
        st.warning("This export has not been imported into the TopScore detail table yet.")
    else:
        imported_row = imported_match.iloc[0]
        st.success(
            f"Imported: {int(imported_row['rows']):,} rows across "
            f"{int(imported_row['transfers']):,} transfers."
        )

    if st.button("Import Selected TopScore Export"):
        seen, inserted = _import_topscore_export(selected_file)
        st.success(f"Read {seen:,} rows and inserted {inserted:,} new rows.")
        st.rerun()

    scan_df = _scan_topscore_products(selected_file)
    if scan_df.empty:
        st.info("No product names found in this file.")
        return

    mapped_count = scan_df["grouping"].notna().sum()
    unmapped_count = scan_df["grouping"].isna().sum()
    s1, s2, s3 = st.columns(3)
    s1.metric("Distinct Products", f"{len(scan_df):,}")
    s2.metric("Mapped", f"{mapped_count:,}")
    s3.metric("Unmapped", f"{unmapped_count:,}")

    tab_unmapped, tab_mapped = st.tabs(["Unmapped Products", "Mapped Products"])

    with tab_unmapped:
        unmapped_df = scan_df[scan_df["grouping"].isna()].copy()
        if unmapped_df.empty:
            st.success("All product names in this export have active mappings.")
        else:
            unmapped_df = unmapped_df.sort_values("net_amount", key=lambda col: col.abs(), ascending=False)
            unmapped_df["net_amount"] = unmapped_df["net_amount"].map(lambda value: f"${value:,.2f}")
            st.dataframe(
                unmapped_df.rename(columns={
                    "product_name": "Product Name",
                    "rows": "Rows",
                    "net_amount": "Net Amount",
                })[["Product Name", "Rows", "Net Amount"]],
                hide_index=True,
                use_container_width=True,
            )

    with tab_mapped:
        mapped_df = scan_df[scan_df["grouping"].notna()].copy()
        if mapped_df.empty:
            st.info("No mapped products found in this export.")
        else:
            mapped_df = mapped_df.sort_values(["grouping", "product_name"])
            mapped_df["net_amount"] = mapped_df["net_amount"].map(lambda value: f"${value:,.2f}")
            st.dataframe(
                mapped_df.rename(columns={
                    "product_name": "Product Name",
                    "rows": "Rows",
                    "net_amount": "Net Amount",
                    "grouping": "Grouping",
                    "category": "Category",
                    "allocation_method": "Allocation Method",
                })[["Grouping", "Product Name", "Rows", "Net Amount", "Category", "Allocation Method"]],
                hide_index=True,
                use_container_width=True,
            )

    st.write("### TopScore Reports")
    imported_files = _load_imported_files()
    if imported_files.empty:
        st.info("Import a TopScore export above to populate reports.")
        return

    with st.expander("Imported Files", expanded=False):
        files_display = imported_files.copy()
        files_display["net_amount"] = files_display["net_amount"].map(lambda value: f"${value:,.2f}")
        st.dataframe(
            files_display.rename(columns={
                "source_filename": "File",
                "rows": "Rows",
                "transfers": "Transfers",
                "first_timestamp": "First Timestamp",
                "last_timestamp": "Last Timestamp",
                "net_amount": "Net Amount",
            }),
            hide_index=True,
            use_container_width=True,
        )

    report_tab_summary, report_tab_players = st.tabs(["Grouping Summary", "Player Counts"])

    with report_tab_summary:
        grouping_summary = _load_grouping_summary()
        if grouping_summary.empty:
            st.info("No imported TopScore detail rows found.")
        else:
            summary_display = grouping_summary.copy()
            for column in ["payments", "refunds", "fees", "net_amount"]:
                summary_display[column] = summary_display[column].map(lambda value: f"${value:,.2f}")
            st.dataframe(
                summary_display.rename(columns={
                    "grouping": "Grouping",
                    "rows": "Rows",
                    "positive_payment_count": "Positive Payment Count",
                    "payments": "Payments",
                    "refunds": "Refunds",
                    "fees": "Fees",
                    "net_amount": "Net Amount",
                }),
                hide_index=True,
                use_container_width=True,
            )

    with report_tab_players:
        player_counts = _load_player_counts()
        if player_counts.empty:
            st.info("No High School, Middle School, or Winter payment rows found.")
        else:
            totals = player_counts.groupby("season", as_index=False).agg({
                "payment_rows": "sum",
                "players": "sum",
                "gross_amount": "sum",
            })
            season_order = {
                "High School Fall": 0,
                "High School Winter": 1,
                "High School Spring": 2,
                "Middle School Fall": 3,
                "Middle School Spring": 4,
            }
            totals["_sort"] = totals["season"].map(season_order).fillna(9)
            totals = totals.sort_values(["_sort", "season"]).drop(columns=["_sort"])
            totals["gross_amount"] = totals["gross_amount"].map(lambda value: f"${value:,.2f}")
            st.write("#### Totals by Season Group")
            st.dataframe(
                totals.rename(columns={
                    "season": "Season",
                    "payment_rows": "Payment Items",
                    "players": "Distinct Charge IDs",
                    "gross_amount": "Gross Amount",
                }),
                hide_index=True,
                use_container_width=True,
            )

            detail = player_counts.copy()
            detail["gross_amount"] = detail["gross_amount"].map(lambda value: f"${value:,.2f}")
            st.write("#### Detail by Product")
            st.dataframe(
                detail.rename(columns={
                    "grouping": "Season Group",
                    "season": "Season",
                    "product_name": "Product Name",
                    "payment_rows": "Payment Rows",
                    "players": "Distinct Charge IDs",
                    "gross_amount": "Gross Amount",
                })[["Season", "Season Group", "Product Name", "Payment Rows", "Distinct Charge IDs", "Gross Amount"]],
                hide_index=True,
                use_container_width=True,
            )
