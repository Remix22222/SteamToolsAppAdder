import requests
import zipfile
import shutil
import subprocess
import os
import time
import re
from difflib import get_close_matches
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox
import threading
import sys
import ctypes
import webbrowser
from urllib.parse import quote
import io
from bs4 import BeautifulSoup
import http.client
from typing import Optional, Tuple, List, Dict, Any


class SteamWebSearch:
    """Handles searching Steam store for games using web scraping."""

    def __init__(self):
        self.search_cache = {}

    def search_steam_store(self, query: str) -> List[Dict[str, Any]]:
        """
        Search Steam store for games by name.
        Returns list of dicts with 'name', 'appid', and 'url' keys.
        """
        if query in self.search_cache:
            return self.search_cache[query]

        try:
            # URL encode the query
            encoded_query = quote(query)
            url = f"https://store.steampowered.com/search/?term={encoded_query}"

            # Set headers to mimic a browser
            headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8',
                'Accept-Language': 'en-US,en;q=0.5',
                'Accept-Encoding': 'gzip, deflate, br',
                'Connection': 'keep-alive',
            }

            response = requests.get(url, headers=headers, timeout=15)
            response.raise_for_status()

            # Parse HTML
            soup = BeautifulSoup(response.content, 'html.parser')
            results = []

            # Find all search result rows
            search_result_rows = soup.find_all('a', {'data-ds-appid': True})

            for row in search_result_rows[:10]:  # Limit to first 10 results
                try:
                    # Get appid from data attribute
                    appid = row.get('data-ds-appid', '').split(',')[0]
                    if not appid.isdigit():
                        continue

                    # Get game name
                    title_span = row.find('span', class_='title')
                    if not title_span:
                        continue

                    name = title_span.text.strip()

                    # Get game URL
                    href = row.get('href', '')

                    results.append({
                        'name': name,
                        'appid': int(appid),
                        'url': href
                    })

                except (AttributeError, ValueError, IndexError):
                    continue

            # Alternative method if first method doesn't work
            if not results:
                # Look for app links directly
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    app_match = re.search(r'/app/(\d+)/', href)
                    if app_match:
                        appid = app_match.group(1)
                        name = link.text.strip()
                        if name and appid.isdigit():
                            results.append({
                                'name': name[:100] if name else f"App {appid}",
                                'appid': int(appid),
                                'url': href if href.startswith('http') else f'https://store.steampowered.com{href}'
                            })

            # Remove duplicates by appid
            unique_results = []
            seen_appids = set()
            for result in results:
                if result['appid'] not in seen_appids:
                    unique_results.append(result)
                    seen_appids.add(result['appid'])

            self.search_cache[query] = unique_results
            return unique_results

        except Exception as e:
            print(f"Error searching Steam store: {e}")
            return []

    def extract_appid_from_url(self, url: str) -> Optional[int]:
        """Extract App ID from any Steam URL."""
        try:
            # Common Steam URL patterns
            patterns = [
                r'/app/(\d+)',  # Standard app URLs
                r'app/(\d+)',  # Alternative format
                r'AppId=(\d+)',  # Query parameter
                r'id=(\d+)',  # Another query parameter
            ]

            for pattern in patterns:
                match = re.search(pattern, url)
                if match and match.group(1).isdigit():
                    return int(match.group(1))

            return None
        except Exception as e:
            print(f"Error extracting App ID from URL: {e}")
            return None


