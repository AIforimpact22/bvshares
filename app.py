
from flask import Flask, render_template, request
from datetime import date, timedelta

app = Flask(__name__)

# --------- Helpers ---------
def parse_date(s, default=None):
    """Parse YYYY-MM-DD string to date; return default if empty/invalid."""
    if not s:
        return default
    try:
        parts = [int(p) for p in s.split("-")]
        return date(parts[0], parts[1], parts[2])
    except Exception:
        return default

def parse_float(s, default=0.0):
    """Parse string to float; accept both comma and dot decimals."""
    if s is None or str(s).strip() == "":
        return default
    try:
        return float(str(s).replace(",", ".").strip())
    except Exception:
        return default

def days_inclusive(start: date, end: date) -> int:
    """Inclusive day count from start to end; 0 if end < start."""
    if start is None or end is None:
        return 0
    if end < start:
        return 0
    return (end - start).days + 1

def add_months(d: date, months: int) -> date:
    """Add months to a date (keeping day when possible; snaps to last valid day)."""
    y = d.year + (d.month - 1 + months) // 12
    m = (d.month - 1 + months) % 12 + 1
    # Handle end-of-month
    day = min(d.day, [31,
                      29 if (y % 4 == 0 and (y % 100 != 0 or y % 400 == 0)) else 28,
                      31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m-1])
    return date(y, m, day)

def eur(x):
    return f"€{x:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")

