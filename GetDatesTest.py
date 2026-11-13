#tests that dates are extracted from manifest correctly
#assumes manifest is combined 10-Q and 10-K in chronological order
import json
import pandas as pd

MANIFEST_PATH = "data\\sec\\CIK0000320193\\20261113T015022563029Z-1c14074d\\manifest.json"


def get_dates(manifest_path):

    with open(manifest_path, "r", encoding="utf-8") as file:
        manifest = json.load(file)

    # Get 10-Q and 10-K reports
    filings = [
        filing for filing in manifest["filings"]
        if filing["form"] in ["10-Q", "10-K"]
        and filing.get("reportDate")
    ]

    # Sort by reporting period end date
    filings.sort(key=lambda x: x["reportDate"])

    previous_report_date = None

    print("\nSEC REPORTING PERIODS")
    print("-" * 60)

    for filing in filings:

        form = filing["form"]
        report_date = pd.to_datetime(filing["reportDate"])
        accession_number = filing["accessionNumber"]
        primary_document = filing["primaryDocument"]

        # Calculate range from previous report
        print(f"\nForm: {form}")
        print(f"Accession Number: {accession_number}")
        print(f"Primary Document: {primary_document}")
        
        if previous_report_date is not None:

            start_date = previous_report_date + pd.Timedelta(days=1)
            end_date = report_date

            days = (end_date - start_date).days + 1

           
            print(f"Start: {start_date.date()}")
            print(f"End:   {end_date.date()}")
            print(f"Days:  {days}")

        else:
            print(f"Report date: {report_date.date()}")
            print("First report - no previous period available.")

        previous_report_date = report_date


if __name__ == "__main__":
    get_dates(MANIFEST_PATH)
