import ctypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import webbrowser
import zipfile
from difflib import get_close_matches
from pathlib import Path
from typing import Any, Dict, List, Optional, Union
from urllib.parse import quote

import requests
import tkinter as tk
from tkinter import ttk, messagebox
from bs4 import BeautifulSoup

# --- Constants ---
MANIFEST_HUB_URL = "https://codeload.github.com/SSMGAlt/ManifestHub2/zip/refs/heads/{}"
STEAM_API_APP_LIST = "https://api.steampowered.com/ISteamApps/GetAppList/v2/"
STEAM_STORE_SEARCH = "https://store.steampowered.com/search/?term={}"
STEAM_APP_DETAILS = "https://store.steampowered.com/api/appdetails?appids={}"

APPID_URL_PATTERNS = [
    r'/app/(\d+)',
    r'app/(\d+)',
    r'AppId=(\d+)',
    r'id=(\d+)',
]

BROWSER_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
    'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
    'Accept-Language': 'en-US,en;q=0.5',
    'Accept-Encoding': 'gzip, deflate, br',
    'Connection': 'keep-alive',
}


def resource_path(relative_path: str) -> str:
    """Resolve path for both development and PyInstaller bundled environments."""
    try:
        base = sys._MEIPASS
    except AttributeError:
        base = os.path.abspath(".")
    return os.path.join(base, relative_path)


class SteamWebSearch:
    """Handles searching Steam store for games using web scraping."""

    def __init__(self):
        self.search_cache: Dict[str, List[Dict[str, Any]]] = {}

    def search_steam_store(self, query: str) -> List[Dict[str, Any]]:
        """Search Steam store for games by name.

        Returns list of dicts with 'name', 'appid', and 'url' keys.
        """
        if query in self.search_cache:
            return self.search_cache[query]

        try:
            url = STEAM_STORE_SEARCH.format(quote(query))
            response = requests.get(url, headers=BROWSER_HEADERS, timeout=15)
            response.raise_for_status()

            soup = BeautifulSoup(response.content, 'html.parser')
            results: List[Dict[str, Any]] = []

            for row in soup.find_all('a', {'data-ds-appid': True})[:10]:
                try:
                    appid = row.get('data-ds-appid', '').split(',')[0]
                    if not appid.isdigit():
                        continue

                    title_span = row.find('span', class_='title')
                    if not title_span:
                        continue

                    results.append({
                        'name': title_span.text.strip(),
                        'appid': int(appid),
                        'url': row.get('href', ''),
                    })
                except (AttributeError, ValueError, IndexError):
                    continue

            # Fallback: scan all links for /app/<id>/ patterns
            if not results:
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    match = re.search(r'/app/(\d+)/', href)
                    if match:
                        appid = match.group(1)
                        name = link.text.strip()
                        if name and appid.isdigit():
                            results.append({
                                'name': name[:100],
                                'appid': int(appid),
                                'url': href if href.startswith('http')
                                       else f'https://store.steampowered.com{href}',
                            })

            # Deduplicate by appid
            seen: set = set()
            unique_results = []
            for r in results:
                if r['appid'] not in seen:
                    unique_results.append(r)
                    seen.add(r['appid'])

            self.search_cache[query] = unique_results
            return unique_results

        except Exception as e:
            print(f"Error searching Steam store: {e}")
            return []

    def extract_appid_from_url(self, url: str) -> Optional[int]:
        """Extract App ID from any Steam URL."""
        try:
            for pattern in APPID_URL_PATTERNS:
                match = re.search(pattern, url)
                if match and match.group(1).isdigit():
                    return int(match.group(1))
        except Exception as e:
            print(f"Error extracting App ID from URL: {e}")
        return None


