#!/usr/bin/env python3
"""
song_downloader.py
------------------
Reads a Firefox bookmarks .html export, finds every YouTube / SoundCloud link
inside the folder you choose, and downloads each one as an MP3.

Anything that fails (video removed, track deleted, private, geo-blocked, ...)
gets written to a plain text file with its title + URL so you can review later.

Stupid-simple to run:

    python song_downloader.py

...and it will ask you three questions (with sensible defaults). Or pass them
on the command line:

    python song_downloader.py --bookmarks bookmarks.html --folder "Songs/Songs Cont VII" --out downloads

Handy extras:
    python song_downloader.py --list                 # just show every folder + how many songs are in it
    python song_downloader.py --folder Songs          # grabs EVERYTHING under "Songs" (all sub-folders too)

YouTube tip (important):
    YouTube blocks anonymous downloads. Log into youtube.com in Firefox once,
    then re-run. Or close Chrome and use:  --cookies-from-browser chrome

Requirements:
    - Python 3.8+
    - yt-dlp + ejs  ->  pip install -U "yt-dlp[default]"
    - FFmpeg        ->  winget install Gyan.FFmpeg
    - Deno          ->  winget install DenoLand.Deno   (YouTube JS challenges)
"""

import argparse
import html
import os
import re
import shutil
import sys
from urllib.parse import urlparse

try:
    import yt_dlp
except ImportError:
    sys.exit(
        "yt-dlp is not installed.\n"
        'Install it with:  pip install -U "yt-dlp[default]"'
    )


# ----------------------------------------------------------------------------
# Defaults you can tweak if you don't feel like typing them each time.
# ----------------------------------------------------------------------------
DEFAULT_BOOKMARKS = "bookmarks.html"
DEFAULT_FOLDER = "Songs"          # folder (or folder path) to pull from
DEFAULT_OUTDIR = "downloads"
MP3_QUALITY = "192"               # kbps. 192 = plenty for a portable player.

# YouTube now often says "Sign in to confirm you're not a bot" for anonymous
# downloads. yt-dlp can borrow the cookies from your browser to get past that.
# You use Firefox (these are Firefox bookmarks!), so we default to "firefox".
# Set to "" to disable, or use "chrome", "edge", "brave", etc.
DEFAULT_COOKIES_BROWSER = "firefox"

BOT_HELP = (
    "\nYouTube is blocking the download (bot / login check).\n"
    "Fix it like this, then re-run:\n"
    "  1. Open Firefox, go to youtube.com, and LOG IN.\n"
    "  2. Or close Chrome completely and run with:\n"
    "       --cookies-from-browser chrome\n"
)

# Domains we care about.
SITE_HOSTS = (
    "youtube.com",
    "youtu.be",
    "music.youtube.com",
    "soundcloud.com",
    "snd.sc",
)


# ----------------------------------------------------------------------------
# 1. Parsing the Firefox bookmark file
# ----------------------------------------------------------------------------
# A Netscape bookmark file looks like:
#
#   <DT><H3>Folder name</H3>
#   <DL><p>
#       <DT><A HREF="http://...">Title</A>
#       <DT><H3>Sub folder</H3>
#       <DL><p>
#           ...
#       </DL><p>
#   </DL><p>
#
# So: <H3> names the folder that the *next* <DL> will contain, and </DL> closes
# the most recently opened folder. We walk the tokens in order and keep a stack.

_TOKEN_RE = re.compile(
    r'<H3[^>]*>(?P<h3>.*?)</H3>'                       # a folder header
    r'|<A\s+[^>]*HREF="(?P<href>[^"]*)"[^>]*>(?P<title>.*?)</A>'  # a bookmark
    r'|(?P<dl_open><DL\b)'                             # open a folder scope
    r'|(?P<dl_close></DL>)',                           # close a folder scope
    re.IGNORECASE | re.DOTALL,
)


