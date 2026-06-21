import sqlite3

def get_connection():
    return sqlite3.connect("data/ledger.db")


def recalculate_running_balances(conn):
    """Rebuild stored balances for all active, posted ledger transactions."""
    conn.execute("""
        UPDATE ledger
        SET running_balance = NULL
        WHERE is_deleted != 0
            OR transaction_date = 'pending'
    """)
    conn.execute("""
        WITH calculated_balances AS (
            SELECT
                id,
                ROUND(
                    SUM(amount) OVER (
                        ORDER BY transaction_date, id
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    2
                ) AS balance
            FROM ledger
            WHERE is_deleted = 0
                AND transaction_date != 'pending'
        )
        UPDATE ledger
        SET running_balance = (
            SELECT balance
            FROM calculated_balances
            WHERE calculated_balances.id = ledger.id
        )
        WHERE id IN (SELECT id FROM calculated_balances)
    """)


def set_transaction_deleted(transaction_id, is_deleted, conn=None):
    """Soft-delete or restore one transaction and immediately rebuild balances."""
    owns_connection = conn is None
    active_conn = conn or get_connection()
    try:
        cursor = active_conn.execute(
            "UPDATE ledger SET is_deleted = ? WHERE id = ?",
            (1 if is_deleted else 0, int(transaction_id)),
        )
        recalculate_running_balances(active_conn)
        if owns_connection:
            active_conn.commit()
        return cursor.rowcount
    except Exception:
        if owns_connection:
            active_conn.rollback()
        raise
    finally:
        if owns_connection:
            active_conn.close()

def init_db():
    with get_connection() as conn:
        cursor = conn.cursor()
        # Add tables for Programs, Categories, Fiscal Years
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS fy (
                    fiscal_year_id INTEGER PRIMARY KEY, 
                    name TEXT,
                    start_date TEXT NOT NULL,
                    end_date TEXT NOT NULL,
                    active_ind INTEGER DEFAULT 0
                )
                       """)
        cursor.execute("""-- Create the categories table
                CREATE TABLE IF NOT EXISTS categories (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    flow text not null,
                    name TEXT NOT NULL UNIQUE,
                    description TEXT,
                    active_ind integer default 1
                )
                       """)
        cursor.execute("""
                CREATE TABLE IF NOT EXISTS import_staging (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    import_batch_id TEXT NOT NULL,      -- Identifier to group this specific file import
                    import_date TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    -- Raw Data Fields (kept as strings to avoid format errors during import)
                    raw_date TEXT,
                    raw_description TEXT,
                    raw_amount TEXT,
                    raw_check_number TEXT,
                    
                    -- Status Tracking
                    is_processed INTEGER DEFAULT 0,    -- 0: New, 1: Mapped to Ledger, 2: Ignored
                    original_filename TEXT
                )
                """)

        cursor.execute("""
                CREATE TABLE IF NOT EXISTS ledger (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    
                    -- Transaction Data
                    transaction_date TEXT NOT NULL,
                    description TEXT,
                    amount REAL NOT NULL,
                    transaction_type TEXT, -- Credit, Debit, Accrual
                    check_number TEXT,
                    daily_posted_balance REAL,      -- Can be NULL
                    running_balance REAL,
                    
                    -- Source and Metadata
                    source_indicator TEXT NOT NULL, -- 'Manual' or 'Bank'
                    is_deleted INTEGER DEFAULT 0,   -- 0 for active, 1 for deleted
                    notes TEXT,
                    more_notes TEXT,
                    
                    -- Relational Foreign Keys
                    category_id INTEGER,
                    fiscal_year_id INTEGER,
                    allocation_method_id INTEGER,
                    
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    
                    FOREIGN KEY(category_id) REFERENCES categories(id),
                    FOREIGN KEY(fiscal_year_id) REFERENCES fiscal_years(id)
                )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                code TEXT NOT NULL UNIQUE,
                description TEXT,
                active_ind INTEGER DEFAULT 1
            )
        """)
        cursor.execute("""
            CREATE VIEW IF NOT EXISTS ledger_balance_view AS
            SELECT 
                *,
                SUM(CASE 
                    WHEN transaction_type = 'Income' THEN amount 
                    WHEN transaction_type = 'Expense' THEN -amount 
                    ELSE 0 
                END) OVER (ORDER BY transaction_date, id) AS running_balance
            FROM ledger
            WHERE is_deleted = 0
        """)
        cursor.execute("""
            -- The "Method" definition
            CREATE TABLE IF NOT EXISTS allocation_methods (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE,
                description TEXT
            )
        """)
        cursor.execute("""
            -- The "Rules" or "Breakdown" (Where the percentages live)
            CREATE TABLE IF NOT EXISTS allocation_rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                method_id INTEGER NOT NULL,
                program_id INTEGER NOT NULL,
                percentage REAL NOT NULL, -- e.g., 0.50 for 50%
                FOREIGN KEY(method_id) REFERENCES allocation_methods(id),
                FOREIGN KEY(program_id) REFERENCES programs(id)
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topscore_product_mappings (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                product_name TEXT NOT NULL UNIQUE,
                grouping TEXT NOT NULL,
                category_id INTEGER,
                allocation_method_id INTEGER,
                primary_registration_ind INTEGER DEFAULT 0,
                active_ind INTEGER DEFAULT 1,
                notes TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP,
                FOREIGN KEY(category_id) REFERENCES categories(id),
                FOREIGN KEY(allocation_method_id) REFERENCES allocation_methods(id)
            )
        """)
        mapping_columns = [
            row[1]
            for row in cursor.execute("PRAGMA table_info(topscore_product_mappings)").fetchall()
        ]
        if "primary_registration_ind" not in mapping_columns:
            cursor.execute("""
                ALTER TABLE topscore_product_mappings
                ADD COLUMN primary_registration_ind INTEGER DEFAULT 0
            """)
        cursor.execute("""
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
        conn.commit()
