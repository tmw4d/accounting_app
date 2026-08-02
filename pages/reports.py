from datetime import datetime
from io import BytesIO
import database as db
import email_service
import pandas as pd
import streamlit as st


def _currency(value):
    return f"${float(value or 0):,.0f}"


def _get_fiscal_year(fy_id):
    with db.get_connection() as conn:
        row = conn.execute("""
            SELECT name, start_date, end_date
            FROM fy
            WHERE fiscal_year_id = ?
        """, (fy_id,)).fetchone()
    if not row:
        return f"FY {fy_id}", "", ""
    return row


def _get_summary(fy_id):
    with db.get_connection() as conn:

        first_posted = conn.execute("""
            SELECT amount, daily_posted_balance
            FROM ledger
            WHERE fiscal_year_id = ?
                AND is_deleted = 0
                AND transaction_date != 'pending'
                AND daily_posted_balance IS NOT NULL
            ORDER BY transaction_date ASC, id ASC
            LIMIT 1
        """, (fy_id,)).fetchone()
        latest_posted = conn.execute("""
            SELECT daily_posted_balance
            FROM ledger
            WHERE fiscal_year_id = ?
                AND is_deleted = 0
                AND transaction_date != 'pending'
                AND daily_posted_balance IS NOT NULL
            ORDER BY transaction_date DESC, id DESC
            LIMIT 1
        """, (fy_id,)).fetchone()
        pending_sum = conn.execute("""
            SELECT SUM(amount)
            FROM ledger
            WHERE fiscal_year_id = ?
                AND is_deleted = 0
                AND transaction_date = 'pending'
        """, (fy_id,)).fetchone()

    if first_posted and first_posted[1] is not None:
        initial = float(first_posted[1]) - float(first_posted[0] or 0)
    else:
        initial = 0.0
    posted = float(latest_posted[0]) if latest_posted and latest_posted[0] is not None else 0.0
    pending = float(pending_sum[0]) if pending_sum and pending_sum[0] is not None else 0.0
    return initial, posted, pending, posted + pending


def _get_recent_fiscal_years(selected_fy_id, count=2):
    with db.get_connection() as conn:

        selected = conn.execute("""
            SELECT end_date
            FROM fy
            WHERE fiscal_year_id = ?
        """, (selected_fy_id,)).fetchone()
        if not selected:
            return []

        rows = conn.execute("""
            SELECT fiscal_year_id, name, start_date, end_date
            FROM fy
            WHERE end_date <= ?
            ORDER BY end_date DESC
            LIMIT ?
        """, (selected[0], count)).fetchall()

    return list(reversed(rows))


def _get_program_breakdown(fy_id):
    with db.get_connection() as conn:

        query = """
            SELECT
                p.name AS program_name,
                CASE
                    WHEN lower(c.flow) = 'income' THEN 'Income'
                    WHEN lower(c.flow) = 'expense' THEN 'Expense'
                    ELSE 'Uncategorized'
                END AS flow,
                CASE WHEN lower(c.flow) = 'expense'
                    THEN -SUM(l.amount * ifnull(ar.percentage, 0))
                    ELSE SUM(l.amount * ifnull(ar.percentage, 0))
                END AS total
            FROM ledger l
            LEFT JOIN allocation_methods am ON l.allocation_method_id = am.id
            LEFT JOIN allocation_rules ar ON am.id = ar.method_id
            LEFT JOIN programs p ON ar.program_id = p.id
            LEFT JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ?
                AND l.is_deleted = 0
            GROUP BY p.name, c.flow

            UNION ALL

            SELECT
                'Total' AS program_name,
                CASE
                    WHEN lower(c.flow) = 'income' THEN 'Income'
                    WHEN lower(c.flow) = 'expense' THEN 'Expense'
                    ELSE 'Uncategorized'
                END AS flow,
                CASE WHEN lower(c.flow) = 'expense'
                    THEN -SUM(l.amount)
                    ELSE SUM(l.amount)
                END AS total
            FROM ledger l
            LEFT JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ?
                AND l.is_deleted = 0
            GROUP BY c.flow
        """
        df = pd.read_sql_query(query, conn, params=(fy_id, fy_id))

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

    for required_row in ["Income", "Expense"]:
        if required_row not in pivot_df.index:
            pivot_df.loc[required_row] = 0

    pivot_df.loc["Total"] = pivot_df.loc["Income"] - pivot_df.loc["Expense"]
    pivot_df = pivot_df.reindex(["Income", "Expense", "Total"])
    pivot_df.index.name = "Flow"
    return pivot_df


