"""
Compute chart-ready financial metrics from parsed P&L and Balance Sheet data.
"""

from __future__ import annotations

from datetime import datetime

# ---------------------------------------------------------------------------
# Expense grouping: 44 line items → 9 readable categories
# ---------------------------------------------------------------------------

EXPENSE_GROUPS = {
    "Labor": [
        "Payroll: Staff Wages",
        "Payroll: Staff Tips",
        "Payroll: Employer Payroll Taxes",
        "Payroll: Employer Payroll Tax Adjustment",
        "Payroll: SIMPLE Employer Match",
        "Payroll: Staff Wages - ERC Adjustment",
        "Casual Labor",
        "Non-Employee Tips",
        "Mass. Employee Health Tax - EMAC",
        "2% Shareholders Health Ins",
    ],
    "Third Party Fees": [
        "Third Party Fees",
        "Toast Delivery Fees",
        "Merchant Service Fees",
    ],
    "Rent & Occupancy": [
        "Rents",
        "Utilities",
        "Telephone & Internet",
        "Storage",
    ],
    "Professional & Admin": [
        "Professional Fees",
        "Insurance",
        "Bank Charges",
        "Taxes & Licenses",
        "Taxes & Licenses: State PTE Tax",
        "Postage & Shipping",
    ],
    "Marketing": [
        "Advertising & Promotion",
        "Events & Meetings",
        "Contributions",
        "Meals & Entertainment-50%",
    ],
    "Technology": [
        "Software Apps & Subscriptions",
        "Dues & Subscriptions",
    ],
    "Capital & Depreciation": [
        "Depreciation",
        "Amortization",
        "De Minimus Expensed Asset",
        "Interest Expense",
    ],
    "Other": [],  # Catch-all for unmatched accounts
}

# Build reverse lookup
_ACCT_TO_GROUP: dict[str, str] = {}
for group, accts in EXPENSE_GROUPS.items():
    for acct in accts:
        _ACCT_TO_GROUP[acct] = group


# Accounts excluded from expenses entirely (treated as owner compensation)
_OWNER_COMP_ACCOUNTS = {"Payroll: Officers Salaries"}


def _group_for(acct: str) -> str:
    if acct in _OWNER_COMP_ACCOUNTS:
        return None  # Excluded from expense grouping
    return _ACCT_TO_GROUP.get(acct, "Other")


def _fmt_month_label(ym: str) -> str:
    """Convert '2024-01' to 'Jan 2024'."""
    try:
        dt = datetime.strptime(ym, "%Y-%m")
        return dt.strftime("%b %Y")
    except ValueError:
        return ym


def _fmt_month_short(ym: str) -> str:
    """Convert '2024-01' to 'Jan '24'."""
    try:
        dt = datetime.strptime(ym, "%Y-%m")
        return dt.strftime("%b '%y")
    except ValueError:
        return ym


# ---------------------------------------------------------------------------
# Main metrics computation
# ---------------------------------------------------------------------------

def _slice_pl(pl_data: dict, start: int, end: int) -> dict:
    """Slice all P&L arrays to [start:end]."""
    out = dict(pl_data)
    out["months"] = pl_data["months"][start:end]
    out["income"] = {k: v[start:end] for k, v in pl_data["income"].items()}
    out["cogs"] = pl_data["cogs"][start:end]
    out["opex"] = {k: v[start:end] for k, v in pl_data["opex"].items()}
    out["total_income"] = pl_data["total_income"][start:end]
    out["gross_profit"] = pl_data["gross_profit"][start:end]
    out["total_opex"] = pl_data["total_opex"][start:end]
    out["total_other_income"] = pl_data["total_other_income"][start:end]
    out["net_income"] = pl_data["net_income"][start:end]
    return out


def _slice_bs(bs_data: dict, start: int, end: int) -> dict:
    """Slice all BS arrays to [start:end]."""
    out = dict(bs_data)
    out["months"] = bs_data["months"][start:end]
    for k in ("total_cash", "total_assets", "total_liabilities",
              "total_equity", "total_long_term_liabilities"):
        if k in bs_data:
            out[k] = bs_data[k][start:end]
    return out


