# extractTripit.py

A Python script to extract travel itinerary data from TripIt ICS calendar files and convert it to CSV format.

## Overview

This script parses ICS (iCalendar) files exported from TripIt and extracts event information including flights, lodging, car rentals, tours, cruises, and other travel activities. It processes the data and outputs structured CSV files.

## Files

- `extractTripit.py` - Main script for extracting and processing ICS data
- `data/sample_trip.ics` - Sample TripIt ICS file
- `extractTripit.csv` - Processed and filtered event data
- `extractTripit_summary.csv` - Summary of location/lodging data only

## Usage

```bash
python3 extractTripit.py [input.ics] [options]
```

### Options
- `-e`, `--extract` - Extract output file name (if no slash, saved to results/)
- `-s`, `--summary` - Summary output file name (if no slash, saved to results/)

### Default paths
- Input: `data/sample_trip.ics` (relative to script directory)
- Extract output: `results/extractTripit.csv`
- Summary output: `results/extractTripit_summary.csv`

### Examples
```bash
python3 extractTripit.py
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

## Output Format

### extraceTripit.csv
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

### extraceTripit_summary.csv
Location/lodging summary containing only Lodging type records:
| Field | Description |
|-------|-------------|
| Hotel | Event summary (hotel/check-in/check-out name) |
| location | Event location |
| StartDate | Start date/time |
| end date | End date/time |

## Requirements

- Python 3.x
- Standard library only (csv, re, datetime, pathlib, os)

## Notes

- The script is designed for TripIt ICS exports
- Handles various ICS formatting quirks including line folding and escaped characters
- All paths are relative to the script location for portability
