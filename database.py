import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ledger.db"


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection():
    ensure_data_dir()
    return sqlite3.connect(DB_PATH)


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
            CREATE TABLE IF NOT EXISTS registration_groupings (
                name TEXT PRIMARY KEY
            )
        """)

        # Product mappings used to include accounting metadata. Registrations only
        # needs a product, its grouping, and whether it is a primary registration.
        mapping_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(topscore_product_mappings)").fetchall()
        }
        expected_mapping_columns = {"product_name", "grouping", "primary_registration_ind"}
        if mapping_columns and mapping_columns != expected_mapping_columns:
            cursor.execute("ALTER TABLE topscore_product_mappings RENAME TO topscore_product_mappings_legacy")

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topscore_product_mappings (
                product_name TEXT PRIMARY KEY,
                grouping TEXT NOT NULL,
                primary_registration_ind INTEGER NOT NULL DEFAULT 0
                    CHECK (primary_registration_ind IN (0, 1))
            )
        """)

        legacy_table = cursor.execute("""
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'topscore_product_mappings_legacy'
        """).fetchone()
        if legacy_table:
            legacy_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(topscore_product_mappings_legacy)").fetchall()
            }
            primary_column = "primary_registration_ind" if "primary_registration_ind" in legacy_columns else "0"
            cursor.execute(f"""
                INSERT OR REPLACE INTO topscore_product_mappings (
                    product_name, grouping, primary_registration_ind
                )
                SELECT product_name, grouping, COALESCE({primary_column}, 0)
                FROM topscore_product_mappings_legacy
                WHERE product_name IS NOT NULL
                    AND trim(product_name) != ''
                    AND grouping IS NOT NULL
                    AND trim(grouping) != ''
            """)
            cursor.execute("DROP TABLE topscore_product_mappings_legacy")

        cursor.executemany(
            "INSERT OR IGNORE INTO registration_groupings (name) VALUES (?)",
            [
                ("High School",),
                ("Middle School",),
                ("Winter",),
                ("Donation",),
                ("Fundraising",),
                ("Merchandise",),
                ("Tournament Bids",),
                ("Other",),
            ],
        )
        cursor.execute("""
            INSERT OR IGNORE INTO registration_groupings (name)
            SELECT DISTINCT grouping
            FROM topscore_product_mappings
            WHERE trim(grouping) != ''
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
