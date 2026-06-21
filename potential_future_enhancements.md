# Potential Future Enhancements

## Supabase / PostgreSQL Database Migration

The app currently uses a local SQLite database at `data/ledger.db`. A future branch could migrate the data layer to Supabase PostgreSQL so the application can use a hosted database instead of local file storage.

### Recommended Direction

Use a direct PostgreSQL connection through SQLAlchemy or `psycopg`, rather than the Supabase Python REST client.

Direct PostgreSQL is a better fit because the app already relies heavily on SQL queries, joins, aggregates, reports, and DataFrame reads.

### Suggested Branch

```bash
git checkout -b codex/supabase-database
```

Before branching, check the worktree and decide whether to include or ignore any local uncommitted changes.

### Work Required

1. Inventory database usage
   - Find all direct `sqlite3.connect("data/ledger.db")` calls.
   - Identify SQL that uses SQLite-specific syntax.

2. Centralize database access
   - Move connection handling into `database.py`.
   - Replace direct SQLite connections with shared helpers.
   - Consider helpers such as `read_df`, `fetchone`, `fetchall`, and `execute`.

3. Add configuration
   - Use an environment variable such as `DATABASE_URL`.
   - Keep credentials out of Git.
   - Use `.env`, Streamlit secrets, or environment variables depending on deployment.

4. Convert schema to PostgreSQL
   - Convert SQLite `INTEGER PRIMARY KEY AUTOINCREMENT` to PostgreSQL identity columns.
   - Review timestamp, boolean, and foreign-key behavior.
   - Tables include `fy`, `categories`, `programs`, `allocation_methods`, `allocation_rules`, `ledger`, `import_staging`, `topscore_product_mappings`, and `topscore_transfer_items`.

5. Update SQL syntax
   - `ifnull(...)` -> `COALESCE(...)`
   - `INSERT OR IGNORE` -> `ON CONFLICT DO NOTHING`
   - Review date parsing such as `date(substr(...))`
   - Confirm `ON CONFLICT` statements are valid PostgreSQL syntax.

6. Migrate data
   - Prefer a Python migration script that reads from SQLite and inserts into PostgreSQL.
   - Validate row counts, financial totals, TopScore counts, and sample records after migration.

7. Add dependencies
   - Likely packages: `sqlalchemy`, `psycopg[binary]`, and possibly `python-dotenv`.
   - The Supabase Python client is only needed if using Supabase auth/storage APIs later.

8. Test app workflows
   - Dashboard metrics and financial breakdown
   - Transactions page viewing and editing
   - Manual pending transactions
   - Bank import
   - Reconciliation
   - Reports PDF export
   - TopScore product mappings and player counts

### Security Notes

- Do not commit Supabase credentials.
- Do not expose the Supabase service role key in client-facing code.
- A local Streamlit app can use backend database credentials, but deployed environments should use secrets or environment variables.
- If the app becomes multi-user, revisit authentication, authorization, and audit logging.

### Main Risk

The migration is less about Supabase itself and more about the app's current direct use of SQLite connections across many pages. The first meaningful step is centralizing database access so future database changes are easier to manage.
