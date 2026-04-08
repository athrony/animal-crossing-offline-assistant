# Animal Crossing Offline Assistant

A local desktop helper for browsing `items.csv`, comparing English and Chinese names, and caching offline Animal Crossing reference data from Nookipedia.

## Features

- Search by English, Chinese, item ID, category, and type
- Filter by `category_zh` and `item_kind`
- One-click copy for English and Chinese names
- Offline encyclopedia sections for villagers, art, DIY recipes, fossils, and events
- On-demand item caching for icons and wiki summaries
- Windows EXE packaging with PyInstaller

## Expected Local Data

The app will look for `items.csv` in this order:

1. Next to the EXE
2. `items.csv`
3. The current working directory

Offline cache defaults to:

`offline_cache/`

## Run Locally

```powershell
py -3.11 app.py
```

## Build Lightweight Offline Encyclopedia Cache

```powershell
py -3.11 app.py --csv items.csv --sync-wiki
```

This sync keeps the built-in encyclopedia lightweight.
Large item records are cached on demand inside the app with the `缓存当前词条` button.

## Self Test

```powershell
py -3.11 app.py --csv items.csv --self-test
```

## Build EXE

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

Output:

- `dist/ItemsBilingualViewer.exe`
- `dist/offline_cache/`

## Data Source

- Nookipedia wiki: https://nookipedia.com/wiki/Main_Page
- Nookipedia API docs: https://api.nookipedia.com/doc

This project stores only local cache files for offline viewing.
