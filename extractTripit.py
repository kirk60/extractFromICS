#!/usr/bin/env python3
"""
extractTripit - Extract data from TripIt ICS files into CSV
"""

import csv
import os
import re
import json
import time
from datetime import datetime, timedelta
from pathlib import Path
try:
    import requests
    REQUESTS_AVAILABLE = True
except ImportError:
    REQUESTS_AVAILABLE = False
try:
    from zoneinfo import ZoneInfo
    ZONEINFO_AVAILABLE = True
except ImportError:
    try:
        import pytz
        PYTZ_AVAILABLE = True
        ZONEINFO_AVAILABLE = False
    except ImportError:
        PYTZ_AVAILABLE = False
        ZONEINFO_AVAILABLE = False


def unfold_lines(content):
    """Unfold ICS lines (long lines are folded with space at start of next line)"""
    lines = []
    for line in content.splitlines():
        if line and (line[0] in ' \t'):
            if lines:
                lines[-1] += line[1:]
        else:
            lines.append(line)
    return lines


def extract_events(ics_path):
    """Extract all VEVENT blocks from ICS file"""
    with open(ics_path, 'r', encoding='utf-8', errors='ignore') as f:
        content = f.read()
    
    lines = unfold_lines(content)
    events = []
    current_event = {}
    in_event = False
    
    for line in lines:
        line = line.strip()
        if line == 'BEGIN:VEVENT':
            in_event = True
            current_event = {}
        elif line == 'END:VEVENT':
            in_event = False
            if current_event:
                events.append(current_event)
            current_event = {}
        elif in_event and ':' in line:
            key_part, value = line.split(':', 1)
            key = key_part.split(';')[0]
            current_event[key] = {
                'value': value,
                'key_part': key_part
            }
    
    return events


def extract_trip_data(events):
    """Extract relevant trip data from events"""
    extracted = []
    
    for event in events:
        row = {}
        row['UID'] = event.get('UID', {}).get('value', '') if isinstance(event.get('UID'), dict) else event.get('UID', '')
        summary = event.get('SUMMARY', {}).get('value', '').replace('\\,', ',').replace('\\;', ';') if isinstance(event.get('SUMMARY'), dict) else str(event.get('SUMMARY', '')).replace('\\,', ',').replace('\\;', ';')
        row['SUMMARY'] = summary
        row['LOCATION'] = event.get('LOCATION', {}).get('value', '').replace('\\,', ',').replace('\\;', ';') if isinstance(event.get('LOCATION'), dict) else str(event.get('LOCATION', '')).replace('\\,', ',').replace('\\;', ';')
        row['DESCRIPTION'] = event.get('DESCRIPTION', {}).get('value', '').replace('\\n', ' ').replace('\\,', ',') if isinstance(event.get('DESCRIPTION'), dict) else str(event.get('DESCRIPTION', '')).replace('\\n', ' ')
        
        dtstart = event.get('DTSTART')
        dtend = event.get('DTEND')
        dtstamp = event.get('DTSTAMP')
        
        if isinstance(dtstart, dict):
            row['DTSTART'] = dtstart['value']
            key_part_start = dtstart.get('key_part', '')
            row['DTSTART_IS_DATE'] = any('VALUE=DATE' in str(p) for p in key_part_start.split(';')[1:]) if ';' in key_part_start else (len(dtstart['value'].replace('Z', '').replace('+0000', '')) == 8 and 'T' not in dtstart['value'])
        else:
            row['DTSTART'] = str(dtstart) if dtstart else ''
            row['DTSTART_IS_DATE'] = False
            
        if isinstance(dtend, dict):
            row['DTEND'] = dtend['value']
            key_part_end = dtend.get('key_part', '')
            row['DTEND_IS_DATE'] = any('VALUE=DATE' in str(p) for p in key_part_end.split(';')[1:]) if ';' in key_part_end else (len(dtend['value'].replace('Z', '').replace('+0000', '')) == 8 and 'T' not in dtend['value'])
        else:
            row['DTEND'] = str(dtend) if dtend else ''
            row['DTEND_IS_DATE'] = False
            
        if isinstance(dtstamp, dict):
            row['DTSTAMP'] = dtstamp['value']
        else:
            row['DTSTAMP'] = str(dtstamp) if dtstamp else ''
        
        summary_lower = row['SUMMARY'].lower()
        if 'flight' in summary_lower or re.match(r'^[A-Z]{2}\d+', row['SUMMARY'].strip()):
            row['TYPE'] = 'Flight'
        elif 'check-in' in summary_lower or 'check out' in summary_lower or 'check-out' in summary_lower or 'lodging' in summary_lower or 'hotel' in summary_lower or 'mantarays' in summary_lower or 'ingot' in summary_lower or 'continental' in summary_lower or 'argus' in summary_lower or 'beachfront' in summary_lower or 'hyde perth' in summary_lower:
            row['TYPE'] = 'Lodging'
        elif 'car rental' in summary_lower or 'pick up' in summary_lower or 'drop off' in summary_lower or 'avis' in summary_lower:
            row['TYPE'] = 'Car Rental'
        elif 'cruise' in summary_lower or 'embark' in summary_lower or 'disembark' in summary_lower or 'coral expeditions' in summary_lower:
            row['TYPE'] = 'Cruise'
        elif 'tour' in summary_lower or 'whale shark' in summary_lower:
            row['TYPE'] = 'Tour/Activity'
        else:
            if row['DTSTART_IS_DATE'] and row['DTEND_IS_DATE'] and len(row['SUMMARY']) > 10 and 'to ' not in row['SUMMARY'].lower() and re.match(r'^[A-Z]{2}\d+', row['SUMMARY'].strip()) == None and 'flight' not in summary_lower:
                row['TYPE'] = 'Trip'
            else:
                row['TYPE'] = 'Other'
        
        if row['TYPE'] == 'Other':
            if summary.strip().lower().startswith('check-in') or summary.strip().lower().startswith('check out') or summary.strip().lower().startswith('check-out'):
                row['TYPE'] = 'Lodging'
        
        extracted.append(row)
    
    return extracted


