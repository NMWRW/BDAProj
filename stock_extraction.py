#created with assistance from chatGPT

import json
import argparse
import pandas as pd
import os
import requests
from pathlib import Path
from datetime import date, timedelta


# ============================================================
# INPUT DATA
# ============================================================


def load_input_data(manifest_path):
    # Load company information and SEC filings from the manifest.

    with open(manifest_path, "r", encoding="utf-8") as file:
        manifest = json.load(file)

    # Extract company information
    ticker = manifest["ticker"]
    company = manifest.get("company", ticker)
    cik = manifest["cik"]

    # Get filings with valid reporting dates
    all_filings = [
        filing for filing in manifest["filings"]
        if filing.get("reportDate")
    ]

    if not all_filings:
        raise ValueError("No filings with reporting dates found.")

    # Keep only 10-Q and 10-K filings
    filings = [
        filing for filing in all_filings
        if filing.get("form") in ["10-Q", "10-K"]
    ]

    # Sort filings chronologically
    filings.sort(key=lambda x: x["reportDate"])

    if not filings:
        raise ValueError("No 10-Q or 10-K filings found.")

    return company, cik, ticker, filings


# ============================================================
# MOCK STOCK DATA
# ============================================================

def get_stock_data(symbol, start_date, end_date):
    """
    Retrieve daily historical stock data from Twelve Data.
    Returns records in the format expected by the report functions.
    """

    api_key = os.getenv("TWELVE_DATA_API_KEY")

    if not api_key:
        raise ValueError("Twelve Data API key not found.")

    url = "https://api.twelvedata.com/time_series"

    params = {
        "symbol": symbol,
        "interval": "1day",
        "start_date": start_date,
        "end_date": end_date,
        "order": "ASC",
        "format": "JSON",
        "apikey": api_key
    }

    response = requests.get(url, params=params, timeout=30)
    response.raise_for_status()

    data = response.json()

    # Check for API errors
    if data.get("status") == "error" or "values" not in data:
        raise RuntimeError(
            f"Twelve Data error: {data.get('message', data)}"
        )

    stock_data = []

    # Convert API records into the format used by the reports
    for record in data["values"]:

        stock_data.append({
            "date": record["datetime"],
            "open": record.get("open"),
            "high": record.get("high"),
            "low": record.get("low"),
            "close": record.get("close"),
            "volume": record.get("volume")
        })

    # Keep records in chronological order
    stock_data.sort(key=lambda x: x["date"])

    print(
        f"Retrieved {len(stock_data)} daily records "
        f"for {symbol}."
    )
    return stock_data


def get_stock_date_range(fiscal_years):
    """
    Determine the complete stock-data date range
    from the fiscal years being processed.
    """

    if not fiscal_years:
        raise ValueError("No fiscal years available for stock data.")

    start_dates = []
    end_dates = []

    for fiscal_year in fiscal_years:

        start_dates.append(
            fiscal_year["fiscal_year_start"]
        )

        # Completed fiscal year
        if fiscal_year["fiscal_year_end"] is not None:
            end_dates.append(
                fiscal_year["fiscal_year_end"]
            )

        # Current unfinished fiscal year
        else:
            for filing in fiscal_year["quarter_filings"]:
                end_dates.append(
                    date.fromisoformat(filing["reportDate"])
                )

    if not start_dates or not end_dates:
        raise ValueError("No fiscal-year dates available.")

    return min(start_dates), max(end_dates)



def get_Fake_stock_data(symbol, start_date, end_date):
    """
    Generate fake daily stock data for testing.

    Only weekdays are generated.
    No external API calls are made.
    """

    stock_data = []

    current_date = date.fromisoformat(start_date)
    end_date = date.fromisoformat(end_date)

    # Starting test stock price
    price = 150.00

    while current_date <= end_date:

        # Generate records for weekdays only
        if current_date.weekday() < 5:

            # Deterministic price movement for repeatable testing
            change = ((current_date.toordinal() % 11) - 5) * 0.15

            open_price = price
            close_price = round(price + change, 2)

            high_price = round(
                max(open_price, close_price) + 0.75, 2
            )

            low_price = round(
                min(open_price, close_price) - 0.75, 2
            )

            # Match Twelve Data's typical string value format
            stock_data.append({
                "date": current_date.isoformat(),
                "open": f"{open_price:.2f}",
                "high": f"{high_price:.2f}",
                "low": f"{low_price:.2f}",
                "close": f"{close_price:.2f}",
                "volume": str(
                    1000000 + current_date.toordinal() % 500000
                )
            })

            price = close_price

        current_date += timedelta(days=1)

    print(
        f"Generated {len(stock_data)} mock daily records "
        f"for {symbol}."
    )

    return stock_data