def _get_category_totals(fy_id):
    with db.get_connection() as conn:

        query = """
            SELECT
                CASE
                    WHEN c.name IS NULL THEN 'Uncategorized'
                    ELSE c.name
                END AS category,
                CASE
                    WHEN lower(c.flow) = 'income' THEN 'Income'
                    WHEN lower(c.flow) = 'expense' THEN 'Expense'
                    WHEN l.amount >= 0 THEN 'Income'
                    ELSE 'Expense'
                END AS flow,
                SUM(ABS(l.amount)) AS total
            FROM ledger l
            LEFT JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id = ?
                AND l.is_deleted = 0
                --AND l.transaction_date != 'pending'
            GROUP BY category, flow
            HAVING total > 0
            ORDER BY
                CASE flow WHEN 'Income' THEN 0 WHEN 'Expense' THEN 1 ELSE 2 END,
                total DESC
        """
        return pd.read_sql_query(query, conn, params=(fy_id,))


def _get_two_year_category_program_breakdown(fy_id):
    fiscal_years = _get_recent_fiscal_years(fy_id)
    if not fiscal_years:
        return pd.DataFrame(), []

    fy_ids = [row[0] for row in fiscal_years]
    placeholders = ",".join("?" for _ in fy_ids)

    with db.get_connection() as conn:

        query = f"""
            SELECT
                l.fiscal_year_id,
                CASE
                    WHEN lower(c.flow) = 'income' THEN 'Income'
                    WHEN lower(c.flow) = 'expense' THEN 'Expense'
                    WHEN l.amount >= 0 THEN 'Income'
                    ELSE 'Expense'
                END AS flow,
                ifnull(c.name, 'Uncategorized') AS category,
                p.name AS program_name,
                CASE
                    WHEN lower(c.flow) = 'expense' THEN -SUM(l.amount * ifnull(ar.percentage, 0))
                    ELSE SUM(l.amount * ifnull(ar.percentage, 0))
                END AS total
            FROM ledger l
            LEFT JOIN categories c ON l.category_id = c.id
            LEFT JOIN allocation_methods am ON l.allocation_method_id = am.id
            LEFT JOIN allocation_rules ar ON am.id = ar.method_id
            LEFT JOIN programs p ON ar.program_id = p.id
            WHERE l.fiscal_year_id IN ({placeholders})
                AND l.is_deleted = 0
                --AND l.transaction_date != 'pending'
            GROUP BY l.fiscal_year_id, flow, category, p.name

            UNION ALL

            SELECT
                l.fiscal_year_id,
                CASE
                    WHEN lower(c.flow) = 'income' THEN 'Income'
                    WHEN lower(c.flow) = 'expense' THEN 'Expense'
                    WHEN l.amount >= 0 THEN 'Income'
                    ELSE 'Expense'
                END AS flow,
                ifnull(c.name, 'Uncategorized') AS category,
                'Total' AS program_name,
                CASE
                    WHEN lower(c.flow) = 'expense' THEN -SUM(l.amount)
                    ELSE SUM(l.amount)
                END AS total
            FROM ledger l
            LEFT JOIN categories c ON l.category_id = c.id
            WHERE l.fiscal_year_id IN ({placeholders})
                AND l.is_deleted = 0
                --AND l.transaction_date != 'pending'
            GROUP BY l.fiscal_year_id, flow, category
        """
        df = pd.read_sql_query(query, conn, params=fy_ids + fy_ids)

    if df.empty:
        return pd.DataFrame(), fiscal_years

    program_order = []
    for program_name in df["program_name"].dropna().unique().tolist():
        if program_name != "Total":
            program_order.append(program_name)
    program_order = sorted(program_order)
    program_order.append("Total")

    pivot = df.pivot_table(
        index=["flow", "category"],
        columns=["fiscal_year_id", "program_name"],
        values="total",
        aggfunc="sum",
        fill_value=0,
    )

    ordered_columns = []
    for year_id in fy_ids:
        for program_name in program_order:
            if (year_id, program_name) in pivot.columns:
                ordered_columns.append((year_id, program_name))
            else:
                pivot[(year_id, program_name)] = 0
                ordered_columns.append((year_id, program_name))
    pivot = pivot[ordered_columns]

    flow_order = {"Income": 0, "Expense": 1}
    pivot = pivot.reset_index()
    pivot["_flow_sort"] = pivot["flow"].map(flow_order).fillna(9)
    pivot = pivot.sort_values(["_flow_sort", "category"]).drop(columns=["_flow_sort"])
    pivot = pivot.set_index(["flow", "category"])
    pivot.columns = pd.MultiIndex.from_tuples(pivot.columns, names=["Fiscal Year", "Program"])

    zero_totals = pd.Series(0.0, index=pivot.columns)
    income_totals = (
        pivot.xs("Income", level="flow").sum()
        if "Income" in pivot.index.get_level_values("flow")
        else zero_totals.copy()
    )
    expense_totals = (
        pivot.xs("Expense", level="flow").sum()
        if "Expense" in pivot.index.get_level_values("flow")
        else zero_totals.copy()
    )

    sections = []
    for flow, label, totals in [
        ("Income", "Total Income", income_totals),
        ("Expense", "Total Expenses", expense_totals),
    ]:
        if flow in pivot.index.get_level_values("flow"):
            sections.append(pivot.xs(flow, level="flow", drop_level=False))
        subtotal = pd.DataFrame(
            [totals],
            index=pd.MultiIndex.from_tuples([(flow, label)], names=pivot.index.names),
            columns=pivot.columns,
        )
        sections.append(subtotal)

    net_total = pd.DataFrame(
        [income_totals - expense_totals],
        index=pd.MultiIndex.from_tuples([("Net", "Net Total")], names=pivot.index.names),
        columns=pivot.columns,
    )
    sections.append(net_total)
    return pd.concat(sections), fiscal_years


