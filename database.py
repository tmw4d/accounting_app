import sqlite3
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
DB_PATH = DATA_DIR / "ledger.db"

REGISTRATION_ALLOCATION_PROGRAMS = (
    "High School Fall",
    "Middle School Fall",
    "High School Spring",
    "Middle School Spring",
    "High School Winter",
)


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
                    SUM(CASE
                        WHEN m.grouping = 'High School'
                            AND strftime('%m', date(t.transfer_timestamp)) > '06'
                            AND lower(t.item_type) = 'payment'
                        THEN m.primary_registration_ind ELSE 0
                    END) AS hs_fall_players,
                    SUM(CASE
                        WHEN m.grouping = 'Winter'
                            AND lower(t.item_type) = 'payment'
                        THEN 1 ELSE 0
                    END) AS winter_players,
                    SUM(CASE
                        WHEN m.grouping = 'High School'
                            AND strftime('%m', date(t.transfer_timestamp)) <= '06'
                            AND lower(t.item_type) = 'payment'
                        THEN m.primary_registration_ind ELSE 0
                    END) AS hs_spring_players,
                    SUM(CASE
                        WHEN m.grouping = 'Middle School'
                            AND strftime('%m', date(t.transfer_timestamp)) > '06'
                            AND lower(t.item_type) = 'payment'
                        THEN m.primary_registration_ind ELSE 0
                    END) AS ms_fall_players,
                    SUM(CASE
                        WHEN m.grouping = 'Middle School'
                            AND strftime('%m', date(t.transfer_timestamp)) <= '06'
                            AND lower(t.item_type) = 'payment'
                        THEN m.primary_registration_ind ELSE 0
                    END) AS ms_spring_players,
                    SUM(CASE
                        WHEN lower(t.item_type) = 'payment'
                            AND m.grouping IN ('High School', 'Winter', 'Middle School')
                        THEN m.primary_registration_ind ELSE 0
                    END) AS total_players,
                    SUM(CASE
                        WHEN m.grouping = 'High School'
                            AND strftime('%m', date(t.transfer_timestamp)) > '06'
                        THEN t.amount ELSE 0
                    END) AS hs_fall_dollars,
                    SUM(CASE WHEN m.grouping = 'Winter' THEN t.amount ELSE 0 END) AS winter_dollars,
                    SUM(CASE
                        WHEN m.grouping = 'High School'
                            AND strftime('%m', date(t.transfer_timestamp)) <= '06'
                        THEN t.amount ELSE 0
                    END) AS hs_spring_dollars,
                    SUM(CASE
                        WHEN m.grouping = 'Middle School'
                            AND strftime('%m', date(t.transfer_timestamp)) > '06'
                        THEN t.amount ELSE 0
                    END) AS ms_fall_dollars,
                    SUM(CASE
                        WHEN m.grouping = 'Middle School'
                            AND strftime('%m', date(t.transfer_timestamp)) <= '06'
                        THEN t.amount ELSE 0
                    END) AS ms_spring_dollars,
                    SUM(CASE
                        WHEN m.grouping IN ('High School', 'Winter', 'Middle School')
                        THEN t.amount ELSE 0
                    END) AS program_dollars
                FROM topscore_transfer_items t
                JOIN topscore_product_mappings m ON m.product_name = t.product_name
                WHERE date(t.transfer_timestamp) IS NOT NULL
                GROUP BY 1
            )
            SELECT
                *,
                hs_fall_players / CAST(NULLIF(total_players, 0) AS REAL) AS hs_fall_player_pct,
                winter_players / CAST(NULLIF(total_players, 0) AS REAL) AS winter_player_pct,
                hs_spring_players / CAST(NULLIF(total_players, 0) AS REAL) AS hs_spring_player_pct,
                ms_fall_players / CAST(NULLIF(total_players, 0) AS REAL) AS ms_fall_player_pct,
                ms_spring_players / CAST(NULLIF(total_players, 0) AS REAL) AS ms_spring_player_pct,
                hs_fall_dollars / CAST(NULLIF(program_dollars, 0) AS REAL) AS hs_fall_dollar_pct,
                winter_dollars / CAST(NULLIF(program_dollars, 0) AS REAL) AS winter_dollar_pct,
                hs_spring_dollars / CAST(NULLIF(program_dollars, 0) AS REAL) AS hs_spring_dollar_pct,
                ms_fall_dollars / CAST(NULLIF(program_dollars, 0) AS REAL) AS ms_fall_dollar_pct,
                ms_spring_dollars / CAST(NULLIF(program_dollars, 0) AS REAL) AS ms_spring_dollar_pct
            FROM yearly_summary
        """)
        cursor.execute("""
            CREATE VIEW registration_allocation_rules_v AS
            WITH rule_values(fy, method_id, program_code, percentage) AS (
                SELECT fy, 21, 'HS01', hs_fall_player_pct FROM program_registration_summary
                UNION ALL SELECT fy, 21, 'HS02', winter_player_pct FROM program_registration_summary
                UNION ALL SELECT fy, 21, 'HS03', hs_spring_player_pct FROM program_registration_summary
                UNION ALL SELECT fy, 21, 'MS01', ms_fall_player_pct FROM program_registration_summary
                UNION ALL SELECT fy, 21, 'MS02', ms_spring_player_pct FROM program_registration_summary
                UNION ALL SELECT fy, 22, 'HS01', hs_fall_dollar_pct FROM program_registration_summary
                UNION ALL SELECT fy, 22, 'HS02', winter_dollar_pct FROM program_registration_summary
                UNION ALL SELECT fy, 22, 'HS03', hs_spring_dollar_pct FROM program_registration_summary
                UNION ALL SELECT fy, 22, 'MS01', ms_fall_dollar_pct FROM program_registration_summary
                UNION ALL SELECT fy, 22, 'MS02', ms_spring_dollar_pct FROM program_registration_summary
            )
            SELECT f.fiscal_year_id, rv.method_id, p.id AS program_id, rv.percentage
            FROM rule_values rv
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
    """Return net TopScore dollars by independent July-June allocation year and program."""
    return conn.execute("""
        WITH eligible_items AS (
            SELECT
                CASE
                    WHEN m.grouping = 'Winter' THEN CAST(strftime(
                        '%Y', date(substr(t.transfer_timestamp, 1, 10), '+6 months')
                    ) AS INTEGER)
                    WHEN CAST(strftime('%m', substr(t.transfer_timestamp, 1, 10)) AS INTEGER) > 6
                        THEN CAST(strftime('%Y', substr(t.transfer_timestamp, 1, 10)) AS INTEGER) + 1
                    ELSE CAST(strftime('%Y', substr(t.transfer_timestamp, 1, 10)) AS INTEGER)
                END AS allocation_year,
                CASE
                    WHEN m.grouping = 'Winter' THEN 'High School Winter'
                    WHEN m.grouping = 'High School'
                        AND CAST(strftime('%m', substr(t.transfer_timestamp, 1, 10)) AS INTEGER) > 6
                        THEN 'High School Fall'
                    WHEN m.grouping = 'High School' THEN 'High School Spring'
                    WHEN m.grouping = 'Middle School'
                        AND CAST(strftime('%m', substr(t.transfer_timestamp, 1, 10)) AS INTEGER) > 6
                        THEN 'Middle School Fall'
                    WHEN m.grouping = 'Middle School' THEN 'Middle School Spring'
                END AS program_name,
                t.amount
            FROM topscore_transfer_items t
            JOIN topscore_product_mappings m ON m.product_name = t.product_name
            WHERE m.grouping IN ('High School', 'Middle School', 'Winter')
                AND date(substr(t.transfer_timestamp, 1, 10)) IS NOT NULL
        )
        SELECT allocation_year, program_name, ROUND(SUM(amount), 2) AS net_dollars
        FROM eligible_items
        WHERE allocation_year IS NOT NULL AND program_name IS NOT NULL
        GROUP BY allocation_year, program_name
        ORDER BY allocation_year, program_name
    """).fetchall()


def get_registration_allocation_summary(conn=None):
    """Return calculated registration allocation dollars and percentages by program."""
    owns_connection = conn is None
    active_conn = conn or get_connection()
    try:
        grouped = {}
        for allocation_year, program_name, net_dollars in _registration_distribution_rows(active_conn):
            grouped.setdefault(int(allocation_year), {})[program_name] = float(net_dollars or 0)

        summary = []
        for allocation_year, amounts in sorted(grouped.items()):
            total_net = sum(amounts.values())
            if abs(total_net) < 0.005:
                continue
            for program_name in REGISTRATION_ALLOCATION_PROGRAMS:
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


