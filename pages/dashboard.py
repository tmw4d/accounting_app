import streamlit as st
import database as db
import pandas as pd


TOPSCORE_PROGRAM_ORDER = [
    "High School Fall",
    "High School Winter",
    "High School Spring",
    "Middle School Fall",
    "Middle School Spring",
]

FINANCIAL_PROGRAM_ORDER = [
    "High School Fall",
    "High School Winter",
    "High School Spring",
    "Middle School Fall",
    "Middle School Spring",
    "Total",
    "Unallocated",
]


def render_unreconciled_alert():
    fy_id = st.session_state.selected_fy
    with db.get_connection() as conn:
        unrec_txns = conn.execute("""
            SELECT id, transaction_date, description, amount
            FROM ledger
            WHERE fiscal_year_id = ? AND is_deleted = 0 AND allocation_method_id IS NULL
            ORDER BY transaction_date DESC, id DESC
        """, (fy_id,)).fetchall()

    if unrec_txns:
        unrec_count = len(unrec_txns)
        unrec_sum = sum(t[3] for t in unrec_txns)
        st.warning(
            f"⚠️ **{unrec_count} Unreconciled Transaction(s)** totaling **${unrec_sum:,.2f}** require allocation in this fiscal year."
        )
        with st.expander(f"View {unrec_count} Unreconciled Transaction(s)", expanded=False):
            df = pd.DataFrame(unrec_txns, columns=["ID", "Date", "Description", "Amount"])
            df["Amount"] = df["Amount"].apply(lambda x: f"${x:,.2f}")
            st.dataframe(df[["Date", "Description", "Amount"]], width="stretch", hide_index=True)