def parse_bookmarks(path):
    """Return a list of dicts: {'folders': [names...], 'url': str, 'title': str}."""
    with open(path, "r", encoding="utf-8", errors="replace") as fh:
        text = fh.read()

    links = []
    stack = []            # names of currently open folders (root first)
    pending_name = None   # the H3 name waiting for its <DL>

    for m in _TOKEN_RE.finditer(text):
        if m.group("h3") is not None:
            pending_name = clean_text(m.group("h3"))
        elif m.group("dl_open") is not None:
            # This <DL> belongs to the folder named by the last <H3>
            stack.append(pending_name)
            pending_name = None
        elif m.group("dl_close") is not None:
            if stack:
                stack.pop()
        elif m.group("href") is not None:
            folders = [name for name in stack if name]   # drop the None root
            links.append(
                {
                    "folders": folders,
                    "url": html.unescape(m.group("href")).strip(),
                    "title": clean_text(m.group("title")),
                }
            )
    return links


def clean_text(raw):
    """Strip tags/entities/whitespace from a bit of bookmark text."""
    if raw is None:
        return ""
    no_tags = re.sub(r"<[^>]+>", "", raw)
    return html.unescape(no_tags).strip()


# ----------------------------------------------------------------------------
# 2. Filtering
# ----------------------------------------------------------------------------
def folder_matches(link_folders, wanted):
    """
    True if `wanted` (a list of folder names) appears as consecutive folders in
    the link's folder path. This means:
        wanted = ["Songs"]                -> matches "Songs" and everything nested under it
        wanted = ["Songs", "Songs Cont"]  -> matches that sub-folder and its children
    Matching is case-insensitive and whitespace-tolerant.
    """
    if not wanted:
        return True  # no folder given -> everything

    lf = [f.casefold() for f in link_folders]
    w = [f.casefold() for f in wanted]
    for i in range(len(lf) - len(w) + 1):
        if lf[i : i + len(w)] == w:
            return True
    return False


def is_supported(url):
    try:
        host = urlparse(url).netloc.lower()
    except ValueError:
        return False
    host = host[4:] if host.startswith("www.") else host
    return any(host == h or host.endswith("." + h) for h in SITE_HOSTS)


def split_folder(folder_arg):
    """'Songs/Songs Cont VII' -> ['Songs', 'Songs Cont VII'] (also accepts '\\')."""
    if not folder_arg:
        return []
    parts = re.split(r"[\\/]+", folder_arg)
    return [p.strip() for p in parts if p.strip()]


def build_folder_counts(supported_links):
    """
    Build per-folder song counts for the GUI tree.

    Returns (own_counts, total_counts) where keys are folder-path tuples:
      own_counts[path]   = songs sitting exactly in that folder
      total_counts[path] = songs in that folder or any nested sub-folder
    """
    from collections import defaultdict

    own = defaultdict(int)
    for link in supported_links:
        own[tuple(link["folders"])] += 1

    total = defaultdict(int)
    for path, n in own.items():
        # Empty tuple = top-level; always count toward root as well.
        total[()] += n
        for i in range(1, len(path) + 1):
            total[path[:i]] += n
    return dict(own), dict(total)


def links_under_folders(supported_links, selected_paths):
    """
    Pick songs that live under any of the selected folder paths (prefix match).
    selected_paths: iterable of folder-path tuples, e.g. (("Songs",), ("Songs", "Songs Cont VII"))
    De-duplicates by URL. Empty tuple () means "everything".
    """
    selected = [tuple(p) for p in selected_paths if p is not None]
    if not selected:
        return []

    chosen = []
    seen = set()
    for link in supported_links:
        folders = tuple(link["folders"])
        matched = False
        for sel in selected:
            if len(sel) == 0 or folders[: len(sel)] == sel:
                matched = True
                break
        if not matched or link["url"] in seen:
            continue
        seen.add(link["url"])
        chosen.append(link)
    return chosen


