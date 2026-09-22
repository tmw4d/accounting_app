import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ledger.db"


def ensure_data_dir():
    DATA_DIR.mkdir(parents=True, exist_ok=True)


def get_connection():
    ensure_data_dir()
    return sqlite3.connect(DB_PATH, timeout=30.0)



def recalculate_running_balances(conn):
    """Rebuild stored balances for all active ledger transactions ordered by Fiscal Year sequence."""
    conn.execute("""
        UPDATE ledger
        SET running_balance = NULL
        WHERE is_deleted != 0
    """)
    conn.execute("""
        WITH calculated_balances AS (
            SELECT
                l.id,
                ROUND(
                    SUM(l.amount) OVER (
                        ORDER BY 
                            COALESCE(fy.start_date, '9999-12-31') ASC,
                            CASE WHEN l.transaction_date = 'pending' THEN 1 ELSE 0 END ASC,
                            l.transaction_date ASC,
                            l.id ASC
                        ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW
                    ),
                    2
                ) AS balance
            FROM ledger l
            LEFT JOIN fy ON l.fiscal_year_id = fy.fiscal_year_id
            WHERE l.is_deleted = 0
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
                product_name TEXT PRIMARY KEY,
                category TEXT,
                program_code TEXT,
                primary_registration_ind INTEGER NOT NULL DEFAULT 0
                    CHECK (primary_registration_ind IN (0, 1)),
                FOREIGN KEY(program_code) REFERENCES programs(code)
            )
        """)

        # Migrate from the old grouping-based schema to category + program_code.
        mapping_columns = {
            row[1] for row in cursor.execute("PRAGMA table_info(topscore_product_mappings)").fetchall()
        }
        if "grouping" in mapping_columns and "category" not in mapping_columns:
            cursor.execute("ALTER TABLE topscore_product_mappings RENAME TO _pm_grouping_backup")
            cursor.execute("""
                CREATE TABLE topscore_product_mappings (
                    product_name TEXT PRIMARY KEY,
                    category TEXT,
                    program_code TEXT,
                    primary_registration_ind INTEGER NOT NULL DEFAULT 0
                        CHECK (primary_registration_ind IN (0, 1)),
                    FOREIGN KEY(program_code) REFERENCES programs(code)
                )
            """)
            # Map old grouping values to income category names.
            cursor.execute("""
                INSERT INTO topscore_product_mappings (
                    product_name, category, program_code, primary_registration_ind
                )
                SELECT
                    product_name,
                    CASE grouping
                        WHEN 'High School' THEN 'Registration'
                        WHEN 'Middle School' THEN 'Registration'
                        WHEN 'Winter' THEN 'Registration'
                        WHEN 'Merchandise' THEN 'Merchandise Sales'
                        WHEN 'Tournament Bids' THEN 'YULA Invite Bids'
                        WHEN 'Donation' THEN 'Donation'
                        WHEN 'Fundraising' THEN 'Fundraising'
                        WHEN 'Other' THEN 'Other Income'
                        ELSE grouping
                    END,
                    CASE
                        WHEN grouping = 'Winter' THEN 'HS02'
                        WHEN grouping = 'High School' AND product_name LIKE '%Fall%' THEN 'HS01'
                        WHEN grouping = 'High School' AND product_name LIKE '%Spring%' THEN 'HS03'
                        WHEN grouping = 'High School' AND product_name LIKE '%Winter%' THEN 'HS02'
                        WHEN grouping = 'Middle School' AND product_name LIKE '%Fall%' THEN 'MS01'
                        WHEN grouping = 'Middle School' AND product_name LIKE '%Spring%' THEN 'MS02'
                        ELSE NULL
                    END,
                    COALESCE(primary_registration_ind, 0)
                FROM _pm_grouping_backup
                WHERE product_name IS NOT NULL AND trim(product_name) != ''
            """)
            cursor.execute("DROP TABLE _pm_grouping_backup")

        # Also handle the older legacy table from a prior migration.
        legacy_table = cursor.execute("""
            SELECT 1
            FROM sqlite_master
            WHERE type = 'table' AND name = 'topscore_product_mappings_legacy'
        """).fetchone()
        if legacy_table:
            legacy_columns = {
                row[1] for row in cursor.execute("PRAGMA table_info(topscore_product_mappings_legacy)").fetchall()
            }
            primary_col = "primary_registration_ind" if "primary_registration_ind" in legacy_columns else "0"
            has_grouping = "grouping" in legacy_columns
            if has_grouping:
                cursor.execute(f"""
                    INSERT OR IGNORE INTO topscore_product_mappings (
                        product_name, category, primary_registration_ind
                    )
                    SELECT
                        product_name,
                        CASE grouping
                            WHEN 'High School' THEN 'Registration'
                            WHEN 'Middle School' THEN 'Registration'
                            WHEN 'Winter' THEN 'Registration'
                            WHEN 'Merchandise' THEN 'Merchandise Sales'
                            WHEN 'Tournament Bids' THEN 'YULA Invite Bids'
                            WHEN 'Donation' THEN 'Donation'
                            WHEN 'Fundraising' THEN 'Fundraising'
                            WHEN 'Other' THEN 'Other Income'
                            ELSE grouping
                        END,
                        COALESCE({primary_col}, 0)
                    FROM topscore_product_mappings_legacy
                    WHERE product_name IS NOT NULL AND trim(product_name) != ''
                """)
            cursor.execute("DROP TABLE topscore_product_mappings_legacy")

        # Drop the obsolete registration_groupings table if it exists.
        cursor.execute("DROP TABLE IF EXISTS registration_groupings")

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
        # These methods are deliberately permanent.  Their percentages live in
        # views below, not in allocation_rules, because they vary by fiscal year
        # as TopScore data is imported or product mappings are adjusted.
        cursor.executemany("""
            INSERT OR IGNORE INTO allocation_methods (id, name, description)
            VALUES (?, ?, ?)
        """, [
            (21, "Registration Player Distribution", "Live TopScore player-count allocation by fiscal year."),
            (22, "Registration Dollar Distribution", "Live TopScore dollar allocation by fiscal year."),
        ])

        # Convert the legacy, generated per-year methods once.  The statements
        # are idempotent and intentionally retain the old methods/rules for
        # historical auditability; reports no longer depend on those rows.
        has_legacy_reg_methods = cursor.execute("""
            SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'registration_allocation_methods'
        """).fetchone()
        if has_legacy_reg_methods:
            cursor.execute("""
                UPDATE ledger
                SET allocation_method_id = 22
                WHERE allocation_method_id IN (
                    SELECT allocation_method_id
                    FROM registration_allocation_methods
                )
            """)
        cursor.execute("""
            UPDATE ledger
            SET allocation_method_id = 21
            WHERE allocation_method_id IN (
                SELECT id
                FROM allocation_methods
                WHERE name LIKE '____ Player Distribution'
            )
        """)
        cursor.execute("""
            UPDATE ledger
            SET allocation_method_id = 22
            WHERE allocation_method_id IN (
                SELECT id
                FROM allocation_methods
                WHERE name LIKE '____ Registration Distribution'
            )
        """)

        # Recreate these views on startup so existing databases receive their
        # current definitions without a separate migration runner.
        cursor.execute("DROP VIEW IF EXISTS allocation_rules_by_fy_v")
        cursor.execute("DROP VIEW IF EXISTS registration_allocation_rules_v")
        cursor.execute("DROP VIEW IF EXISTS program_registration_summary")
        cursor.execute("""
            CREATE VIEW program_registration_summary AS
            WITH yearly_summary AS (
                SELECT
                    'FY' || strftime(
                        '%Y', date(t.transfer_timestamp, '+6 month', 'start of year')
                    ) AS fy,
                    m.program_code,
                    SUM(CASE
                        WHEN lower(t.item_type) = 'payment'
                        THEN m.primary_registration_ind ELSE 0
                    END) AS players,
                    SUM(t.amount) AS dollars
                FROM topscore_transfer_items t
                JOIN topscore_product_mappings m ON m.product_name = t.product_name
                WHERE m.program_code IS NOT NULL
                    AND date(t.transfer_timestamp) IS NOT NULL
                GROUP BY 1, 2
            ),
            fy_totals AS (
                SELECT
                    fy,
                    SUM(players) AS total_players,
                    SUM(dollars) AS total_dollars
                FROM yearly_summary
                GROUP BY fy
            )
            SELECT
                ys.fy,
                ys.program_code,
                ys.players,
                ys.dollars,
                ys.players / CAST(NULLIF(ft.total_players, 0) AS REAL) AS player_pct,
                ys.dollars / CAST(NULLIF(ft.total_dollars, 0) AS REAL) AS dollar_pct
            FROM yearly_summary ys
            JOIN fy_totals ft ON ft.fy = ys.fy
        """)
        cursor.execute("""
            CREATE VIEW registration_allocation_rules_v AS
            SELECT
                f.fiscal_year_id,
                rv.method_id,
                p.id AS program_id,
                rv.percentage
            FROM (
                SELECT fy, 21 AS method_id, program_code, player_pct AS percentage
                FROM program_registration_summary
                UNION ALL
                SELECT fy, 22 AS method_id, program_code, dollar_pct AS percentage
                FROM program_registration_summary
            ) rv
            JOIN fy f ON f.name = rv.fy
            JOIN programs p ON p.code = rv.program_code
            WHERE rv.percentage IS NOT NULL
        """)
        cursor.execute("""
            CREATE VIEW allocation_rules_by_fy_v AS
            SELECT f.fiscal_year_id, ar.method_id, ar.program_id, ar.percentage
            FROM fy f
            CROSS JOIN allocation_rules ar
            WHERE ar.method_id NOT IN (21, 22)
            UNION ALL
            SELECT fiscal_year_id, method_id, program_id, percentage
            FROM registration_allocation_rules_v
        """)
        conn.commit()