def render_dashboard_table():
    render_unreconciled_alert()

    fy_id = st.session_state.selected_fy
    
    with db.get_connection() as conn:
        # 1. Fetch data: Sums grouped by flow, program, and category
        query = """
            SELECT 
                p.name as program_name,
                CASE
                    WHEN lower(c.flow) = 'income' THEN 'Income'
                    WHEN lower(c.flow) = 'expense' THEN 'Expense'
                    WHEN l.amount >= 0 THEN 'Income'
                    ELSE 'Expense'
                END as flow,
                CASE 
                    WHEN (lower(c.flow) = 'expense' OR (c.flow IS NULL AND l.amount < 0)) 
                    THEN - SUM(l.amount * ifnull(ar.percentage,0))
                    ELSE SUM(l.amount * ifnull(ar.percentage,0)) 
                END as total
            FROM ledger l
            LEFT JOIN allocation_methods am ON l.allocation_method_id = am.id
            LEFT JOIN allocation_rules ar ON am.id = ar.method_id
            LEFT JOIN programs p ON ar.program_id = p.id
            LEFT JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ? AND l.is_deleted = 0
            GROUP BY p.name, flow

            UNION ALL

            SELECT 
                'Total' as program_name,
                CASE
                    WHEN lower(c.flow) = 'income' THEN 'Income'
                    WHEN lower(c.flow) = 'expense' THEN 'Expense'
                    WHEN l.amount >= 0 THEN 'Income'
                    ELSE 'Expense'
                END as flow,
                CASE 
                    WHEN (lower(c.flow) = 'expense' OR (c.flow IS NULL AND l.amount < 0)) 
                    THEN - SUM(l.amount)
                    ELSE SUM(l.amount) 
                END as total
            FROM ledger l
            LEFT JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ? AND l.is_deleted = 0
            GROUP BY flow
        """
        df = pd.read_sql_query(query, conn, params=(fy_id, fy_id))

    if df.empty:
        st.write("### Financial Breakdown by Program")
        st.info("No financial data found for the selected fiscal year.")
        render_topscore_player_counts()
        return

    # 2. Pivot the table
    # Rows: Flow (Income/Expense), Columns: Program
    pivot_df = df.pivot_table(index='flow', columns='program_name', values='total', aggfunc='sum', fill_value=0)
    
    # 3. Add "Unallocated" Column
    if "Total" in pivot_df.columns:
        pivot_df['Unallocated'] = pivot_df["Total"] - (pivot_df.sum(axis=1) - pivot_df["Total"])

    for flow_row in ["Income", "Expense"]:
        if flow_row not in pivot_df.index:
            pivot_df.loc[flow_row] = 0

    # 4. Calculate Net Total Row (Income - Expense)
    pivot_df.loc['Total'] = pivot_df.loc['Income'] - pivot_df.loc['Expense']
    
    # Reorder index so Income is first
    pivot_df = pivot_df.reindex(['Income', 'Expense', 'Total'])
    ordered_columns = [col for col in FINANCIAL_PROGRAM_ORDER if col in pivot_df.columns]
    remaining_columns = [col for col in pivot_df.columns if col not in ordered_columns]
    pivot_df = pivot_df[ordered_columns + remaining_columns]
    pivot_df.index.name = "Flow"

    # 5. Display
    st.write("### Financial Breakdown by Program")
    total_col_idx = list(pivot_df.columns).index("Total") if "Total" in pivot_df.columns else None
    total_row_idx = list(pivot_df.index).index("Total") if "Total" in pivot_df.index else None

    def highlight_totals(data):
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        if "Total" in styles.columns:
            styles["Total"] = (
                "background-color: #1f6feb; color: #ffffff; font-weight: 700; "
                "border-left: 2px solid #ffffff55; border-right: 2px solid #ffffff55;"
            )
        if "Total" in styles.index:
            styles.loc["Total", :] = (
                "background-color: #238636; color: #ffffff; font-weight: 700; "
                "border-top: 2px solid #ffffff55; border-bottom: 2px solid #ffffff55;"
            )
            if "Total" in styles.columns:
                styles.loc["Total", "Total"] = (
                    "background-color: #8250df; color: #ffffff; font-weight: 800; "
                    "border: 2px solid #ffffff88;"
                )
        return styles

    table_styles = [
        {"selector": "th", "props": [("font-size", "16px"), ("font-weight", "700")]},
        {"selector": "td", "props": [("font-size", "16px")]},
    ]
    if total_col_idx is not None:
        table_styles.append({
            "selector": f".col{total_col_idx}",
            "props": [
                ("background-color", "#1f6feb"),
                ("color", "#ffffff"),
                ("font-weight", "700"),
            ],
        })
    if total_row_idx is not None:
        table_styles.append({
            "selector": f".row{total_row_idx}",
            "props": [
                ("background-color", "#238636"),
                ("color", "#ffffff"),
                ("font-weight", "700"),
            ],
        })

    styled_df = (
        pivot_df.style
        .format("${:,.0f}")
        .apply(highlight_totals, axis=None)
        .set_properties(**{
            "font-size": "16px",
            "text-align": "right",
            "white-space": "nowrap",
        })
        .set_table_styles(table_styles)
    )
    st.dataframe(
        styled_df,
        width="stretch",
        height=180,
    )

    render_topscore_player_counts()


