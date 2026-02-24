#!/usr/bin/env python3
"""Parse team staff page structure"""
from bs4 import BeautifulSoup
import json

# Load the saved HTML
with open('.tmp/raw_scrapes/bc_baseball_coaches.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

print("Looking for staff members...\n")

# Strategy 1: Look for roster player cards (Sidearm uses these for coaches too)
roster_cards = soup.select('.sidearm-roster-player')
print(f"Strategy 1: .sidearm-roster-player cards: {len(roster_cards)}")

if roster_cards:
    print("\nSample cards:")
    for i, card in enumerate(roster_cards[:3], 1):
        # Get name
        name_elem = card.select_one('.sidearm-roster-player-name, a')
        name = name_elem.get_text(strip=True) if name_elem else "Unknown"

        # Get title/position
        title_elem = card.select_one('.sidearm-roster-player-position')
        title = title_elem.get_text(strip=True) if title_elem else "Unknown"

        # Get email
        email_elem = card.select_one('a[href^="mailto:"]')
        email = email_elem.get('href', '').replace('mailto:', '') if email_elem else "Not Found"

        print(f"  {i}. {name} - {title}")
        print(f"     Email: {email}")

# Strategy 2: Look for any elements with coach info
# Get all mailto links and their parent context
all_emails = soup.select('a[href^="mailto:"]')
print(f"\n\nStrategy 2: All mailto links: {len(all_emails)}")

staff_list = []
for email_link in all_emails:
    email = email_link.get('href', '').replace('mailto:', '')

    # Skip generic emails
    if email.lower() in ['baseball@bc.edu', 'housing@bc.edu']:
        continue

    # Try to find name and title from parent context
    parent = email_link.parent
    while parent and parent.name != 'body':
        # Look for name in nearby elements
        name_elem = parent.select_one('.sidearm-roster-player-name, h3, h4, strong')
        if name_elem:
            name = name_elem.get_text(strip=True)

            # Look for title
            title_elem = parent.select_one('.sidearm-roster-player-position, .title, em')
            title = title_elem.get_text(strip=True) if title_elem else "Unknown"

            staff_list.append({
                'name': name,
                'title': title,
                'email': email
            })
            break

        parent = parent.parent

if staff_list:
    print("\nExtracted staff:")
    for i, staff in enumerate(staff_list, 1):
        print(f"{i}. {staff['name']}")
        print(f"   Title: {staff['title']}")
        print(f"   Email: {staff['email']}")
else:
    print("\nNo staff extracted with Strategy 2")

# Strategy 3: Look for all text near email links
print("\n\nStrategy 3: Text context around emails")
for i, email_link in enumerate(all_emails[:5], 1):
    email = email_link.get('href', '').replace('mailto:', '')

    # Get parent's text
    parent = email_link.parent
    if parent:
        # Get all text from parent, split into lines
        parent_text = parent.get_text(separator='|', strip=True)
        parts = [p.strip() for p in parent_text.split('|') if p.strip()]

        print(f"\n{i}. Email: {email}")
        print(f"   Parent text parts: {parts[:10]}")  # First 10 parts