# ============================================================
# REPORTING PERIODS
# ============================================================

def get_reporting_periods(filings):
    """
    Calculate reporting periods between consecutive SEC filings.

    Each period starts the day after the previous filing's
    report date and ends on the current filing's report date.
    """

    reporting_periods = []
    previous_report_date = None

    for filing in filings:

        report_date = date.fromisoformat(
            filing["reportDate"]
        )

        form = filing["form"]
        accession_number = filing["accessionNumber"]
        primary_document = filing["primaryDocument"]

        full_file_name = (
            accession_number + "_" + primary_document
        )

        # Calculate period using the previous filing date
        if previous_report_date is not None:

            start_date = (
                previous_report_date + timedelta(days=1)
            )

            end_date = report_date

            days = (end_date - start_date).days + 1

            reporting_periods.append({
                "form": form,
                "accession_number": accession_number,
                "primary_document": primary_document,
                "full_file_name": full_file_name,
                "report_date": report_date.isoformat(),
                "start": start_date.isoformat(),
                "end": end_date.isoformat(),
                "days": days
            })

        previous_report_date = report_date

    return reporting_periods


# ============================================================
# FISCAL YEAR / QUARTER DATES
# ============================================================

def get_fiscal_years(filings):
    """
    Group 10-Q filings between consecutive 10-K filings.

    Q1-Q3 use their respective 10-Q report dates.
    Q4 runs from the third 10-Q through the 10-K report date.

    An unfinished fiscal year can also produce Q1-Q3 files
    when a previous 10-K establishes its starting date.
    """

    # Separate annual and quarterly filings
    annual_filings = [
        filing for filing in filings
        if filing["form"] == "10-K"
    ]

    quarterly_filings = [
        filing for filing in filings
        if filing["form"] == "10-Q"
    ]

    fiscal_years = []

    previous_annual_date = None

    # --------------------------------------------------------
    # Process completed fiscal years
    # --------------------------------------------------------

    for annual in annual_filings:

        annual_end = date.fromisoformat(
            annual["reportDate"]
        )

        # Determine fiscal year starting date
        if previous_annual_date is not None:

            fiscal_year_start = (
                previous_annual_date + timedelta(days=1)
            )

            estimated_start = False

        else:

            # Estimate the first fiscal year if no previous
            # 10-K is available in the manifest
            try:
                previous_year = annual_end.replace(
                    year=annual_end.year - 1
                )

            except ValueError:
                # Handle February 29 when moving to a
                # non-leap year
                previous_year = annual_end.replace(
                    year=annual_end.year - 1,
                    day=28
                )

            fiscal_year_start = (
                previous_year + timedelta(days=1)
            )

            estimated_start = True

        # Find 10-Q filings within this fiscal year
        year_quarters = [
            filing for filing in quarterly_filings
            if fiscal_year_start <= date.fromisoformat(
                filing["reportDate"]
            ) < annual_end
        ]

        # Sort quarterly filings chronologically
        year_quarters.sort(
            key=lambda x: x["reportDate"]
        )

        # Remove duplicate reporting dates
        unique_quarters = []
        seen_dates = set()

        for filing in year_quarters:

            report_date = filing["reportDate"]

            if report_date not in seen_dates:
                unique_quarters.append(filing)
                seen_dates.add(report_date)

        year_quarters = unique_quarters
        # Skip fiscal years that do not have all three 10-Q filings
        if len(year_quarters) != 3:
            print(f"Skipping fiscal year ending {annual_end}: "f"Expected 3 quarterly filings, found {len(year_quarters)}")

            previous_annual_date = annual_end
            continue

        # Store fiscal year information
        fiscal_years.append({
            "annual_filing": annual,
            "fiscal_year_start": fiscal_year_start,
            "fiscal_year_end": annual_end,
            "estimated_start": estimated_start,
            "quarter_filings": year_quarters,
            "completed": len(year_quarters) == 3
        })

        previous_annual_date = annual_end

    # --------------------------------------------------------
    # Handle an unfinished/current fiscal year
    # --------------------------------------------------------

    # The latest 10-K establishes the current fiscal year start
    if previous_annual_date is not None:

        current_start = (
            previous_annual_date + timedelta(days=1)
        )

        current_quarters = [
            filing for filing in quarterly_filings
            if date.fromisoformat(
                filing["reportDate"]
            ) >= current_start
        ]

        current_quarters.sort(
            key=lambda x: x["reportDate"]
        )

        # Remove duplicate reporting dates
        unique_quarters = []
        seen_dates = set()

        for filing in current_quarters:

            report_date = filing["reportDate"]

            if report_date not in seen_dates:
                unique_quarters.append(filing)
                seen_dates.add(report_date)

        current_quarters = unique_quarters

        # Add the unfinished fiscal year if quarterly filings exist
        if current_quarters:

            fiscal_years.append({
                "annual_filing": None,
                "fiscal_year_start": current_start,
                "fiscal_year_end": None,
                "estimated_start": False,
                "quarter_filings": current_quarters[:3],
                "completed": False
            })

    return fiscal_years