def _compact_pie_data(df, flow, max_slices=7):
    flow_df = df[df["flow"] == flow].copy()
    if flow_df.empty:
        return [], []

    flow_df = flow_df.sort_values("total", ascending=False)
    top = flow_df.head(max_slices)
    other = flow_df.iloc[max_slices:]["total"].sum()

    labels = top["category"].tolist()
    values = top["total"].astype(float).tolist()
    if other > 0:
        labels.append("Other")
        values.append(float(other))
    return labels, values


def _build_pie_chart(title, labels, values):
    from reportlab.graphics.charts.piecharts import Pie
    from reportlab.graphics.shapes import Drawing, Rect, String
    from reportlab.lib import colors

    drawing = Drawing(245, 190)
    drawing.add(String(8, 172, title, fontName="Helvetica-Bold", fontSize=11))

    if not values:
        drawing.add(String(40, 90, "No posted data", fontName="Helvetica", fontSize=10))
        return drawing

    palette = [
        colors.HexColor("#1f77b4"),
        colors.HexColor("#ff7f0e"),
        colors.HexColor("#2ca02c"),
        colors.HexColor("#d62728"),
        colors.HexColor("#9467bd"),
        colors.HexColor("#8c564b"),
        colors.HexColor("#17becf"),
        colors.HexColor("#7f7f7f"),
    ]

    pie = Pie()
    pie.x = 20
    pie.y = 38
    pie.width = 110
    pie.height = 110
    pie.data = values
    pie.labels = None
    pie.sideLabels = False
    for idx in range(len(values)):
        pie.slices[idx].fillColor = palette[idx % len(palette)]
    drawing.add(pie)

    total = sum(values)
    legend_y = 142
    for idx, (label, value) in enumerate(zip(labels, values)):
        y = legend_y - (idx * 14)
        drawing.add(Rect(145, y - 1, 8, 8, fillColor=palette[idx % len(palette)], strokeColor=None))
        legend_label = label if len(label) <= 19 else f"{label[:18]}..."
        drawing.add(String(158, y, f"{legend_label} {_currency(value)}", fontName="Helvetica", fontSize=8))

    return drawing


