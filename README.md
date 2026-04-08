# Animal Crossing Offline Assistant

A simplified local desktop helper for Animal Crossing item lookup and offline encyclopedia browsing.

## What Changed

This project no longer runs from `items.csv`.

It now ships with:

- a bundled SQLite database
- a simplified item table with empty-name rows removed
- offline encyclopedia entries stored in the database
- bundled local image assets in `data/images/`
- NHSE-derived item menu icons and villager portraits
- a built-in ACNH pattern browser and local pattern library

## Main Files

- `app.py`: database-driven desktop app
- `build_database.py`: converts `items.csv` + lightweight cache into SQLite
- `build_database.py`: converts `items.csv` + lightweight cache + NHSE assets into SQLite
- `pattern_support.py`: parses the ACNH Pattern Dump Index and manages downloaded/imported patterns
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
- NHSE text and sprite assets: https://github.com/kwsch/NHSE
- ACNHDesignPatternEditor reference project: https://github.com/FluffyFishGames/ACNHDesignPatternEditor
- ACNH Pattern Dump Index: https://www.vectorcmdr.xyz/ACNH-Pattern-Dump-Index/

## Pattern Browser

The app includes a `设计图` tab that can:

- browse the ACNH Pattern Dump Index
- search by title, creator, type, and tags
- preview selected patterns
- download `.nhd`, `.acnl`, and QR PNG files
- import local `.nhd`, `.acnl`, and `.png` files into a local library

The bundled database is preloaded with the pattern site index during `build.ps1`.

## Repository Note

This repository intentionally tracks the bundled database, NHSE-enriched image assets, and the packaged EXE.

## License

This project is distributed under GPL-3.0 because it includes and derives data/assets from GPL-3.0 projects:

- NHSE: https://github.com/kwsch/NHSE
- ACNHDesignPatternEditor reference project: https://github.com/FluffyFishGames/ACNHDesignPatternEditor
