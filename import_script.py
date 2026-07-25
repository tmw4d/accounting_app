import pandas as pd
import database as db

# ~/Downloads/'YULA Financial Management - 2025-2026 - BankAccount_mod.csv'


def import_csv_with_mapping(file_path):
    # 1. Load data
    df = pd.read_csv(file_path)
    
    # 2. Connect and get Category mapping
    conn = db.get_connection()
    # Fetch {name: id} pairs from categories table
    cat_map = pd.read_sql("SELECT name, id FROM categories", conn).set_index('name')['id'].to_dict()
    
    # 3. Clean and map
    def clean_currency(x):
        if isinstance(x, str):
            x = x.replace('$', '').replace(',', '')
            if '(' in x or '-' in x:
                return -float(x.replace('(', '').replace(')', '').replace('-', ''))
            return float(x)
        return x

    df['amount'] = df['Amount'].apply(clean_currency)
    df['daily_posted_balance'] = df['Daily Posted Balance'].apply(clean_currency)
    
    # Map CSV 'Category' to category_id
    # If not found, it defaults to None (NULL in SQL)
    df['category_id'] = df['Category'].map(cat_map)

    # 4. Prepare for Ledger
    ledger_df = pd.DataFrame({
        'transaction_date': df['Date'],
        'description': df['Description'],
        'amount': df['amount'],
        'daily_posted_balance': df['daily_posted_balance'],
        'transaction_type': df['Transaction Type'],
        'category_id': df['category_id'],
        'source_indicator': 'Bank',
        'is_deleted': 0
        # Add other fields here as needed
    })

    # 5. Insert
    ledger_df.to_sql('ledger', conn, if_exists='append', index=False)
    db.recalculate_running_balances(conn)
    conn.commit()
    conn.close()
    print("Import complete with category mapping applied.")