def _registration_distribution_rows(conn):
    """Return net TopScore dollars by fiscal-year allocation year and program."""
    return conn.execute("""
        SELECT
            CAST(strftime(
                '%Y', date(substr(t.transfer_timestamp, 1, 10), '+6 months')
            ) AS INTEGER) AS allocation_year,
            p.name AS program_name,
            ROUND(SUM(t.amount), 2) AS net_dollars
        FROM topscore_transfer_items t
        JOIN topscore_product_mappings m ON m.product_name = t.product_name
        JOIN programs p ON p.code = m.program_code
        WHERE m.program_code IS NOT NULL
            AND date(substr(t.transfer_timestamp, 1, 10)) IS NOT NULL
        GROUP BY allocation_year, p.name
        ORDER BY allocation_year, p.name
    """).fetchall()


def get_registration_allocation_summary(conn=None):
    """Return calculated registration allocation dollars and percentages by program."""
    owns_connection = conn is None
    active_conn = conn or get_connection()
    try:
        # Get program names from the programs table for consistent ordering.
        program_names = [
            row[0] for row in active_conn.execute(
                "SELECT name FROM programs WHERE active_ind = 1 ORDER BY code"
            ).fetchall()
        ]

        grouped = {}
        for allocation_year, program_name, net_dollars in _registration_distribution_rows(active_conn):
            grouped.setdefault(int(allocation_year), {})[program_name] = float(net_dollars or 0)

        summary = []
        for allocation_year, amounts in sorted(grouped.items()):
            total_net = sum(amounts.values())
            if abs(total_net) < 0.005:
                continue
            for program_name in program_names:
                net_dollars = amounts.get(program_name, 0.0)
                summary.append({
                    "allocation_year": allocation_year,
                    "program_name": program_name,
                    "net_dollars": net_dollars,
                    "percentage": net_dollars / total_net,
                })
        return summary
    finally:
        if owns_connection:
            active_conn.close()