# ----------------------------------------------------------------------------
# 3. FFmpeg / Deno discovery (works even before a shell restart)
# ----------------------------------------------------------------------------
def _find_exe(name):
    """Find an .exe by PATH, then by walking the winget packages folder."""
    exe = shutil.which(name) or shutil.which(f"{name}.exe")
    if exe:
        return exe

    base = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Microsoft", "WinGet", "Packages"
    )
    if os.path.isdir(base):
        target = f"{name}.exe"
        for root, _dirs, files in os.walk(base):
            if target in files:
                return os.path.join(root, target)
    return None


def find_ffmpeg_dir():
    exe = _find_exe("ffmpeg")
    return os.path.dirname(exe) if exe else None


def find_deno():
    return _find_exe("deno")


def is_bot_error(message):
    text = (message or "").lower()
    return "sign in to confirm" in text or "not a bot" in text


def base_ydl_opts(out_dir, ffmpeg_dir, cookies_browser, cookies_file, archive_path=None):
    """Shared yt-dlp options for downloads and the preflight check."""
    opts = {
        "format": "bestaudio/best",
        "outtmpl": os.path.join(out_dir, "%(title)s.%(ext)s"),
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "ignoreerrors": False,
        "retries": 3,
        "postprocessors": [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
                "preferredquality": MP3_QUALITY,
            }
        ],
    }
    if archive_path:
        opts["download_archive"] = archive_path
    if ffmpeg_dir:
        opts["ffmpeg_location"] = ffmpeg_dir

    deno = find_deno()
    if deno:
        opts["js_runtimes"] = {"deno": {"path": deno}}

    if cookies_file:
        opts["cookiefile"] = cookies_file
    elif cookies_browser:
        opts["cookiesfrombrowser"] = (cookies_browser,)
    return opts


# ----------------------------------------------------------------------------
# 4. Downloading
# ----------------------------------------------------------------------------
def download_all(
    items,
    out_dir,
    ffmpeg_dir,
    failures_path,
    cookies_browser,
    cookies_file,
    on_progress=None,
    should_cancel=None,
):
    """
    Download items as MP3s.

    on_progress(i, total, title, status, detail="")
        status is one of: "start", "saved", "skipped", "failed", "cancelled"
    should_cancel() -> bool
        polled between items; if True, stop early.
    """
    os.makedirs(out_dir, exist_ok=True)
    archive_path = os.path.join(out_dir, ".downloaded.txt")  # lets you re-run to resume

    ydl_opts = base_ydl_opts(out_dir, ffmpeg_dir, cookies_browser, cookies_file, archive_path)

    # Tiny hook so we can tell "downloaded" apart from "already had it".
    state = {"downloaded": False}

    def hook(d):
        if d.get("status") == "finished":
            state["downloaded"] = True

    ydl_opts["progress_hooks"] = [hook]

    ok = 0
    failed = 0
    skipped = 0
    bot_hits = 0
    total = len(items)

    def emit(i, title, status, detail=""):
        if on_progress:
            on_progress(i, total, title, status, detail)
        elif status == "start":
            print(f"[{i}/{total}] {title}")
        elif status == "saved":
            print("        saved")
        elif status == "skipped":
            print("        already have it (skipped)")
        elif status == "failed":
            print(f"        FAILED: {detail}")
        elif status == "cancelled":
            print("        cancelled")

    # Fresh failures log each run.
    with open(failures_path, "w", encoding="utf-8") as failog:
        failog.write("# Links that could not be downloaded (title -- URL)\n")

    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        for i, item in enumerate(items, 1):
            if should_cancel and should_cancel():
                emit(i, item.get("title") or item["url"], "cancelled")
                break

            url = item["url"]
            title = item["title"] or url
            emit(i, title, "start")
            state["downloaded"] = False
            try:
                ydl.download([url])
                ok += 1
                if state["downloaded"]:
                    emit(i, title, "saved")
                else:
                    skipped += 1
                    emit(i, title, "skipped")
            except Exception as exc:  # noqa: BLE001 - keep going no matter what
                failed += 1
                reason = str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__
                emit(i, title, "failed", reason)
                if is_bot_error(reason):
                    bot_hits += 1
                with open(failures_path, "a", encoding="utf-8") as failog:
                    failog.write(f"{title} -- {url}\n")

    return ok, failed, skipped, bot_hits