# --------- Core calculator ---------
def calculate(params):
    # Inputs
    share_pct = parse_float(params.get("share_pct", "40")) / 100.0

    # Key dates
    calc_start = parse_date(params.get("calc_start"), date(2024, 9, 1))
    join_date = parse_date(params.get("join_date"), date.today())
    projection_start = parse_date(params.get("projection_start"), date(2026, 1, 1))
    projection_months = int(parse_float(params.get("projection_months", "24"), 24.0))

    director_start = parse_date(params.get("director_start"), date(2025, 1, 1))

    # Amounts
    past_cash_total = parse_float(params.get("past_cash_total"), 0.0)
    portal_val = parse_float(params.get("portal_val"), 0.0)
    finance_val = parse_float(params.get("finance_val"), 0.0)
    website_val = parse_float(params.get("website_val"), 0.0)
    monthly_ops = parse_float(params.get("monthly_ops"), 0.0)
    legal_fee = parse_float(params.get("legal_fee"), 0.0)
    legal_fee_date = parse_date(params.get("legal_fee_date"), date(2026, 1, 1))
    director_salary_year = parse_float(params.get("director_salary_year"), 56000.0)

    # RIV projections (revenues)
    bootcamp_start = parse_date(params.get("riv_bootcamp_start"), projection_start)
    bootcamp_per_year = parse_float(params.get("riv_bootcamp_per_year"), 4.0)
    bootcamp_amount = parse_float(params.get("riv_bootcamp_amount"), 7000.0)

    coaching_start = parse_date(params.get("riv_coaching_start"), projection_start)
    coaching_per_year = parse_float(params.get("riv_coaching_per_year"), 12.0)
    coaching_amount = parse_float(params.get("riv_coaching_amount"), 900.0)

    llm_start = parse_date(params.get("riv_llm_start"), projection_start)
    llm_clients = parse_float(params.get("riv_llm_clients"), 1.0)
    llm_monthly = parse_float(params.get("riv_llm_monthly"), 300.0)

    # Defensive: if join_date before calc_start, swap logic for past period (no negative)
    past_period_days = days_inclusive(calc_start, join_date)

    # Per-day costs (use per-day based on yearly equivalents)
    # monthly_ops per day ≈ monthly * 12 / 365.25
    per_day_ops = monthly_ops * 12.0 / 365.25
    past_ops_cost = per_day_ops * past_period_days

    # Director time from director_start to join_date
    director_days_past = days_inclusive(max(director_start, calc_start), join_date)
    per_day_director = director_salary_year / 365.25
    past_director_cost = per_day_director * director_days_past

    # Legal fee: count in "past" only if fee date <= join_date
    past_legal = legal_fee if (legal_fee_date is not None and legal_fee_date <= join_date) else 0.0

    # Past assets valuation (portal + finance + website)
    assets_total = portal_val + finance_val + website_val

    # Past totals
    past_total_company = past_cash_total + assets_total + past_ops_cost + past_director_cost + past_legal
    past_total_friend = past_total_company * share_pct

    # --- Projection ---
    proj_start = projection_start
    proj_end = add_months(proj_start, projection_months)
    # Ops projected: straight monthly * months
    proj_ops_cost = monthly_ops * projection_months
    # Director projected: pro-rate by months (months/12 of yearly salary)
    proj_director_cost = director_salary_year * (projection_months / 12.0)
    # Legal fee: count in projection if fee date in [proj_start, proj_end]
    proj_legal = 0.0
    if legal_fee_date is not None and proj_start <= legal_fee_date <= proj_end:
        proj_legal = legal_fee

    proj_total_company = proj_ops_cost + proj_director_cost + proj_legal
    proj_total_friend = proj_total_company * share_pct
    proj_avg_month_friend = proj_total_friend / projection_months if projection_months > 0 else 0.0

    # Detailed components for display
    past_components = {
        "Past cash (you paid)": past_cash_total,
        "Assets valuation (portal + finance + website)": assets_total,
        "Ops (from {:%d %b %Y} to {:%d %b %Y})".format(calc_start, join_date): past_ops_cost,
        "Director time (from {:%d %b %Y} to {:%d %b %Y})".format(max(director_start, calc_start), join_date): past_director_cost,
        "Legal fee (<= join date)": past_legal
    }
    proj_components = {
        "Ops ({} months)".format(projection_months): proj_ops_cost,
        "Director time ({} months)".format(projection_months): proj_director_cost,
        "Legal fee within projection window": proj_legal
    }

    # --- RIV revenue projection ---
    def riv_component(label, start_dt, annual_revenue):
        if start_dt is None or projection_months <= 0:
            return label, 0.0
        if start_dt > proj_end:
            return label, 0.0
        active_start = max(start_dt, proj_start)
        active_days = days_inclusive(active_start, proj_end)
        if active_days <= 0:
            return label, 0.0
        daily_revenue = annual_revenue / 365.25
        return label, daily_revenue * active_days

    bootcamp_label = "Bootcamps ({:.0f}/yr from {:%d %b %Y})".format(bootcamp_per_year, bootcamp_start)
    coaching_label = "1:1 sessions ({:.0f}/yr from {:%d %b %Y})".format(coaching_per_year, coaching_start)
    llm_label = "LLM analysis subscriptions ({:.0f} clients × €{:.0f}/mo from {:%d %b %Y})".format(
        llm_clients, llm_monthly, llm_start
    )

    bootcamp_revenue = riv_component(bootcamp_label, bootcamp_start, bootcamp_per_year * bootcamp_amount)
    coaching_revenue = riv_component(coaching_label, coaching_start, coaching_per_year * coaching_amount)
    llm_revenue = riv_component(llm_label, llm_start, llm_clients * llm_monthly * 12.0)

    riv_components = {
        bootcamp_revenue[0]: bootcamp_revenue[1],
        coaching_revenue[0]: coaching_revenue[1],
        llm_revenue[0]: llm_revenue[1]
    }

    riv_total_company = sum(riv_components.values())
    riv_total_friend = riv_total_company * share_pct

    return {
        "inputs": {
            "share_pct": share_pct * 100.0,
            "calc_start": calc_start.isoformat(),
            "join_date": join_date.isoformat(),
            "projection_start": proj_start.isoformat(),
            "projection_months": projection_months,
            "director_start": director_start.isoformat(),
            "past_cash_total": past_cash_total,
            "assets": {
                "portal_val": portal_val,
                "finance_val": finance_val,
                "website_val": website_val
            },
            "monthly_ops": monthly_ops,
            "legal_fee": legal_fee,
            "legal_fee_date": legal_fee_date.isoformat() if legal_fee_date else None,
            "director_salary_year": director_salary_year,
            "riv": {
                "bootcamp_start": bootcamp_start.isoformat() if bootcamp_start else None,
                "bootcamp_per_year": bootcamp_per_year,
                "bootcamp_amount": bootcamp_amount,
                "coaching_start": coaching_start.isoformat() if coaching_start else None,
                "coaching_per_year": coaching_per_year,
                "coaching_amount": coaching_amount,
                "llm_start": llm_start.isoformat() if llm_start else None,
                "llm_clients": llm_clients,
                "llm_monthly": llm_monthly
            }
        },
        "past": {
            "components": past_components,
            "company_total": past_total_company,
            "friend_total": past_total_friend
        },
        "projection": {
            "components": proj_components,
            "company_total": proj_total_company,
            "friend_total": proj_total_friend,
            "friend_avg_month": proj_avg_month_friend,
            "window": {
                "start": proj_start.isoformat(),
                "end": proj_end.isoformat()
            },
            "riv": {
                "components": riv_components,
                "company_total": riv_total_company,
                "friend_total": riv_total_friend
            }
        },
        "summary": {
            "friend_due_to_join": past_total_friend,
            "friend_future_{}m".format(projection_months): proj_total_friend,
            "friend_total_all": past_total_friend + proj_total_friend,
            "friend_riv_projection": riv_total_friend,
            "friend_net_after_riv": past_total_friend + proj_total_friend - riv_total_friend
        }
    }