# ============================================================
# FISCAL YEAR LABEL
# ============================================================

def get_fiscal_year_label(fiscal_year):
    """Return the year used to identify a fiscal year."""

    # Completed fiscal year: use its ending year
    if fiscal_year["fiscal_year_end"] is not None:
        return fiscal_year["fiscal_year_end"].year

    # Estimate the ending year for an unfinished fiscal year
    return (
        fiscal_year["fiscal_year_start"] + timedelta(days=364)
    ).year


# ============================================================
# STOCK DATA PROCESSING
# ============================================================

def get_period_stock_data(stock_data, start_date, end_date):
    #Filter daily stock records for a specific date range.

    return [
        record for record in stock_data
        if start_date <= record["date"] <= end_date
    ]


def build_stock_report(
    company,
    cik,
    ticker,
    fiscal_year,
    period_name,
    start_date,
    end_date,
    filing,
    stock_data
):
    #Build a JSON report containing stock data and period details.

    # Get stock records within the reporting period
    period_stock_data = get_period_stock_data(
        stock_data,
        start_date.isoformat(),
        end_date.isoformat()
    )

    # Build the report
    report = {
        "company": company,
        "cik": cik,
        "ticker": ticker,
        "fiscal_year": fiscal_year,
        "period": period_name,
        "form": filing["form"],
        "accession_number": filing["accessionNumber"],
        "primary_document": filing["primaryDocument"],
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "calendar_days": (end_date - start_date).days + 1,
        "trading_days": len(period_stock_data),
        "stock_data": period_stock_data
    }

    return report


# ============================================================
# SAVE JSON FILES
# ============================================================

def save_json(output_path, data):
    #Save data to a formatted JSON file.

    with open(output_path, "w", encoding="utf-8") as file:
        json.dump(data, file, indent=4)

    print(f"Saved: {output_path.name}")


# ============================================================
# CREATE QUARTERLY AND YEARLY REPORTS
# ============================================================

