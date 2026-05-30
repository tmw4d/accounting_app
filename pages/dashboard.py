import streamlit as st
import sqlite3
import pandas as pd

def render_dashboard_table():
    fy_id = st.session_state.selected_fy
    
    with sqlite3.connect("data/ledger.db") as conn:
        # 1. Fetch data: Sums grouped by flow, program, and category
        query = """
            SELECT 
                p.name as program_name,
                ifnull(c.flow, 'Uncategorized') as flow,
                case when c.flow = 'expense' 
                    then - SUM(l.amount * ifnull(ar.percentage,0))
                    else SUM(l.amount * ifnull(ar.percentage,0)) 
                    end as total
            FROM ledger l
            left join allocation_methods am on l.allocation_method_id = am.id
            left join allocation_rules ar on am.id = ar.method_id
            left JOIN programs p ON ar.program_id = p.id
            left JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ?
            GROUP BY p.name, c.flow
            union all
            SELECT 
                'Total' as program_name,
                ifnull(c.flow, 'Uncategorized') as flow,
                SUM(l.amount) as total
            FROM ledger l
            left JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ?
            GROUP BY c.flow

        """
        df = pd.read_sql_query(query, conn, params=(fy_id,fy_id,))

    # 2. Pivot the table
    # Rows: Flow (Income/Expense), Columns: Program
    pivot_df = df.pivot_table(index='flow', columns='program_name', values='total', aggfunc='sum', fill_value=0)
    
    # 3. Add "All Programs" Column (Sum across rows)
    pivot_df['Unallocated'] = pivot_df["Total"] - (pivot_df.sum(axis=1) - pivot_df["Total"])
    
    # 4. Calculate Total Row (Income - Expense)
    # We treat Income as positive and Expense as negative based on your flow logic
    pivot_df.loc['Total'] = pivot_df.loc['Income'] + pivot_df.loc['Expense']
    
    # Reorder index so Income is first
    pivot_df = pivot_df.reindex(['Income', 'Expense', 'Total'])
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
        use_container_width=True,
        height=180,
    )

