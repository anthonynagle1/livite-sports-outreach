#!/usr/bin/env python3
"""CLI wrapper for Livite scheduling operations.

Usage:
    python tools/scheduling_cli.py schedule [--week this|next] [--json]
    python tools/scheduling_cli.py available Mon [--start 7] [--end 21] [--dept FOH|BOH]
    python tools/scheduling_cli.py employees [--json]
    python tools/scheduling_cli.py demand 2026-02-28 4500 [--json]
    python tools/scheduling_cli.py patterns [--weeks 8]
    python tools/scheduling_cli.py coverage [--week this|next]
"""

import argparse
import json
import os
import sys
from datetime import datetime

# Add project root to path so 'tools.scheduling' resolves
_BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _BASE_DIR)

from dotenv import load_dotenv

load_dotenv(os.path.join(_BASE_DIR, ".env"))

from tools.scheduling.scheduler import generate_weekly_schedule
from tools.scheduling.availability import (
    get_available_employees,
    get_all_employees,
    load_availability,
)
from tools.scheduling.demand import compute_labor_demand
from tools.scheduling.patterns import get_employee_patterns


def cmd_schedule(args):
    """Generate weekly schedule."""
    result = generate_weekly_schedule(week=args.week)

    if args.json:
        print(json.dumps(result, indent=2))
        return

    print(f"Schedule: {result['week_label']}")
    print(f"Coverage: {result['coverage_score']}%")
    print(f"Total labor cost: ${result['labor_cost_estimate']:,.2f}")
    print(f"Total hours: {result['total_labor_hours']}")
    print(f"Revenue forecast: ${result['total_revenue_forecast']:,.0f}")
    print(f"Projected SPLH: ${result['projected_splh']:.2f}")
    print()

    for day in result["days"]:
        rev = day.get("revenue_forecast", 0)
        print(
            f"--- {day['label']} | {day['headcount']} staff | "
            f"${day['labor_cost']:,.0f} labor | {day['total_hours']}h | "
            f"Revenue: ${rev:,.0f} ---"
        )
        for shift in sorted(day["shifts"], key=lambda s: s["start"]):
            print(
                f"  {shift['display_name']:12s} {shift['dept']:4s} "
                f"{shift['start']}-{shift['end']} ({shift['hours']}h) "
                f"${shift['cost']:,.0f}"
            )
        # Coverage gaps
        gaps = [s for s in day.get("coverage", []) if s.get("gap", 0) > 0]
        if gaps:
            gap_strs = ["{} (-{})".format(g["time"], g["gap"]) for g in gaps]
            print(f"  GAPS: {', '.join(gap_strs)}")
        print()

    print("Employee Summary:")
    print(f"{'Name':14s} {'Dept':5s} {'Hours':>6s} {'Max':>5s} {'Shifts':>6s} {'Cost':>8s}")
    print("-" * 50)
    for emp in sorted(result["employee_summary"], key=lambda e: -e["weekly_hours"]):
        flag = " OVER!" if emp["over_max"] else ""
        print(
            f"{emp['display_name']:14s} {emp['department']:5s} "
            f"{emp['weekly_hours']:6.1f} {emp['max_hours']:5d} "
            f"{emp['shifts']:6d} ${emp['cost']:>7,.0f}{flag}"
        )


def cmd_available(args):
    """Check who's available for a day/time."""
    employees = get_available_employees(
        day_name=args.day,
        start_hour=args.start or 7.0,
        end_hour=args.end or 21.0,
        department=args.dept,
    )

    if args.json:
        print(json.dumps(employees, indent=2, default=str))
        return

    dept_label = f" {args.dept}" if args.dept else ""
    start_h = args.start or 7.0
    end_h = args.end or 21.0
    print(f"Available{dept_label} on {args.day} ({start_h:.0f}:00-{end_h:.0f}:00):")
    print(f"{'Name':14s} {'Dept':5s} {'Available':>20s} {'Wage':>8s} {'Type':>10s}")
    print("-" * 60)
    for e in employees:
        avs = f"{e['avail_start']:.0f}:00-{e['avail_end']:.0f}:00"
        print(
            f"{e['display_name']:14s} {e['department']:5s} "
            f"{avs:>20s} ${e['wage']:>6}/hr {e['type']:>10s}"
        )
    print(f"\nTotal: {len(employees)} employees")