class SteamToolsDownloader:
    """Handles Steam game downloading and installation logic."""

    def __init__(self):
        self.games_cache: Dict[str, int] = {}
        self.steamtools_exe = self.find_steamtools_exe()
        self._steam_folder: Optional[Path] = None
        self.web_searcher = SteamWebSearch()

    def find_steamtools_exe(self) -> Optional[Path]:
        """Find SteamTools executable in common installation paths."""
        search_roots = [
            Path.home() / "AppData" / "Local" / "SteamTools",
            Path.home() / "AppData" / "Roaming" / "SteamTools",
            Path("C:/Program Files/SteamTools"),
            Path("C:/Program Files (x86)/SteamTools"),
        ]
        for root in search_roots:
            if root.exists():
                for exe in root.rglob("SteamTools.exe"):
                    return exe
        return None

    def get_app_list(self) -> Dict[str, int]:
        """Fetch and cache the full Steam app list."""
        if not self.games_cache:
            try:
                response = requests.get(STEAM_API_APP_LIST, timeout=15)
                apps = response.json()['applist']['apps']
                self.games_cache = {app['name'].lower(): app['appid'] for app in apps}
            except Exception as e:
                print(f"Error fetching app list: {e}")
        return self.games_cache

    def find_steam_folder(self) -> Optional[Path]:
        """Find Steam installation folder automatically."""
        if self._steam_folder:
            return self._steam_folder

        candidates = [
            Path(os.environ.get('PROGRAMFILES(X86)', 'C:\\Program Files (x86)')) / 'Steam',
            Path(os.environ.get('PROGRAMFILES', 'C:\\Program Files')) / 'Steam',
            Path('C:\\Program Files (x86)\\Steam'),
            Path('C:\\Program Files\\Steam'),
        ]
        for path in candidates:
            if path.exists():
                self._steam_folder = path
                return path
        return None

    def find_game(self, query: str) -> Union[int, List[Dict[str, Any]], None]:
        """Find a game by name, App ID, or Steam URL.

        Returns:
            int  — single App ID when the match is unambiguous
            list — multiple candidates for user selection
            None — no match found
        """
        if 'store.steampowered.com' in query or 'steamcommunity.com' in query:
            appid = self.web_searcher.extract_appid_from_url(query)
            if appid:
                return appid

        if query.isdigit():
            return int(query)

        web_results = self.web_searcher.search_steam_store(query)
        if web_results:
            return web_results[0]['appid'] if len(web_results) == 1 else web_results

        # API fallback with fuzzy matching
        games = self.get_app_list()
        if not games:
            return None

        query_lower = query.lower()
        if query_lower in games:
            return games[query_lower]

        matches = get_close_matches(query_lower, games.keys(), n=5, cutoff=0.7)
        if matches:
            return [{'name': m, 'appid': games[m]} for m in matches]

        return None

    def get_app_details(self, app_id: int) -> Optional[Dict[str, Any]]:
        """Get detailed app information from Steam Store API."""
        try:
            response = requests.get(STEAM_APP_DETAILS.format(app_id), timeout=10)
            data = response.json()
            entry = data.get(str(app_id), {})
            if entry.get('success'):
                return entry['data']
        except Exception as e:
            print(f"Error fetching app details: {e}")
        return None

    def download_appid_zip(self, app_id: int, output_dir: str = "downloads",
                           log_callback=None) -> bool:
        """Download and extract game data from ManifestHub2."""
        def log(msg):
            if log_callback:
                log_callback(msg)

        log(f"[2/5] Downloading {app_id}.zip from server storage...")

        output_path = Path(output_dir)
        output_path.mkdir(parents=True, exist_ok=True)
        zip_path = output_path / f"{app_id}.zip"

        try:
            response = requests.get(MANIFEST_HUB_URL.format(app_id), timeout=30, stream=True)
            if response.status_code == 404:
                log(f"No data found for App ID {app_id}")
                return False
            response.raise_for_status()

            with open(zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            log(f"Downloaded: {zip_path.name}")
            log("Extracting...")

            with zipfile.ZipFile(zip_path, 'r') as zf:
                zf.extractall(output_dir)

            log("Extracted successfully")
            zip_path.unlink()
            return True

        except Exception as e:
            log(f"Error during download/extraction: {e}")
            return False

    def copy_files_to_steam(self, source_dir: str = "downloads", log_callback=None) -> bool:
        """Copy .lua/.st files to stplug-in and .manifest files to depotcache."""
        def log(msg):
            if log_callback:
                log_callback(msg)

        source = Path(source_dir)
        lua_files = list(source.rglob("*.lua"))
        st_files = list(source.rglob("*.st"))
        manifest_files = list(source.rglob("*.manifest"))

        if not lua_files and not st_files and not manifest_files:
            log("No files found to copy.")
            return False

        log("\n[3/5] Copying files to Steam...")

        steam_folder = self.find_steam_folder()
        if not steam_folder:
            log("\nCould not find Steam installation.")
            return False

        stplug_folder = steam_folder / 'config' / 'stplug-in'
        depotcache_folder = steam_folder / 'depotcache'
        stplug_folder.mkdir(parents=True, exist_ok=True)
        depotcache_folder.mkdir(parents=True, exist_ok=True)

        plugin_files = lua_files + st_files
        if plugin_files:
            log("\nCopying plugin file(s) to config/stplug-in...")
            for fp in plugin_files:
                try:
                    shutil.copy2(fp, stplug_folder / fp.name)
                except Exception as e:
                    log(f"  ✗ Failed: {e}")

        if manifest_files:
            log("\nCopying manifest file(s) to depotcache...")
            for fp in manifest_files:
                try:
                    shutil.copy2(fp, depotcache_folder / fp.name)
                except Exception as e:
                    log(f"  ✗ Failed: {e}")

        log("\n[4/5] Cleaning up...")
        try:
            shutil.rmtree(source)
            log("✓ Deleted temporary files")
        except Exception as e:
            log(f"⚠ Could not delete downloads folder: {e}")

        return True

    def close_steam(self, log_callback=None) -> bool:
        """Force-close Steam."""
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'steam.exe'],
                           capture_output=True, timeout=10)
            time.sleep(1)
            if log_callback:
                log_callback("✓ Steam closed")
            return True
        except Exception as e:
            if log_callback:
                log_callback(f"⚠ Could not close Steam: {e}")
            return False

    def start_steam(self, log_callback=None) -> bool:
        """Launch Steam."""
        steam_folder = self.find_steam_folder()
        if not steam_folder:
            return False

        steam_exe = steam_folder / 'steam.exe'
        if not steam_exe.exists():
            return False

        try:
            subprocess.Popen([str(steam_exe)], shell=True)
            time.sleep(1)
            if log_callback:
                log_callback("✓ Steam started")
            return True
        except Exception as e:
            if log_callback:
                log_callback(f"⚠ Could not start Steam: {e}")
            return False

    def launch_steamtools(self, log_callback=None) -> bool:
        """Launch SteamTools.exe."""
        if not self.steamtools_exe:
            self.steamtools_exe = self.find_steamtools_exe()

        if not self.steamtools_exe or not self.steamtools_exe.exists():
            if log_callback:
                log_callback("⚠ SteamTools.exe not found. Skipping launch.")
            return False

        try:
            subprocess.Popen([str(self.steamtools_exe)], shell=True)
            time.sleep(2)
            if log_callback:
                log_callback("✓ SteamTools launched")
            return True
        except Exception as e:
            if log_callback:
                log_callback(f"⚠ Could not launch SteamTools: {e}")
            return False


