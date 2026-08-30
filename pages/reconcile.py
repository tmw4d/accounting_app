import database as db
import pandas as pd
import streamlit as st

def render_reconciliation():
    st.title("⚖️ Reconcile Expenses & Income")
    st.write("Review, categorize, and allocate ledger transactions. Focus on pending allocations or bulk-reconcile recurring items.")

    # Fetch reference tables for dropdowns and mappings
    with db.get_connection() as conn:

        methods = conn.execute("SELECT id, name FROM allocation_methods ORDER BY name").fetchall()
        method_map = {m[1]: m[0] for m in methods}
        
        categories = conn.execute("SELECT id, name, flow FROM categories WHERE active_ind = 1 ORDER BY flow, name").fetchall()
        # Create map showing flow type for better clarity (e.g. "Expense - Supplies")
        cat_map = {f"{c[2]} - {c[1]}": c[0] for c in categories}
        cat_reverse_map = {c[0]: f"{c[2]} - {c[1]}" for c in categories}

        fiscal_years = conn.execute("SELECT fiscal_year_id, name FROM fy ORDER BY start_date DESC").fetchall()
        fy_map = {f[1]: f[0] for f in fiscal_years}

    fy_id = st.session_state.selected_fy

    # --- 1. METRICS PANEL ---
    with db.get_connection() as conn:

        # Get count and sums for progress
        total_stats = conn.execute("""
            SELECT COUNT(*), SUM(amount) FROM ledger 
            WHERE fiscal_year_id = ? AND is_deleted = 0
        """, (fy_id,)).fetchone()
        
        unallocated_stats = conn.execute("""
            SELECT COUNT(*), SUM(amount) FROM ledger 
            WHERE fiscal_year_id = ? AND is_deleted = 0 AND allocation_method_id IS NULL
        """, (fy_id,)).fetchone()

    total_count = total_stats[0] if total_stats else 0
    total_sum = total_stats[1] if total_stats and total_stats[1] is not None else 0.0
    
    unallocated_count = unallocated_stats[0] if unallocated_stats else 0
    unallocated_sum = unallocated_stats[1] if unallocated_stats and unallocated_stats[1] is not None else 0.0
    
    allocated_count = total_count - unallocated_count
    allocated_sum = total_sum - unallocated_sum

    progress_pct = (allocated_count / total_count * 100.0) if total_count > 0 else 100.0
    progress_val = (allocated_count / total_count) if total_count > 0 else 1.0

    st.write("### 📈 Reconciliation Progress")
    col1, col2, col3 = st.columns(3)
    col1.metric("Pending Reconciliation", f"{unallocated_count} txns", f"${unallocated_sum:,.2f}", delta_color="inverse")
    col2.metric("Allocated / Completed", f"{allocated_count} txns", f"${allocated_sum:,.2f}")
    col3.metric("Completion Rate", f"{progress_pct:.1f}%")
    st.progress(progress_val)

    st.divider()

    # --- 2. BULK RECONCILIATION TOOL ---
    st.write("### 🛠️ Bulk Reconciliation Tool")
    with st.expander("💡 Match and reconcile multiple transactions at once", expanded=False):
        bulk_keyword = st.text_input("Search Keyword for Bulk Selection", value="TopScore", help="Finds unallocated transactions with descriptions containing this text.")
        
        if bulk_keyword:
            with db.get_connection() as conn:

                bulk_txns = conn.execute("""
                    SELECT id, transaction_date, description, amount 
                    FROM ledger 
                    WHERE fiscal_year_id = ? AND is_deleted = 0 
                    AND allocation_method_id IS NULL 
                    AND description LIKE ?
                """, (fy_id, f"%{bulk_keyword}%")).fetchall()
            
            if not bulk_txns:
                st.info(f"No unallocated transactions found containing '{bulk_keyword}'.")
            else:
                st.success(f"Found **{len(bulk_txns)}** unallocated transactions matching **'{bulk_keyword}'**!")
                
                # Show preview table
                df_preview = pd.DataFrame(bulk_txns, columns=["ID", "Date", "Description", "Amount"])
                df_preview["Amount"] = df_preview["Amount"].apply(lambda x: f"${x:,.2f}")
                st.dataframe(df_preview[["Date", "Description", "Amount"]], width="stretch")
                
                st.write("#### Apply Reconciliation values to all matching transactions:")
                with st.form("bulk_reconcile_form"):
                    bc1, bc2, bc3 = st.columns(3)
                    
                    bulk_method = bc1.selectbox("Select Allocation Method", ["None"] + list(method_map.keys()), key="bulk_meth_sel")
                    bulk_cat = bc2.selectbox("Select Category", ["Uncategorized"] + list(cat_map.keys()), key="bulk_cat_sel")
                    bulk_fy = bc3.selectbox("Fiscal Year", ["Keep Current FY"] + list(fy_map.keys()), key="bulk_fy_sel")
                    
                    bulk_note = bc1.text_input("Notes (applies to all)", value=f"Bulk Reconciled - {bulk_keyword}")
                    bulk_more_note = bc2.text_area("More Notes", value="", height=68)
                    
                    if st.form_submit_button(f"Apply changes to all {len(bulk_txns)} matching transactions"):
                        method_db_id = method_map[bulk_method] if bulk_method != "None" else None
                        category_db_id = cat_map[bulk_cat] if bulk_cat != "Uncategorized" else None
                        target_fy_id = fy_map[bulk_fy] if bulk_fy != "Keep Current FY" else None
                        
                        bulk_ids = [t[0] for t in bulk_txns]
                        
                        with db.get_connection() as conn:

                            # Build parameterized query for IDs list
                            placeholders = ",".join("?" for _ in bulk_ids)
                            if target_fy_id is not None:
                                conn.execute(f"""
                                    UPDATE ledger 
                                    SET allocation_method_id = ?, category_id = ?, fiscal_year_id = ?, notes = ?, more_notes = ?
                                    WHERE id IN ({placeholders})
                                """, (method_db_id, category_db_id, target_fy_id, bulk_note, bulk_more_note, *bulk_ids))
                            else:
                                conn.execute(f"""
                                    UPDATE ledger 
                                    SET allocation_method_id = ?, category_id = ?, notes = ?, more_notes = ?
                                    WHERE id IN ({placeholders})
                                """, (method_db_id, category_db_id, bulk_note, bulk_more_note, *bulk_ids))
                            db.recalculate_running_balances(conn)
                            conn.commit()
                        
                        st.success(f"Successfully reconciled {len(bulk_txns)} transactions!")
                        st.rerun()

    st.divider()

    # --- 3. ADVANCED SEARCH & FILTERING ---
    st.write("### 🔍 Filter and Sort Transactions")
    fc1, fc2, fc3, fc4 = st.columns(4)
    
    with fc1:
        search_q = st.text_input("Text Search", value="", placeholder="Search description/notes...", help="Matches description, notes, or more notes.")
    with fc2:
        status_filter = st.selectbox(
            "Reconciliation Status", 
            ["Unallocated (Pending)", "Allocated (Completed)", "All Transactions"],
            index=0
        )
    with fc3:
        cat_filter_names = ["All Categories"] + list(cat_map.keys())
        selected_cat_filter = st.selectbox("Category Filter", cat_filter_names)
    with fc4:
        sort_by = st.selectbox(
            "Sort Order", 
            ["Date (Newest First)", "Date (Oldest First)", "Amount (Largest First)", "Amount (Smallest First)"],
            index=0
        )

    # Build SQL Query dynamically based on search & filters
    query = """
        SELECT 
            l.id, l.transaction_date, l.description, l.amount, l.transaction_type, 
            l.check_number, l.source_indicator, l.notes, l.more_notes, 
            l.category_id, l.allocation_method_id, am.name as method_name
        FROM ledger l
        LEFT JOIN categories c ON l.category_id = c.id
        LEFT JOIN allocation_methods am ON l.allocation_method_id = am.id
        WHERE l.fiscal_year_id = ? AND l.is_deleted = 0
    """
    params = [fy_id]

    if status_filter == "Unallocated (Pending)":
        query += " AND l.allocation_method_id IS NULL"
    elif status_filter == "Allocated (Completed)":
        query += " AND l.allocation_method_id IS NOT NULL"

    if selected_cat_filter != "All Categories":
        query += " AND l.category_id = ?"
        params.append(cat_map[selected_cat_filter])

    if search_q:
        query += " AND (l.description LIKE ? OR l.notes LIKE ? OR l.more_notes LIKE ?)"
        like_q = f"%{search_q}%"
        params.extend([like_q, like_q, like_q])

    # Add sorting logic
    if sort_by == "Date (Newest First)":
        query += " ORDER BY l.transaction_date DESC, l.id DESC"
    elif sort_by == "Date (Oldest First)":
        query += " ORDER BY l.transaction_date ASC, l.id ASC"
    elif sort_by == "Amount (Largest First)":
        query += " ORDER BY abs(l.amount) DESC"
    elif sort_by == "Amount (Smallest First)":
        query += " ORDER BY abs(l.amount) ASC"

    with db.get_connection() as conn:

        txns = conn.execute(query, params).fetchall()

    if not txns:
        st.info("No transactions found matching the filter criteria.")
        return

    # --- 4. PAGINATION ---
    page_size = 15
    total_txns = len(txns)
    num_pages = max(1, (total_txns + page_size - 1) // page_size)
    
    col_p1, col_p2 = st.columns([3, 1])
    with col_p1:
        st.write(f"Showing {total_txns} transactions matching criteria.")
    with col_p2:
        page_num = st.selectbox("Page", range(1, num_pages + 1), index=0)

    start_idx = (page_num - 1) * page_size
    end_idx = min(start_idx + page_size, total_txns)
    paginated_txns = txns[start_idx:end_idx]

    # --- 5. RENDER TRANSACTION CARDS ---
    for txn in paginated_txns:
        tid, tdate, desc, amt, tx_type, check_num, src_ind, notes, more_notes, category_id, alloc_method_id, method_name = txn
        
        # Color coding for amounts
        amt_color = "red" if amt < 0 else "green"
        flow_type = "Expense" if amt < 0 else "Income"
        
        with st.container(border=True):
            # Header Columns
            h1, h2, h3 = st.columns([3, 1, 1])
            h1.markdown(f"#### {desc}")
            h2.markdown(f"📅 **Date:** `{tdate}`")
            h3.markdown(f"💰 **Amount:** :{amt_color}[${amt:,.2f}]")
            
            # Metadata Badges
            b1, b2, b3, b4 = st.columns(4)
            b1.markdown(f"🏷️ **Type:** ` {flow_type} `")
            b2.markdown(f"🔌 **Source:** ` {src_ind or 'Manual'} `")
            
            # Map category flow for visual clarity
            cat_display = cat_reverse_map.get(category_id, "Uncategorized")
            b3.markdown(f"📂 **Category:** ` {cat_display.split(' - ')[-1]} `")
            b4.markdown(f"🔢 **Check #:** `{check_num or 'N/A'}`")
            
            # Display notes if any
            if notes or more_notes:
                notes_text = []
                if notes:
                    notes_text.append(f"**Primary Note:** {notes}")
                if more_notes:
                    notes_text.append(f"**Extended Note:** {more_notes}")
                st.info("\n\n".join(notes_text))
            
            # Reconciliation form for this transaction card
            with st.expander("✏️ Reconcile / Edit Transaction Details", expanded=False):
                with st.form(f"form_card_{tid}"):
                    c1, c2 = st.columns(2)
                    
                    # Allocation select index
                    method_idx = 0
                    if alloc_method_id and alloc_method_id in method_map.values():
                        method_idx = list(method_map.values()).index(alloc_method_id) + 1
                    
                    sel_method = c1.selectbox(
                        "Allocation Method",
                        ["None"] + list(method_map.keys()),
                        index=method_idx,
                        key=f"sel_meth_{tid}"
                    )
                    
                    # Category select index
                    category_idx = 0
                    if category_id and category_id in cat_map.values():
                        # Find matching category key
                        rev_key = cat_reverse_map.get(category_id)
                        if rev_key in cat_map:
                            category_idx = list(cat_map.keys()).index(rev_key) + 1
                    
                    sel_cat = c2.selectbox(
                        "Transaction Category",
                        ["Uncategorized"] + list(cat_map.keys()),
                        index=category_idx,
                        key=f"sel_cat_{tid}"
                    )
                    
                    notes_val = c1.text_input("Primary Note", value=notes or "", key=f"note_{tid}")
                    more_notes_val = c2.text_area("Extended Notes", value=more_notes or "", key=f"more_note_{tid}", height=68)
                    
                    if st.form_submit_button("Save Allocation"):
                        method_db_id = method_map[sel_method] if sel_method != "None" else None
                        category_db_id = cat_map[sel_cat] if sel_cat != "Uncategorized" else None
                        
                        with db.get_connection() as conn:

                            conn.execute("""
                                UPDATE ledger 
                                SET allocation_method_id = ?, category_id = ?, notes = ?, more_notes = ?
                                WHERE id = ?
                            """, (method_db_id, category_db_id, notes_val, more_notes_val, tid))
                            
                        st.success("Reconciliation saved!")
                        st.rerun()
