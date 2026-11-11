# Paperflow

Paperflow is a Windows-compatible Python application for discovering, classifying, and downloading scholarly research papers. It uses OpenAlex as its primary discovery source and can also use Semantic Scholar and Crossref to enrich records.

The project provides:

- A browser-based dashboard for configuring and starting a collection.
- A terminal-based CLI for repeatable and automated runs.
- Relevance and publication-type classification before download.
- Validation of downloaded files so only usable PDFs count toward the target.
- DOI-first and title-based duplicate detection backed by SQLite checkpoints.
- Year-based PDF folders and CSV exports for downloaded, unavailable, subscription, and manual-review records.
- Optional institutional-access support that uses a visible browser and never bypasses credentials, CAPTCHAs, or publisher access controls.
- Resumable collections that preserve progress if a run is interrupted.

## Requirements

- Windows
- Python 3.11 or newer
- Internet access for the first setup and for paper discovery/downloads
- An OpenAlex API key for OpenAlex searches and cached-content downloads

Semantic Scholar API access and a Crossref email address are optional. Keep API keys private and never commit them to GitHub.

## Easy setup: double-click the launcher

This is the recommended setup for most users.

1. Install Python 3.11 or newer. During installation, enable **Add Python to PATH** if the installer offers that option.
2. Download or clone this project and open its folder in File Explorer.
3. Double-click **`Start_Paperflow.bat`**.
4. Wait while the launcher creates a private `.venv` environment and installs Paperflow and its required dependencies automatically. The first run needs internet access.
5. Paperflow opens a local web dashboard in your browser at `http://127.0.0.1:8765`.

The launcher is self-contained: it uses the project-local environment, so it does not modify your system-wide Python packages. If setup fails, the terminal window displays the error. Confirm that Python 3.11 or newer is installed, then run the launcher again.

Keep the terminal window open while using the dashboard. Close that window when you want to stop the local server.

### Using the dashboard

The dashboard lets you:

1. Enter comma-separated search phrases.
2. Enter an OpenAlex API key for the current run.
3. Select one or more years from 2000 through 2026.
4. Select publication types.
5. Set the number of validated open-access PDFs to collect.
6. Choose the folder where the collection should be saved.
7. Start and monitor the collection from the browser.

The dashboard binds to `127.0.0.1`, so it is available only on the local computer by default. The OpenAlex key entered in the dashboard is used for that run and is not saved in application settings or logs.

By default, the dashboard stores results in `Paperflow\ResearchPaper` and the duplicate-checkpoint database in `Paperflow\data\collection.sqlite3`. You can choose a different workspace with the folder picker.

## Terminal setup

Use the terminal setup when you want scripted, repeatable, or advanced runs.

Open PowerShell in the project folder and run:

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e .
```

For tests, install the test extra:

```powershell
python -m pip install -e ".[test]"
```

For the optional institutional browser workflow, install the browser extra and Chromium:

```powershell
python -m pip install -e ".[browser]"
python -m playwright install chromium
```

Create a `.env` file from the example when using the CLI:

```powershell
Copy-Item .env.example .env
```

Set `OPENALEX_API_KEY` in `.env`. The optional variables are `SEMANTIC_SCHOLAR_API_KEY` and `CROSSREF_MAILTO`.

### Start the dashboard from the terminal

With the virtual environment activated:

```powershell
paper-collector-ui
```

You can also start it without activating the environment:

```powershell
.\.venv\Scripts\paper-collector-ui.exe
```

### Run a collection from the CLI

The main command is:

```powershell
paper-collector full
```

`full` discovers papers, classifies them, downloads legitimate open-access PDFs, and stops when it reaches the configured target in `config.toml`. Use `--limit` for a smaller run:

```powershell
paper-collector full --limit 10
```

Other useful modes are:

```powershell
paper-collector test-openalex  # Verify that OpenAlex can be reached
paper-collector discover       # Discover and save records without downloading PDFs
paper-collector filter         # Reapply classification rules to saved records
paper-collector download       # Download PDFs for accepted records already in SQLite
paper-collector resume         # Retry saved candidates and continue discovery
paper-collector test-doi       # Resolve and classify the sample DOI
```

To try an institutional download for a specific DOI:

```powershell
paper-collector institutional-check --doi 10.1016/j.jallcom.2023.170773
```

Sign in yourself in the visible browser if prompted and use the publisher's ordinary PDF button. Paperflow will not bypass a CAPTCHA or security barrier.

## Output files

The default collection layout is:

```text
ResearchPaper\
  2022\
    Article title.pdf
    metadata.csv
  2023\
    Article title.pdf
    metadata.csv
  papers_without_pdf.csv
  subscription_candidates.csv
  manual_review.csv
  reports\
    collection_summary.txt
data\
  collection.sqlite3
logs\
  collector.log
```

Year folders contain validated PDFs and metadata for successfully downloaded papers. Records without an available open PDF remain in the CSV exports and do not count toward the free-PDF target. The SQLite database makes duplicate detection and resume operations possible.

OpenAlex cached-content downloads are limited to 80 attempts per UTC day by default to avoid unexpected API usage. This value can be changed with `openalex_content_downloads_per_day` in `config.toml` if required.