def _dataframe_to_pdf_table(df, max_cols=8):
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Table, TableStyle

    pdf_df = df.copy()
    if len(pdf_df.columns) > max_cols:
        keep_cols = list(pdf_df.columns[: max_cols - 2])
        for important_col in ["Total", "Unallocated"]:
            if important_col in pdf_df.columns and important_col not in keep_cols:
                keep_cols.append(important_col)
        pdf_df = pdf_df[keep_cols]

    data = [["Flow"] + list(pdf_df.columns)]
    for idx, row in pdf_df.iterrows():
        data.append([idx] + [_currency(value) for value in row.tolist()])

    table = Table(data, repeatRows=1, hAlign="LEFT")
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, -1), "Helvetica"),
        ("FONTSIZE", (0, 0), (-1, -1), 7),
        ("ALIGN", (1, 1), (-1, -1), "RIGHT"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 4),
        ("RIGHTPADDING", (0, 0), (-1, -1), 4),
    ])

    if "Total" in pdf_df.columns:
        total_col = list(pdf_df.columns).index("Total") + 1
        style.add("BACKGROUND", (total_col, 1), (total_col, -1), colors.HexColor("#dbeafe"))
        style.add("FONTNAME", (total_col, 1), (total_col, -1), "Helvetica-Bold")

    if "Total" in list(pdf_df.index):
        total_row = list(pdf_df.index).index("Total") + 1
        style.add("BACKGROUND", (0, total_row), (-1, total_row), colors.HexColor("#dcfce7"))
        style.add("FONTNAME", (0, total_row), (-1, total_row), "Helvetica-Bold")

    table.setStyle(style)
    table._argW = [0.75 * inch] + [None] * len(pdf_df.columns)
    return table