def geocode_location(location, cache_file=None):
    """Geocode location using Nominatim (OpenStreetMap) API with fallback"""
    if not location or not location.strip():
        return None, None
    
    if cache_file is None:
        cache_file = Path(__file__).parent / 'results' / 'geocode_cache.json'
    
    # Load cache
    cache = {}
    if cache_file.exists():
        try:
            with open(cache_file, 'r', encoding='utf-8') as f:
                cache = json.load(f)
        except:
            cache = {}
    
    cache_key = location.strip().lower()
    if cache_key in cache:
        cached = cache[cache_key]
        return cached.get('city'), cached.get('country')
    
    city, country = None, None
    if REQUESTS_AVAILABLE:
        try:
            # Use Nominatim API (free, rate-limited to 1 req/sec)
            response = requests.get(
                "https://nominatim.openstreetmap.org/search",
                params={"q": location, "format": "json", "addressdetails": 1, "limit": 1},
                headers={"User-Agent": "extractTripit/1.0"},
                timeout=10
            )
            if response.status_code == 200:
                data = response.json()
                if data:
                    addr = data[0].get('address', {})
                    city = addr.get('city') or addr.get('town') or addr.get('village') or addr.get('municipality') or addr.get('suburb') or addr.get('county')
                    country = addr.get('country')
                    # Save to cache
                    cache[cache_key] = {'city': city, 'country': country}
                    cache_file.parent.mkdir(exist_ok=True)
                    try:
                        with open(cache_file, 'w', encoding='utf-8') as f:
                            json.dump(cache, f, indent=2)
                    except:
                        pass
                    time.sleep(1)  # Respect rate limit
                    return city, country
        except Exception as e:
            pass
    
    return None, None


