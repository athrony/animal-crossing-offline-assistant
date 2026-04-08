# Animal Crossing Offline Assistant

A simplified local desktop helper for Animal Crossing item lookup and offline encyclopedia browsing.

## What Changed

This project no longer runs from `items.csv`.

It now ships with:

- a bundled SQLite database
- a simplified item table with empty-name rows removed
- offline encyclopedia entries stored in the database
- optional local image assets in `data/images/`

## Main Files

- `app.py`: database-driven desktop app
- `build_database.py`: converts `items.csv` + lightweight cache into SQLite
- `build.ps1`: rebuilds the database and packages the EXE
- `data/animal_crossing_offline.db`: bundled runtime database
- `dist/ItemsBilingualViewer.exe`: packaged Windows executable

## Run Locally

```powershell
py -3.11 app.py
```

The app looks for the database in:

1. `data/animal_crossing_offline.db` next to the app
2. `animal_crossing_offline.db` next to the app
3. the same two locations under the current working directory

## Rebuild The Database

```powershell
py -3.11 build_database.py --csv items.csv --output-dir data
```

## Self Test

```powershell
py -3.11 app.py --self-test
```

## Build EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Build output:

- `dist/ItemsBilingualViewer.exe`
- `dist/data/animal_crossing_offline.db`

## Data Source

- Local `items.csv`
- Lightweight cached Nookipedia-derived offline knowledge data

## Repository Note

This repository intentionally tracks both the bundled database and the packaged EXE.
