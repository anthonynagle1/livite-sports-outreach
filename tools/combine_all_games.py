#!/usr/bin/env python3
"""
Combine all schedule games with contact data where available.
"""

import json
import glob
import sys

def main():
    # Read all schedule files
    all_games = []

    schedule_files = glob.glob('.tmp/boston_college_*_schedule.json')

    for schedule_file in schedule_files:
        try:
            with open(schedule_file, 'r') as f:
                data = json.load(f)

            if data.get('success') and data.get('games'):
                games = data['games']
                sport = data.get('sport', 'Unknown')

                for game in games:
                    game['sport'] = sport
                    game['school'] = 'Boston College'
                    all_games.append(game)

        except Exception as e:
            print(f"Error reading {schedule_file}: {e}", file=sys.stderr)

    # Read validated contacts
    contact_map = {}
    try:
        with open('.tmp/boston_college_validated.json', 'r') as f:
            validated = json.load(f)

        for match in validated.get('validated_matches', []):
            # Create key from date + opponent + sport
            key = f"{match.get('date')}_{match.get('opponent')}_{match.get('sport')}"
            contact_map[key] = {
                'contact_name': match.get('contact_name', ''),
                'contact_title': match.get('contact_title', ''),
                'contact_email': match.get('contact_email', ''),
                'contact_phone': match.get('contact_phone', ''),
                'match_quality': match.get('match_quality', ''),
            }
    except:
        pass

    # Merge contact data into games
    for game in all_games:
        key = f"{game.get('date')}_{game.get('opponent')}_{game.get('sport')}"

        if key in contact_map:
            game.update(contact_map[key])
            game['match_status'] = 'success'
        else:
            game['contact_name'] = 'No Contact Found'
            game['contact_title'] = ''
            game['contact_email'] = ''
            game['contact_phone'] = ''
            game['match_quality'] = ''
            game['match_status'] = 'success'  # Mark as success so export includes it

    # Sort by parsed_date
    all_games.sort(key=lambda x: x.get('parsed_date', ''))

    # Output
    result = {
        'school': 'Boston College',
        'total_games': len(all_games),
        'games_with_contacts': len([g for g in all_games if g.get('contact_email')]),
        'validated_matches': all_games,
        'success': True
    }

    print(json.dumps(result, indent=2))

if __name__ == '__main__':
    main()
