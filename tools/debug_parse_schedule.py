#!/usr/bin/env python3
"""Debug schedule parsing"""
from bs4 import BeautifulSoup
import sys
sys.path.insert(0, 'tools')

# Import the parsing function
from scrape_schedule import parse_schedule_list, filter_games_by_academic_year

# Load the rendered HTML
with open('.tmp/raw_scrapes/bc_baseball_rendered.html', 'r') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

# Parse games
print("Parsing schedule...")
games = parse_schedule_list(soup, "https://bceagles.com/sports/baseball/schedule")
print(f"Found {len(games)} games (before filtering)\n")

for i, game in enumerate(games[:10], 1):
    print(f"{i}. {game['date']} {game['time']} - {game['opponent']} at {game['venue']}")

print(f"\nFiltering for academic year and future games...")
filtered = filter_games_by_academic_year(games)
print(f"After filtering: {len(filtered)} games")

for i, game in enumerate(filtered[:5], 1):
    print(f"{i}. {game['date']} {game['time']} - {game['opponent']}")