class SteamToolsDownloader:
    """Handles Steam game downloading and installation logic."""

    def __init__(self):
        self.games_cache = {}
        self.base_url = "https://api.steampowered.com"
        self.r2_base_url = "https://pub-5b6d3b7c03fd4ac1afb5bd3017850e20.r2.dev"
        self.steamtools_exe = self.find_steamtools_exe()
        self._steam_folder = None
        self.web_searcher = SteamWebSearch()

    def find_steamtools_exe(self):
        """Find SteamTools executable in common installation paths."""
        common_paths = [
            Path.home() / "AppData" / "Local" / "SteamTools",
            Path.home() / "AppData" / "Roaming" / "SteamTools",
            Path("C:/Program Files/SteamTools"),
            Path("C:/Program Files (x86)/SteamTools"),
        ]

        for base_path in common_paths:
            if base_path.exists():
                for exe_file in base_path.rglob("SteamTools.exe"):
                    return exe_file
        return None

    def get_app_list(self):
        """Fetch and cache the full Steam app list."""
        if not self.games_cache:
            try:
                url = f"{self.base_url}/ISteamApps/GetAppList/v2/"
                response = requests.get(url, timeout=15)
                apps = response.json()['applist']['apps']
                self.games_cache = {app['name'].lower(): app['appid'] for app in apps}
            except Exception as e:
                print(f"Error fetching app list: {e}")
        return self.games_cache

    def find_steam_folder(self):
        """Find Steam installation folder automatically."""
        if self._steam_folder:
            return self._steam_folder

        possible_paths = [
            Path(os.environ.get('PROGRAMFILES(X86)', 'C:\\Program Files (x86)')) / 'Steam',
            Path(os.environ.get('PROGRAMFILES', 'C:\\Program Files')) / 'Steam',
            Path('C:\\Program Files (x86)\\Steam'),
            Path('C:\\Program Files\\Steam'),
        ]

        for steam_path in possible_paths:
            if steam_path.exists():
                self._steam_folder = steam_path
                return self._steam_folder
        return None

    def find_game(self, query):
        """Find game by name, AppID, or URL with multiple search methods."""
        # Check if it's a Steam URL
        if 'store.steampowered.com' in query or 'steamcommunity.com' in query:
            appid = self.web_searcher.extract_appid_from_url(query)
            if appid:
                return appid

        # Check if it's a direct AppID
        if query.isdigit():
            return int(query)

        # Try web search first
        web_results = self.web_searcher.search_steam_store(query)
        if web_results:
            # Return first result for direct match, or list for selection
            if len(web_results) == 1:
                return web_results[0]['appid']
            else:
                # Return list of dicts for better display
                return web_results

        # Fallback to API search if web search fails
        games = self.get_app_list()
        if not games:
            return None

        query_lower = query.lower()

        # Exact match
        if query_lower in games:
            return games[query_lower]

        # Fuzzy match
        matches = get_close_matches(query_lower, games.keys(), n=5, cutoff=0.7)
        if matches:
            # Convert to list of dicts for consistency
            return [{'name': match, 'appid': games[match]} for match in matches]

        return None

    def get_app_details(self, app_id):
        """Get detailed app information from Steam Store API."""
        url = f"https://store.steampowered.com/api/appdetails?appids={app_id}"
        try:
            response = requests.get(url, timeout=10)
            data = response.json()
            if str(app_id) in data and data[str(app_id)]['success']:
                return data[str(app_id)]['data']
        except Exception as e:
            print(f"Error fetching app details: {e}")
        return None

    def download_appid_zip(self, app_id, output_dir="downloads", log_callback=None):
        """Download and extract game data from R2 storage."""
        if log_callback:
            log_callback(f"[2/5] Downloading {app_id}.zip from R2 storage...")

        Path(output_dir).mkdir(parents=True, exist_ok=True)
        url = f"{self.r2_base_url}/{app_id}.zip"
        zip_path = Path(output_dir) / f"{app_id}.zip"

        try:
            response = requests.get(url, timeout=30, stream=True)
            if response.status_code == 404:
                if log_callback:
                    log_callback(f"No data found for App ID {app_id}")
                return False

            response.raise_for_status()

            # Download file
            with open(zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            if log_callback:
                log_callback(f"Downloaded: {zip_path.name}")
                log_callback(f"Extracting...")

            # Extract archive
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(output_dir)

            if log_callback:
                log_callback(f"Extracted successfully")

            # Clean up zip file
            zip_path.unlink()
            return True

        except Exception as e:
            if log_callback:
                log_callback(f"Error during download/extraction: {e}")
            return False

    def copy_files_to_steam(self, source_dir="downloads", log_callback=None):
        """Copy lua, .st files to stplug-in and manifest files to depotcache."""
        source_path = Path(source_dir)
        lua_files = list(source_path.rglob("*.lua"))
        manifest_files = list(source_path.rglob("*.manifest"))
        st_files = list(source_path.rglob("*.st"))

        if not lua_files and not manifest_files and not st_files:
            if log_callback:
                log_callback("No files found to copy.")
            return False

        if log_callback:
            log_callback(f"\n[3/5] Copying files to Steam...")

        steam_folder = self.find_steam_folder()
        if not steam_folder:
            if log_callback:
                log_callback("\nCould not find Steam installation.")
            return False

        stplug_folder = steam_folder / 'config' / 'stplug-in'
        depotcache_folder = steam_folder / 'depotcache'

        stplug_folder.mkdir(parents=True, exist_ok=True)
        depotcache_folder.mkdir(parents=True, exist_ok=True)

        # Copy plugin files
        files_to_copy = lua_files + st_files
        if files_to_copy:
            if log_callback:
                log_callback(f"\nCopying plugin file(s) to config/stplug-in...")
            for file_path in files_to_copy:
                try:
                    dest_path = stplug_folder / file_path.name
                    shutil.copy2(file_path, dest_path)
                except Exception as e:
                    if log_callback:
                        log_callback(f"  ✗ Failed: {e}")

        # Copy manifest files
        if manifest_files:
            if log_callback:
                log_callback(f"\nCopying manifest file(s) to depotcache...")
            for file_path in manifest_files:
                try:
                    dest_path = depotcache_folder / file_path.name
                    shutil.copy2(file_path, dest_path)
                except Exception as e:
                    if log_callback:
                        log_callback(f"  ✗ Failed: {e}")

        # Clean up temporary files
        if log_callback:
            log_callback(f"\n[4/5] Cleaning up...")
        try:
            shutil.rmtree(source_path)
            if log_callback:
                log_callback(f"✓ Deleted temporary files")
        except Exception as e:
            if log_callback:
                log_callback(f"⚠ Could not delete downloads folder: {e}")

        return True

    def close_steam(self, log_callback=None):
        """Close Steam completely."""
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

    def start_steam(self, log_callback=None):
        """Start Steam."""
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

    def launch_steamtools(self, log_callback=None):
        """Launch SteamTools."""
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
    """Custom styled button with hover effects."""

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

    def configure_state(self, enabled):
        """Enable or disable the button."""
        self.is_enabled = enabled
        if enabled:
            self.itemconfig(self.rect, fill=self.bg_normal)
        else:
            self.itemconfig(self.rect, fill="#6c757d")

    def draw(self):
        """Draw the button with rounded corners."""
        self.delete("all")
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()

        self.rect = self.create_rounded_rect(0, 0, width, height, 10,
                                             fill=self.bg_normal, outline="")
        self.text_id = self.create_text(width // 2, height // 2, text=self.text,
                                        fill=self.fg_color, font=("Segoe UI", 11, "bold"))

    def create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        """Create a rounded rectangle polygon."""
        points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
        return self.create_polygon(points, smooth=True, **kwargs)

    def on_enter(self, e):
        """Handle mouse enter event."""
        if self.is_enabled:
            self.itemconfig(self.rect, fill=self.bg_hover)

    def on_leave(self, e):
        """Handle mouse leave event."""
        if self.is_enabled:
            self.itemconfig(self.rect, fill=self.bg_normal)

    def on_click(self, e):
        """Handle mouse click event."""
        if not self.is_enabled:
            return

        self.itemconfig(self.rect, fill=self.bg_active)
        self.after(100, lambda: self.itemconfig(self.rect, fill=self.bg_hover))
        if self.command:
            self.command()


class SteamToolsInstaller:
    """Main GUI application for Steam Tools installation."""

    def __init__(self, root):
        self.root = root
        self.root.title("Steam Tools App Adder Made By Remix")
        self.root.geometry("600x600")
        self.root.resizable(False, False)

        # Color scheme
        self.bg_color = "#1a1b26"
        self.card_color = "#24283b"
        self.text_color = "#c0caf5"
        self.accent_color = "#5c7cfa"

        self.root.configure(bg=self.bg_color)

        self.downloader = SteamToolsDownloader()
        self.is_processing = False
        self.selection_popup = None  # Track the selection popup

        self.create_widgets()

        # Check if SteamTools is installed
        if not self.downloader.steamtools_exe:
            self.install_btn.configure_state(False)
            self.update_status("ERROR: SteamTools not found.")
            self.show_steamtools_missing_dialog()

    def show_steamtools_missing_dialog(self):
        """Display dialog when SteamTools is not found."""
        popup = tk.Toplevel(self.root)
        popup.title("SteamTools Not Found")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(bg=self.bg_color)

        popup_width = 600
        popup_height = 450

        # Center popup on screen
        screen_x = self.root.winfo_screenwidth()
        screen_y = self.root.winfo_screenheight()
        center_x = (screen_x - popup_width) // 2
        center_y = (screen_y - popup_height) // 2
        popup.geometry(f"{popup_width}x{popup_height}+{center_x}+{center_y}")

        # Main container
        main_frame = tk.Frame(popup, bg=self.bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True)

        # Header
        header_frame = tk.Frame(main_frame, bg="#ff6b6b", height=100)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        title = tk.Label(header_frame, text="⚠️  SteamTools Not Found",
                         font=("Segoe UI", 18, "bold"),
                         fg="#ffffff", bg="#ff6b6b")
        title.pack(pady=(25, 10))

        subtitle = tk.Label(header_frame, text="Required component missing",
                            font=("Segoe UI", 10),
                            fg="#ffe0e0", bg="#ff6b6b")
        subtitle.pack(pady=(0, 15))

        # Content
        content_frame = tk.Frame(main_frame, bg=self.bg_color)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=40, pady=35)

        # Icon
        icon_label = tk.Label(content_frame, text="📥",
                              font=("Segoe UI", 48),
                              bg=self.bg_color)
        icon_label.pack(pady=(0, 20))

        # Message
        message = tk.Label(content_frame,
                           text="SteamTools.exe is required to use this application.\n"
                                "Please download and install it first,\n"
                                "then restart this application.",
                           font=("Segoe UI", 11),
                           fg=self.text_color, bg=self.bg_color,
                           justify=tk.CENTER, wraplength=450)
        message.pack(pady=(0, 35))

        # Buttons
        button_frame = tk.Frame(content_frame, bg=self.bg_color)
        button_frame.pack(fill=tk.X)

        def open_download_link():
            """Open SteamTools download link in browser."""
            webbrowser.open(
                "https://store2.gofile.io/download/web/b1610f35-acac-453b-9677-505200f0eefc/st-setup-1.8.17r2.exe")
            messagebox.showinfo("Download Started",
                                "The download has been opened in your browser.\n\nAfter installation, please restart this application.")
            popup.destroy()

        def on_close():
            """Close the popup."""
            popup.destroy()

        # Create buttons with better sizing
        download_btn = ModernButton(button_frame, "⬇️  Download SteamTools", open_download_link,
                                    width=280, height=50, bg=self.bg_color)
        download_btn.pack(side=tk.LEFT, padx=(0, 10))

        close_btn = ModernButton(button_frame, "Close", on_close,
                                 width=140, height=50, bg=self.bg_color)
        close_btn.pack(side=tk.LEFT)

    def create_widgets(self):
        """Create main application interface."""
        main_frame = tk.Frame(self.root, bg=self.bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)

        # Title
        title = tk.Label(main_frame, text="Steam Tools App Adder",
                         font=("Segoe UI", 24, "bold"),
                         fg=self.text_color, bg=self.bg_color)
        title.pack(pady=(0, 10))

        subtitle = tk.Label(main_frame, text="Enter game name, App ID or Steam URL",
                            font=("Segoe UI", 11),
                            fg="#7982a9", bg=self.bg_color)
        subtitle.pack(pady=(0, 5))

        # Input card
        input_card = tk.Frame(main_frame, bg=self.card_color)
        input_card.pack(fill=tk.X, pady=(0, 20))

        input_inner = tk.Frame(input_card, bg=self.card_color)
        input_inner.pack(padx=20, pady=20)

        input_label = tk.Label(input_inner, text="Search for Game",
                               font=("Segoe UI", 10),
                               fg="#7982a9", bg=self.card_color)
        input_label.pack(anchor="w", pady=(0, 8))

        self.search_entry = tk.Entry(input_inner, font=("Segoe UI", 12),
                                     bg="#414868", fg=self.text_color,
                                     relief=tk.FLAT, insertbackground=self.text_color,
                                     bd=0, highlightthickness=2,
                                     highlightbackground="#414868",
                                     highlightcolor=self.accent_color)
        self.search_entry.pack(fill=tk.X, ipady=8, ipadx=10)
        self.search_entry.bind("<Return>", lambda e: self.start_download())

        # Install button
        btn_frame = tk.Frame(main_frame, bg=self.bg_color)
        btn_frame.pack(pady=10)

        self.install_btn = ModernButton(btn_frame, "Search & Install", self.start_download,
                                        width=200, height=50, bg=self.bg_color)
        self.install_btn.pack()

        # Progress card
        progress_card = tk.Frame(main_frame, bg=self.card_color)
        progress_card.pack(fill=tk.BOTH, expand=True, pady=(0, 0))

        progress_inner = tk.Frame(progress_card, bg=self.card_color)
        progress_inner.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        self.status_label = tk.Label(progress_inner, text="Ready",
                                     font=("Segoe UI", 11),
                                     fg=self.text_color, bg=self.card_color,
                                     anchor="w")
        self.status_label.pack(fill=tk.X, pady=(0, 10))

        # Progress bar
        style = ttk.Style()
        style.theme_use('clam')
        style.configure("Custom.Horizontal.TProgressbar",
                        troughcolor='#414868',
                        bordercolor=self.card_color,
                        background=self.accent_color,
                        lightcolor=self.accent_color,
                        darkcolor=self.accent_color)

        self.progress_bar = ttk.Progressbar(progress_inner, mode='indeterminate',
                                            style="Custom.Horizontal.TProgressbar")
        self.progress_bar.pack(fill=tk.X, pady=(0, 15))

        # Activity log
        log_label = tk.Label(progress_inner, text="Activity Log",
                             font=("Segoe UI", 9, "bold"),
                             fg="#7982a9", bg=self.card_color,
                             anchor="w")
        log_label.pack(fill=tk.X, pady=(0, 8))

        log_frame = tk.Frame(progress_inner, bg="#414868", bd=0)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(log_frame, font=("Consolas", 9),
                                bg="#414868", fg="#a9b1d6",
                                relief=tk.FLAT, bd=0, padx=10, pady=10,
                                height=8, wrap=tk.WORD, state=tk.DISABLED)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview,
                                 bg="#414868", troughcolor="#414868",
                                 bd=0, highlightthickness=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.config(yscrollcommand=scrollbar.set)

    def log(self, message):
        """Append message to activity log."""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def update_status(self, status):
        """Update the status label."""
        self.status_label.config(text=status)

    def start_download(self):
        """Initialize the download process."""
        if self.is_processing:
            return

        if not self.downloader.steamtools_exe:
            messagebox.showerror("Missing Requirement",
                                 "SteamTools.exe was not found. Please install SteamTools and restart.")
            return

        query = self.search_entry.get().strip()
        if not query:
            messagebox.showwarning("Input Required", "Please enter a game name, App ID, or URL")
            return

        self.is_processing = True
        self.install_btn.configure_state(False)
        self.progress_bar.start(10)

        # Clear log
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        # Start search thread
        thread = threading.Thread(target=self.initial_search_thread, args=(query,))
        thread.daemon = True
        thread.start()

    def initial_search_thread(self, query):
        """Perform initial game search in background thread."""
        try:
            self.root.after(0, lambda: self.update_status("Searching for game..."))
            self.root.after(0, lambda: self.log(f"Searching: {query}"))

            app_match_result = self.downloader.find_game(query)

            if isinstance(app_match_result, int):
                # Direct match found
                self.root.after(0, lambda: self.download_thread_start(app_match_result))
            elif isinstance(app_match_result, list) and app_match_result:
                # Multiple matches found
                self.root.after(0, lambda: self.show_match_selection(app_match_result, query))
            else:
                # No match found
                self.root.after(0, lambda: messagebox.showerror("Not Found",
                                                                f"No game found for: {query}"))
                self.root.after(0, self.finish_processing)

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error during search: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Error",
                                                            f"An error occurred during search:\n{str(e)}"))
            self.root.after(0, self.finish_processing)

    def show_match_selection(self, matches, original_query):
        """Display dialog for selecting from multiple game matches."""
        # Close any existing selection popup
        if self.selection_popup and self.selection_popup.winfo_exists():
            self.selection_popup.destroy()

        self.selection_popup = tk.Toplevel(self.root)
        popup = self.selection_popup
        popup.title(f"Select Game - Search: '{original_query}'")
        popup.transient(self.root)
        popup.grab_set()
        popup.resizable(False, False)
        popup.configure(bg=self.bg_color)

        # Set up window close handler
        def on_popup_close():
            """Handle popup window close event."""
            if popup.winfo_exists():
                popup.destroy()
            self.root.after(0, self.finish_processing)

        popup.protocol("WM_DELETE_WINDOW", on_popup_close)

        popup_width = 550
        popup_height = 500

        # Center popup
        screen_x = self.root.winfo_screenwidth()
        screen_y = self.root.winfo_screenheight()
        center_x = (screen_x - popup_width) // 2
        center_y = (screen_y - popup_height) // 2
        popup.geometry(f"{popup_width}x{popup_height}+{center_x}+{center_y}")

        # Header
        header_frame = tk.Frame(popup, bg="#5c7cfa", height=110)
        header_frame.pack(fill=tk.X)
        header_frame.pack_propagate(False)

        title = tk.Label(header_frame, text="🔍  Found Similar Games",
                         font=("Segoe UI", 17, "bold"),
                         fg="#ffffff", bg="#5c7cfa")
        title.pack(pady=(20, 8))

        subtitle = tk.Label(header_frame,
                            text=f"Multiple games matched '{original_query}'. Please select one:",
                            font=("Segoe UI", 10),
                            fg="#e0e0ff", bg="#5c7cfa")
        subtitle.pack(pady=(0, 15))

        # Content
        content_frame = tk.Frame(popup, bg=self.bg_color)
        content_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)

        list_label = tk.Label(content_frame, text="Select a game:",
                              font=("Segoe UI", 10, "bold"),
                              fg="#7982a9", bg=self.bg_color)
        list_label.pack(anchor="w", pady=(0, 12))

        # Listbox
        listbox_frame = tk.Frame(content_frame, bg="#414868", relief=tk.FLAT, bd=1)
        listbox_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 25))

        match_listbox = tk.Listbox(listbox_frame, height=10, selectmode=tk.SINGLE,
                                   bg="#414868", fg=self.text_color, relief=tk.FLAT, bd=0,
                                   selectbackground="#5c7cfa", font=("Segoe UI", 10),
                                   activestyle='none', highlightthickness=0)
        match_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True, padx=12, pady=12)

        scrollbar = tk.Scrollbar(listbox_frame, command=match_listbox.yview,
                                 bg="#414868", troughcolor="#414868",
                                 bd=0, highlightthickness=0)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y, padx=(0, 8), pady=12)
        match_listbox.config(yscrollcommand=scrollbar.set)

        # Populate listbox
        for i, match in enumerate(matches):
            if isinstance(match, dict):
                name = match.get('name', 'Unknown')
                appid = match.get('appid', 'N/A')
                display_text = f"  {name[:45]} (App ID: {appid})"
            elif isinstance(match, tuple) and len(match) == 2:
                name, appid = match
                display_text = f"  {name[:45]} (App ID: {appid})"
            else:
                display_text = f"  {str(match)[:50]}"
            match_listbox.insert(tk.END, display_text)

        match_listbox.select_set(0)

        def on_select():
            """Handle game selection."""
            try:
                selection = match_listbox.curselection()
                if selection:
                    idx = selection[0]
                    match = matches[idx]

                    # Extract appid from different match formats
                    if isinstance(match, dict):
                        app_id = match.get('appid')
                    elif isinstance(match, tuple) and len(match) == 2:
                        app_id = match[1]  # (name, appid) format
                    else:
                        app_id = match  # assume it's already an appid

                    if app_id:
                        popup.destroy()
                        self.download_thread_start(app_id)
                        return
                messagebox.showwarning("Selection Error", "Please select a game from the list.")
            except Exception as e:
                messagebox.showerror("Error", f"Error during selection: {str(e)}")
                popup.destroy()
                self.finish_processing()

        def on_cancel():
            """Cancel selection and close popup."""
            popup.destroy()
            self.root.after(0, self.finish_processing)

        def on_try_again():
            """Try again with a different search."""
            popup.destroy()
            self.root.after(0, self.finish_processing)
            # Focus back to search entry for new input
            self.root.after(100, lambda: self.search_entry.focus_set())
            self.root.after(100, lambda: self.search_entry.select_range(0, tk.END))

        # Buttons
        button_frame = tk.Frame(content_frame, bg=self.bg_color)
        button_frame.pack(fill=tk.X)

        # Left side buttons
        left_button_frame = tk.Frame(button_frame, bg=self.bg_color)
        left_button_frame.pack(side=tk.LEFT, fill=tk.X, expand=True)

        # Right side buttons
        right_button_frame = tk.Frame(button_frame, bg=self.bg_color)
        right_button_frame.pack(side=tk.RIGHT)

        ModernButton(left_button_frame, "✓  Confirm Selection", on_select,
                     width=180, height=45, bg=self.bg_color).pack(side=tk.LEFT, padx=2)

        ModernButton(right_button_frame, "↻  Try Different Search", on_try_again,
                     width=160, height=45, bg=self.bg_color).pack(side=tk.LEFT, padx=2)

        ModernButton(right_button_frame, "✕  Cancel", on_cancel,
                     width=100, height=45, bg=self.bg_color).pack(side=tk.LEFT, padx=2)

        # Bind Enter key to confirm selection
        match_listbox.bind("<Double-Button-1>", lambda e: on_select())
        match_listbox.bind("<Return>", lambda e: on_select())

        self.root.wait_window(popup)

    def download_thread_start(self, app_id):
        """Start the download process in a new thread."""
        self.root.after(0, lambda: self.log(f"Selected App ID: {app_id}"))
        thread = threading.Thread(target=self.download_thread, args=(app_id,))
        thread.daemon = True
        thread.start()

    def download_thread(self, app_id):
        """Execute the complete download and installation process."""
        try:
            self.root.after(0, lambda: self.log(f"\n{'=' * 60}\nProcessing App ID: {app_id}\n{'=' * 60}"))
            self.root.after(0, lambda: self.update_status("Getting game details..."))

            # Fetch game details
            self.root.after(0, lambda: self.log("\n[1/5] Fetching store details..."))
            app_details = self.downloader.get_app_details(app_id)

            if app_details:
                game_name = app_details.get('name', 'Unknown')
                self.root.after(0, lambda: self.log(f"Found: {game_name}"))
            else:
                self.root.after(0, lambda: self.log("Store details not available"))

            # Download files
            self.root.after(0, lambda: self.update_status("Downloading files..."))
            success = self.downloader.download_appid_zip(
                app_id,
                log_callback=lambda msg: self.root.after(0, lambda m=msg: self.log(m))
            )

            if not success:
                self.root.after(0, lambda: messagebox.showerror("Download Failed",
                                                                "Could not download game data"))
                self.root.after(0, self.finish_processing)
                return

            self.root.after(0, lambda: self.log("Download complete"))
            self.root.after(0, lambda: self.update_status("Installing files..."))

            # Copy files to Steam
            self.downloader.copy_files_to_steam(
                log_callback=lambda msg: self.root.after(0, lambda m=msg: self.log(m))
            )
            self.root.after(0, lambda: self.log("Files installed"))

            # Restart Steam components
            self.root.after(0, lambda: self.update_status("Restarting Steam components..."))
            self.root.after(0, lambda: self.log("\n[5/5] Restarting Steam components..."))

            self.downloader.close_steam(
                log_callback=lambda msg: self.root.after(0, lambda m=msg: self.log(m))
            )
            time.sleep(1)

            self.downloader.launch_steamtools(
                log_callback=lambda msg: self.root.after(0, lambda m=msg: self.log(m))
            )
            time.sleep(2)

            self.downloader.start_steam(
                log_callback=lambda msg: self.root.after(0, lambda m=msg: self.log(m))
            )

            # Show success message
            self.root.after(0, lambda: self.update_status("Complete!"))
            self.root.after(0, lambda: self.log(f"\n{'=' * 60}\n✓ Complete!\n{'=' * 60}"))
            self.root.after(0, lambda: messagebox.showinfo("Success",
                                                           "Installation complete!\n\nSteam has been restarted."))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Fatal Error: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Fatal Error",
                                                            f"A fatal error occurred:\n{str(e)}"))

        finally:
            self.root.after(0, self.finish_processing)

    def finish_processing(self):
        """Reset GUI to ready state."""
        self.is_processing = False
        self.progress_bar.stop()
        self.install_btn.configure_state(True)
        self.update_status("Ready")

        # Clear selection popup reference
        self.selection_popup = None


def is_admin():
    """Check if running with administrator privileges (Windows)."""
    if sys.platform != 'win32':
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """Re-launch the script with administrator privileges (Windows)."""
    if sys.platform == 'win32':
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
    """Main entry point."""
    if sys.platform == 'win32':
        if not is_admin():
            messagebox.showwarning("Administrator Permissions Required",
                                   "This application requires Administrator permissions to modify Steam files.\n"
                                   "Restarting with elevated privileges...")
            run_as_admin()

    root = tk.Tk()
    app = SteamToolsInstaller(root)
    root.mainloop()


if __name__ == "__main__":
    main()