def create_fiscal_reports(
    company,
    cik,
    ticker,
    fiscal_years,
    stock_data,
    output_dir
):
    #Create individual quarter files and combined yearly files.

    fiscal_year_details = []

    for fiscal_year in fiscal_years:

        annual_filing = fiscal_year["annual_filing"]
        quarter_filings = fiscal_year["quarter_filings"]

        fiscal_year_start = fiscal_year["fiscal_year_start"]
        fiscal_year_end = fiscal_year["fiscal_year_end"]

        fiscal_year_label = get_fiscal_year_label(
            fiscal_year
        )

        # Skip an unanchored first fiscal year if fewer than three 10-Q filings are available to establish quarters
        if fiscal_year["estimated_start"] and len(quarter_filings) < 3:

            print(
                "\nSkipping incomplete first fiscal year: "
                "quarter numbering cannot be established reliably."
            )

            fiscal_year_details.append({
                "fiscal_year": fiscal_year_label,
                "start": fiscal_year_start.isoformat(),
                "end": (
                    fiscal_year_end.isoformat()
                    if fiscal_year_end else None
                ),
                "quarter_files": [],
                "year_file": None,
                "complete": False,
                "skipped": True
            })

            continue

        print("\n" + "=" * 60)
        print(f"Fiscal Year: {fiscal_year_label}")
        print(f"Starting: {fiscal_year_start}")
        print(f"Ending: {fiscal_year_end or 'In progress'}")
        print("=" * 60)

        # Store generated quarter reports
        quarter_reports = []
        quarter_file_names = []

        previous_end = (
            fiscal_year_start - timedelta(days=1)
        )

        # ----------------------------------------------------
        # Create Q1-Q3 from 10-Q filings
        # ----------------------------------------------------

        for index, filing in enumerate(quarter_filings):

            quarter_number = index + 1

            # Only Q1-Q3 are valid 10-Q quarters
            if quarter_number > 3:
                print(
                    "Warning: More than three 10-Q filings found."
                )
                break

            quarter_end = date.fromisoformat(
                filing["reportDate"]
            )

            quarter_start = (
                previous_end + timedelta(days=1)
            )

            period_name = f"Q{quarter_number}"

            # Build the quarter report
            report = build_stock_report(
                company,
                cik,
                ticker,
                fiscal_year_label,
                period_name,
                quarter_start,
                quarter_end,
                filing,
                stock_data
            )

            # Use the original SEC filing name
            base_name = (
                filing["accessionNumber"] + "_" +
                filing["primaryDocument"]
            )

            output_path = (
                output_dir /
                f"{base_name}_{period_name}.json"
            )

            save_json(output_path, report)

            quarter_reports.append(report)
            quarter_file_names.append(output_path.name)

            print(
                f"{period_name}: {quarter_start} to {quarter_end} "
                f"| {report['trading_days']} trading days"
            )

            previous_end = quarter_end

        # ----------------------------------------------------
        # Create Q4 and yearly report when a 10-K is available
        # ----------------------------------------------------

        if annual_filing is not None:

            # Require all three 10-Q filings before creating Q4
            if len(quarter_filings) == 3:

                quarter_start = (
                    previous_end + timedelta(days=1)
                )

                quarter_end = fiscal_year_end

                # Build Q4 using the 10-K filing
                q4_report = build_stock_report(
                    company,
                    cik,
                    ticker,
                    fiscal_year_label,
                    "Q4",
                    quarter_start,
                    quarter_end,
                    annual_filing,
                    stock_data
                )

                annual_base_name = (
                    annual_filing["accessionNumber"] + "_" +
                    annual_filing["primaryDocument"]
                )

                q4_path = (
                    output_dir /
                    f"{annual_base_name}_Q4.json"
                )

                save_json(q4_path, q4_report)

                quarter_reports.append(q4_report)
                quarter_file_names.append(q4_path.name)

                print(
                    f"Q4: {quarter_start} to {quarter_end} "
                    f"| {q4_report['trading_days']} trading days"
                )

                # ------------------------------------------------
                # Combine Q1-Q4 stock data into yearly report
                # ------------------------------------------------

                yearly_stock_data = []

                for quarter in quarter_reports:
                    yearly_stock_data.extend(
                        quarter["stock_data"]
                    )

                # Sort records chronologically
                yearly_stock_data.sort(
                    key=lambda x: x["date"]
                )

                # Remove duplicate trading dates
                unique_stock_data = []
                seen_dates = set()

                for record in yearly_stock_data:

                    record_date = record["date"]

                    if record_date not in seen_dates:
                        unique_stock_data.append(record)
                        seen_dates.add(record_date)

                # Build yearly report
                yearly_report = {
                    "company": company,
                    "cik": cik,
                    "ticker": ticker,
                    "fiscal_year": fiscal_year_label,
                    "period": "Year",
                    "start_date": fiscal_year_start.isoformat(),
                    "end_date": fiscal_year_end.isoformat(),
                    "calendar_days": (
                        fiscal_year_end - fiscal_year_start
                    ).days + 1,
                    "trading_days": len(unique_stock_data),
                    "quarter_files": quarter_file_names,
                    "stock_data": unique_stock_data
                }

                # Save yearly report using the 10-K filename
                year_number = fiscal_year_end.year

                yearly_path = (
                    output_dir /
                    f"{annual_base_name}_Year{year_number}.json"
                )

                save_json(yearly_path, yearly_report)

                print(
                    f"Yearly report: {len(unique_stock_data)} "
                    f"daily stock records"
                )

                fiscal_year_details.append({
                    "fiscal_year": fiscal_year_label,
                    "start": fiscal_year_start.isoformat(),
                    "end": fiscal_year_end.isoformat(),
                    "quarter_files": quarter_file_names,
                    "year_file": yearly_path.name,
                    "complete": True
                })

            else:

                print(
                    "\nYearly report not created. "
                    "Three 10-Q filings are required for Q1-Q3."
                )

                fiscal_year_details.append({
                    "fiscal_year": fiscal_year_label,
                    "start": fiscal_year_start.isoformat(),
                    "end": fiscal_year_end.isoformat(),
                    "quarter_files": quarter_file_names,
                    "year_file": None,
                    "complete": False
                })

        else:

            # Current fiscal year: save available Q1-Q3 files.
            # Q4 and yearly reports require a 10-K.
            fiscal_year_details.append({
                "fiscal_year": fiscal_year_label,
                "start": fiscal_year_start.isoformat(),
                "end": None,
                "quarter_files": quarter_file_names,
                "year_file": None,
                "complete": False
            })

    return fiscal_year_details


