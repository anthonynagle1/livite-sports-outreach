"""
dispatch_metrobi.py — Metrobi dispatch tool for catering orders.

Usage:
    python3 tools/dispatch_metrobi.py <notion_page_id>
    python3 tools/dispatch_metrobi.py <notion_page_id> --dry-run

Reads the Notion order page and dispatches via the Metrobi delivery API.

Trigger: set Metrobi Approval on the Notion page to one of:
  "Approved"                → dispatch to Metrobi network (normal)
  "Approved – Self Managed" → dispatch to Pedro via self-managed driver flow

After a successful dispatch:
  - Sets Metrobi Status = "Scheduled"
  - Sets Metrobi ID = <delivery_id>
  - Sets Order Status = "Driver Assigned"
"""

import re
import sys
import json
import time
import requests
from datetime import datetime

ENV_PATH = '/Users/anthonynagle/Documents/Agentic Workflows/Livite Main Agent/.env'
METROBI_API_URL = 'https://delivery-api.metrobi.com/api/v1/delivery'
PICKUP_ADDRESS = '1644 Beacon St, Brookline MA 02446'
PICKUP_NAME = 'Livite'
PICKUP_PHONE = '6174217548'


def load_env():
    env = {}
    with open(ENV_PATH) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith('#') and '=' in line:
                k, v = line.split('=', 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    return env


def rt_text(prop):
    return ''.join(t['plain_text'] for t in prop.get('rich_text', []))


def fetch_order(page_id, notion_headers):
    r = requests.get(f'https://api.notion.com/v1/pages/{page_id}', headers=notion_headers)
    time.sleep(0.4)
    if r.status_code != 200:
        print(f'ERROR fetching page {page_id}: {r.status_code} {r.text[:200]}')
        sys.exit(1)
    return r.json()


def build_payload(props, self_managed):
    del_dt_raw = props.get('Delivery Date & Time', {}).get('date', {}).get('start', '')
    if not del_dt_raw:
        print('ERROR: Delivery Date & Time is empty on this page.')
        sys.exit(1)

    # Pickup window — Metrobi uses {"date": "YYYY-MM-DD", "time": "HH:MM"} (24h, no seconds)
    # The API auto-sets a 15-min window after the given time.
    date_part = del_dt_raw[:10]  # YYYY-MM-DD
    pickup_window_raw = rt_text(props.get('Metrobi Pickup Window', {}))
    pickup_time_str = '12:00'  # fallback

    if '–' in pickup_window_raw:
        start_part = pickup_window_raw.split('–')[0].strip()
        try:
            t_obj = datetime.strptime(start_part, '%I:%M %p')
            pickup_time_str = t_obj.strftime('%H:%M')
        except ValueError:
            try:
                t_obj = datetime.strptime(start_part, '%I %p')
                pickup_time_str = t_obj.strftime('%H:%M')
            except ValueError:
                pass
    elif del_dt_raw and 'T' in del_dt_raw:
        pickup_time_str = del_dt_raw[11:16]  # HH:MM from ISO

    delivery_address = rt_text(props.get('Delivery Address', {}))
    if not delivery_address:
        print('ERROR: Delivery Address is empty on this page.')
        sys.exit(1)

    order_name = ''.join(t['plain_text'] for t in props.get('Order Name', {}).get('title', []))
    delivery_notes = rt_text(props.get('Delivery Notes (350 char MAX)', {}))

    delivery_notes = rt_text(props.get('Delivery Notes (350 char MAX)', {}))

    # Parse recipient phone and email from delivery notes
    dropoff_phone = None
    dropoff_email = None
    if delivery_notes:
        m = re.search(r'\(?\d{3}\)?[\s.\-]\d{3}[\s.\-]\d{4}', delivery_notes)
        if m:
            dropoff_phone = re.sub(r'[^\d]', '', m.group())  # digits only
        em = re.search(r'[\w.\-+]+@[\w.\-]+\.[a-z]{2,}', delivery_notes)
        if em:
            dropoff_email = em.group()

    pickup_stop = {
        'address': PICKUP_ADDRESS,
        'name': PICKUP_NAME,
        'contact': {'phone': PICKUP_PHONE},
    }
    dropoff_stop = {
        'address': delivery_address,
        'name': order_name,
    }
    dropoff_contact = {}
    if dropoff_phone:
        dropoff_contact['phone'] = dropoff_phone
    if dropoff_email:
        dropoff_contact['email'] = dropoff_email
    if dropoff_contact:
        dropoff_stop['contact'] = dropoff_contact
    if delivery_notes:
        dropoff_stop['instructions'] = delivery_notes[:500]

    payload = {
        'cargo_size': 'medium',
        'pickup_time': {
            'date': date_part,
            'time': pickup_time_str,
        },
        'pickup_stop': pickup_stop,
        'dropoff_stop': dropoff_stop,
        'settings': {
            'self_managed': self_managed,
            'merge_delivery': False,
            'return_to_pickup': False,
        },
    }
    return payload


def update_notion_after_dispatch(page_id, delivery_id, notion_headers):
    def rt(text):
        return [{'type': 'text', 'text': {'content': text}}]

    r = requests.patch(
        f'https://api.notion.com/v1/pages/{page_id}',
        headers=notion_headers,
        json={
            'properties': {
                'Order Status':   {'select': {'name': 'Driver Assigned'}},
                'Metrobi Status': {'rich_text': rt('Scheduled')},
                'Metrobi ID':     {'rich_text': rt(str(delivery_id))},
            }
        }
    )
    time.sleep(0.4)
    return r.status_code


def main():
    if len(sys.argv) < 2:
        print('Usage: python3 tools/dispatch_metrobi.py <notion_page_id> [--dry-run]')
        sys.exit(1)

    page_id = sys.argv[1]
    dry_run = '--dry-run' in sys.argv

    env = load_env()
    notion_token = env.get('NOTION_API_KEY')
    metrobi_key = env.get('METROBI_API_KEY')

    if not notion_token:
        print('ERROR: NOTION_API_KEY not found in .env')
        sys.exit(1)
    if not metrobi_key:
        print('ERROR: METROBI_API_KEY not found in .env')
        sys.exit(1)

    notion_headers = {
        'Authorization': f'Bearer {notion_token}',
        'Notion-Version': '2025-09-03',
        'Content-Type': 'application/json',
    }
    metrobi_headers = {
        'X-Api-Key': metrobi_key,
        'Content-Type': 'application/json',
    }

    print(f'Fetching Notion page {page_id}...')
    page = fetch_order(page_id, notion_headers)
    props = page.get('properties', {})

    order_name = ''.join(t['plain_text'] for t in props.get('Order Name', {}).get('title', []))
    order_num = rt_text(props.get('Order Number', {}))
    metrobi_approval = props.get('Metrobi Approval', {}).get('select', {}).get('name', '')
    current_status = props.get('Order Status', {}).get('select', {}).get('name', '')

    APPROVED_NORMAL = 'Approved'
    APPROVED_PEDRO  = 'Approved – Self Managed'

    print(f'  Order: #{order_num} — {order_name}')
    print(f'  Status: {current_status}')
    print(f'  Metrobi Approval: {metrobi_approval}')
    print()

    if metrobi_approval not in (APPROVED_NORMAL, APPROVED_PEDRO):
        print(f'WARNING: Metrobi Approval is "{metrobi_approval}". Aborting.')
        print(f'  Set to "{APPROVED_NORMAL}" (Metrobi network) or "{APPROVED_PEDRO}" (Pedro) first.')
        sys.exit(1)

    self_managed = (metrobi_approval == APPROVED_PEDRO)
    payload = build_payload(props, self_managed)

    dispatch_mode = 'Pedro (self-managed)' if self_managed else 'Metrobi network'
    print(f'Dispatch mode: {dispatch_mode}')
    print(f'Pickup time: {payload["pickup_time"]["date"]} {payload["pickup_time"]["time"]}')
    print(f'Dropoff: {payload["dropoff_stop"]["address"]}')
    print()

    if dry_run:
        print('DRY RUN — payload that would be sent:')
        print(json.dumps(payload, indent=2))
        print()
        print('No API call made.')
        return

    print('POSTing to Metrobi...')
    r = requests.post(METROBI_API_URL, headers=metrobi_headers, json=payload)

    if r.status_code in (200, 201):
        data = r.json()
        delivery_id = (
            (data.get('response') or {}).get('data', {}).get('delivery_id') or
            (data.get('response') or {}).get('id') or
            data.get('delivery_id') or
            'unknown'
        )
        print(f'  Dispatched! Delivery ID: {delivery_id}')
        print(f'  Response: {json.dumps(data, indent=2)[:400]}')
        print()
        print('Updating Notion...')
        status = update_notion_after_dispatch(page_id, delivery_id, notion_headers)
        print(f'  Notion update: {status}')
        print()
        print(f'Done — #{order_num} dispatched via {dispatch_mode}')
    else:
        print(f'ERROR {r.status_code}: {r.text[:400]}')
        sys.exit(1)


if __name__ == '__main__':
    main()
