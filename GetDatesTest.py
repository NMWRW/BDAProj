#tests that dates are extracted from manifest correctly
#assumes manifest is combined 10-Q and 10-K in chronological order
#use acquire_sec.py with limit x+1, first document will be skipped 
import json
import argparse
from pathlib import Path
from datetime import timedelta


#Test manifest MANIFEST_PATH = "data\\sec\\CIK0000320193\\20261113T015022563029Z-1c14074d\\manifest.json"

def get_dates(manifest_path):

    with open(manifest_path, "r", encoding="utf-8") as file:
        manifest = json.load(file)

    company = manifest["company"]
    cik = manifest["cik"]
    ticker = manifest['ticker']
    
    # Get 10-Q and 10-K reports
    filings = [
        filing for filing in manifest["filings"]
        if filing["form"] in ["10-Q", "10-K"]
        and filing.get("reportDate")
    ]

    # Sort by reporting period end date
    filings.sort(key=lambda x: x["reportDate"])

    previous_report_date = None
    reporting_periods = []

    print("\nSEC REPORTING PERIODS")
    print("-" * 60)
    
    first_date = None
    last_date = None
    
    for filing in filings:

        form = filing["form"]
        report_date = filing["reportDate"]
        accession_number = filing["accessionNumber"]
        primary_document = filing["primaryDocument"]
        if first_date is None or report_date < first_date:
            first_date = report_date
        if last_date is None or report_date > last_date:
            last_date = report_date
        
        filing["full"] = accession_number + "_" + primary_document

        print(f"\nForm: {form}")
        print(f"Accession Number: {accession_number}")
        print(f"Primary Document: {primary_document}")

        if previous_report_date is not None:

            start_date = (__import__("datetime").date.fromisoformat(previous_report_date)+ timedelta(days=1))

            end_date = __import__("datetime").date.fromisoformat(report_date)

            days = (end_date - start_date).days + 1

            print(f"Start: {start_date}")
            print(f"End:   {end_date}")
            print(f"Days:  {days}")

            reporting_periods.append({
                "form": form,
                "accession_number": accession_number,
                "primary_document": primary_document,
                "full_file_name": filing["full"],
                "report_date": report_date,
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": days
            })

        else:
            print(f"Report date: {report_date}")
            print("First report - no previous period available.")

        previous_report_date = report_date

    # Create output JSON
    output = {
        "company": company,
        "cik": cik,
        "ticker": ticker,
        "firstDate": first_date,
        "lastDate": last_date,
        "reporting_periods": reporting_periods
    }

    # Save JSON in same directory as manifest
    output_path = manifest_path.parent / "reporting_periods.json"

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(output, file, indent=4)

    print(f"\nSaved reporting periods to: {output_path}")


def main(argv=None):

    parser = argparse.ArgumentParser(description=__doc__)

    parser.add_argument(
        "input",
        type=Path,
        help="Acquisition manifest.json"
    )

    args = parser.parse_args(argv)

    get_dates(args.input)


if __name__ == "__main__":
    main()