def _two_year_matrix_to_pdf_table(df, fiscal_years):
    from reportlab.lib import colors
    from reportlab.lib.units import inch
    from reportlab.platypus import Table, TableStyle

    if df.empty:
        return None

    year_names = {row[0]: row[1] for row in fiscal_years}
    programs_by_year = {
        year_id: [col[1] for col in df.columns if col[0] == year_id]
        for year_id, *_ in fiscal_years
    }

    header_top = ["Flow", "Category"]
    header_bottom = ["", ""]
    spans = []
    col_idx = 2
    for year_id, *_ in fiscal_years:
        programs = programs_by_year[year_id]
        header_top.extend([year_names[year_id]] + [""] * (len(programs) - 1))
        header_bottom.extend(programs)
        spans.append((col_idx, col_idx + len(programs) - 1))
        col_idx += len(programs)

    data = [header_top, header_bottom]
    section_start_rows = []
    subtotal_rows = []
    net_total_row = None
    previous_flow = None
    for (flow, category), row in df.iterrows():
        if flow != previous_flow:
            section_start_rows.append(len(data))
            previous_flow = flow
        if category in {"Total Income", "Total Expenses"}:
            subtotal_rows.append(len(data))
        elif category == "Net Total":
            net_total_row = len(data)
        data.append([flow, category] + [_currency(value) for value in row.tolist()])

    table = Table(
        data,
        repeatRows=2,
        hAlign="LEFT",
        colWidths=[0.55 * inch, 1.25 * inch] + [0.62 * inch] * (len(data[0]) - 2),
    )
    style = TableStyle([
        ("BACKGROUND", (0, 0), (-1, 1), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 1), colors.white),
        ("FONTNAME", (0, 0), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 5.8),
        ("ALIGN", (2, 2), (-1, -1), "RIGHT"),
        ("ALIGN", (2, 0), (-1, 1), "CENTER"),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ("ROWBACKGROUNDS", (0, 2), (-1, -1), [colors.white, colors.HexColor("#f9fafb")]),
        ("LEFTPADDING", (0, 0), (-1, -1), 2),
        ("RIGHTPADDING", (0, 0), (-1, -1), 2),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
    ])

    for start, end in spans:
        style.add("SPAN", (start, 0), (end, 0))
        style.add("BOX", (start, 0), (end, -1), 0.75, colors.black)

    for row_idx in section_start_rows:
        style.add("LINEABOVE", (0, row_idx), (-1, row_idx), 1.0, colors.black)
        style.add("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Bold")

    for row_idx in subtotal_rows:
        style.add("BACKGROUND", (0, row_idx), (-1, row_idx), colors.HexColor("#dbeafe"))
        style.add("FONTNAME", (0, row_idx), (-1, row_idx), "Helvetica-Bold")
        style.add("LINEABOVE", (0, row_idx), (-1, row_idx), 0.75, colors.HexColor("#2563eb"))

    if net_total_row is not None:
        style.add("BACKGROUND", (0, net_total_row), (-1, net_total_row), colors.HexColor("#dcfce7"))
        style.add("FONTNAME", (0, net_total_row), (-1, net_total_row), "Helvetica-Bold")
        style.add("LINEABOVE", (0, net_total_row), (-1, net_total_row), 1.25, colors.HexColor("#166534"))

    for col_idx, label in enumerate(header_bottom):
        if label == "Total":
            style.add("BACKGROUND", (col_idx, 2), (col_idx, -1), colors.HexColor("#dbeafe"))
            style.add("FONTNAME", (col_idx, 2), (col_idx, -1), "Helvetica-Bold")

    table.setStyle(style)
    return table