@app.route("/", methods=["GET", "POST"])
def index():
    # Defaults
    base_defaults = {
        "share_pct": "40",
        "calc_start": "2024-09-01",
        "join_date": date.today().isoformat(),
        "projection_start": "2026-01-01",
        "projection_months": "24",
        "director_start": "2025-01-01",
        "past_cash_total": "4000",
        "portal_val": "6000",
        "finance_val": "5000",
        "website_val": "2000",
        "monthly_ops": "500",
        "legal_fee": "2500",
        "legal_fee_date": "2026-01-15",
        "director_salary_year": "56000",
        "riv_bootcamp_start": "2026-02-01",
        "riv_bootcamp_per_year": "4",
        "riv_bootcamp_amount": "7000",
        "riv_coaching_start": "2026-01-15",
        "riv_coaching_per_year": "20",
        "riv_coaching_amount": "900",
        "riv_llm_start": "2026-03-01",
        "riv_llm_clients": "5",
        "riv_llm_monthly": "300"
    }

    results = None
    form_values = dict(base_defaults)
    if request.method == "POST":
        params = {k: request.form.get(k, "") for k in base_defaults.keys()}
        form_values.update(params)
        results = calculate(params)
    else:
        # On GET, pre-calc with defaults so page shows numbers immediately
        results = calculate(base_defaults)

    # Format for display
    def fmt_components(d):
        return [(k, eur(v)) for k, v in d.items()]

    display = None
    if results:
        display = {
            "inputs": results["inputs"],
            "past_components": fmt_components(results["past"]["components"]),
            "past_company_total": eur(results["past"]["company_total"]),
            "past_friend_total": eur(results["past"]["friend_total"]),
            "proj_components": fmt_components(results["projection"]["components"]),
            "proj_company_total": eur(results["projection"]["company_total"]),
            "proj_friend_total": eur(results["projection"]["friend_total"]),
            "proj_friend_avg_month": eur(results["projection"]["friend_avg_month"]),
            "proj_window": results["projection"]["window"],
            "riv_components": fmt_components(results["projection"]["riv"]["components"]),
            "riv_company_total": eur(results["projection"]["riv"]["company_total"]),
            "riv_friend_total": eur(results["projection"]["riv"]["friend_total"]),
            "summary_friend_due_to_join": eur(results["summary"]["friend_due_to_join"]),
            "summary_friend_future": eur(results["summary"]["friend_future_{}m".format(results['inputs']['projection_months'])]),
            "summary_friend_total_all": eur(results["summary"]["friend_total_all"]),
            "summary_friend_riv": eur(results["summary"]["friend_riv_projection"]),
            "summary_friend_net": eur(results["summary"]["friend_net_after_riv"])
        }

    return render_template("index.html", defaults=form_values, display=display)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
