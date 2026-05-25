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

    # 5. Display
    st.write("### Financial Breakdown by Program")
    st.dataframe(pivot_df.style.format("${:,.2f}"))