def cmd_employees(args):
    """List all employees."""
    employees = get_all_employees()

    if args.json:
        print(json.dumps(employees, indent=2, default=str))
        return

    print(f"All employees ({len(employees)}):")
    print(f"{'Name':14s} {'Dept':5s} {'Wage':>8s} {'Type':>10s} {'Max/wk':>7s} {'Skills':20s} Available")
    print("-" * 100)
    for e in sorted(employees, key=lambda x: x["department"]):
        avail_days = e.get("available_days", [])
        if avail_days and isinstance(avail_days[0], dict):
            days = ", ".join(d["day"] for d in avail_days)
        else:
            days = ", ".join(str(d) for d in avail_days)
        skills = ", ".join(e.get("skills", []))
        print(
            f"{e['display_name']:14s} {e['department']:5s} "
            f"${e['wage']:>6}/hr {e['type']:>10s} {e['max_hours_week']:>5}h  "
            f"{skills:20s} {days}"
        )


def cmd_demand(args):
    """Compute labor demand for a date."""
    dt = datetime.strptime(args.date, "%Y-%m-%d")
    demand = compute_labor_demand(dt, revenue_forecast=args.revenue, weeks_back=4)

    if args.json:
        print(json.dumps(demand, indent=2))
        return

    print(f"Labor demand for {args.date} (${args.revenue:,.0f} revenue forecast):")
    print(f"  Total hours needed: {demand['total_labor_hours']}")
    print(f"  FOH: {demand['foh_hours']}h | BOH: {demand['boh_hours']}h")
    print(f"  Estimated labor cost: ${demand['estimated_labor_cost']:,.0f}")
    print(f"  Scale factor vs history: {demand['scale_factor']:.2f}")
    print(f"  Blended wage: ${demand['blended_wage']:.2f}/hr")
    print()
    print("Daypart breakdown:")
    for dp in demand.get("daypart_demand", []):
        print(
            f"  {dp['daypart']:12s} ({dp['hours']}): "
            f"FOH {dp['avg_foh']:.1f} | BOH {dp['avg_boh']:.1f} | "
            f"Total {dp['avg_total']:.1f}"
        )


def cmd_patterns(args):
    """Show employee shift patterns from historical data."""
    patterns = get_employee_patterns(weeks_back=args.weeks)

    if args.json:
        print(json.dumps(patterns, indent=2))
        return

    print(f"Shift patterns (last {args.weeks} weeks):")
    for name, days in sorted(patterns.items()):
        print(f"\n  {name}:")
        for dow, pat in sorted(days.items(), key=lambda x: ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"].index(x[0])):
            freq_pct = pat["freq"] * 100
            print(
                f"    {dow}: {pat['avg_start']:.0f}:00-{pat['avg_end']:.0f}:00 "
                f"({pat['avg_hours']:.1f}h avg, {freq_pct:.0f}% freq, "
                f"{pat['sample_count']} samples)"
            )


def cmd_coverage(args):
    """Show coverage analysis for the week."""
    result = generate_weekly_schedule(week=args.week)

    print(f"Coverage Report: {result['week_label']}")
    print(f"Overall coverage score: {result['coverage_score']}%")
    print()

    for day in result["days"]:
        gaps = [s for s in day.get("coverage", []) if s.get("gap", 0) > 0]
        status = "GAPS" if gaps else "OK"
        print(f"{day['label']}: {status} ({day['headcount']} staff)")
        if gaps:
            for g in gaps:
                print(f"  {g['time']}: need {g['needed']}, have {g['assigned']} (-{g['gap']})")


def main():
    parser = argparse.ArgumentParser(description="Livite Scheduling CLI")
    sub = parser.add_subparsers(dest="command")

    # schedule
    p = sub.add_parser("schedule", help="Generate weekly schedule")
    p.add_argument("--week", default="this", choices=["this", "next"])
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_schedule)

    # available
    p = sub.add_parser("available", help="Check availability")
    p.add_argument("day", help="Day of week: Mon, Tue, Wed, Thu, Fri, Sat, Sun")
    p.add_argument("--start", type=float, help="Start hour (default 7)")
    p.add_argument("--end", type=float, help="End hour (default 21)")
    p.add_argument("--dept", choices=["FOH", "BOH"], help="Department filter")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_available)

    # employees
    p = sub.add_parser("employees", help="List all employees")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_employees)

    # demand
    p = sub.add_parser("demand", help="Compute labor demand")
    p.add_argument("date", help="Date: YYYY-MM-DD")
    p.add_argument("revenue", type=float, help="Revenue forecast")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_demand)

    # patterns
    p = sub.add_parser("patterns", help="Show historical shift patterns")
    p.add_argument("--weeks", type=int, default=8, help="Weeks of history (default 8)")
    p.add_argument("--json", action="store_true")
    p.set_defaults(func=cmd_patterns)

    # coverage
    p = sub.add_parser("coverage", help="Coverage gap analysis")
    p.add_argument("--week", default="this", choices=["this", "next"])
    p.set_defaults(func=cmd_coverage)

    args = parser.parse_args()
    if not args.command:
        parser.print_help()
        return

    args.func(args)


if __name__ == "__main__":
    main()
