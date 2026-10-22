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


def load_json(path, default):
    """Read a JSON file, returning a default when it cannot be read"""
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (OSError, ValueError):
        return default


def save_json(path, data):
    """Write a JSON file, ignoring failures"""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, sort_keys=True)
    except OSError:
        pass


def geocode_location(location, cache_file=None):
    """Geocode location using Nominatim (OpenStreetMap) API with fallback"""
    if not location or not location.strip():
        return None, None
    
    if cache_file is None:
        cache_file = Path(__file__).parent / 'results' / 'geocode_cache.json'
    
    # Load cache
    cache = load_json(cache_file, {})
    
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
                    save_json(cache_file, cache)
                    time.sleep(1)  # Respect rate limit
                    return city, country
        except Exception as e:
            pass
    
    return None, None


# ---------------------------------------------------------------------------
# City name canonicalisation
# ---------------------------------------------------------------------------

# The other programs expect a plain city name ("Darwin"), but the name found
# in a location is often a district ("Darwin CBD"), a suburb ("Djugun
# Broome") or a place name with its state and postcode attached ("Exmouth WA
# 6707"). The functions below turn such a name into the accepted city name
# that matches it best: the city table (data/cities.csv) is consulted first,
# and the geocoder the other programs use is asked when the table does not
# know the name.

# Trailing words that mark a district within a city rather than the city.
CITY_QUALIFIER_WORDS = {
    'cbd', 'city', 'centre', 'center', 'downtown', 'inner', 'business', 'district',
}

# Country names the geocoder reports under a different name.
COUNTRY_ALIASES = {
    'usa': 'united states',
    'us': 'united states',
    'united states of america': 'united states',
    'uk': 'united kingdom',
    'great britain': 'united kingdom',
    'greatbritain': 'united kingdom',
    'uae': 'united arab emirates',
}

# The geocoder used for city names: the same service the other programs in
# the trip planning suite geocode with, so the names it returns are names
# those programs accept.
CITY_GEOCODER_URL = 'https://geocoding-api.open-meteo.com/v1/search'


def collapse_spaces(text):
    """Reduce runs of whitespace to a single space"""
    return re.sub(r'\s+', ' ', text or '').strip()


def country_key(name):
    """Normalise a country name so it can be compared"""
    key = collapse_spaces(name).lower()
    return COUNTRY_ALIASES.get(key, key)


def country_matches(result_country, wanted):
    """Whether a geocoder result belongs to the wanted country"""
    if not wanted:
        return True
    result = country_key(result_country)
    want = country_key(wanted)
    if not result:
        return False
    return result == want or want in result or result in want


def city_name_candidates(city):
    """Progressively simpler forms of a city name, best first.

    "Darwin CBD"        -> ["Darwin CBD", "Darwin"]
    "Exmouth WA 6707"   -> ["Exmouth WA 6707", "Exmouth"]
    "Djugun Broome"     -> ["Djugun Broome", "Broome", "Djugun"]
    """
    candidates = []
    seen = set()

    def add(name):
        name = collapse_spaces(name).strip(' ,.-')
        if name and name.lower() not in seen:
            seen.add(name.lower())
            candidates.append(name)

    base = collapse_spaces(city)
    if '>' in base:  # OpenStreetMap style: "Darwin > CBD"
        base = collapse_spaces(base.split('>')[0])
    add(base)

    # Drop a trailing state and/or postcode ("Exmouth WA 6707")
    without_postcode = re.sub(r'\s+[A-Za-z]{2,3}\s*\d{3,5}$', '', base)
    without_postcode = re.sub(r'\s+\d{3,5}$', '', without_postcode)
    add(without_postcode)

    # Drop trailing district qualifiers ("Darwin CBD")
    words = without_postcode.split()
    while len(words) > 1 and words[-1].lower().strip('.,') in CITY_QUALIFIER_WORDS:
        words.pop()
    add(' '.join(words))

    # A suburb with its city ("Djugun Broome") keeps the city in the last
    # word; a city with its suburb ("Broome Djugun") in the first one.
    parts = without_postcode.split()
    if len(parts) > 1:
        add(parts[-1])
        add(parts[0])

    return candidates


