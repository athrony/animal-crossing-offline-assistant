# Animal Crossing Offline Assistant

一个为《集合啦！动物森友会》准备的本地离线助手，支持物品查询、离线百科、设计图浏览与下载。

An offline desktop helper for Animal Crossing: New Horizons, focused on item lookup, encyclopedia browsing, and pattern collection management.

## 中文说明

这个项目现在已经不依赖 `items.csv` 直接运行。

仓库里自带：

- SQLite 本地数据库
- 清理过的物品表，去掉了空名称条目
- 离线百科数据
- NHSE 提供的物品图标和村民头像资源
- 设计图索引、下载与导入功能
- Windows EXE 打包产物

当前主要功能：

- 物品搜索、分类筛选、中英文对照
- 离线百科浏览
- 左侧菜单切换页面、右侧卡片式内容区
- 设计图网站索引浏览
- 下载 `.nhd`、`.acnl`、QR PNG
- 导入本地图案文件
- 一键下载并启动 NHSE
- 一键下载并启动 ACNHDesignPatternEditor
- 一键同步整个 ACNH Pattern Dump Index 到本地镜像后离线浏览

主要文件：

- `app.py`：桌面应用主程序
- `build_database.py`：数据库构建器
- `pattern_support.py`：设计图索引与下载管理
- `build.ps1`：重建数据库并打包 EXE
- `data/animal_crossing_offline.db`：运行数据库
- `dist/ItemsBilingualViewer.exe`：打包好的 EXE

本地运行：

```powershell
py -3.11 app.py
```

重建数据库：

```powershell
py -3.11 build_database.py --csv items.csv --output-dir data
```

自检：

```powershell
py -3.11 app.py --self-test
```

打包：

```powershell
powershell -ExecutionPolicy Bypass -File .\build.ps1
```

## English

This project no longer depends on `items.csv` at runtime.

It ships with:

- a bundled SQLite database
- a cleaned item table with empty-name rows removed
- offline encyclopedia content
- NHSE-derived item icons and villager portraits
- built-in pattern browsing, download, and import support
- a packaged Windows EXE

Key features:

- item search with bilingual names
- encyclopedia browsing
- sidebar navigation with page switching
- pattern index browsing
- `.nhd`, `.acnl`, and QR image downloads
- local pattern import support
- one-click download and launch for NHSE
- one-click download and launch for ACNHDesignPatternEditor
- full local mirror sync for ACNH Pattern Dump Index

## Data Sources

- Local `items.csv`
- Lightweight cached Nookipedia-derived knowledge data
- NHSE text and sprite assets: https://github.com/kwsch/NHSE
- ACNHDesignPatternEditor reference project: https://github.com/FluffyFishGames/ACNHDesignPatternEditor
- ACNH Pattern Dump Index: https://www.vectorcmdr.xyz/ACNH-Pattern-Dump-Index/

## License

This repository is distributed under GPL-3.0 because it includes and derives data/assets from GPL-3.0 projects such as NHSE and ACNHDesignPatternEditor.