def preflight_youtube(sample_url, out_dir, ffmpeg_dir, cookies_browser, cookies_file):
    """Quick probe: can we extract a YouTube URL at all? Returns (ok, message)."""
    opts = base_ydl_opts(out_dir, ffmpeg_dir, cookies_browser, cookies_file)
    opts["skip_download"] = True
    opts.pop("postprocessors", None)
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(sample_url, download=False)
        title = (info or {}).get("title") or "ok"
        return True, title
    except Exception as exc:  # noqa: BLE001
        return False, str(exc).splitlines()[0] if str(exc) else exc.__class__.__name__


# ----------------------------------------------------------------------------
# 5. Glue / CLI
# ----------------------------------------------------------------------------
def ask(prompt, default):
    try:
        reply = input(f"{prompt} [{default}]: ").strip()
    except EOFError:
        reply = ""
    return reply or default


def main():
    # Bookmark titles can contain any Unicode; make sure printing them never
    # crashes on a legacy (cp1252) Windows console.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(
        description="Download YouTube/SoundCloud songs from a Firefox bookmark folder as MP3s."
    )
    ap.add_argument("--bookmarks", help="Path to the Firefox bookmarks .html file.")
    ap.add_argument("--folder", help='Folder to pull from, e.g. "Songs" or "Songs/Songs Cont VII".')
    ap.add_argument("--out", help="Where to save the MP3s.")
    ap.add_argument("--failures", help="Where to write the list of failed links.")
    ap.add_argument("--cookies-from-browser", dest="cookies_browser",
                    help='Browser to borrow cookies from (gets past YouTube\'s bot check). '
                         f'Default "{DEFAULT_COOKIES_BROWSER}". Use "none" to disable.')
    ap.add_argument("--cookies", dest="cookies_file",
                    help="Path to a cookies.txt file (alternative to --cookies-from-browser).")
    ap.add_argument("--list", action="store_true",
                    help="Just list every folder and how many songs it holds, then exit.")
    ap.add_argument("--yes", action="store_true",
                    help="Don't ask questions; use defaults / provided flags.")
    ap.add_argument("--skip-preflight", action="store_true",
                    help="Don't stop early if a YouTube probe fails.")
    args = ap.parse_args()

    # Resolve the bookmarks file.
    bookmarks = args.bookmarks
    if not bookmarks and not args.yes:
        bookmarks = ask("Bookmarks file", DEFAULT_BOOKMARKS)
    bookmarks = bookmarks or DEFAULT_BOOKMARKS

    if not os.path.isfile(bookmarks):
        sys.exit(f"Can't find the bookmarks file: {bookmarks}")

    all_links = parse_bookmarks(bookmarks)
    supported = [l for l in all_links if is_supported(l["url"])]

    # --list: show the map of folders and bail.
    if args.list:
        print_folder_overview(supported)
        return

    # Resolve the folder.
    folder_arg = args.folder
    if folder_arg is None and not args.yes:
        folder_arg = ask("Folder to download (blank = ALL folders)", DEFAULT_FOLDER)
    if folder_arg is None:
        folder_arg = DEFAULT_FOLDER
    wanted = split_folder(folder_arg)

    # Resolve output + failures paths.
    out_dir = args.out or (DEFAULT_OUTDIR if args.yes else ask("Save MP3s to", DEFAULT_OUTDIR))
    failures_path = args.failures or os.path.join(out_dir, "failed_links.txt")

    # Filter down to what we'll actually download (de-duplicated by URL).
    chosen = []
    seen = set()
    for link in supported:
        if not folder_matches(link["folders"], wanted):
            continue
        if link["url"] in seen:
            continue
        seen.add(link["url"])
        chosen.append(link)

    if not chosen:
        print(f'\nNo YouTube/SoundCloud links found under "{folder_arg}".')
        print("Here are the folders that DO contain songs:\n")
        print_folder_overview(supported)
        return

    yt = sum(1 for c in chosen if "soundcloud" not in c["url"] and "snd.sc" not in c["url"])
    sc = len(chosen) - yt
    where = folder_arg if wanted else "ALL folders"
    print(f'\nFound {len(chosen)} songs under "{where}"  ({yt} YouTube, {sc} SoundCloud).')

    cookies_browser = args.cookies_browser if args.cookies_browser is not None else DEFAULT_COOKIES_BROWSER
    if cookies_browser.strip().lower() in ("", "none", "no", "off"):
        cookies_browser = ""
    cookies_file = args.cookies_file or ""
    if cookies_file and not os.path.isfile(cookies_file):
        sys.exit(f"Can't find cookies file: {cookies_file}")

    ffmpeg_dir = find_ffmpeg_dir()
    if not ffmpeg_dir:
        print(
            "\nWARNING: FFmpeg was not found, so MP3 conversion will fail.\n"
            "Install it with:  winget install Gyan.FFmpeg   (then restart the shell)\n"
        )
    if not find_deno():
        print(
            "\nWARNING: Deno was not found. YouTube downloads may fail.\n"
            "Install it with:  winget install DenoLand.Deno   (then restart the shell)\n"
        )

    # Quick YouTube probe so we don't burn through hundreds of bot-blocked links.
    sample_yt = next(
        (c["url"] for c in chosen if "soundcloud" not in c["url"] and "snd.sc" not in c["url"]),
        None,
    )
    if sample_yt and not args.skip_preflight:
        print("Checking that YouTube downloads work...")
        ok_probe, probe_msg = preflight_youtube(
            sample_yt, out_dir, ffmpeg_dir, cookies_browser, cookies_file
        )
        if not ok_probe:
            print(f"YouTube probe failed: {probe_msg}")
            if is_bot_error(probe_msg):
                print(BOT_HELP)
            if not args.yes:
                go_anyway = ask("Continue anyway? SoundCloud may still work (y/n)", "n").lower()
                if not go_anyway.startswith("y"):
                    print("Okay, stopped. Fix YouTube cookies, then re-run.")
                    return
            else:
                print("Continuing anyway (--yes). Failed YouTube links will be logged.")

    if not args.yes:
        go = ask("Start downloading? (y/n)", "y").lower()
        if not go.startswith("y"):
            print("Okay, stopped. Nothing was downloaded.")
            return

    ok, failed, skipped, bot_hits = download_all(
        chosen, out_dir, ffmpeg_dir, failures_path, cookies_browser, cookies_file
    )

    print("\n" + "-" * 48)
    fresh = ok - skipped
    print(f"Done. {fresh} new, {skipped} already had, {failed} failed.")
    print(f"MP3s saved in : {os.path.abspath(out_dir)}")
    if failed:
        print(f"Failed links  : {os.path.abspath(failures_path)}")
    else:
        print("No failures. Nice.")
    if bot_hits:
        print(BOT_HELP)


def print_folder_overview(supported_links):
    """Print each folder path that contains songs, with a count."""
    from collections import Counter

    counts = Counter()
    for link in supported_links:
        path = " / ".join(link["folders"]) if link["folders"] else "(top level)"
        counts[path] += 1

    if not counts:
        print("No YouTube/SoundCloud bookmarks found anywhere in this file.")
        return

    width = max(len(p) for p in counts)
    for path in sorted(counts):
        print(f"  {path.ljust(width)}   {counts[path]:>4} songs")
    print(f"\nTotal: {sum(counts.values())} songs across {len(counts)} folders.")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped.")