class CityTable:
    """The accepted city names and the names they may be found under.

    The table is a CSV file with the columns ``city``, ``country`` and
    ``canonical``: ``city`` is a name as it may turn up in a location (either
    an accepted city name or an alias for one), ``country`` the country it is
    in (blank to match the name in any country) and ``canonical`` the
    accepted city name to report for it (blank when ``city`` already is an
    accepted name).
    """

    def __init__(self, path=None):
        self.by_key = {}
        self.by_name = {}
        if path:
            self.load(path)

    def load(self, path):
        """Read the table from a CSV file"""
        path = Path(path)
        if not path.exists():
            return
        with open(path, 'r', encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f):
                name = (row.get('city') or '').strip()
                country = (row.get('country') or '').strip()
                canonical = (row.get('canonical') or '').strip() or name
                if not name:
                    continue
                key = (collapse_spaces(name).lower(), country.lower())
                self.by_key[key] = canonical
                self.by_name.setdefault(key[0], set()).add((key[1], canonical))

    def lookup(self, name, country):
        """The accepted name for a city name, or None when it is unknown"""
        key = collapse_spaces(name).lower()
        if not key:
            return None
        want_country = collapse_spaces(country).lower()
        if want_country:
            if (key, want_country) in self.by_key:
                return self.by_key[(key, want_country)]
            if (key, '') in self.by_key:
                return self.by_key[(key, '')]
            return None
        # Without a country the name is only usable when it is unambiguous.
        canonicals = {canonical for _, canonical in self.by_name.get(key, set())}
        if len(canonicals) == 1:
            return canonicals.pop()
        return None


def score_city_result(result, candidate):
    """Score a geocoder result against the city name that was looked up"""
    score = 0.0
    name = (result.get('name') or '').strip()
    if name.lower() == candidate.lower():
        score += 100
    elif candidate.lower() in name.lower():
        score += 40
    feature = (result.get('feature_code') or '').upper()
    if feature.startswith('PPL'):  # populated place
        score += 20
    elif feature.startswith('ADM'):  # administrative area
        score += 5
    population = result.get('population')
    if isinstance(population, (int, float)):
        score += min(population / 1000000, 10)
    return score


def lookup_city_geocoder(candidate, country, cache_file=None, use_network=True):
    """Look a city name up in the geocoder the other programs use.

    Returns the (city, country) of the best match, or None when the name is
    unknown. Results are cached, so a name is only looked up once.
    """
    cache = load_json(cache_file, {}) if cache_file else {}
    cache_key = f"{candidate.lower()}|{country_key(country)}"
    if cache_key in cache:
        cached = cache[cache_key]
        return (cached['city'], cached['country']) if cached else None

    found = None
    if use_network and REQUESTS_AVAILABLE:
        try:
            response = requests.get(
                CITY_GEOCODER_URL,
                params={"name": candidate, "count": 10, "language": "en", "format": "json"},
                headers={"User-Agent": "extractTripit/1.0"},
                timeout=10
            )
            if response.status_code == 200:
                results = response.json().get('results') or []
                if results:
                    in_country = [r for r in results if country_matches(r.get('country'), country)]
                    best = max(in_country or results, key=lambda r: score_city_result(r, candidate))
                    found = ((best.get('name') or '').strip(), (best.get('country') or '').strip())
        except Exception:
            found = None

    if cache_file is not None:
        cache[cache_key] = {'city': found[0], 'country': found[1]} if found else None
        save_json(cache_file, cache)

    return found


