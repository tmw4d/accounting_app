import streamlit as st
import pandas as pd
import sqlite3
import os
from datetime import datetime

import database as db

def render():
    st.title("📥 Import Bank Files")
    st.write("Upload a bank statement CSV file. Preview records, match pending items, and identify potential duplicates before writing to the ledger.")


    # Initialize key versioning for file uploader to allow programmatic resets
    if "uploader_version" not in st.session_state:
        st.session_state.uploader_version = 0

    # --- helper functions ---
    def clean_currency(x):
        if pd.isna(x):
            return 0.0
        if isinstance(x, (int, float)):
            return float(x)
        x_str = str(x).strip().replace('$', '').replace(',', '')
        if not x_str:
            return 0.0
        # Handle parentheses representing negatives, e.g. ($41.00)
        if '(' in x_str and ')' in x_str:
            x_str = '-' + x_str.replace('(', '').replace(')', '')
        try:
            return float(x_str)
        except ValueError:
            return 0.0

    def clean_optional_currency(x):
        if pd.isna(x) or not str(x).strip():
            return None
        return clean_currency(x)

    def clean_date(x):
        if pd.isna(x):
            return ""
        x_str = str(x).strip()
        try:
            # Parse standard formats and format into YYYY-MM-DD
            dt = pd.to_datetime(x_str, errors='coerce')
            if pd.notna(dt):
                return dt.strftime('%Y-%m-%d')
            return x_str
        except Exception:
            return x_str

    def clean_check_num(x):
        if pd.isna(x):
            return ""
        x_str = str(x).strip()
        # Remove trailing .0 from float conversion (e.g. "975014.0" -> "975014")
        if x_str.endswith('.0'):
            x_str = x_str[:-2]
        if x_str.lower() in ('nan', 'none', '<null>', ''):
            return ""
        return x_str

    def detect_and_parse_csv(file_source):
        # Read the first few lines of the file to inspect headers
        try:
            if isinstance(file_source, str):
                with open(file_source, 'r', encoding='utf-8', errors='ignore') as f:
                    lines = [f.readline().strip() for _ in range(10)]
            else:
                file_source.seek(0)
                lines = [file_source.readline().decode('utf-8', errors='ignore').strip() for _ in range(10)]
                file_source.seek(0)
        except Exception as e:
            return None, f"Error reading file headers: {str(e)}"
        
        format_type = None
        header_idx = None
        
        for idx, line in enumerate(lines):
            # Format A check (Direct Bank statement)
            if "Posted Date" in line and "Full description" in line:
                format_type = "Format_A"
                header_idx = idx
                break
            # Format B check (Historical Ledger Export)
            if "Date" in line and "Transaction Type" in line and "Check Number" in line:
                format_type = "Format_B"
                header_idx = idx
                break

        if not format_type:
            return None, "Unsupported CSV format. Could not locate standard header columns (e.g. 'Posted Date' or 'Check Number')."

        try:
            if isinstance(file_source, str):
                df = pd.read_csv(file_source, skiprows=header_idx)
            else:
                file_source.seek(0)
                df = pd.read_csv(file_source, skiprows=header_idx)
            
            if format_type == "Format_A":
                parsed_df = pd.DataFrame({
                    'date': df['Transaction Date'].apply(clean_date),
                    'description': df['Full description'].astype(str).str.strip(),
                    'amount': df['Amount'].apply(clean_currency),
                    'daily_posted_balance': df['Daily Posted Balance'].apply(clean_optional_currency) if 'Daily Posted Balance' in df.columns else None,
                    'check_number': df['Check/Serial #'].apply(clean_check_num),
                    'category_name': df['Category name'].fillna('').astype(str).str.strip().replace('nan', ''),
                    'transaction_type': df['Transaction Type'].astype(str).str.strip().str.lower(),
                    'notes': None,
                    'more_notes': None
                })
            else: # Format_B
                parsed_df = pd.DataFrame({
                    'date': df['Date'].apply(clean_date),
                    'description': df['Description'].astype(str).str.strip(),
                    'amount': df['Amount'].apply(clean_currency),
                    'daily_posted_balance': df['Daily Posted Balance'].apply(clean_optional_currency) if 'Daily Posted Balance' in df.columns else None,
                    'check_number': df['Check Number'].apply(clean_check_num),
                    'category_name': df['Category'].fillna('').astype(str).str.strip().replace('nan', ''),
                    'transaction_type': df['Transaction Type'].astype(str).str.strip().str.lower(),
                    'notes': df['Notes'].fillna('').astype(str).str.strip().replace('nan', '') if 'Notes' in df.columns else None,
                    'more_notes': df['More Notes'].fillna('').astype(str).str.strip().replace('nan', '') if 'More Notes' in df.columns else None
                })

            # Drop completely empty dates/amounts or rows that duplicate headers
            parsed_df = parsed_df.dropna(subset=['date', 'amount'])
            parsed_df = parsed_df[parsed_df['date'] != '']
            parsed_df = parsed_df[parsed_df['date'].str.lower() != 'date']
            
            # Reset index
            parsed_df = parsed_df.reset_index(drop=True)
            return parsed_df, format_type
        except Exception as e:
            return None, f"Error parsing CSV content: {str(e)}"

    st.write("### 📂 Upload Bank Statement CSV")
    uploaded_file = st.file_uploader(
        "Upload CSV statement", 
        type=["csv"], 
        key=f"uploader_{st.session_state.uploader_version}"
    )

    if uploaded_file is None:
        st.info("Please upload a bank CSV file to continue.")
        return

    # --- B. PARSE CSV ---
    parsed_data, parse_status = detect_and_parse_csv(uploaded_file)


    if parsed_data is None:
        st.error(parse_status)
        return

    st.success(f"Successfully parsed **{len(parsed_data)}** transactions using **{parse_status.replace('_', ' ')}**!")

    # Fetch reference mappings from DB
    with db.get_connection() as conn:

        categories = conn.execute("SELECT id, name FROM categories").fetchall()
        cat_map = {c[1].lower(): c[0] for c in categories}
        cat_id_name_map = {c[0]: c[1] for c in categories}

    # Normalize category mapping
    def map_category(cat_name):
        if not cat_name:
            return None
        c_name = str(cat_name).lower()
        if c_name in cat_map:
            return cat_map[c_name]
        return None

    parsed_data['category_id'] = parsed_data['category_name'].apply(map_category)
    parsed_data['allocation_method_id'] = None  # Will be mapped if pending matches

    # --- C. DUPLICATE CHECKING PROCESS ---
    st.write("### 🛡️ Duplicate Verification Staging")

    # Get transaction dates range to pull ledger transactions for matching
    min_date = parsed_data['date'].min()
    max_date = parsed_data['date'].max()

    with db.get_connection() as conn:

        ledger_rows = conn.execute("""
            SELECT id, transaction_date, amount, description, check_number 
            FROM ledger 
            WHERE transaction_date >= ? AND transaction_date <= ? AND is_deleted = 0
        """, (min_date, max_date)).fetchall()

        # Fetch active pending transactions for matching
        pending_rows = conn.execute("""
            SELECT id, amount, category_id, allocation_method_id, notes, more_notes 
            FROM ledger 
            WHERE transaction_date = 'pending' AND is_deleted = 0
        """).fetchall()

    # Organize ledger rows by date for fast and correct multi-occurrence matching
    ledger_by_date = {}
    for lid, tdate, amount, desc, check_num in ledger_rows:
        if tdate not in ledger_by_date:
            ledger_by_date[tdate] = []
        ledger_by_date[tdate].append({
            'id': lid,
            'amount': float(amount),
            'description': str(desc or '').strip(),
            'check_number': clean_check_num(check_num),
            'matched': False
        })

    # Prepare pending list for multi-occurrence amount matching
    pending_list = [{
        'id': r[0],
        'amount': float(r[1]),
        'category_id': r[2],
        'allocation_method_id': r[3],
        'notes': r[4],
        'more_notes': r[5],
        'matched': False
    } for r in pending_rows]

    # Fetch the latest prior import date to run date-based sanity checks
    with db.get_connection() as conn:

        res = conn.execute("""
            SELECT MAX(transaction_date) FROM ledger 
            WHERE source_indicator IN ('Bank', 'Import') AND is_deleted = 0 AND transaction_date != 'pending'
        """).fetchone()
        max_imported_date = res[0] if res else None

    staged_list = []
    new_count = 0
    duplicate_count = 0

    for idx, row in parsed_data.iterrows():
        s_date = row['date']
        s_amount = float(row['amount'])
        s_desc = str(row['description'] or '').strip()
        s_check = clean_check_num(row['check_number'])
        
        match_found = False
        matched_id = None
        matched_desc = ""
        
        # Check against ledger rows of the same date
        if s_date in ledger_by_date:
            for l_tx in ledger_by_date[s_date]:
                if l_tx['matched']:
                    continue
                
                # Check 1: Amount must match exactly
                if abs(l_tx['amount'] - s_amount) >= 0.01:
                    continue
                
                # Check 2: Check numbers must match if either exists
                l_check = l_tx['check_number']
                if (l_check and s_check and l_check != s_check):
                    continue
                
                # Check 3: Description similarity (exact match or substring match)
                l_desc_lower = l_tx['description'].lower()
                s_desc_lower = s_desc.lower()
                if (l_desc_lower == s_desc_lower or 
                    l_desc_lower in s_desc_lower or 
                    s_desc_lower in l_desc_lower):
                    
                    l_tx['matched'] = True
                    match_found = True
                    matched_id = l_tx['id']
                    matched_desc = l_tx['description']
                    break
        
        staged_row = row.copy()
        
        # Date-based duplication sanity check (red-flag for dates earlier than latest import)
        is_prior_date = False
        if max_imported_date and s_date < max_imported_date:
            is_prior_date = True

        if match_found:
            staged_row['status'] = 'Potential Duplicate'
            staged_row['matched_ledger_id'] = matched_id
            staged_row['matched_ledger_desc'] = f"🚩 Exact Match: ID {matched_id} - {matched_desc}" if is_prior_date else f"ID {matched_id} - {matched_desc}"
            staged_row['matched_pending_id'] = None
            staged_row['matched_pending_desc'] = ""
            duplicate_count += 1
        elif is_prior_date:
            staged_row['status'] = 'Potential Duplicate'
            staged_row['matched_ledger_id'] = None
            staged_row['matched_ledger_desc'] = f"🚩 Prior Date Warning: Date {s_date} is earlier than latest import date ({max_imported_date})"
            staged_row['matched_pending_id'] = None
            staged_row['matched_pending_desc'] = ""
            duplicate_count += 1
        else:
            staged_row['status'] = 'New (Ready to Import)'
            staged_row['matched_ledger_id'] = None
            staged_row['matched_ledger_desc'] = ""
            
            # --- PENDING TRANSACTION MATCHING ---
            matched_pending_id = None
            matched_pending_desc = ""
            
            for p_tx in pending_list:
                if p_tx['matched']:
                    continue
                # Amount matches pending amount exactly
                if abs(p_tx['amount'] - s_amount) < 0.01:
                    p_tx['matched'] = True
                    matched_pending_id = p_tx['id']
                    matched_pending_desc = f"Pending ID {p_tx['id']} ({p_tx['notes'] or 'No Notes'})"
                    
                    # Inherit category and allocation method from pending
                    if p_tx['category_id']:
                        staged_row['category_id'] = p_tx['category_id']
                    if p_tx['allocation_method_id']:
                        staged_row['allocation_method_id'] = p_tx['allocation_method_id']
                    
                    # Inherit/Merge notes
                    staged_row['notes'] = p_tx['notes'] if p_tx['notes'] else staged_row['notes']
                    staged_row['more_notes'] = p_tx['more_notes'] if p_tx['more_notes'] else staged_row['more_notes']
                    break
            
            staged_row['matched_pending_id'] = matched_pending_id
            staged_row['matched_pending_desc'] = matched_pending_desc
            new_count += 1
            
        staged_list.append(staged_row)

    staged_df = pd.DataFrame(staged_list)

    # Display Metrics Bar
    mcol1, mcol2, mcol3 = st.columns(3)
    mcol1.metric("Total Parsed", f"{len(staged_df)} transactions")
    mcol2.metric("New to Import", f"{new_count} transactions", delta=f"+{new_count}", delta_color="normal")
    mcol3.metric("Potential Duplicates", f"{duplicate_count} transactions", delta=f"{duplicate_count} matches", delta_color="inverse")

    # --- D. TABS FOR REVIEW ---
    tab1, tab2 = st.tabs(["🆕 New Transactions", "⚠️ Potential Duplicates"])
    
    with tab1:
        new_tx = staged_df[staged_df['status'] == 'New (Ready to Import)'].copy()
        if new_tx.empty:
            st.info("No new transactions found in this statement.")
        else:
            # Map category ID to printable name for preview
            new_tx['Mapped Category'] = new_tx['category_id'].apply(lambda x: cat_id_name_map.get(x, 'Uncategorized'))
            
            # Format Pending Match column nicely
            def format_pending_match(desc):
                if not desc:
                    return "None"
                return f"🔗 {desc}"
            new_tx['Pending Match'] = new_tx['matched_pending_desc'].apply(format_pending_match)
            
            st.write("The following transactions will be written to the ledger:")
            st.dataframe(
                new_tx[['date', 'description', 'amount', 'daily_posted_balance', 'check_number', 'Mapped Category', 'Pending Match']],
                width="stretch"
            )
            
    with tab2:
        dup_tx = staged_df[staged_df['status'] == 'Potential Duplicate'].copy()
        if dup_tx.empty:
            st.success("No potential duplicates identified!")
        else:
            def format_conflict(row):
                if pd.isna(row['matched_ledger_id']) or row['matched_ledger_id'] is None:
                    return row['matched_ledger_desc']
                return f"ID {row['matched_ledger_id']} - {row['matched_ledger_desc']}"
            dup_tx['Conflict Ledger Transaction'] = dup_tx.apply(format_conflict, axis=1)
            
            st.warning("The following transactions appear to match records already in the ledger or fall prior to the latest import date. They will be skipped by default.")
            st.dataframe(
                dup_tx[['date', 'description', 'amount', 'check_number', 'Conflict Ledger Transaction']],
                width="stretch"
            )

    st.divider()

    # --- E. IMPORT CONFIRMATION & ACTIONS ---
    st.write("### ⚙️ Import Options")
    
    skip_duplicates = st.checkbox("Skip potential duplicates (Recommended)", value=True, help="If checked, only transactions flagged as 'New' will be written to the ledger.")
    
    # Pre-calculate what will be written
    to_import_df = staged_df[staged_df['status'] == 'New (Ready to Import)']
    if not skip_duplicates:
        to_import_df = staged_df.copy()
        
    st.write(f"Ready to import **{len(to_import_df)}** transactions to the ledger database.")

    # Render Confirm and Cancel buttons side-by-side
    col_btn1, col_btn2 = st.columns([1, 1])
    
    confirm_btn = col_btn1.button("🚀 Confirm Import and Write to Ledger", type="primary", disabled=len(to_import_df) == 0, width="stretch")
    cancel_btn = col_btn2.button("❌ Cancel & Reset Selection", type="secondary", width="stretch")

    if cancel_btn:
        # Increment uploader version to clear file upload buffer in UI
        st.session_state.uploader_version += 1
        st.info("Import cancelled. Resetting file selection...")
        st.rerun()

    if confirm_btn:
        imported_success_count = 0
        removed_pending_count = 0
        
        with db.get_connection() as conn:

            # Fetch active Fiscal Year to fall back to if dates are out of bounds
            active_year = conn.execute("SELECT fiscal_year_id FROM fy WHERE active_ind = 1").fetchone()
            default_fy_id = active_year[0] if active_year else st.session_state.selected_fy
            
            # Map fiscal years lookup in memory for efficiency
            fy_rows = conn.execute("SELECT fiscal_year_id, start_date, end_date FROM fy").fetchall()
            
            for _, row in to_import_df.iterrows():
                tx_date = row['date']
                tx_desc = row['description']
                tx_amt = row['amount']
                tx_daily_posted_balance = row.get('daily_posted_balance')
                if pd.isna(tx_daily_posted_balance):
                    tx_daily_posted_balance = None
                tx_check = row['check_number'] or None
                tx_cat = row['category_id']
                tx_notes = row['notes'] or None
                tx_more_notes = row['more_notes'] or None
                
                # Fetch inherited allocation method if present
                tx_alloc = row.get('allocation_method_id') if pd.notna(row.get('allocation_method_id')) else None
                
                # Determine transaction type (debit/credit)
                tx_type = 'debit' if tx_amt < 0 else 'credit'
                
                # Determine correct Fiscal Year dynamically based on date
                matched_fy = default_fy_id
                for fy_id, start, end in fy_rows:
                    if tx_date >= start and tx_date <= end:
                        matched_fy = fy_id
                        break
                
                # Insert into ledger
                conn.execute("""
                    INSERT INTO ledger (
                        transaction_date, description, amount, transaction_type, check_number, daily_posted_balance,
                        category_id, fiscal_year_id, allocation_method_id, source_indicator, notes, more_notes, is_deleted
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'Bank', ?, ?, 0)
                """, (tx_date, tx_desc, tx_amt, tx_type, tx_check, tx_daily_posted_balance, tx_cat, matched_fy, tx_alloc, tx_notes, tx_more_notes))
                
                # Soft delete matched pending transaction if one exists
                matched_p_id = row.get('matched_pending_id')
                if matched_p_id and pd.notna(matched_p_id):
                    conn.execute("UPDATE ledger SET is_deleted = 1 WHERE id = ?", (int(matched_p_id),))
                    removed_pending_count += 1
                
                imported_success_count += 1

            db.recalculate_running_balances(conn)
            conn.commit()
            
        success_msg = f"Import Complete! Successfully added **{imported_success_count}** bank transactions to the ledger."
        if removed_pending_count > 0:
            success_msg += f" Automatically reconciled and removed **{removed_pending_count}** matched pending transactions."
            
        st.success(success_msg)
        st.balloons()
        st.rerun()