def render_topscore_player_counts():
    fy_id = st.session_state.selected_fy

    with db.get_connection() as conn:

        has_topscore_table = conn.execute("""
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table'
                AND name = 'topscore_transfer_items'
        """).fetchone()

        if not has_topscore_table:
            return

        query = """
            WITH classified AS (
                SELECT
                    CASE
                        WHEN m.grouping = 'Winter'
                            OR (m.grouping = 'High School' AND t.product_name LIKE '%Winter%')
                            THEN 'High School Winter'
                        WHEN m.grouping = 'High School' AND t.product_name LIKE '%Fall%'
                            THEN 'High School Fall'
                        WHEN m.grouping = 'High School' AND t.product_name LIKE '%Spring%'
                            THEN 'High School Spring'
                        WHEN m.grouping = 'High School'
                            THEN 'High School Other'
                        WHEN m.grouping = 'Middle School' AND t.product_name LIKE '%Fall%'
                            THEN 'Middle School Fall'
                        WHEN m.grouping = 'Middle School' AND t.product_name LIKE '%Spring%'
                            THEN 'Middle School Spring'
                        WHEN m.grouping = 'Middle School'
                            THEN 'Middle School Other'
                        ELSE NULL
                    END AS program_name,
                    ifnull(m.primary_registration_ind, 0) AS primary_registration_ind,
                    t.identifier,
                    t.amount
                FROM topscore_transfer_items t
                JOIN fy ON date(substr(t.transfer_timestamp, 1, 10)) BETWEEN fy.start_date AND fy.end_date
                LEFT JOIN topscore_product_mappings m
                    ON t.product_name = m.product_name
                    AND m.active_ind = 1
                WHERE fy.fiscal_year_id = ?
                    AND lower(t.item_type) = 'payment'
                    AND lower(t.type) = 'payment'
                    AND t.amount > 0
                    AND m.grouping IN ('High School', 'Middle School', 'Winter')
            )
            SELECT
                program_name,
                COUNT(*) AS registration_items,
                SUM(CASE WHEN primary_registration_ind = 1 THEN 1 ELSE 0 END) AS primary_registration_items,
                COUNT(DISTINCT identifier) AS distinct_charge_ids,
                COUNT(DISTINCT CASE WHEN primary_registration_ind = 1 THEN identifier END) AS primary_charge_ids,
                SUM(amount) AS gross_amount
            FROM classified
            WHERE program_name IS NOT NULL
            GROUP BY program_name
        """
        counts_df = pd.read_sql_query(query, conn, params=(fy_id,))

    if counts_df.empty:
        st.write("### TopScore Player Counts by Program")
        st.info("No imported TopScore registration activity found for this fiscal year.")
        return

    ordered_df = pd.DataFrame({"program_name": TOPSCORE_PROGRAM_ORDER})
    ordered_df = ordered_df.merge(counts_df, on="program_name", how="left")

    other_df = counts_df[~counts_df["program_name"].isin(TOPSCORE_PROGRAM_ORDER)]
    if not other_df.empty:
        ordered_df = pd.concat([ordered_df, other_df], ignore_index=True)

    ordered_df[["registration_items", "distinct_charge_ids", "gross_amount"]] = ordered_df[
        ["registration_items", "distinct_charge_ids", "gross_amount"]
    ].fillna(0)
    ordered_df[["primary_registration_items", "primary_charge_ids"]] = ordered_df[
        ["primary_registration_items", "primary_charge_ids"]
    ].fillna(0)

    metrics_df = ordered_df.set_index("program_name")[
        [
            "primary_registration_items",
            "registration_items",
            "primary_charge_ids",
            "distinct_charge_ids",
            "gross_amount",
        ]
    ].T
    metrics_df.index = [
        "Primary Registration Items",
        "All Registration Items",
        "Primary Charge IDs",
        "All Distinct Charge IDs",
        "Gross Amount",
    ]
    metrics_df["Total"] = metrics_df.sum(axis=1)
    metrics_df.index.name = "Metric"

    st.write("### TopScore Player Counts by Program")
    st.caption("Based on imported TopScore payment rows in the selected fiscal year. Primary rows use products tagged as primary registrations in Configuration > TopScore Products.")

    def highlight_total_column(data):
        styles = pd.DataFrame("", index=data.index, columns=data.columns)
        if "Total" in styles.columns:
            styles["Total"] = "background-color: #1f6feb; color: #ffffff; font-weight: 700;"
        return styles

    styled_counts = (
        metrics_df.style
        .format(lambda value: f"{float(value):,.0f}")
        .format(lambda value: f"${float(value):,.0f}", subset=pd.IndexSlice[["Gross Amount"], :])
        .apply(highlight_total_column, axis=None)
        .set_properties(**{
            "font-size": "16px",
            "text-align": "right",
            "white-space": "nowrap",
        })
    )

    st.dataframe(
        styled_counts,
        width="stretch",
        height=180,
    )