def get_timezone(city, country):
    """Get timezone for city/country"""
    # Common Australian city timezones
    tz_map = {
        'perth': 'Australia/Perth',
        'sydney': 'Australia/Sydney',
        'melbourne': 'Australia/Melbourne',
        'brisbane': 'Australia/Brisbane',
        'adelaide': 'Australia/Adelaide',
        'darwin': 'Australia/Darwin',
        'hobart': 'Australia/Hobart',
        'canberra': 'Australia/Sydney',
        'gold coast': 'Australia/Brisbane',
        'cairns': 'Australia/Brisbane',
        'broome': 'Australia/Perth',
        'exmouth': 'Australia/Perth',
        'port douglas': 'Australia/Brisbane',
        'learmonth': 'Australia/Perth',
    }
    if city:
        city_lower = city.lower()
        for key, tz in tz_map.items():
            if key in city_lower:
                if ZONEINFO_AVAILABLE:
                    try:
                        return ZoneInfo(tz)
                    except:
                        pass
                elif PYTZ_AVAILABLE:
                    try:
                        import pytz
                        return pytz.timezone(tz)
                    except:
                        pass
    if country and country.lower() == 'australia':
        if ZONEINFO_AVAILABLE:
            try:
                return ZoneInfo('Australia/Sydney')
            except:
                pass
        elif PYTZ_AVAILABLE:
            try:
                import pytz
                return pytz.timezone('Australia/Sydney')
            except:
                pass
    return None


def convert_to_local_time(utc_str, city, country):
    """Convert UTC timestamp to local time for city/country"""
    if not utc_str:
        return utc_str
    try:
        # Parse UTC format like 20270604T070000Z
        if utc_str.endswith('Z'):
            clean = utc_str.replace('Z', '+0000')
            dt = datetime.strptime(clean, '%Y%m%dT%H%M%S%z')
        elif len(utc_str) == 8 and 'T' not in utc_str:
            # Date only
            return utc_str
        else:
            # Try to parse
            clean = utc_str.replace('+0000', '').replace('Z', '')
            if 'T' in clean and len(clean) >= 15:
                dt = datetime.strptime(clean[:15], '%Y%m%dT%H%M%S')
                dt = dt.replace(tzinfo=ZoneInfo('UTC') if ZONEINFO_AVAILABLE else None)
            else:
                return utc_str
        
        tz = get_timezone(city, country)
        if tz:
            if ZONEINFO_AVAILABLE and hasattr(dt, 'astimezone'):
                local_dt = dt.astimezone(tz)
                return local_dt.strftime('%Y%m%dT%H%M%S')
            elif PYTZ_AVAILABLE:
                utc_dt = dt
                if utc_dt.tzinfo is None:
                    import pytz
                    utc_dt = pytz.UTC.localize(utc_dt)
                local_dt = utc_dt.astimezone(tz)
                return local_dt.strftime('%Y%m%dT%H%M%S')
        return utc_str
    except Exception as e:
        return utc_str


