# Song Download

Pull YouTube and SoundCloud links out of a Firefox bookmarks export and save them as MP3s.

Failed links (deleted videos, private tracks, bot blocks, etc.) go into a text file with the **bookmark title and URL**, so you can review them later.

## Requirements

- Python 3.8+
- [yt-dlp](https://github.com/yt-dlp/yt-dlp) (with EJS extras)
- [FFmpeg](https://ffmpeg.org/) — converts audio to MP3
- [Deno](https://deno.com/) — needed for YouTube’s JS challenges

### Install (Windows)

```powershell
pip install -U -r requirements.txt
winget install Gyan.FFmpeg
winget install DenoLand.Deno
```

Restart the terminal after installing FFmpeg / Deno so they’re on your PATH.

## YouTube login (important)

YouTube blocks anonymous downloads. Your Firefox profile currently needs a real YouTube login:

1. Open **Firefox** → go to [youtube.com](https://www.youtube.com) → **log in**
2. Re-run the downloader

Or close Chrome completely and use Chrome cookies:

```powershell
python song_downloader.py --cookies-from-browser chrome
```

## Usage

### GUI (recommended)

```powershell
python song_gui.py
```

1. Load your bookmarks HTML (auto-loads `bookmarks.html` if it’s in the same folder).
2. Tick the folders you want in the tree.
3. Click **Download selected**.

### Command line

Put your bookmarks export next to the script (or pass a path). Default file name: `bookmarks.html`.

#### Interactive

```powershell
python song_downloader.py
```

It asks for the bookmarks file, folder, and output directory.

#### Common commands

```powershell
# See every folder and how many songs it has
python song_downloader.py --list

# One sub-folder (example)
python song_downloader.py --folder "Songs/Songs Cont VII"

# Everything under Songs (all Cont I / II / VII / … included)
python song_downloader.py --folder Songs

# Explicit paths
python song_downloader.py --bookmarks bookmarks.html --folder "Songs/Songs Cont VII" --out downloads
```

Folder names match your bookmark tree. In this export they’re like `Songs Cont VII`, not `Songs VII`. Use `--list` or the GUI tree if you’re unsure.

## What you get

| Output | Meaning |
| --- | --- |
| `downloads/*.mp3` | Downloaded tracks (192 kbps, title as filename) |
| `downloads/failed_links.txt` | Failures as `Title -- URL` |
| `downloads/.downloaded.txt` | Resume archive — re-runs skip songs you already have |

## Options

| Flag | What it does |
| --- | --- |
| `--bookmarks PATH` | Firefox bookmarks `.html` export |
| `--folder PATH` | Folder to download, e.g. `Songs` or `Songs/Songs Cont VII` |
| `--out DIR` | Where to save MP3s (default: `downloads`) |
| `--failures PATH` | Where to write the failure list |
| `--cookies-from-browser NAME` | Borrow cookies (`firefox`, `chrome`, `edge`, …). Use `none` to disable |
| `--cookies FILE` | Use a `cookies.txt` file instead |
| `--list` | List folders + song counts, then exit |
| `--yes` | Don’t ask questions; use defaults / flags |
| `--skip-preflight` | Don’t stop early if a YouTube probe fails |

## Notes

- Only **YouTube** and **SoundCloud** links are downloaded; other bookmarks are ignored.
- Duplicate URLs (e.g. the same song under Trash and the live toolbar) are downloaded once.
- SoundCloud often works without browser cookies; YouTube usually does not.
- Prefer downloading music you have the right to keep / personal backups of links you already bookmarked.