# ============================================================
# MAIN PROCESSING
# ============================================================

def process_stock_data(manifest_path):
    # Process SEC filings and save quarterly and yearly reports.

    manifest_path = Path(manifest_path).expanduser().resolve()

    if not manifest_path.is_file():
        raise FileNotFoundError(
            f"Manifest not found: {manifest_path}"
        )

    # Create Stock Report beside the manifest
    output_dir = manifest_path.parent / "Stock Report"
    output_dir.mkdir(parents=True, exist_ok=True)

    print("\n" + "=" * 60)
    print("STOCK REPORT FOLDER")
    print("=" * 60)
    print(f"Output folder: {output_dir}")
    print(f"Folder exists: {output_dir.exists()}")

    # Load company and SEC filing information
    company, cik, ticker, filings = load_input_data(manifest_path)

    print(f"\nCompany: {company}")
    print(f"CIK: {cik}")
    print(f"Ticker: {ticker}")

    # --------------------------------------------------------
    # Calculate reporting periods and fiscal years
    # --------------------------------------------------------

    reporting_periods = get_reporting_periods(filings)
    fiscal_years = get_fiscal_years(filings)

    print(f"\nReporting periods found: {len(reporting_periods)}")
    print(f"Fiscal years found: {len(fiscal_years)}")

    # --------------------------------------------------------
    # Determine complete stock data date range
    # --------------------------------------------------------

    data_start, data_end = get_stock_date_range(fiscal_years)

    print(
        f"\nStock Data Date Range: "
        f"{data_start} to {data_end}"
    )

    # --------------------------------------------------------
    # Retrieve historical stock data from Twelve Data
    # --------------------------------------------------------

    print("\nRetrieving historical stock data from Twelve Data...")

    stock_data = get_stock_data(
        ticker,
        data_start.isoformat(),
        data_end.isoformat()
    )

    print(
        f"Retrieved {len(stock_data)} daily stock records."
    )

    # --------------------------------------------------------
    # Save historical stock data locally
    # --------------------------------------------------------

    historical_data_path = (
        output_dir / "historical_stock_data.json"
    )

    save_json(historical_data_path, stock_data)

    # --------------------------------------------------------
    # Create quarterly and yearly reports
    # --------------------------------------------------------

    fiscal_year_details = create_fiscal_reports(
        company,
        cik,
        ticker,
        fiscal_years,
        stock_data,
        output_dir
    )

    # --------------------------------------------------------
    # Save reporting dates
    # --------------------------------------------------------

    reporting_dates = {
        "company": company,
        "cik": cik,
        "ticker": ticker,
        "stock_data_start": data_start.isoformat(),
        "stock_data_end": data_end.isoformat(),
        "reporting_periods": reporting_periods,
        "fiscal_years": fiscal_year_details
    }

    reporting_dates_path = (
        output_dir / "reporting_dates.json"
    )

    save_json(reporting_dates_path, reporting_dates)

    # --------------------------------------------------------
    # Verify output files
    # --------------------------------------------------------

    print("\n" + "=" * 60)
    print("STOCK DATA PROCESSING COMPLETE")
    print("=" * 60)

    print(f"\nOutput folder: {output_dir}")
    print(f"Folder exists: {output_dir.exists()}")

    output_files = sorted(
        output_dir.glob("*.json")
    )

    print(f"\nJSON files created: {len(output_files)}")

    for output_file in output_files:
        print(f"  {output_file.name}")

    if not output_files:
        print("\nWARNING: No JSON files were created.")

    return reporting_dates


# ============================================================
# COMMAND LINE
# ============================================================

def main(argv=None):
    #Parse command-line arguments and execute processing.
    print("running script")
    parser = argparse.ArgumentParser(
        description=(
            "Generate stock reports from an SEC manifest."
        )
    )

    parser.add_argument(
        "input",
        type=Path,
        help="Path to the SEC manifest.json file."
    )

    args = parser.parse_args(argv)

    process_stock_data(args.input)


# ============================================================
# PROGRAM ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()
