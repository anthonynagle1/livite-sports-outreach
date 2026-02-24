#!/usr/bin/env python3
"""Extract staff from team-specific coaches page"""
from bs4 import BeautifulSoup
import json

with open('.tmp/raw_scrapes/bc_baseball_coaches_networkidle.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

# Find the coaches content section
coaches_section = soup.select_one('.c-coaches-page__content')

if coaches_section:
    print("Found coaches section\n")

    # Look for elements with staff classes
    staff_titles = coaches_section.select('.staff_title')
    staff_emails = coaches_section.select('.staff_email')
    staff_phones = coaches_section.select('.staff_phone')

    print(f"Found {len(staff_titles)} staff titles")
    print(f"Found {len(staff_emails)} staff emails")
    print(f"Found {len(staff_phones)} staff phones")

    # Try to match them up
    staff_list = []
    for i in range(max(len(staff_titles), len(staff_emails))):
        title = staff_titles[i].get_text(strip=True) if i < len(staff_titles) else "Unknown"
        email = staff_emails[i].get_text(strip=True) if i < len(staff_emails) else "Not Found"
        phone = staff_phones[i].get_text(strip=True) if i < len(staff_phones) else "Not Found"

        # The name might be in a sibling or parent element
        # Let's check the full text structure
        if i < len(staff_titles):
            parent = staff_titles[i].parent
            parent_text = parent.get_text(separator='|', strip=True)
            parts = [p.strip() for p in parent_text.split('|') if p.strip()]

            print(f"\n{i+1}. Title: {title}")
            print(f"   Email: {email}")
            print(f"   Phone: {phone}")
            print(f"   Parent text parts: {parts[:5]}")

    # Alternative: Get all text from the section and parse it
    print("\n\n" + "="*60)
    print("Alternative: Parse section text")
    print("="*60)

    section_text = coaches_section.get_text(separator='|', strip=True)
    parts = [p.strip() for p in section_text.split('|') if p.strip() and len(p.strip()) > 1]

    # Filter out common noise
    parts = [p for p in parts if not any(skip in p.lower() for skip in [
        'skip ad', 'choose a season', '2026', '2025', '2024', '2023', 'coaching staff'
    ])]

    print(f"\nFiltered parts: {len(parts)}")
    for i, part in enumerate(parts[:30], 1):
        print(f"{i}. {part}")

else:
    print("Coaches section not found")

# Try another approach: look for table rows or structured data
print("\n\n" + "="*60)
print("Looking for table structure")
print("="*60)

tables = soup.select('table')
print(f"Found {len(tables)} tables")

for i, table in enumerate(tables, 1):
    rows = table.select('tr')
    if rows:
        print(f"\nTable {i}: {len(rows)} rows")
        # Check if it looks like a staff table
        first_row_text = rows[0].get_text(strip=True).lower()
        if any(keyword in first_row_text for keyword in ['name', 'title', 'email', 'coach']):
            print(f"  Looks like a staff table!")
            # Print first few rows
            for j, row in enumerate(rows[:5], 1):
                cells = row.select('td, th')
                cell_texts = [cell.get_text(strip=True) for cell in cells]
                print(f"  Row {j}: {cell_texts}")
