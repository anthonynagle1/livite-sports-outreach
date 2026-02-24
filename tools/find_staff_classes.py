#!/usr/bin/env python3
"""Find all classes related to staff/coaches on the page"""
from bs4 import BeautifulSoup

with open('.tmp/raw_scrapes/bc_baseball_coaches_networkidle.html', 'r', encoding='utf-8') as f:
    html = f.read()

soup = BeautifulSoup(html, 'html.parser')

# Find all unique classes
all_classes = set()
for elem in soup.find_all(class_=True):
    classes = elem.get('class', [])
    all_classes.update(classes)

# Filter for staff/coach/roster/person related classes
relevant_classes = [cls for cls in all_classes if any(
    keyword in cls.lower()
    for keyword in ['staff', 'coach', 'roster', 'person', 'member', 'player', 'bio']
)]

print(f"Found {len(relevant_classes)} relevant classes:")
for cls in sorted(relevant_classes):
    print(f"  .{cls}")

# Also look for data attributes
print("\n\nLooking for elements with 'coach' or 'staff' in data attributes...")
for elem in soup.find_all(attrs={'data-test-id': True}):
    test_id = elem.get('data-test-id', '')
    if 'coach' in test_id.lower() or 'staff' in test_id.lower() or 'roster' in test_id.lower():
        print(f"  data-test-id=\"{test_id}\" on {elem.name}")

# Look for sections/divs that might contain staff info
print("\n\nLooking for sections with staff content...")
sections = soup.select('section, div[class*="content"], main')
for section in sections[:10]:  # Check first 10 sections
    text = section.get_text(strip=True)
    if any(keyword in text.lower() for keyword in ['head coach', 'assistant', 'director', '@bc.edu']):
        classes = section.get('class', [])
        print(f"  Found section with classes: {classes}")
        print(f"    Contains: {text[:100]}...")