class ModernButton(tk.Canvas):
    """Custom styled button with hover effects and rounded corners."""

    def __init__(self, parent, text, command, **kwargs):
        super().__init__(parent, highlightthickness=0, **kwargs)
        self.command = command
        self.text = text

        self.bg_normal = "#5c7cfa"
        self.bg_hover = "#4c6ef5"
        self.bg_active = "#3b5bdb"
        self.fg_color = "#ffffff"

        self.rect = None
        self.text_id = None
        self.is_enabled = True

        self.bind("<Button-1>", self.on_click)
        self.bind("<Enter>", self.on_enter)
        self.bind("<Leave>", self.on_leave)

        self.draw()

    def configure_state(self, enabled: bool):
        """Enable or disable the button."""
        self.is_enabled = enabled
        fill = self.bg_normal if enabled else "#6c757d"
        self.itemconfig(self.rect, fill=fill)

    def draw(self):
        """Draw the button with rounded corners."""
        self.delete("all")
        w = self.winfo_reqwidth()
        h = self.winfo_reqheight()
        self.rect = self.create_rounded_rect(0, 0, w, h, 10,
                                             fill=self.bg_normal, outline="")
        self.text_id = self.create_text(w // 2, h // 2, text=self.text,
                                        fill=self.fg_color,
                                        font=("Segoe UI", 11, "bold"))

    def create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """Create a rounded rectangle using a smooth polygon."""
        points = [
            x1 + radius, y1, x2 - radius, y1, x2, y1,
            x2, y1 + radius, x2, y2 - radius, x2, y2,
            x2 - radius, y2, x1 + radius, y2, x1, y2,
            x1, y2 - radius, x1, y1 + radius, x1, y1,
        ]
        return self.create_polygon(points, smooth=True, **kwargs)

    def on_enter(self, _):
        if self.is_enabled:
            self.itemconfig(self.rect, fill=self.bg_hover)

    def on_leave(self, _):
        if self.is_enabled:
            self.itemconfig(self.rect, fill=self.bg_normal)

    def on_click(self, _):
        if not self.is_enabled:
            return
        self.itemconfig(self.rect, fill=self.bg_active)
        self.after(100, lambda: self.itemconfig(self.rect, fill=self.bg_hover))
        if self.command:
            self.command()


