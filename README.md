# extractTripit.py

A Python script to extract travel itinerary data from TripIt ICS calendar files and convert it to CSV format.

## Overview

This script parses ICS (iCalendar) files exported from TripIt and extracts event information including flights, lodging, car rentals, tours, cruises, and other travel activities. It processes the data and outputs structured CSV files.

## Layout

```
data/      the ICS files and the city name table
results/   the generated reports and the geocoding caches
```

- `extractTripit.py` - Main script for extracting and processing ICS data
- `data/sample_trip.ics` - Sample TripIt ICS file
- `data/cities.csv` - Accepted city names (and the names they may be found under)
- `results/extractTripit.csv` - Processed and filtered event data
- `results/extractTripit_summary.csv` - Summary of the lodging data only
- `results/geocode_cache.json` - Cache of the locations that were geocoded
- `results/city_cache.json` - Cache of the city names that were looked up

## Usage

```bash
python3 extractTripit.py [input.ics] [options]
```

### Options
- `-e`, `--extract` - Extract output file name (if no slash, saved to results/)
- `-s`, `--summary` - Summary output file name (if no slash, saved to results/)
- `--geocode` - Use the geocoding APIs to standardize the locations (the
  default, when `requests` is installed)
- `--no-geocode` - Disable the geocoding APIs and read the city and the
  country out of the location string only
- `--cities-file PATH` - City name table to use (default: `data/cities.csv`)
- `--no-canonical-cities` - Report the city names as they are found, without
  replacing them by the accepted city names
- `--html` - Write the report as HTML instead of CSV (also used when the
  output file name ends in `.html`)

### Default paths
- Input: `data/sample_trip.ics` (relative to script directory)
- Extract output: `results/extractTripit.csv`
- Summary output: `results/extractTripit_summary.csv`
- City name table: `data/cities.csv`

### Examples
```bash
python3 extractTripit.py
python3 extractTripit.py data/WA_trip.ics
python3 extractTripit.py -e custom_extract.csv
python3 extractTripit.py -s custom_summary.csv
python3 extractTripit.py -e /path/to/extract.csv -s /path/to/summary.csv
```

## Features

### Data Extraction
- Parses VEVENT blocks from ICS files
- Handles folded lines (ICS line continuation)
- Extracts: UID, SUMMARY, LOCATION, DTSTART, DTEND, DTSTAMP, DESCRIPTION
- Detects if dates are date-only (VALUE=DATE) or datetime values

### Event Classification
Events are automatically categorized by type:
- **Flight** - Flights (identified by airline codes like QF649, VA1485)
- **Lodging** - Hotels, accommodations, check-in/check-out events
- **Car Rental** - Car rentals, pickups, and drop-offs
- **Cruise** - Cruise embarkation/disembarkation
- **Tour/Activity** - Tours and activities
- **Trip** - Main trip/event (filtered out from final output)
- **Other** - Miscellaneous events

### Data Processing
- Removes events with type "Trip" from output
- Sorts events by DTSTART
- Converts "Check-in"/"Check-out" events classified as "Other" to "Lodging" type
- Unescapes ICS special characters (\, \;, \n)
- Builds the summary from the check-in/check-out pairs of each lodging
- Converts the times of a lodging to the local time of its city
- Replaces the city name of every lodging with the accepted city name that
  matches it best (see below)

### City names
The city found in a location is often a district (`Darwin CBD`), a suburb
(`Djugun Broome`) or a place name with its state and postcode attached
(`Exmouth WA 6707`). Other programs expect a plain city name, so the name in
the summary is replaced by the accepted city name that matches it best. The
location itself is reported as it was found.

The name is resolved in two steps:
1. **The city table** (`data/cities.csv`) is consulted first. It holds the
   accepted city names and the other names they may be found under.
2. **The geocoder** (Open-Meteo, the service the other programs geocode
   with) is asked when the table does not know the name. The name it returns
   is a name the other programs accept.

Both are asked for **English** names, so the city and the country are
reported in English whatever the local names of the place are. The lookups
are cached in `results/city_cache.json`, so a city is only looked up once.

The city table is a CSV file with the columns `city`, `country` and
`canonical`:

```csv
city,country,canonical
Perth,Australia,
Broome,Australia,
Darwin CBD,Australia,Darwin
Djugun Broome,Australia,Broome
```

- `city` — a name as it may turn up in a location, either an accepted city
  name or an alias for one
- `country` — the country the city is in (blank matches the name in any
  country)
- `canonical` — the accepted city name to report for it (blank when `city`
  already is an accepted name)

Add the cities of a trip to the table to have them reported by their
accepted name without a lookup, or to give a name another program accepts.
The table wins over the geocoder, so it can also correct a name the
geocoder reports differently.

## Output Format

### extractTripit.csv
| Field | Description |
|-------|-------------|
| TYPE | Event category (Flight, Lodging, Car Rental, Cruise, Tour/Activity, Other) |
| SUMMARY | Event title/description |
| LOCATION | Event location |
| DTSTART | Start date/time |
| DTEND | End date/time |
| DTSTART_IS_DATE | Boolean indicating if DTSTART is date-only |
| DTEND_IS_DATE | Boolean indicating if DTEND is date-only |
| UID | Unique identifier from ICS |
| DTSTAMP | Timestamp when event was created/modified |
| DESCRIPTION | Full event description |

### extractTripit_summary.csv
Lodging summary: one row per check-in that has a matching check-out:
| Field | Description |
|-------|-------------|
| Hotel Name | Event summary (hotel/check-in/check-out name) |
| location | Event location (the address as it was found) |
| City | The accepted city name (e.g. `Darwin`, not `Darwin CBD`) |
| Country | The country, in English |
| StartDate | Start date/time, in the local time of the city |
| EndDate | End date/time, in the local time of the city |

## Requirements

- Python 3.9 or later (for `zoneinfo`; `pytz` is used instead when it is
  installed and `zoneinfo` is not available)
- Standard library only (csv, re, datetime, pathlib, os); `requests` is
  optional and only used for the geocoding lookups

## Notes

- The script is designed for TripIt ICS exports
- Handles various ICS formatting quirks including line folding and escaped characters
- All paths are relative to the script location for portability
- The caches are safe to delete: the lookups are simply made again