def get_program_financial_breakdown(fy_id, conn=None):
    """Return pivoted program breakdown DataFrame for a given fiscal year.

    Calculates allocated program sums using allocation_rules_by_fy_v so dynamic
    methods (e.g. Methods 21 and 22) are properly resolved per fiscal year.
    """
    import pandas as pd

    owns_connection = conn is None
    active_conn = conn or get_connection()
    try:
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
                    WHEN lower(c.flow) = 'expense' THEN - SUM(l.amount * ifnull(ar.percentage,0))
                    WHEN lower(c.flow) = 'income' THEN SUM(l.amount * ifnull(ar.percentage,0))
                    WHEN l.amount < 0 THEN - SUM(l.amount * ifnull(ar.percentage,0))
                    ELSE SUM(l.amount * ifnull(ar.percentage,0)) 
                END as total
            FROM ledger l
            LEFT JOIN allocation_methods am ON l.allocation_method_id = am.id
            LEFT JOIN allocation_rules_by_fy_v ar 
                ON am.id = ar.method_id AND ar.fiscal_year_id = l.fiscal_year_id
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
                    WHEN lower(c.flow) = 'expense' THEN - SUM(l.amount)
                    WHEN lower(c.flow) = 'income' THEN SUM(l.amount)
                    WHEN l.amount < 0 THEN - SUM(l.amount)
                    ELSE SUM(l.amount) 
                END as total
            FROM ledger l
            LEFT JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ? AND l.is_deleted = 0
            GROUP BY flow
        """
        df = pd.read_sql_query(query, active_conn, params=(fy_id, fy_id))

        if df.empty:
            return pd.DataFrame()

        pivot_df = df.pivot_table(
            index="flow",
            columns="program_name",
            values="total",
            aggfunc="sum",
            fill_value=0,
        )

        if "Total" in pivot_df.columns:
            pivot_df["Unallocated"] = pivot_df["Total"] - (pivot_df.sum(axis=1) - pivot_df["Total"])

        for flow_row in ["Income", "Expense"]:
            if flow_row not in pivot_df.index:
                pivot_df.loc[flow_row] = 0.0

        pivot_df.loc["Total"] = pivot_df.loc["Income"] - pivot_df.loc["Expense"]
        return pivot_df
    finally:
        if owns_connection:
            active_conn.close()


def get_fiscal_year_summary(fy_id, conn=None):
    """Return (initial_balance, latest_posted_balance, pending_sum, total_balance) for a fiscal year.

    Calculates sequential running balances ordered by fiscal year sequence, correctly handling:
    - Initial balance carried forward from prior fiscal years
    - Latest posted balance (excluding pending transactions)
    - Pending transactions total
    - Projected total balance (including pending transactions)
    """
    def as_float(value):
        return float(value) if value is not None else 0.0

    owns_connection = conn is None
    active_conn = conn or get_connection()
    try:
        recalculate_running_balances(active_conn)

        first_txn = active_conn.execute("""
            SELECT l.amount, l.running_balance
            FROM ledger l
            WHERE l.fiscal_year_id = ?
            AND l.is_deleted = 0
            ORDER BY 
                CASE WHEN l.transaction_date = 'pending' THEN 1 ELSE 0 END ASC,
                l.transaction_date ASC,
                l.id ASC
            LIMIT 1
        """, (fy_id,)).fetchone()

        if first_txn and first_txn[1] is not None:
            initial_balance_val = as_float(first_txn[1]) - as_float(first_txn[0])
        else:
            prior_txn = active_conn.execute("""
                SELECT l.running_balance
                FROM ledger l
                JOIN fy ON l.fiscal_year_id = fy.fiscal_year_id
                WHERE fy.start_date < (SELECT start_date FROM fy WHERE fiscal_year_id = ?)
                AND l.is_deleted = 0
                ORDER BY 
                    fy.start_date DESC,
                    CASE WHEN l.transaction_date = 'pending' THEN 1 ELSE 0 END DESC,
                    l.transaction_date DESC,
                    l.id DESC
                LIMIT 1
            """, (fy_id,)).fetchone()
            initial_balance_val = as_float(prior_txn[0]) if prior_txn else 0.0

        latest_posted = active_conn.execute("""
            SELECT running_balance 
            FROM ledger 
            WHERE transaction_date != 'pending' 
            AND fiscal_year_id = ?
            AND is_deleted = 0
            ORDER BY transaction_date DESC, id DESC
            LIMIT 1
        """, (fy_id,)).fetchone()

        pending_sum = active_conn.execute("""
            SELECT SUM(amount) 
            FROM ledger 
            WHERE transaction_date = 'pending'
            AND fiscal_year_id = ?
            AND is_deleted = 0
        """, (fy_id,)).fetchone()

        last_txn = active_conn.execute("""
            SELECT running_balance
            FROM ledger
            WHERE fiscal_year_id = ?
            AND is_deleted = 0
            ORDER BY 
                CASE WHEN transaction_date = 'pending' THEN 1 ELSE 0 END DESC,
                transaction_date DESC,
                id DESC
            LIMIT 1
        """, (fy_id,)).fetchone()

        latest_posted_val = as_float(latest_posted[0]) if latest_posted else initial_balance_val
        pending_sum_val = as_float(pending_sum[0]) if (pending_sum and pending_sum[0] is not None) else 0.0
        total_balance = as_float(last_txn[0]) if (last_txn and last_txn[0] is not None) else (latest_posted_val + pending_sum_val)

        return initial_balance_val, latest_posted_val, pending_sum_val, total_balance
    finally:
        if owns_connection:
            active_conn.close()