def main():
    script_dir = Path(__file__).parent
    ics_path = script_dir / 'data' / 'sample_trip.ics'
    results_dir = script_dir / 'results'
    results_dir.mkdir(exist_ok=True)
    extract_path = results_dir / 'extractTripit.csv'
    summary_path = results_dir / 'extractTripit_summary.csv'
    cache_file = results_dir / 'geocode_cache.json'
    
    import sys
    import argparse
    parser = argparse.ArgumentParser(description='Extract data from TripIt ICS files into CSV/HTML')
    parser.add_argument('ics', nargs='?', help='Input ICS file')
    parser.add_argument('-e', '--extract', help='Extract output file name')
    parser.add_argument('-s', '--summary', help='Summary output file name')
    parser.add_argument('--geocode', action='store_true', help='Use geocoding API to standardize locations')
    parser.add_argument('--no-geocode', action='store_true', help='Disable geocoding, use regex only')
    parser.add_argument('--html', action='store_true', help='Output HTML format instead of CSV (default is CSV)')
    args = parser.parse_args()
    
    use_geocode = args.geocode and not args.no_geocode
    if REQUESTS_AVAILABLE and not args.no_geocode and args.geocode:
        use_geocode = True
    elif REQUESTS_AVAILABLE and not args.geocode and not args.no_geocode:
        # Auto-enable geocoding if requests available
        use_geocode = True
    
    if args.ics:
        ics_path = Path(args.ics)
    
    output_extract = extract_path
    output_summary = summary_path
    do_extract = True
    do_summary = True
    
    if args.extract:
        do_summary = False
        if '/' in args.extract or '\\' in args.extract:
            output_extract = Path(args.extract)
        else:
            output_extract = results_dir / args.extract
    if args.summary:
        do_extract = False
        if '/' in args.summary or '\\' in args.summary:
            output_summary = Path(args.summary)
        else:
            output_summary = results_dir / args.summary
    if args.extract and args.summary:
        do_extract = True
        do_summary = True
        if '/' in args.extract or '\\' in args.extract:
            output_extract = Path(args.extract)
        else:
            output_extract = results_dir / args.extract
        if '/' in args.summary or '\\' in args.summary:
            output_summary = Path(args.summary)
        else:
            output_summary = results_dir / args.summary
    
    print(f"Reading from: {ics_path}")
    
    events = extract_events(ics_path)
    print(f"Found {len(events)} events")
    
    trip_data = extract_trip_data(events)
    
    filtered = [row for row in trip_data if row['TYPE'] != 'Trip']
    
    def sort_key(row):
        dtstart = row['DTSTART']
        try:
            clean = dtstart.replace('Z', '').replace('+0000', '')
            if len(clean) == 8:
                return datetime.strptime(clean, '%Y%m%d')
            elif 'T' in clean:
                date_part = clean.split('T')[0]
                if len(date_part) == 8:
                    return datetime.strptime(date_part, '%Y%m%d')
            return clean
        except:
            return dtstart
    
    filtered_sorted = sorted(filtered, key=sort_key)
    
    if do_extract:
        print(f"Writing extract to: {output_extract}")
        fieldnames = ['TYPE', 'SUMMARY', 'LOCATION', 'DTSTART', 'DTEND', 'DTSTART_IS_DATE', 
                      'DTEND_IS_DATE', 'UID', 'DTSTAMP', 'DESCRIPTION']
        
        if args.html or (str(output_extract).lower().endswith('.html')):
            html_content = []
            html_content.append('<!DOCTYPE html>')
            html_content.append('<html lang="en">')
            html_content.append('<head>')
            html_content.append('    <meta charset="UTF-8">')
            html_content.append('    <meta name="viewport" content="width=device-width, initial-scale=1.0">')
            html_content.append('    <title>TripIt Extract</title>')
            html_content.append('    <style>')
            html_content.append('        body { font-family: Arial, sans-serif; margin: 20px; }')
            html_content.append('        table { border-collapse: collapse; width: 100%; }')
            html_content.append('        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }')
            html_content.append('        th { background-color: #f2f2f2; }')
            html_content.append('        tr:nth-child(even) { background-color: #f9f9f9; }')
            html_content.append('    </style>')
            html_content.append('</head>')
            html_content.append('<body>')
            html_content.append('    <h1>TripIt Extract</h1>')
            html_content.append('    <table>')
            html_content.append('        <thead>')
            html_content.append('            <tr>')
            for field in fieldnames:
                html_content.append(f'                <th>{field}</th>')
            html_content.append('            </tr>')
            html_content.append('        </thead>')
            html_content.append('        <tbody>')
            for row in filtered_sorted:
                html_content.append('            <tr>')
                for field in fieldnames:
                    value = str(row.get(field, '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    html_content.append(f'                <td>{value}</td>')
                html_content.append('            </tr>')
            html_content.append('        </tbody>')
            html_content.append('    </table>')
            html_content.append('</body>')
            html_content.append('</html>')
            with open(output_extract, 'w', encoding='utf-8') as f:
                f.write('\n'.join(html_content))
        else:
            with open(output_extract, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
                writer.writeheader()
                for row in filtered_sorted:
                    out_row = {k: row.get(k, '') for k in fieldnames}
                    writer.writerow(out_row)
        
        print(f"Successfully extracted data to {output_extract}")
        print(f"Rows written: {len(filtered_sorted)}")
    
    # Create summary - match check-in with check-out
    if do_summary:
        def extract_city_country(location):
            if not location:
                return '', ''
            # Try geocoding first if enabled
            if use_geocode and REQUESTS_AVAILABLE:
                geo_city, geo_country = geocode_location(location, cache_file)
                if geo_city or geo_country:
                    # Use geocoded results if available
                    city = geo_city or ''
                    country = geo_country or ''
                    # If geocoding gave us country but not city, try regex as fallback
                    if not city and country:
                        parts = [p.strip() for p in location.split(',')]
                        for i, p in enumerate(parts):
                            if i == 0 or i == len(parts) - 1:
                                continue
                            p_clean = p.strip()
                            if not p_clean or re.match(r'^\d{4,5}$', p_clean) or re.match(r'^[A-Z]{2,3}\s*\d{4,5}$', p_clean) or re.match(r'^[A-Z]{2,3}$', p_clean) or re.match(r'^\d+\s+', p_clean):
                                continue
                            city = p_clean
                            break
                    # If geocoding gave city but we want to be more specific, sometimes use regex
                    elif city and not country:
                        parts = [p.strip() for p in location.split(',')]
                        if parts:
                            country = parts[-1].strip()
                    return city, country
            # Fallback to regex parsing
            # Try to split by comma
            parts = [p.strip() for p in location.split(',')]
            if not parts:
                return '', ''
            country = parts[-1].strip()
            city = ''
            # For Australian addresses, common pattern is often: Street Address, Suburb City, State Postcode, Country
            # Or sometimes: Street Address, Suburb, State Postcode, Country
            # Look for actual city name (not street address with numbers, not postcode alone)
            # Check all parts except first (usually street) and last (country)
            for i, p in enumerate(parts):
                if i == 0 or i == len(parts) - 1:  # skip street address and country
                    continue
                p_clean = p.strip()
                if not p_clean:
                    continue
                # Skip pure postcodes
                if re.match(r'^\d{4,5}$', p_clean):
                    continue
                # Skip state + postcode like "WA 6707", "6000" etc
                if re.match(r'^[A-Z]{2,3}\s*\d{4,5}$', p_clean):
                    continue
                # Skip if it's just a state code
                if re.match(r'^[A-Z]{2,3}$', p_clean):
                    continue
                # Skip street addresses
                if re.match(r'^\d+\s+', p_clean):
                    continue
                # This is likely the city/suburb
                city = p_clean
                break
            # If not found, try different strategy - look for known Australian cities
            if not city:
                australian_cities = ['Sydney', 'Melbourne', 'Brisbane', 'Perth', 'Adelaide', 'Gold Coast', 
                                    'Newcastle', 'Canberra', 'Sunshine Coast', 'Wollongong', 'Hobart',
                                    'Geelong', 'Townsville', 'Cairns', 'Darwin', 'Toowoomba', 'Ballarat',
                                    'Bendigo', 'Albury', 'Launceston', 'Mackay', 'Rockhampton', 'Bunbury',
                                    'Bundaberg', 'Hervey Bay', 'Wagga Wagga', 'Coffs Harbour', 'Shepparton',
                                    'Mildura', 'Port Douglas', 'Broome', 'Exmouth']
                location_lower = location.lower()
                for c in australian_cities:
                    if c.lower() in location_lower:
                        city = c
                        break
            return city, country

        lodging_entries = []
        for row in filtered_sorted:
            row_type = row.get('TYPE', '')
            if row_type == 'Lodging':
                summary = row.get('SUMMARY', '').strip()
                summary_lower = summary.lower()
                if summary_lower.startswith('check-in:') or summary_lower.startswith('check-in'):
                    lodging_entries.append({
                        'type': 'checkin',
                        'hotel_full': summary,
                        'hotel_name': summary.replace('Check-in:', '').replace('Check-in', '').strip(),
                        'location': row.get('LOCATION', '').strip(),
                        'start': row.get('DTSTART', ''),
                        'end': row.get('DTEND', ''),
                        'row': row
                    })
                elif summary_lower.startswith('check-out:') or summary_lower.startswith('check-out'):
                    lodging_entries.append({
                        'type': 'checkout',
                        'hotel_full': summary,
                        'hotel_name': summary.replace('Check-out:', '').replace('Check-out', '').strip(),
                        'location': row.get('LOCATION', '').strip(),
                        'start': row.get('DTSTART', ''),
                        'end': row.get('DTEND', ''),
                        'row': row
                    })
        
        # Match checkins with checkouts
        summary_rows = []
        used_checkouts = set()
        for i, checkin in enumerate(lodging_entries):
            if checkin['type'] == 'checkin':
                # Look for matching checkout
                matched_checkout = None
                for j, entry in enumerate(lodging_entries):
                    if entry['type'] == 'checkout' and j not in used_checkouts:
                        # Match by hotel name similarity
                        if entry['hotel_name'].lower() == checkin['hotel_name'].lower() or \
                           entry['hotel_name'].lower() in checkin['hotel_name'].lower() or \
                           checkin['hotel_name'].lower() in entry['hotel_name'].lower() or \
                           entry['location'] == checkin['location']:
                            matched_checkout = entry
                            used_checkouts.add(j)
                            break
                if matched_checkout:
                    location = checkin['location'] or matched_checkout['location']
                    city, country = extract_city_country(location)
                    start_local = convert_to_local_time(checkin['start'], city, country)
                    end_local = convert_to_local_time(matched_checkout['end'], city, country)
                    summary_rows.append({
                        'Hotel Name': checkin['hotel_name'],
                        'location': location,
                        'City': city,
                        'Country': country,
                        'StartDate': start_local,
                        'EndDate': end_local
                    })
        
        print(f"Writing summary to: {output_summary}")
        summary_fieldnames = ['Hotel Name', 'location', 'City', 'Country', 'StartDate', 'EndDate']
        if args.html or (str(output_summary).lower().endswith('.html')):
            html_content = []
            html_content.append('<!DOCTYPE html>')
            html_content.append('<html lang="en">')
            html_content.append('<head>')
            html_content.append('    <meta charset="UTF-8">')
            html_content.append('    <meta name="viewport" content="width=device-width, initial-scale=1.0">')
            html_content.append('    <title>TripIt Summary</title>')
            html_content.append('    <style>')
            html_content.append('        body { font-family: Arial, sans-serif; margin: 20px; }')
            html_content.append('        table { border-collapse: collapse; width: 100%; }')
            html_content.append('        th, td { border: 1px solid #ddd; padding: 8px; text-align: left; }')
            html_content.append('        th { background-color: #f2f2f2; }')
            html_content.append('        tr:nth-child(even) { background-color: #f9f9f9; }')
            html_content.append('    </style>')
            html_content.append('</head>')
            html_content.append('<body>')
            html_content.append('    <h1>TripIt Summary</h1>')
            html_content.append('    <table>')
            html_content.append('        <thead>')
            html_content.append('            <tr>')
            for field in summary_fieldnames:
                html_content.append(f'                <th>{field}</th>')
            html_content.append('            </tr>')
            html_content.append('        </thead>')
            html_content.append('        <tbody>')
            for row in summary_rows:
                html_content.append('            <tr>')
                for field in summary_fieldnames:
                    value = str(row.get(field, '')).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                    html_content.append(f'                <td>{value}</td>')
                html_content.append('            </tr>')
            html_content.append('        </tbody>')
            html_content.append('    </table>')
            html_content.append('</body>')
            html_content.append('</html>')
            with open(output_summary, 'w', encoding='utf-8') as f:
                f.write('\n'.join(html_content))
        else:
            with open(output_summary, 'w', newline='', encoding='utf-8') as csvfile:
                writer = csv.DictWriter(csvfile, fieldnames=summary_fieldnames)
                writer.writeheader()
                for row in summary_rows:
                    writer.writerow(row)
        
        print(f"Successfully extracted summary to {output_summary}")
        print(f"Summary rows written: {len(summary_rows)}")


if __name__ == '__main__':
    main()
