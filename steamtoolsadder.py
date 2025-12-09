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
import ctypes  # For handling admin permissions on Windows


# --- SteamToolsDownloader Logic ---

class SteamToolsDownloader:

    def __init__(self):
        self.games_cache = {}
        self.base_url = "https://api.steampowered.com"
        # Base URL for the App ID manifest files
        self.r2_base_url = "https://pub-5b6d3b7c03fd4ac1afb5bd3017850e20.r2.dev"

        # Initialize and ensure SteamTools is found
        self.steamtools_exe = self.find_steamtools_exe()
        self._steam_folder = None

    def find_steamtools_exe(self):
        """
        Find SteamTools executable in common paths.
        The automatic installation logic has been REMOVED due to UAC conflicts.
        """
        common_paths = [
            Path.home() / "AppData" / "Local" / "SteamTools",
            Path.home() / "AppData" / "Roaming" / "SteamTools",
            Path("C:/Program Files/SteamTools"),
            Path("C:/Program Files (x86)/SteamTools"),
        ]

        for base_path in common_paths:
            if base_path.exists():
                # Search recursively for the executable
                for exe_file in base_path.rglob("SteamTools.exe"):
                    return exe_file
        return None

    # --- Utility Methods (Rest of the class methods remain unchanged) ---

    def get_app_list(self):
        """Cache the full Steam app list"""
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
        """Find Steam installation folder automatically, caching the result"""
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
        """
        Find game by name, AppID, or URL (includes fuzzy matching for typos).
        """
        if 'store.steampowered.com' in query or 'steamcommunity.com' in query:
            match = re.search(r'/app/(\d+)', query)
            if match:
                return int(match.group(1))

        if query.isdigit():
            return int(query)

        games = self.get_app_list()
        if not games:
            return None

        query_lower = query.lower()

        # Exact match
        if query_lower in games:
            return games[query_lower]

        # Fuzzy matching (Close matches for typos)
        matches = get_close_matches(query_lower, games.keys(), n=5,
                                    cutoff=0.7)

        if matches:
            # Return list of (name, app_id) for GUI selection
            return [(match, games[match]) for match in matches]

        return None

    def get_app_details(self, app_id):
        """Get detailed app information from Steam Store API"""
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
        """Download and extract appid.zip from R2 storage"""
        if log_callback: log_callback(f"[2/5] Downloading {app_id}.zip from R2 storage...")

        Path(output_dir).mkdir(parents=True, exist_ok=True)

        url = f"{self.r2_base_url}/{app_id}.zip"
        zip_path = Path(output_dir) / f"{app_id}.zip"

        try:
            response = requests.get(url, timeout=30, stream=True)
            if response.status_code == 404:
                if log_callback: log_callback(f"No data found for App ID {app_id}")
                return False

            response.raise_for_status()

            with open(zip_path, 'wb') as f:
                for chunk in response.iter_content(chunk_size=8192):
                    f.write(chunk)

            if log_callback: log_callback(f"Downloaded: {zip_path.name}")

            if log_callback: log_callback(f"Extracting...")
            with zipfile.ZipFile(zip_path, 'r') as zip_ref:
                zip_ref.extractall(output_dir)

            if log_callback: log_callback(f"Extracted successfully")

            # Delete zip file
            zip_path.unlink()

            return True

        except Exception as e:
            if log_callback: log_callback(f"Error during download/extraction: {e}")
            return False

    def copy_files_to_steam(self, source_dir="downloads", log_callback=None):
        """Copy lua, .st files to stplug-in and manifest files to depotcache"""
        source_path = Path(source_dir)
        lua_files = list(source_path.rglob("*.lua"))
        manifest_files = list(source_path.rglob("*.manifest"))
        st_files = list(source_path.rglob("*.st"))

        if not lua_files and not manifest_files and not st_files:
            if log_callback: log_callback("No files found to copy.")
            return False

        if log_callback: log_callback(f"\n[3/5] Copying files to Steam...")

        steam_folder = self.find_steam_folder()
        if not steam_folder:
            if log_callback: log_callback("\nCould not find Steam installation.")
            return False

        stplug_folder = steam_folder / 'config' / 'stplug-in'
        depotcache_folder = steam_folder / 'depotcache'

        stplug_folder.mkdir(parents=True, exist_ok=True)
        depotcache_folder.mkdir(parents=True, exist_ok=True)

        files_to_copy = lua_files + st_files

        # Copy plugin files
        if files_to_copy:
            if log_callback: log_callback(f"\nCopying plugin file(s) to config/stplug-in...")
            for file_path in files_to_copy:
                try:
                    dest_path = stplug_folder / file_path.name
                    shutil.copy2(file_path, dest_path)
                except Exception as e:
                    if log_callback: log_callback(f"  ✗ Failed: {e}")

        # Copy manifest files
        if manifest_files:
            if log_callback: log_callback(f"\nCopying manifest file(s) to depotcache...")
            for file_path in manifest_files:
                try:
                    dest_path = depotcache_folder / file_path.name
                    shutil.copy2(file_path, dest_path)
                except Exception as e:
                    if log_callback: log_callback(f"  ✗ Failed: {e}")

        if log_callback: log_callback(f"\n[4/5] Cleaning up...")
        try:
            shutil.rmtree(source_path)
            if log_callback: log_callback(f"✓ Deleted temporary files")
        except Exception as e:
            if log_callback: log_callback(f"⚠ Could not delete downloads folder: {e}")

        return True

    def close_steam(self, log_callback=None):
        """Close Steam completely."""
        try:
            subprocess.run(['taskkill', '/F', '/IM', 'steam.exe'],
                           capture_output=True, timeout=10)
            time.sleep(1)
            if log_callback: log_callback("✓ Steam closed")
            return True
        except Exception as e:
            if log_callback: log_callback(f"⚠ Could not close Steam: {e}")
            return False

    def start_steam(self, log_callback=None):
        """Start Steam."""
        steam_folder = self.find_steam_folder()
        if not steam_folder: return False
        steam_exe = steam_folder / 'steam.exe'
        if not steam_exe.exists(): return False

        try:
            subprocess.Popen([str(steam_exe)], shell=True)
            time.sleep(1)
            if log_callback: log_callback("✓ Steam started")
            return True
        except Exception as e:
            if log_callback: log_callback(f"⚠ Could not start Steam: {e}")
            return False

    def launch_steamtools(self, log_callback=None):
        """Launch SteamTools."""
        if not self.steamtools_exe:
            self.steamtools_exe = self.find_steamtools_exe()

        if not self.steamtools_exe or not self.steamtools_exe.exists():
            if log_callback: log_callback("⚠ SteamTools.exe not found. Skipping launch.")
            return False

        try:
            subprocess.Popen([str(self.steamtools_exe)], shell=True)
            time.sleep(2)
            if log_callback: log_callback("✓ SteamTools launched")
            return True
        except Exception as e:
            if log_callback: log_callback(f"⚠ Could not launch SteamTools: {e}")
            return False