def _build_pdf_report(fy_id):
    try:
        from reportlab.lib import colors
        from reportlab.lib.pagesizes import letter, landscape
        from reportlab.lib.styles import getSampleStyleSheet
        from reportlab.lib.units import inch
        from reportlab.platypus import PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
    except ImportError as exc:
        raise RuntimeError("ReportLab is required to generate PDF reports. Install dependencies from requirements.txt.") from exc

    fy_name, fy_start, fy_end = _get_fiscal_year(fy_id)
    initial, posted, pending, projected = _get_summary(fy_id)
    program_breakdown = _get_program_breakdown(fy_id)
    category_totals = _get_category_totals(fy_id)
    two_year_matrix, two_years = _get_two_year_category_program_breakdown(fy_id)
    income_labels, income_values = _compact_pie_data(category_totals, "Income")
    expense_labels, expense_values = _compact_pie_data(category_totals, "Expense")

    buffer = BytesIO()
    doc = SimpleDocTemplate(
        buffer,
        pagesize=landscape(letter),
        rightMargin=0.35 * inch,
        leftMargin=0.35 * inch,
        topMargin=0.35 * inch,
        bottomMargin=0.35 * inch,
    )
    styles = getSampleStyleSheet()
    story = []

    now = datetime.now()
    generated_at = f"{now.strftime('%B')} {now.day}, {now.year} at {now.strftime('%I:%M %p').lstrip('0')}"

    story.append(Paragraph(f"{fy_name} Financial Report", styles["Title"]))
    story.append(Paragraph(f"Fiscal year: {fy_start} to {fy_end} | Generated {generated_at}", styles["Normal"]))
    story.append(Spacer(1, 0.18 * inch))

    summary_table = Table([
        ["Initial FY Balance", "Latest Posted Balance", "Pending Transactions", "Projected Total"],
        [_currency(initial), _currency(posted), _currency(pending), _currency(projected)],
    ], colWidths=[1.85 * inch, 1.85 * inch, 1.85 * inch, 1.85 * inch])
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#1f2937")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTNAME", (0, 1), (-1, 1), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 11),
        ("ALIGN", (0, 0), (-1, -1), "CENTER"),
        ("BACKGROUND", (0, 1), (-1, 1), colors.HexColor("#f3f4f6")),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ("TOPPADDING", (0, 0), (-1, -1), 8),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 8),
    ]))
    story.append(summary_table)
    story.append(Spacer(1, 0.22 * inch))

    story.append(Paragraph("YTD Posted Activity by Category", styles["Heading2"]))
    chart_table = Table([
        [
            _build_pie_chart("Income", income_labels, income_values),
            _build_pie_chart("Expenses", expense_labels, expense_values),
        ]
    ], colWidths=[3.65 * inch, 3.65 * inch])
    chart_table.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("BOX", (0, 0), (-1, -1), 0.25, colors.HexColor("#d1d5db")),
        ("INNERGRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#e5e7eb")),
    ]))
    story.append(chart_table)
    story.append(Spacer(1, 0.22 * inch))

    story.append(Paragraph("Financial Breakdown by Program", styles["Heading2"]))
    if program_breakdown.empty:
        story.append(Paragraph("No ledger activity found for this fiscal year.", styles["Normal"]))
    else:
        story.append(_dataframe_to_pdf_table(program_breakdown))

    story.append(PageBreak())
    if two_years:
        year_names = " and ".join(row[1] for row in two_years)
        story.append(Paragraph(f"Category by Program: {year_names}", styles["Title"]))
    else:
        story.append(Paragraph("Category by Program", styles["Title"]))
    story.append(Paragraph("Posted fiscal-year activity by category and program. Budget columns are intentionally omitted.", styles["Normal"]))
    story.append(Spacer(1, 0.16 * inch))
    matrix_table = _two_year_matrix_to_pdf_table(two_year_matrix, two_years)
    if matrix_table is None:
        story.append(Paragraph("No posted category/program activity found for the selected fiscal years.", styles["Normal"]))
    else:
        story.append(matrix_table)

    doc.build(story)
    return buffer.getvalue(), {
        "fy_name": fy_name,
        "initial": initial,
        "posted": posted,
        "pending": pending,
        "projected": projected,
        "program_breakdown": program_breakdown,
        "category_totals": category_totals,
        "two_year_matrix": two_year_matrix,
        "two_years": two_years,
    }