class SteamToolsInstaller:
    """Main GUI application for Steam Tools installation."""

    BG = "#1a1b26"
    CARD = "#24283b"
    TEXT = "#c0caf5"
    ACCENT = "#5c7cfa"

    def __init__(self, root: tk.Tk):
        self.root = root
        self.root.title("Steam Tools App Adder Made By Remix")
        self.root.geometry("600x600")
        self.root.resizable(False, False)
        self.root.configure(bg=self.BG)

        icon = Path("icon.ico")
        bundled_icon = Path(resource_path("icon.ico"))
        if icon.exists():
            root.wm_iconbitmap(str(icon))
        elif bundled_icon.exists():
            try:
                root.wm_iconbitmap(str(bundled_icon))
            except Exception:
                pass

        self.downloader = SteamToolsDownloader()
        self.is_processing = False
        self.selection_popup: Optional[tk.Toplevel] = None

        self.create_widgets()

        if not self.downloader.steamtools_exe:
            self.install_btn.configure_state(False)
            self.update_status("ERROR: SteamTools not found.")
            self.show_steamtools_missing_dialog()

    def show_steamtools_missing_dialog(self):
        """Display dialog when SteamTools.exe is not found."""
        popup = tk.Toplevel(self.root)
        popup.title("SteamTools Not Found")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(bg=self.BG)

        popup_w, popup_h = 600, 450
        sx = self.root.winfo_screenwidth()
        sy = self.root.winfo_screenheight()
        popup.geometry(f"{popup_w}x{popup_h}+{(sx - popup_w) // 2}+{(sy - popup_h) // 2}")

        main_frame = tk.Frame(popup, bg=self.BG)
        main_frame.pack(fill=tk.BOTH, expand=True)

        header = tk.Frame(main_frame, bg="#ff6b6b", height=100)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="⚠️  SteamTools Not Found",
                 font=("Segoe UI", 18, "bold"), fg="#ffffff", bg="#ff6b6b").pack(pady=(25, 10))
        tk.Label(header, text="Required component missing",
                 font=("Segoe UI", 10), fg="#ffe0e0", bg="#ff6b6b").pack(pady=(0, 15))

        content = tk.Frame(main_frame, bg=self.BG)
        content.pack(fill=tk.BOTH, expand=True, padx=40, pady=35)
        tk.Label(content, text="\U0001f4e5", font=("Segoe UI", 48), bg=self.BG).pack(pady=(0, 20))
        tk.Label(content,
                 text="SteamTools.exe is required to use this application.\n"
                      "Please download and install it first,\n"
                      "then restart this application.",
                 font=("Segoe UI", 11), fg=self.TEXT, bg=self.BG,
                 justify=tk.CENTER, wraplength=450).pack(pady=(0, 35))

        btn_frame = tk.Frame(content, bg=self.BG)
        btn_frame.pack(fill=tk.X)

        def open_download():
            webbrowser.open("https://steamtools.net/download")
            messagebox.showinfo("Download Started",
                                "The download has been opened in your browser.\n\n"
                                "After installation, please restart this application.")
            popup.destroy()

        ModernButton(btn_frame, "⬇️  Download SteamTools", open_download,
                     width=280, height=50, bg=self.BG).pack(side=tk.LEFT, padx=(0, 10))
        ModernButton(btn_frame, "Close", popup.destroy,
                     width=140, height=50, bg=self.BG).pack(side=tk.LEFT)

    def create_widgets(self):
        """Build the main application interface."""
        main = tk.Frame(self.root, bg=self.BG)
        main.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)

        tk.Label(main, text="Steam Tools App Adder",
                 font=("Segoe UI", 24, "bold"), fg=self.TEXT, bg=self.BG).pack(pady=(0, 10))
        tk.Label(main, text="Enter game name, App ID or Steam URL",
                 font=("Segoe UI", 11), fg="#7982a9", bg=self.BG).pack(pady=(0, 5))

        # Input card
        input_card = tk.Frame(main, bg=self.CARD)
        input_card.pack(fill=tk.X, pady=(0, 20))
        input_inner = tk.Frame(input_card, bg=self.CARD)
        input_inner.pack(padx=20, pady=20)
        tk.Label(input_inner, text="Search for Game", font=("Segoe UI", 10),
                 fg="#7982a9", bg=self.CARD).pack(anchor="w", pady=(0, 8))
        self.search_entry = tk.Entry(input_inner, font=("Segoe UI", 12),
                                     bg="#414868", fg=self.TEXT, relief=tk.FLAT,
                                     insertbackground=self.TEXT, bd=0,
                                     highlightthickness=2, highlightbackground="#414868",
                                     highlightcolor=self.ACCENT)
        self.search_entry.pack(fill=tk.X, ipady=8, ipadx=10)
        self.search_entry.bind("<Return>", lambda _: self.start_download())

        # Install button
        btn_frame = tk.Frame(main, bg=self.BG)
        btn_frame.pack(pady=10)
        self.install_btn = ModernButton(btn_frame, "Search & Install", self.start_download,
                                        width=200, height=50, bg=self.BG)
        self.install_btn.pack()

        # Progress card
        prog_card = tk.Frame(main, bg=self.CARD)
        prog_card.pack(fill=tk.BOTH, expand=True)
        prog_inner = tk.Frame(prog_card, bg=self.CARD)
        prog_inner.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        self.status_label = tk.Label(prog_inner, text="Ready", font=("Segoe UI", 11),
                                     fg=self.TEXT, bg=self.CARD, anchor="w")
        self.status_label.pack(fill=tk.X, pady=(0, 10))

        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Custom.Horizontal.TProgressbar",
                        troughcolor='#414868', bordercolor=self.CARD,
                        background=self.ACCENT, lightcolor=self.ACCENT,
                        darkcolor=self.ACCENT)
        self.progress_bar = ttk.Progressbar(prog_inner, mode='indeterminate',
                                            style="Custom.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(0, 15))

        tk.Label(prog_inner, text="Activity Log", font=("Segoe UI", 9, "bold"),
                 fg="#7982a9", bg=self.CARD, anchor="w").pack(fill=tk.X, pady=(0, 8))

        log_frame = tk.Frame(prog_inner, bg="#414868", bd=0)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_text = tk.Text(log_frame, font=("Consolas", 9), bg="#414868", fg="#a9b1d6",
                                relief=tk.FLAT, bd=0, padx=10, pady=10,
                                height=8, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview,
                                 bg="#414868", troughcolor="#414868",
                                 bd=0, highlightthickness=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

    def log(self, message: str):
        """Append a line to the activity log."""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def update_status(self, status: str):
        """Update the status label text."""
        self.status_label.config(text=status)

    def start_download(self):
        """Validate input and kick off the search/install thread."""
        if self.is_processing:
            return

        if not self.downloader.steamtools_exe:
            messagebox.showerror("Missing Requirement",
                                 "SteamTools.exe was not found. "
                                 "Please install SteamTools and restart.")
            return

        query = self.search_entry.get().strip()
        if not query:
            messagebox.showwarning("Input Required",
                                   "Please enter a game name, App ID, or URL")
            return

        self.is_processing = True
        self.install_btn.configure_state(False)
        self.progress_bar.start(10)

        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        t = threading.Thread(target=self.initial_search_thread, args=(query,), daemon=True)
        t.start()

    def initial_search_thread(self, query: str):
        """Background thread: resolve the query to an App ID."""
        try:
            self.root.after(0, lambda: self.update_status("Searching for game..."))
            self.root.after(0, lambda: self.log(f"Searching: {query}"))

            result = self.downloader.find_game(query)

            if isinstance(result, int):
                self.root.after(0, lambda: self.download_thread_start(result))
            elif isinstance(result, list) and result:
                self.root.after(0, lambda: self.show_match_selection(result, query))
            else:
                self.root.after(0, lambda: messagebox.showerror(
                    "Not Found", f"No game found for: {query}"))
                self.root.after(0, self.finish_processing)

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error during search: {e}"))
            self.root.after(0, lambda: messagebox.showerror(
                "Error", f"An error occurred during search:\n{e}"))
            self.root.after(0, self.finish_processing)

    def show_match_selection(self, matches: List[Dict[str, Any]], original_query: str):
        """Display a dialog for the user to pick from multiple game matches."""
        if self.selection_popup and self.selection_popup.winfo_exists():
            self.selection_popup.destroy()

        self.selection_popup = tk.Toplevel(self.root)
        popup = self.selection_popup
        popup.title(f"Select Game - Search: '{original_query}'")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(bg=self.BG)

        popup.protocol("WM_DELETE_WINDOW", lambda: (popup.destroy(),
                                                     self.root.after(0, self.finish_processing)))

        popup_w, popup_h = 550, 500
        sx = self.root.winfo_screenwidth()
        sy = self.root.winfo_screenheight()
        popup.geometry(f"{popup_w}x{popup_h}+{(sx - popup_w) // 2}+{(sy - popup_h) // 2}")

        # Header
        header = tk.Frame(popup, bg=self.ACCENT, height=110)
        header.pack(fill=tk.X)
        header.pack_propagate(False)
        tk.Label(header, text="\U0001f50d  Found Similar Games",
                 font=("Segoe UI", 17, "bold"), fg="#ffffff", bg=self.ACCENT).pack(pady=(20, 8))
        tk.Label(header, text=f"Multiple games matched '{original_query}'. Please select one:",
                 font=("Segoe UI", 10), fg="#e0e0ff", bg=self.ACCENT).pack(pady=(0, 15))

        # Content
        content = tk.Frame(popup, bg=self.BG)
        content.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)
        tk.Label(content, text="Select a game:", font=("Segoe UI", 10, "bold"),
                 fg="#7982a9", bg=self.BG).pack(anchor="w", pady=(0, 12))

        list_frame = tk.Frame(content, bg="#414868", relief=tk.FLAT, bd=1)
        list_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 25))

        listbox = tk.Listbox(list_frame, height=10, selectmode=tk.SINGLE,
                             bg="#414868", fg=self.TEXT, relief=tk.FLAT, bd=0,
                             selectbackground=self.ACCENT, font=("Segoe UI", 10),
                             activestyle='none', highlightthickness=0)
        listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=12)

        scrollbar = tk.Scrollbar(list_frame, command=listbox.yview,
                                 bg="#414868", troughcolor="#414868",
                                 bd=0, highlightthickness=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=12)
        listbox.config(yscrollcommand=scrollbar.set)

        for match in matches:
            if isinstance(match, dict):
                name = match.get('name', 'Unknown')
                appid = match.get('appid', 'N/A')
            elif isinstance(match, tuple) and len(match) == 2:
                name, appid = match
            else:
                name, appid = str(match), ''
            listbox.insert(tk.END, f"  {name[:45]} (App ID: {appid})")

        listbox.select_set(0)

        def on_select():
            sel = listbox.curselection()
            if not sel:
                messagebox.showwarning("Selection Error", "Please select a game from the list.")
                return
            match = matches[sel[0]]
            if isinstance(match, dict):
                app_id = match.get('appid')
            elif isinstance(match, tuple) and len(match) == 2:
                app_id = match[1]
            else:
                app_id = match
            if app_id:
                popup.destroy()
                self.download_thread_start(app_id)

        def on_cancel():
            popup.destroy()
            self.root.after(0, self.finish_processing)

        def on_try_again():
            popup.destroy()
            self.root.after(0, self.finish_processing)
            self.root.after(100, lambda: self.search_entry.focus_set())
            self.root.after(100, lambda: self.search_entry.select_range(0, tk.END))

        btn_frame = tk.Frame(content, bg=self.BG)
        btn_frame.pack(fill=tk.X)
        left = tk.Frame(btn_frame, bg=self.BG)
        left.pack(side=tk.LEFT, fill=tk.X, expand=True)
        right = tk.Frame(btn_frame, bg=self.BG)
        right.pack(side=tk.RIGHT)

        ModernButton(left, "✓  Confirm Selection", on_select,
                     width=180, height=45, bg=self.BG).pack(side=tk.LEFT, padx=2)
        ModernButton(right, "↻  Try Different Search", on_try_again,
                     width=160, height=45, bg=self.BG).pack(side=tk.LEFT, padx=2)
        ModernButton(right, "✕  Cancel", on_cancel,
                     width=100, height=45, bg=self.BG).pack(side=tk.LEFT, padx=2)

        listbox.bind("<Double-Button-1>", lambda _: on_select())
        listbox.bind("<Return>", lambda _: on_select())

        self.root.wait_window(popup)

    def download_thread_start(self, app_id: int):
        """Spawn the download/install worker thread."""
        self.root.after(0, lambda: self.log(f"Selected App ID: {app_id}"))
        t = threading.Thread(target=self.download_thread, args=(app_id,), daemon=True)
        t.start()

    def download_thread(self, app_id: int):
        """Execute the full download and installation pipeline."""
        def log(msg):
            self.root.after(0, lambda m=msg: self.log(m))

        try:
            sep = "=" * 60
            log(f"\n{sep}\nProcessing App ID: {app_id}\n{sep}")
            self.root.after(0, lambda: self.update_status("Getting game details..."))

            log("\n[1/5] Fetching store details...")
            details = self.downloader.get_app_details(app_id)
            log(f"Found: {details['name']}" if details else "Store details not available")

            self.root.after(0, lambda: self.update_status("Downloading files..."))
            success = self.downloader.download_appid_zip(app_id, log_callback=log)
            if not success:
                self.root.after(0, lambda: messagebox.showerror(
                    "Download Failed", "Could not download game data"))
                return

            log("Download complete")
            self.root.after(0, lambda: self.update_status("Installing files..."))
            self.downloader.copy_files_to_steam(log_callback=log)
            log("Files installed")

            self.root.after(0, lambda: self.update_status("Restarting Steam components..."))
            log("\n[5/5] Restarting Steam components...")
            self.downloader.close_steam(log_callback=log)
            time.sleep(1)
            self.downloader.launch_steamtools(log_callback=log)
            time.sleep(2)
            self.downloader.start_steam(log_callback=log)

            self.root.after(0, lambda: self.update_status("Complete!"))
            log(f"\n{sep}\n✓ Complete!\n{sep}")
            self.root.after(0, lambda: messagebox.showinfo(
                "Success", "Installation complete!\n\nSteam has been restarted."))

        except Exception as e:
            log(f"Fatal Error: {e}")
            self.root.after(0, lambda: messagebox.showerror(
                "Fatal Error", f"A fatal error occurred:\n{e}"))
        finally:
            self.root.after(0, self.finish_processing)

    def finish_processing(self):
        """Reset GUI to idle state."""
        self.is_processing = False
        self.selection_popup = None
        self.progress_bar.stop()
        self.install_btn.configure_state(True)
        self.update_status("Ready")


def is_admin() -> bool:
    """Check if running with administrator privileges (Windows only)."""
    if sys.platform != 'win32':
        return True
    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


def run_as_admin():
    """Re-launch the current script with administrator privileges."""
    script = os.path.abspath(sys.argv[0])
    params = ' '.join(sys.argv[1:])
    try:
        ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, script, params, 1)
    except Exception as e:
        messagebox.showerror("Elevation Failed",
                             f"Failed to request administrator privileges: {e}")
        sys.exit(1)
    sys.exit(0)


def main():
    """Application entry point."""
    if sys.platform == 'win32' and not is_admin():
        messagebox.showwarning(
            "Administrator Permissions Required",
            "This application requires Administrator permissions to modify Steam files.\n"
            "Restarting with elevated privileges...")
        run_as_admin()

    root = tk.Tk()
    SteamToolsInstaller(root)
    root.mainloop()


if __name__ == "__main__":
    main()