# ------------------------------------------------------------------

class ModernButton(tk.Canvas):
    """Custom canvas button for modern look and feel."""

    def __init__(self, parent, text, command, **kwargs):
        super().__init__(parent, highlightthickness=0, **kwargs)
        self.command = command
        self.text = text

        # Colors
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
        self.is_enabled = enabled
        if enabled:
            self.itemconfig(self.rect, fill=self.bg_normal)
        else:
            self.itemconfig(self.rect, fill="#6c757d")  # Disabled color

    def draw(self):
        self.delete("all")
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()

        # Rounded rectangle
        self.rect = self.create_rounded_rect(0, 0, width, height, 10, fill=self.bg_normal, outline="")
        self.text_id = self.create_text(width // 2, height // 2, text=self.text,
                                        fill=self.fg_color, font=("Segoe UI", 11, "bold"))

    def create_rounded_rect(self, x1, y1, x2, y2, radius, **kwargs):
        points = [x1 + radius, y1, x2 - radius, y1, x2, y1, x2, y1 + radius,
                  x2, y2 - radius, x2, y2, x2 - radius, y2, x1 + radius, y2,
                  x1, y2, x1, y2 - radius, x1, y1 + radius, x1, y1]
        return self.create_polygon(points, smooth=True, **kwargs)

    def on_enter(self, e):
        if self.is_enabled:
            self.itemconfig(self.rect, fill=self.bg_hover)

    def on_leave(self, e):
        if self.is_enabled:
            self.itemconfig(self.rect, fill=self.bg_normal)

    def on_click(self, e):
        if not self.is_enabled:
            return

        self.itemconfig(self.rect, fill=self.bg_active)
        self.after(100, lambda: self.itemconfig(self.rect, fill=self.bg_hover))
        if self.command:
            self.command()