def compute_financials_metrics(
    pl_data: dict,
    bs_data: dict,
    start_month: str | None = None,
    end_month: str | None = None,
) -> dict:
    """Transform parsed P&L and BS data into chart-ready metrics.

    Args:
        pl_data: Output of parse_all_pl()
        bs_data: Output of parse_all_bs()
        start_month: Optional "YYYY-MM" to filter from (inclusive)
        end_month: Optional "YYYY-MM" to filter to (inclusive)

    Returns dict with keys:
        kpis, revenue_trend, margins, annual, expense_groups,
        expense_trends, balance_sheet, seasonality, period
    """
    # ── Apply date filter ──
    all_months = pl_data["months"]
    if start_month or end_month:
        s_idx = 0
        e_idx = len(all_months)
        if start_month:
            for i, m in enumerate(all_months):
                if m >= start_month:
                    s_idx = i
                    break
        if end_month:
            for i, m in enumerate(all_months):
                if m <= end_month:
                    e_idx = i + 1
        pl_data = _slice_pl(pl_data, s_idx, e_idx)

        # Slice BS to same range (different month array)
        bs_months = bs_data.get("months", [])
        bs_s = 0
        bs_e = len(bs_months)
        if start_month:
            for i, m in enumerate(bs_months):
                if m >= start_month:
                    bs_s = i
                    break
        if end_month:
            for i, m in enumerate(bs_months):
                if m <= end_month:
                    bs_e = i + 1
        bs_data = _slice_bs(bs_data, bs_s, bs_e)

    months = pl_data["months"]
    n = len(months)
    sales = pl_data["income"].get("Sales", [0.0] * n)
    total_income = pl_data["total_income"]
    cogs = pl_data["cogs"]
    gross_profit = pl_data["gross_profit"]

    # Adjust for owner compensation: remove officer salaries from expenses
    # so they flow through to net income (owner's discretionary earnings)
    opex_dict = pl_data["opex"]
    owner_comp = [0.0] * n
    for acct in _OWNER_COMP_ACCOUNTS:
        if acct in opex_dict:
            for i in range(n):
                owner_comp[i] += opex_dict[acct][i]

    total_opex = [pl_data["total_opex"][i] - owner_comp[i] for i in range(n)]
    operating_income = [gross_profit[i] - total_opex[i] for i in range(n)]
    net_income = [operating_income[i] + pl_data["total_other_income"][i] for i in range(n)]

    # Month labels for charts
    labels = [_fmt_month_short(m) for m in months]

    # ── 12-month rolling average ──
    rolling_12 = [None] * n
    for i in range(11, n):
        rolling_12[i] = round(sum(sales[i - 11:i + 1]) / 12, 2)

    # ── Margin percentages ──
    gross_margin_pct = []
    operating_margin_pct = []
    net_margin_pct = []
    cogs_pct = []
    opex_pct = []

    for i in range(n):
        ti = total_income[i]
        # Skip months with negligible or negative income (produces meaningless margins)
        if ti is not None and ti > 5000:
            gross_margin_pct.append(round(gross_profit[i] / ti * 100, 1))
            operating_margin_pct.append(round(operating_income[i] / ti * 100, 1))
            net_margin_pct.append(round(net_income[i] / ti * 100, 1))
            cogs_pct.append(round(cogs[i] / ti * 100, 1))
            opex_pct.append(round(total_opex[i] / ti * 100, 1))
        else:
            gross_margin_pct.append(None)
            operating_margin_pct.append(None)
            net_margin_pct.append(None)
            cogs_pct.append(None)
            opex_pct.append(None)

    # ── Annual comparison ──
    year_data: dict[int, dict] = {}
    for i, m in enumerate(months):
        yr = int(m[:4])
        if yr not in year_data:
            year_data[yr] = {"revenue": 0.0, "net_income": 0.0, "opex": 0.0,
                             "cogs": 0.0, "months": 0}
        year_data[yr]["revenue"] += sales[i]
        year_data[yr]["net_income"] += net_income[i]
        year_data[yr]["opex"] += total_opex[i]
        year_data[yr]["cogs"] += cogs[i]
        year_data[yr]["months"] += 1

    years = sorted(year_data.keys())
    annual_revenue = [round(year_data[y]["revenue"], 2) for y in years]
    annual_net_income = [round(year_data[y]["net_income"], 2) for y in years]
    annual_opex = [round(year_data[y]["opex"], 2) for y in years]

    # ── Expense grouping ──
    group_monthly: dict[str, list[float]] = {}
    for grp in EXPENSE_GROUPS:
        group_monthly[grp] = [0.0] * n

    for acct, vals in opex_dict.items():
        grp = _group_for(acct)
        if grp is None:
            continue  # Owner compensation — excluded from expenses
        for i in range(n):
            group_monthly[grp][i] += vals[i]

    # Also add COGS into a display group
    group_monthly["Cost of Goods"] = list(cogs)

    # Latest 12 months totals for pie chart
    latest_12_start = max(0, n - 12)
    latest_12_revenue = sum(sales[latest_12_start:])
    expense_pie: dict[str, float] = {}
    expense_pie_pct: dict[str, float] = {}
    for grp, vals in group_monthly.items():
        total = sum(vals[latest_12_start:])
        if total > 0:
            expense_pie[grp] = round(total, 2)
            if latest_12_revenue > 0:
                expense_pie_pct[grp] = round(total / latest_12_revenue * 100, 1)

    # Sort by value descending
    expense_pie = dict(sorted(expense_pie.items(), key=lambda x: x[1], reverse=True))
    expense_pie_pct = dict(sorted(expense_pie_pct.items(), key=lambda x: x[1], reverse=True))

    # ── Quarterly aggregation for expense trends ──
    quarter_labels = []
    quarter_data: dict[str, list[float]] = {grp: [] for grp in group_monthly}
    quarter_revenue: list[float] = []
    q_acc: dict[str, float] = {grp: 0.0 for grp in group_monthly}
    q_rev_acc = 0.0
    q_year = None
    q_quarter = None

    for i, m in enumerate(months):
        yr = int(m[:4])
        mo = int(m[5:7])
        q = (mo - 1) // 3 + 1

        if q_year is not None and (yr != q_year or q != q_quarter):
            quarter_labels.append(f"Q{q_quarter} {q_year}")
            quarter_revenue.append(round(q_rev_acc, 2))
            for grp in group_monthly:
                quarter_data[grp].append(round(q_acc[grp], 2))
                q_acc[grp] = 0.0
            q_rev_acc = 0.0

        q_year = yr
        q_quarter = q
        q_rev_acc += sales[i]
        for grp in group_monthly:
            q_acc[grp] += group_monthly[grp][i]

    # Flush last quarter
    if q_year is not None:
        quarter_labels.append(f"Q{q_quarter} {q_year}")
        quarter_revenue.append(round(q_rev_acc, 2))
        for grp in group_monthly:
            quarter_data[grp].append(round(q_acc[grp], 2))

    # Compute quarterly % of revenue
    quarter_data_pct: dict[str, list[float]] = {}
    for grp, vals in quarter_data.items():
        pct_vals = []
        for i, v in enumerate(vals):
            qr = quarter_revenue[i] if i < len(quarter_revenue) else 0
            pct_vals.append(round(v / qr * 100, 1) if qr > 0 else 0.0)
        quarter_data_pct[grp] = pct_vals

    # ── Balance sheet trends ──
    bs_months = bs_data.get("months", [])
    bs_labels = [_fmt_month_short(m) for m in bs_months]
    bs_total_cash = bs_data.get("total_cash", [])
    bs_total_assets = bs_data.get("total_assets", [])
    bs_total_liabilities = bs_data.get("total_liabilities", [])
    bs_total_equity = bs_data.get("total_equity", [])

    # Debt = long-term liabilities
    bs_debt = bs_data.get("total_long_term_liabilities", [])

    # ── Seasonality: average revenue by month-of-year ──
    month_totals: dict[int, list[float]] = {m: [] for m in range(1, 13)}
    for i, ym in enumerate(months):
        mo = int(ym[5:7])
        if sales[i] > 0:
            month_totals[mo].append(sales[i])

    seasonality_labels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    seasonality_values = []
    for mo in range(1, 13):
        vals = month_totals[mo]
        seasonality_values.append(round(sum(vals) / len(vals), 2) if vals else 0)

    # ── KPIs (latest month) ──
    latest_idx = n - 1
    latest_month = _fmt_month_label(months[latest_idx])
    monthly_revenue = round(sales[latest_idx], 2)
    monthly_net = round(net_income[latest_idx], 2)

    # YoY growth (same month last year)
    yoy_revenue_growth = None
    yoy_month = months[latest_idx][:4]
    target_ym = f"{int(yoy_month) - 1}{months[latest_idx][4:]}"
    if target_ym in months:
        prev_idx = months.index(target_ym)
        prev_sales = sales[prev_idx]
        if prev_sales > 0:
            yoy_revenue_growth = round((sales[latest_idx] - prev_sales) / prev_sales * 100, 1)

    # MoM growth
    mom_revenue_growth = None
    if n >= 2 and sales[latest_idx - 1] > 0:
        mom_revenue_growth = round(
            (sales[latest_idx] - sales[latest_idx - 1]) / sales[latest_idx - 1] * 100, 1
        )

    # Latest BS values
    bs_latest_cash = bs_total_cash[-1] if bs_total_cash else 0
    bs_latest_debt = bs_debt[-1] if bs_debt else 0

    # ── Owner compensation totals ──
    latest_12_owner_comp = sum(owner_comp[latest_12_start:])

    # ── Consultant Analysis ──
    analysis = _compute_analysis(
        months, sales, total_income, cogs, gross_profit,
        total_opex, operating_income, net_income,
        gross_margin_pct, operating_margin_pct, net_margin_pct,
        year_data, years, group_monthly, latest_12_start,
        latest_12_revenue, expense_pie, expense_pie_pct,
        seasonality_values, bs_total_cash, bs_debt, bs_total_equity,
        owner_comp, latest_12_owner_comp,
    )

    # ── Period metadata ──
    period_start = _fmt_month_label(months[0]) if months else ""
    period_end = _fmt_month_label(months[-1]) if months else ""
    period_months = n

    # ── Period totals (for multi-month summary) ──
    period_total_revenue = round(sum(sales), 2)
    period_total_net = round(sum(net_income), 2)
    period_avg_revenue = round(period_total_revenue / n, 2) if n else 0
    period_avg_net = round(period_total_net / n, 2) if n else 0

    return {
        "period": {
            "start": period_start,
            "end": period_end,
            "months": period_months,
            "total_revenue": period_total_revenue,
            "total_net_income": period_total_net,
            "avg_monthly_revenue": period_avg_revenue,
            "avg_monthly_net_income": period_avg_net,
            "is_filtered": start_month is not None or end_month is not None,
        },
        "analysis": analysis,
        "kpis": {
            "latest_month": latest_month,
            "monthly_revenue": monthly_revenue,
            "monthly_net_income": monthly_net,
            "gross_margin": gross_margin_pct[latest_idx],
            "operating_margin": operating_margin_pct[latest_idx],
            "net_margin": net_margin_pct[latest_idx],
            "total_cash": round(bs_latest_cash, 2),
            "total_debt": round(bs_latest_debt, 2),
            "yoy_revenue_growth": yoy_revenue_growth,
            "mom_revenue_growth": mom_revenue_growth,
            "cogs_pct": cogs_pct[latest_idx],
            "opex_pct": opex_pct[latest_idx],
            "owner_comp": round(owner_comp[latest_idx], 2),
            "owner_comp_12mo": round(latest_12_owner_comp, 2),
        },
        "revenue_trend": {
            "labels": labels,
            "sales": [round(v, 2) for v in sales],
            "net_income": [round(v, 2) for v in net_income],
            "rolling_12": rolling_12,
        },
        "margins": {
            "labels": labels,
            "gross_margin_pct": gross_margin_pct,
            "operating_margin_pct": operating_margin_pct,
            "net_margin_pct": net_margin_pct,
        },
        "annual": {
            "years": [str(y) for y in years],
            "revenue": annual_revenue,
            "net_income": annual_net_income,
            "opex": annual_opex,
        },
        "expense_groups": {
            "pie": expense_pie,
            "pie_pct": expense_pie_pct,
            "latest_12_revenue": round(latest_12_revenue, 2),
            "monthly_labels": labels,
            "monthly": {grp: [round(v, 2) for v in vals] for grp, vals in group_monthly.items()},
            "quarter_labels": quarter_labels,
            "quarterly": {grp: vals for grp, vals in quarter_data.items()},
            "quarterly_pct": quarter_data_pct,
            "quarter_revenue": quarter_revenue,
        },
        "balance_sheet": {
            "labels": bs_labels,
            "total_cash": [round(v, 2) for v in bs_total_cash],
            "total_assets": [round(v, 2) for v in bs_total_assets],
            "total_liabilities": [round(v, 2) for v in bs_total_liabilities],
            "total_equity": [round(v, 2) for v in bs_total_equity],
            "debt": [round(v, 2) for v in bs_debt],
        },
        "seasonality": {
            "labels": seasonality_labels,
            "values": seasonality_values,
        },
    }