def canonical_city_name(city, country, table=None, cache_file=None, use_network=True):
    """The accepted name of a city, and the country it belongs to.

    The city table is consulted first (it holds the accepted names and the
    aliases of them), the geocoder afterwards, so a city that is not in the
    table still gets an accepted name. The name is returned unchanged when
    nothing matches it.
    """
    if not city:
        return '', country

    candidates = city_name_candidates(city)

    if table is not None:
        for candidate in candidates:
            accepted = table.lookup(candidate, country)
            if accepted:
                return accepted, country

    mismatch = None
    for candidate in candidates:
        found = lookup_city_geocoder(candidate, country, cache_file, use_network)
        if not found:
            continue
        name, found_country = found
        if not country or country_matches(found_country, country):
            return name, found_country or country
        if mismatch is None:
            mismatch = (name, found_country)

    if mismatch:
        # The name is only known in another country: keep the name, but leave
        # the country as it was found.
        return mismatch[0], country

    return candidates[0], country


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
    """Convert UTC timestamp to local time for city/country in ISO 8601 format"""
    if not utc_str:
        return utc_str
    try:
        # Parse UTC format like 20270604T070000Z
        if utc_str.endswith('Z'):
            clean = utc_str.replace('Z', '+0000')
            dt = datetime.strptime(clean, '%Y%m%dT%H%M%S%z')
        elif len(utc_str) == 8 and 'T' not in utc_str:
            # Date only - convert to ISO format
            return f"{utc_str[:4]}-{utc_str[4:6]}-{utc_str[6:8]}"
        else:
            # Try to parse
            clean = utc_str.replace('+0000', '').replace('Z', '')
            if 'T' in clean and len(clean) >= 15:
                dt = datetime.strptime(clean[:15], '%Y%m%dT%H%M%S')
                if ZONEINFO_AVAILABLE:
                    dt = dt.replace(tzinfo=ZoneInfo('UTC'))
                elif PYTZ_AVAILABLE:
                    import pytz
                    dt = pytz.UTC.localize(dt)
                else:
                    dt = dt.replace(tzinfo=__import__('datetime').timezone.utc)
            else:
                return utc_str
        
        tz = get_timezone(city, country)
        if tz:
            if ZONEINFO_AVAILABLE and hasattr(dt, 'astimezone'):
                local_dt = dt.astimezone(tz)
                # ISO 8601 format: 2027-06-04T15:00:00+08:00 or 2027-06-04T15:00:00
                return local_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
            elif PYTZ_AVAILABLE:
                utc_dt = dt
                if utc_dt.tzinfo is None:
                    import pytz
                    utc_dt = pytz.UTC.localize(utc_dt)
                local_dt = utc_dt.astimezone(tz)
                return local_dt.strftime('%Y-%m-%dT%H:%M:%S%z')
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
    city_cache_file = results_dir / 'city_cache.json'
    cities_path = script_dir / 'data' / 'cities.csv'
    
    import sys
    import argparse
    parser = argparse.ArgumentParser(description='Extract data from TripIt ICS files into CSV/HTML')
    parser.add_argument('ics', nargs='?', help='Input ICS file')
    parser.add_argument('-e', '--extract', help='Extract output file name')
    parser.add_argument('-s', '--summary', help='Summary output file name')
    parser.add_argument('--geocode', action='store_true', help='Use geocoding API to standardize locations')
    parser.add_argument('--no-geocode', action='store_true', help='Disable geocoding, use regex only')
    parser.add_argument('--html', action='store_true', help='Output HTML format instead of CSV (default is CSV)')
    parser.add_argument('--cities-file', help='City name table used to canonicalise city names (default: data/cities.csv)')
    parser.add_argument('--no-canonical-cities', action='store_true',
                        help='Report city names as they are found instead of canonicalising them')
    args = parser.parse_args()

    if args.cities_file:
        cities_path = Path(args.cities_file)
    city_table = CityTable(cities_path)
    use_canonical_cities = not args.no_canonical_cities

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
        city_changes = []

        def raw_city_country(location):
            """The city and country as they are written in the location"""
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

        def extract_city_country(location):
            """The city and country, with the city in its accepted form.

            The name found in a location is often a district or a suburb
            ("Darwin CBD") rather than the city itself, so it is replaced by
            the accepted city name that matches it best.
            """
            city, country = raw_city_country(location)
            if not use_canonical_cities or not city:
                return city, country
            accepted, accepted_country = canonical_city_name(
                city, country,
                table=city_table,
                cache_file=city_cache_file,
                use_network=REQUESTS_AVAILABLE and not args.no_geocode,
            )
            if accepted and accepted != city:
                city_changes.append((city, accepted))
            return accepted, accepted_country or country

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
        for found, accepted in dict.fromkeys(city_changes):
            print(f"City name canonicalised: {found} -> {accepted}")


if __name__ == '__main__':
    main()