def render():
    st.title("Reports")
    st.write("Generate a fiscal-year PDF report with summary metrics, program totals, and YTD posted activity charts.")

    fy_id = st.session_state.selected_fy
    pdf_bytes, report_data = _build_pdf_report(fy_id)

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Initial FY Balance", _currency(report_data["initial"]))
    c2.metric("Latest Posted Balance", _currency(report_data["posted"]))
    c3.metric("Pending Transactions", _currency(report_data["pending"]))
    c4.metric("Projected Total", _currency(report_data["projected"]))

    st.write("### Program Breakdown Preview")
    if report_data["program_breakdown"].empty:
        st.info("No program activity found for this fiscal year.")
    else:
        st.dataframe(
            report_data["program_breakdown"].style.format("${:,.0f}"),
            width="stretch",
        )

    st.write("### Posted Category Totals Preview")
    category_preview = report_data["category_totals"].copy()
    if category_preview.empty:
        st.info("No posted category totals found for this fiscal year.")
    else:
        category_preview["total"] = category_preview["total"].apply(_currency)
        st.dataframe(
            category_preview.rename(columns={
                "flow": "Flow",
                "category": "Category",
                "total": "Total",
            })[["Flow", "Category", "Total"]],
            hide_index=True,
            width="stretch",
        )

    st.write("### Two-Year Category by Program Preview")
    if report_data["two_year_matrix"].empty:
        st.info("No posted category/program activity found for the selected fiscal years.")
    else:
        st.dataframe(
            report_data["two_year_matrix"].style.format("${:,.0f}"),
            width="stretch",
        )

    st.divider()
    st.write("### Export & Distribution Options")

    download_col, email_col = st.columns(2)

    filename = f"{report_data['fy_name'].replace(' ', '_')}_financial_report.pdf"

    with download_col:
        st.write("#### Download PDF")
        st.write("Save a local PDF copy of the financial report to your computer.")
        st.download_button(
            "Download PDF Report",
            data=pdf_bytes,
            file_name=filename,
            mime="application/pdf",
            type="primary",
            use_container_width=True,
        )

    with email_col:
        st.write("#### Email Report (AWS SES)")
        st.write("Send the PDF report directly as an email attachment via AWS Simple Email Service.")
        
        recipient_input = st.text_input(
            "Recipient Email Address",
            placeholder="e.g. board@domain.org",
            key="report_recipient_email_input",
            help="Enter a free-form email address. Address format will be validated before sending."
        )

        # Real-time email validation feedback as user types
        if recipient_input:
            is_valid, err_msg = email_service.validate_email(recipient_input)
            if not is_valid:
                st.caption(f"⚠️ {err_msg}")
            else:
                st.caption(f"✓ Valid email format: `{recipient_input.strip()}`")

        send_email_clicked = st.button(
            "Email PDF Report",
            type="primary",
            use_container_width=True,
            key="send_email_report_btn",
        )

        if send_email_clicked:
            if not recipient_input:
                st.error("Please enter a recipient email address before clicking send.")
            else:
                is_valid, val_err = email_service.validate_email(recipient_input)
                if not is_valid:
                    st.error(f"Cannot send email: {val_err}")
                else:
                    with st.spinner("Sending report via AWS SES..."):
                        subject = f"{report_data['fy_name']} Financial Report"
                        body_text = (
                            f"Hello,\n\n"
                            f"Attached is the financial report for {report_data['fy_name']}.\n\n"
                            f"Summary Metrics:\n"
                            f"  - Initial FY Balance: {_currency(report_data['initial'])}\n"
                            f"  - Latest Posted Balance: {_currency(report_data['posted'])}\n"
                            f"  - Pending Transactions: {_currency(report_data['pending'])}\n"
                            f"  - Projected Total: {_currency(report_data['projected'])}\n\n"
                            f"Best regards,\nAccounting Application"
                        )
                        result = email_service.send_report_email(
                            recipient_email=recipient_input,
                            subject=subject,
                            body_text=body_text,
                            pdf_bytes=pdf_bytes,
                            filename=filename,
                        )

                        if result["success"]:
                            st.success(
                                f"Report successfully emailed to **{result['recipient']}**! "
                                f"(AWS SES Message ID: `{result['message_id']}`)"
                            )
                        else:
                            st.error(f"Failed to send email via AWS SES:\n\n{result['error']}")

    with st.expander("AWS SES Diagnostics & Sender Information", expanded=False):
        try:
            aws_config = email_service.get_aws_config()
            st.write(f"- **Configured Sender (`EMAIL_SENDER`):** `{aws_config.get('sender_email') or 'Not configured'}`")
            st.write(f"- **AWS Region:** `{aws_config.get('region_name')}`")
            st.write(f"- **Access Key ID:** `{aws_config.get('aws_access_key_id')[:6]}...`")

            ses_status = email_service.verify_ses_capability()
            if ses_status["ok"]:
                st.success("AWS SES client initialized successfully.")
                if "verified_identities" in ses_status:
                    st.write(f"- **Verified Identities in SES:** `{ses_status['verified_identities']}`")
            else:
                st.warning(f"AWS SES Status Alert: {ses_status.get('error')}")
        except Exception as exc:
            st.error(f"Could not load AWS SES configuration: {exc}")