class SteamToolsInstaller:
    def __init__(self, root):
        self.root = root
        self.root.title("Steam Tools App Adder Made By Remix")
        self.root.geometry("600x600")
        self.root.resizable(False, False)

        # Modern colors
        self.bg_color = "#1a1b26"
        self.card_color = "#24283b"
        self.text_color = "#c0caf5"
        self.accent_color = "#5c7cfa"

        self.root.configure(bg=self.bg_color)

        self.downloader = SteamToolsDownloader()
        self.is_processing = False

        self.create_widgets()

        # Initial check for SteamTools
        if not self.downloader.steamtools_exe:
            self.install_btn.configure_state(False)
            self.update_status("ERROR: SteamTools not found.")
            messagebox.showerror("Missing Requirement",
                                 "SteamTools.exe was not found. Please install SteamTools manually and restart this application.")
            self.log("ERROR: SteamTools not found. Please install manually. Download from https://store2.gofile.io/download/web/b1610f35-acac-453b-9677-505200f0eefc/st-setup-1.8.17r2.exe")
    def create_widgets(self):
        # Main container
        main_frame = tk.Frame(self.root, bg=self.bg_color)
        main_frame.pack(fill=tk.BOTH, expand=True, padx=30, pady=30)

        # Title
        title = tk.Label(main_frame, text="Steam Tools App Adder",
                         font=("Segoe UI", 24, "bold"),
                         fg=self.text_color, bg=self.bg_color)
        title.pack(pady=(0, 10))

        subtitle = tk.Label(main_frame, text="Install games quickly and easily",
                            font=("Segoe UI", 11),
                            fg="#7982a9", bg=self.bg_color)
        subtitle.pack(pady=(0, 30))

        # Input card
        input_card = tk.Frame(main_frame, bg=self.card_color)
        input_card.pack(fill=tk.X, pady=(0, 20))

        input_inner = tk.Frame(input_card, bg=self.card_color)
        input_inner.pack(padx=20, pady=20)

        input_label = tk.Label(input_inner, text="App ID or Steam URL",
                               font=("Segoe UI", 10),
                               fg="#7982a9", bg=self.card_color)
        input_label.pack(anchor="w", pady=(0, 8))

        # Custom entry style
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

        self.install_btn = ModernButton(btn_frame, "Install", self.start_download,
                                        width=200, height=50, bg=self.bg_color)
        self.install_btn.pack()

        # Progress card
        progress_card = tk.Frame(main_frame, bg=self.card_color)
        progress_card.pack(fill=tk.BOTH, expand=True, pady=(0, 0))

        progress_inner = tk.Frame(progress_card, bg=self.card_color)
        progress_inner.pack(padx=20, pady=20, fill=tk.BOTH, expand=True)

        # Status label
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

        # Log area
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
        """Append message to log area."""
        self.log_text.config(state=tk.NORMAL)
        self.log_text.insert(tk.END, message + "\n")
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def update_status(self, status):
        """Update the status label."""
        self.status_label.config(text=status)

    def start_download(self):
        """Initial check and start of the download thread."""
        if self.is_processing:
            return

        # Check for SteamTools again before starting the process
        if not self.downloader.steamtools_exe:
            messagebox.showerror("Missing Requirement",
                                 "SteamTools.exe was not found. Please install SteamTools manually and restart this application.")
            return

        query = self.search_entry.get().strip()
        if not query:
            messagebox.showwarning("Input Required", "Please enter a game name, App ID, or URL")
            return

        self.is_processing = True
        self.install_btn.configure_state(False)
        self.progress_bar.start(10)

        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete(1.0, tk.END)
        self.log_text.config(state=tk.DISABLED)

        thread = threading.Thread(target=self.initial_search_thread, args=(query,))
        thread.daemon = True
        thread.start()

    def initial_search_thread(self, query):
        """Thread to perform the initial game search."""
        try:
            self.root.after(0, lambda: self.update_status("Searching for game..."))
            self.root.after(0, lambda: self.log(f"Searching: {query}"))

            app_match_result = self.downloader.find_game(query)

            if isinstance(app_match_result, int):
                self.root.after(0, lambda: self.download_thread_start(app_match_result))
            elif isinstance(app_match_result, list):
                self.root.after(0, lambda: self.show_match_selection(app_match_result))
            else:
                self.root.after(0, lambda: messagebox.showerror("Not Found", f"No game found for: {query}"))
                self.root.after(0, self.finish_processing)

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Error during search: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Error", f"An error occurred during search:\n{str(e)}"))
            self.root.after(0, self.finish_processing)

    def show_match_selection(self, matches):
        """Show a dialog box for the user to select the correct game (Fuzzy match support)."""

        popup = tk.Toplevel(self.root)
        popup.title("Did you mean...?")
        popup.transient(self.root)
        popup.grab_set()
        popup.focus_set()

        # Calculate center position for the popup
        root_x = self.root.winfo_x()
        root_y = self.root.winfo_y()
        root_width = self.root.winfo_width()
        root_height = self.root.winfo_height()

        popup_width = 300
        popup_height = 200

        center_x = root_x + (root_width - popup_width) // 2
        center_y = root_y + (root_height - popup_height) // 2

        popup.geometry(f"{popup_width}x{popup_height}+{center_x}+{center_y}")
        popup.configure(bg=self.card_color)

        tk.Label(popup, text="Found similar games. Please select:",
                 bg=self.card_color, fg=self.text_color, font=("Segoe UI", 10, "bold")).pack(pady=10)

        listbox_frame = tk.Frame(popup, bg=self.card_color)
        listbox_frame.pack(padx=10, fill=tk.X)

        match_listbox = tk.Listbox(listbox_frame, height=5, selectmode=tk.SINGLE,
                                   bg="#414868", fg=self.text_color, relief=tk.FLAT, bd=0,
                                   selectbackground=self.accent_color, font=("Segoe UI", 10))
        match_listbox.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        scrollbar = tk.Scrollbar(listbox_frame, command=match_listbox.yview)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        match_listbox.config(yscrollcommand=scrollbar.set)

        for name, app_id in matches:
            match_listbox.insert(tk.END, f"{name[:40]} (ID: {app_id})")

        match_listbox.select_set(0)

        def on_select():
            try:
                selection = match_listbox.curselection()
                if selection:
                    selected_text = match_listbox.get(selection[0])
                    match = re.search(r'\(ID: (\d+)\)', selected_text)
                    if match:
                        app_id = int(match.group(1))
                        popup.destroy()
                        self.download_thread_start(app_id)
                        return
                messagebox.showwarning("Selection Error", "Please select a game from the list.")
            except Exception as e:
                messagebox.showerror("Error", f"Error during selection: {str(e)}")
                popup.destroy()
                self.finish_processing()

        def on_cancel():
            popup.destroy()
            self.finish_processing()

        button_frame = tk.Frame(popup, bg=self.card_color)
        button_frame.pack(pady=10)

        ModernButton(button_frame, "Confirm", on_select, width=100, height=35, bg=self.card_color).pack(side=tk.LEFT,
                                                                                                        padx=5)
        ModernButton(button_frame, "Cancel", on_cancel, width=100, height=35, bg=self.card_color).pack(side=tk.LEFT,
                                                                                                       padx=5)

        self.root.wait_window(popup)

    def download_thread_start(self, app_id):
        """Starts the main download process in a new thread."""
        self.root.after(0, lambda: self.log(f"Selected App ID: {app_id}"))
        thread = threading.Thread(target=self.download_thread, args=(app_id,))
        thread.daemon = True
        thread.start()

    def download_thread(self, app_id):
        """Core download and installation logic."""
        try:
            self.root.after(0, lambda: self.log(f"\n{'=' * 60}\nProcessing App ID: {app_id}\n{'=' * 60}"))
            self.root.after(0, lambda: self.update_status("Getting game details..."))

            self.root.after(0, lambda: self.log("\n[1/5] Fetching store details..."))
            app_details = self.downloader.get_app_details(app_id)

            if app_details:
                game_name = app_details.get('name', 'Unknown')
                self.root.after(0, lambda: self.log(f"Found: {game_name}"))
            else:
                self.root.after(0, lambda: self.log("Store details not available"))

            self.root.after(0, lambda: self.update_status("Downloading files..."))
            success = self.downloader.download_appid_zip(app_id, log_callback=lambda msg: self.root.after(0,
                                                                                                          lambda: self.log(
                                                                                                              msg)))

            if not success:
                self.root.after(0, lambda: messagebox.showerror("Download Failed", "Could not download game data"))
                self.root.after(0, self.finish_processing)
                return

            self.root.after(0, lambda: self.log("Download complete"))
            self.root.after(0, lambda: self.update_status("Installing files..."))

            self.downloader.copy_files_to_steam(log_callback=lambda msg: self.root.after(0, lambda: self.log(msg)))
            self.root.after(0, lambda: self.log("Files installed"))

            self.root.after(0, lambda: self.update_status("Restarting Steam components..."))
            self.root.after(0, lambda: self.log("\n[5/5] Restarting Steam components..."))

            self.downloader.close_steam(log_callback=lambda msg: self.root.after(0, lambda: self.log(msg)))
            time.sleep(1)

            self.downloader.launch_steamtools(log_callback=lambda msg: self.root.after(0, lambda: self.log(msg)))
            time.sleep(2)

            self.downloader.start_steam(log_callback=lambda msg: self.root.after(0, lambda: self.log(msg)))

            self.root.after(0, lambda: self.update_status("Complete!"))
            self.root.after(0, lambda: self.log(f"\n{'=' * 60}\n✓ Complete!\n{'=' * 60}"))

            self.root.after(0, lambda: messagebox.showinfo("Success",
                                                           f"Installation complete!\n\nSteam has been restarted."))

        except Exception as e:
            self.root.after(0, lambda: self.log(f"Fatal Error: {str(e)}"))
            self.root.after(0, lambda: messagebox.showerror("Fatal Error", f"A fatal error occurred:\n{str(e)}"))

        finally:
            self.root.after(0, self.finish_processing)

    def finish_processing(self):
        """Resets the GUI to the ready state."""
        self.is_processing = False
        self.progress_bar.stop()
        self.install_btn.configure_state(True)
        self.update_status("Ready")


# ------------------------------------------------------------------

def is_admin():
    """Check if the current process is running with administrative privileges (Windows only)."""
    if sys.platform != 'win32':
        return True
    try:
        return ctypes.windll.shell32.IsUserAnAdmin()
    except:
        return False


def run_as_admin():
    """Re-launch the script with administrative privileges (Windows only)."""
    if sys.platform == 'win32':
        script = os.path.abspath(sys.argv[0])
        params = ' '.join(sys.argv[1:])
        try:
            # Use ShellExecuteW to re-run the script with "runas" verb
            ctypes.windll.shell32.ShellExecuteW(None, "runas", sys.executable, script, params, 1)
        except Exception as e:
            messagebox.showerror("Elevation Failed", f"Failed to request administrator privileges: {e}")
            sys.exit(1)
        sys.exit(0)
    else:
        return


def main():
    if sys.platform == 'win32':
        if not is_admin():
            messagebox.showwarning("Administrator Permissions Required",
                                   "This application requires **Administrator permissions** to modify Steam files. Restarting with elevated privileges...")
            run_as_admin()

    root = tk.Tk()
    app = SteamToolsInstaller(root)
    root.mainloop()


if __name__ == "__main__":
    main()