# ---------------------------------------------------------------------------
# Consultant-style financial analysis
# ---------------------------------------------------------------------------

def _compute_analysis(
    months, sales, total_income, cogs, gross_profit,
    total_opex, operating_income, net_income,
    gross_margin_pct, operating_margin_pct, net_margin_pct,
    year_data, years, group_monthly, latest_12_start,
    latest_12_revenue, expense_pie, expense_pie_pct,
    seasonality_values, bs_total_cash, bs_debt, bs_total_equity,
    owner_comp=None, latest_12_owner_comp=0,
) -> dict:
    """Generate data-driven consultant analysis from financial metrics."""
    n = len(months)
    insights = []

    # Helper: safe average of non-None values
    def _avg(vals):
        clean = [v for v in vals if v is not None]
        return sum(clean) / len(clean) if clean else None

    # Helper: format dollar
    def _fd(v):
        if v is None:
            return "N/A"
        if abs(v) >= 1_000_000:
            return f"${v / 1_000_000:,.1f}M"
        if abs(v) >= 1_000:
            return f"${v / 1_000:,.0f}K"
        return f"${v:,.0f}"

    # ---------- 1. Revenue Growth Trajectory ----------
    rev_section = {"title": "Revenue Growth", "items": []}

    # CAGR from first full year to last full year
    full_years = [y for y in years if year_data[y]["months"] == 12]
    if len(full_years) >= 2:
        first_yr, last_yr = full_years[0], full_years[-1]
        r0 = year_data[first_yr]["revenue"]
        r1 = year_data[last_yr]["revenue"]
        span = last_yr - first_yr
        if r0 > 0 and span > 0:
            cagr = ((r1 / r0) ** (1.0 / span) - 1) * 100
            rev_section["items"].append(
                f"Compound annual growth rate (CAGR) of {cagr:.1f}% from "
                f"{first_yr} ({_fd(r0)}) to {last_yr} ({_fd(r1)})."
            )

    # Recent 12-month vs prior 12-month comparison
    if n >= 24:
        recent_12 = sum(sales[n - 12:])
        prior_12 = sum(sales[n - 24:n - 12])
        if prior_12 > 0:
            yoy_12 = (recent_12 - prior_12) / prior_12 * 100
            direction = "up" if yoy_12 > 0 else "down"
            rev_section["items"].append(
                f"Trailing 12-month revenue ({_fd(recent_12)}) is {direction} "
                f"{abs(yoy_12):.1f}% vs. the prior 12 months ({_fd(prior_12)})."
            )

    # Peak revenue month
    if n > 0:
        peak_idx = max(range(n), key=lambda i: sales[i])
        peak_val = sales[peak_idx]
        peak_label = _fmt_month_label(months[peak_idx])
        rev_section["items"].append(
            f"All-time peak monthly revenue: {_fd(peak_val)} in {peak_label}."
        )

    # Revenue trajectory (last 3 full years)
    recent_full = full_years[-3:] if len(full_years) >= 3 else full_years
    if len(recent_full) >= 2:
        yoy_changes = []
        for i in range(1, len(recent_full)):
            prev_r = year_data[recent_full[i - 1]]["revenue"]
            curr_r = year_data[recent_full[i]]["revenue"]
            if prev_r > 0:
                yoy_changes.append((recent_full[i], (curr_r - prev_r) / prev_r * 100))
        if yoy_changes:
            parts = [f"{yr}: {chg:+.1f}%" for yr, chg in yoy_changes]
            rev_section["items"].append(
                f"Year-over-year revenue changes: {', '.join(parts)}."
            )

    insights.append(rev_section)

    # ---------- 2. Profitability Analysis ----------
    profit_section = {"title": "Profitability", "items": []}

    # Average margins over recent 12 months
    recent_gm = _avg(gross_margin_pct[latest_12_start:])
    recent_om = _avg(operating_margin_pct[latest_12_start:])
    recent_nm = _avg(net_margin_pct[latest_12_start:])

    if recent_gm is not None:
        profit_section["items"].append(
            f"Trailing 12-month average gross margin: {recent_gm:.1f}%. "
            f"Industry benchmark for fast-casual restaurants is typically 60-70%."
        )

    if recent_om is not None:
        om_assessment = "healthy" if recent_om > 10 else "below target" if recent_om > 0 else "negative"
        profit_section["items"].append(
            f"Average operating margin: {recent_om:.1f}% ({om_assessment}). "
            f"Restaurant industry target is 10-15%."
        )

    if recent_nm is not None:
        profit_section["items"].append(
            f"Average net margin: {recent_nm:.1f}%. "
            f"Typical healthy restaurant net margin is 3-9%."
        )

    # Owner compensation note
    if latest_12_owner_comp > 0:
        oc_pct = (latest_12_owner_comp / latest_12_revenue * 100) if latest_12_revenue > 0 else 0
        profit_section["items"].append(
            f"Note: Owner/officer compensation ({_fd(latest_12_owner_comp)}, "
            f"{oc_pct:.1f}% of revenue) is excluded from expenses above. "
            f"Net income shown represents owner's discretionary earnings before officer pay."
        )

    # Profitability trend (compare 2-year halves)
    if n >= 24:
        first_half_nm = _avg(net_margin_pct[n - 24:n - 12])
        second_half_nm = _avg(net_margin_pct[n - 12:])
        if first_half_nm is not None and second_half_nm is not None:
            delta = second_half_nm - first_half_nm
            direction = "improving" if delta > 1 else "declining" if delta < -1 else "stable"
            profit_section["items"].append(
                f"Net margin trend is {direction}: moved from {first_half_nm:.1f}% "
                f"to {second_half_nm:.1f}% (most recent vs. prior 12 months)."
            )

    # Count profitable vs unprofitable months (recent 24)
    window = min(24, n)
    profitable_months = sum(1 for i in range(n - window, n) if net_income[i] > 0)
    profit_section["items"].append(
        f"Profitable in {profitable_months} of last {window} months "
        f"({profitable_months / window * 100:.0f}% profitability rate)."
    )

    insights.append(profit_section)

    # ---------- 3. Cost Structure ----------
    cost_section = {"title": "Cost Structure", "items": []}

    # COGS as % of revenue
    if latest_12_revenue > 0:
        recent_cogs = sum(cogs[latest_12_start:])
        cogs_ratio = recent_cogs / latest_12_revenue * 100
        cost_section["items"].append(
            f"Cost of goods sold: {cogs_ratio:.1f}% of revenue ({_fd(recent_cogs)}) over the last 12 months. "
            f"Fast-casual benchmark is 28-35%."
        )

    # Top expense categories
    if expense_pie_pct:
        sorted_cats = sorted(expense_pie_pct.items(), key=lambda x: x[1], reverse=True)
        top_3 = sorted_cats[:3]
        parts = [f"{cat} ({pct:.1f}%)" for cat, pct in top_3]
        cost_section["items"].append(
            f"Largest expense categories (% of revenue): {', '.join(parts)}."
        )

    # Labor cost ratio
    if "Labor" in expense_pie and latest_12_revenue > 0:
        labor_pct = expense_pie["Labor"] / latest_12_revenue * 100
        assessment = "within range" if 25 <= labor_pct <= 35 else "above target" if labor_pct > 35 else "lean"
        cost_section["items"].append(
            f"Labor costs represent {labor_pct:.1f}% of revenue ({assessment}). "
            f"Restaurant industry target: 25-35%."
        )

    # Total expense ratio
    if latest_12_revenue > 0:
        total_exp = sum(expense_pie.values())
        exp_ratio = total_exp / latest_12_revenue * 100
        cost_section["items"].append(
            f"Total expenses (COGS + OpEx) consume {exp_ratio:.1f}% of revenue. "
            f"Remaining {100 - exp_ratio:.1f}% flows to other income/expense and net income."
        )

    insights.append(cost_section)

    # ---------- 4. Balance Sheet & Liquidity ----------
    bs_section = {"title": "Balance Sheet & Liquidity", "items": []}

    if bs_total_cash:
        latest_cash = bs_total_cash[-1]
        bs_section["items"].append(
            f"Current cash position: {_fd(latest_cash)}."
        )

        # Cash trend
        if len(bs_total_cash) >= 12:
            cash_12_ago = bs_total_cash[-12]
            if cash_12_ago > 0:
                cash_chg = (latest_cash - cash_12_ago) / cash_12_ago * 100
                direction = "increased" if cash_chg > 0 else "decreased"
                bs_section["items"].append(
                    f"Cash has {direction} {abs(cash_chg):.0f}% over the last 12 months "
                    f"(from {_fd(cash_12_ago)} to {_fd(latest_cash)})."
                )

    # Monthly burn rate (for months with negative net income)
    recent_losses = [ni for ni in net_income[latest_12_start:] if ni < 0]
    if latest_cash > 0 and recent_losses:
        avg_burn = abs(sum(recent_losses) / len(recent_losses))
        runway = latest_cash / avg_burn if avg_burn > 0 else float("inf")
        bs_section["items"].append(
            f"In loss-making months, average monthly burn is {_fd(avg_burn)}. "
            f"At current cash levels, that implies ~{runway:.0f} months of runway."
        )

    # Debt position
    if bs_debt:
        latest_debt = bs_debt[-1]
        if latest_debt > 0:
            bs_section["items"].append(
                f"Long-term debt: {_fd(latest_debt)}."
            )
            if bs_total_cash and bs_total_cash[-1] > 0:
                ratio = latest_debt / bs_total_cash[-1]
                bs_section["items"].append(
                    f"Debt-to-cash ratio: {ratio:.2f}x."
                )
        else:
            bs_section["items"].append(
                "No long-term debt outstanding — the business is debt-free."
            )

    # Equity
    if bs_total_equity:
        latest_eq = bs_total_equity[-1]
        bs_section["items"].append(
            f"Total equity: {_fd(latest_eq)}."
        )

    insights.append(bs_section)

    # ---------- 5. Seasonality & Risk ----------
    season_section = {"title": "Seasonality & Risk Factors", "items": []}

    if seasonality_values:
        # Skip zero months
        active = [(i, v) for i, v in enumerate(seasonality_values) if v > 0]
        if active:
            peak_mo_idx = max(active, key=lambda x: x[1])[0]
            trough_mo_idx = min(active, key=lambda x: x[1])[0]
            mo_names = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
                        "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
            peak_val = seasonality_values[peak_mo_idx]
            trough_val = seasonality_values[trough_mo_idx]
            swing = ((peak_val - trough_val) / trough_val * 100) if trough_val > 0 else 0

            season_section["items"].append(
                f"Peak month: {mo_names[peak_mo_idx]} (avg {_fd(peak_val)}). "
                f"Slowest month: {mo_names[trough_mo_idx]} (avg {_fd(trough_val)}). "
                f"Seasonal swing of {swing:.0f}%."
            )

            # Q1 vs Q3 comparison
            q1_avg = _avg([seasonality_values[i] for i in [0, 1, 2]])
            q3_avg = _avg([seasonality_values[i] for i in [6, 7, 8]])
            if q1_avg and q3_avg:
                diff = ((q3_avg - q1_avg) / q1_avg * 100)
                if abs(diff) > 5:
                    stronger = "summer" if diff > 0 else "winter"
                    season_section["items"].append(
                        f"Revenue is {abs(diff):.0f}% stronger in {stronger} months. "
                        f"This seasonal pattern should inform staffing and inventory planning."
                    )

    # Revenue volatility (coefficient of variation, last 24 months)
    if n >= 12:
        window_sales = [s for s in sales[max(0, n - 24):] if s > 0]
        if window_sales:
            mean_s = sum(window_sales) / len(window_sales)
            variance = sum((s - mean_s) ** 2 for s in window_sales) / len(window_sales)
            std_dev = variance ** 0.5
            cv = (std_dev / mean_s * 100) if mean_s > 0 else 0
            stability = "very stable" if cv < 10 else "moderately stable" if cv < 20 else "volatile"
            season_section["items"].append(
                f"Revenue variability (CV): {cv:.1f}% — {stability}. "
                f"Average monthly revenue: {_fd(mean_s)}."
            )

    insights.append(season_section)

    # ---------- 6. Key Takeaways ----------
    takeaway_section = {"title": "Key Takeaways & Recommendations", "items": []}

    # Generate dynamic recommendations based on metrics
    if recent_gm is not None and recent_gm < 60:
        takeaway_section["items"].append(
            f"Gross margin ({recent_gm:.1f}%) is below the 60-70% fast-casual benchmark. "
            f"Evaluate menu pricing, portion sizes, and supplier costs to improve COGS efficiency."
        )
    elif recent_gm is not None and recent_gm >= 65:
        takeaway_section["items"].append(
            f"Gross margin ({recent_gm:.1f}%) is strong. "
            f"Continue monitoring supplier costs and menu mix to maintain this advantage."
        )

    if recent_om is not None and recent_om < 5:
        takeaway_section["items"].append(
            "Operating margin is thin. Focus on expense rationalization — "
            "review labor scheduling, renegotiate vendor contracts, and audit recurring subscriptions."
        )

    if "Labor" in expense_pie_pct and expense_pie_pct.get("Labor", 0) > 35:
        takeaway_section["items"].append(
            f"Labor costs ({expense_pie_pct['Labor']:.1f}% of revenue) exceed the 35% threshold. "
            f"Consider optimizing scheduling, cross-training staff, or investing in labor-saving equipment."
        )

    if "Third Party Fees" in expense_pie_pct and expense_pie_pct.get("Third Party Fees", 0) > 10:
        tp_pct = expense_pie_pct["Third Party Fees"]
        takeaway_section["items"].append(
            f"Third-party delivery fees are {tp_pct:.1f}% of revenue. "
            f"Strategies to shift volume to direct ordering channels could significantly improve margins."
        )

    # Cash position relative to monthly expenses
    if bs_total_cash and latest_12_revenue > 0:
        total_monthly_exp = sum(expense_pie.values()) / 12 if expense_pie else 0
        if total_monthly_exp > 0:
            months_coverage = bs_total_cash[-1] / total_monthly_exp
            if months_coverage < 2:
                takeaway_section["items"].append(
                    f"Cash reserves cover only {months_coverage:.1f} months of expenses. "
                    f"Building a 3-6 month cash reserve should be a priority."
                )
            elif months_coverage >= 3:
                takeaway_section["items"].append(
                    f"Cash reserves cover {months_coverage:.1f} months of expenses — a solid cushion."
                )

    # Revenue growth direction
    if len(full_years) >= 2:
        last_full = full_years[-1]
        prev_full = full_years[-2]
        r_last = year_data[last_full]["revenue"]
        r_prev = year_data[prev_full]["revenue"]
        if r_prev > 0:
            growth = (r_last - r_prev) / r_prev * 100
            if growth > 5:
                takeaway_section["items"].append(
                    f"Revenue grew {growth:.1f}% in {last_full}. "
                    f"Sustaining this growth while maintaining margins is the key financial objective."
                )
            elif growth < -5:
                takeaway_section["items"].append(
                    f"Revenue declined {abs(growth):.1f}% in {last_full}. "
                    f"Identifying and addressing the drivers of this decline should be the top priority."
                )

    if not takeaway_section["items"]:
        takeaway_section["items"].append(
            "Overall financial health appears stable. Continue monitoring margins and cash flow monthly."
        )

    insights.append(takeaway_section)

    return {"sections": insights}
