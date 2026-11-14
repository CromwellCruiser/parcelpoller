# --- Always run inside this folder's venv, however the script is launched ---
# (double-click, shortcut or "py" all start Windows' default Python, which
#  doesn't have this project's packages; this hands over to venv\Scripts\pythonw.exe)
import os
import subprocess
import sys

_VENV_PYTHONW = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'venv', 'Scripts', 'pythonw.exe')
if sys.prefix == sys.base_prefix and os.path.isfile(_VENV_PYTHONW):
    subprocess.Popen([_VENV_PYTHONW, os.path.abspath(__file__)] + sys.argv[1:])
    sys.exit(0)
# ---------------------------------------------------------------------------

import tkinter as tk
from tkinter import scrolledtext, messagebox
import threading
import queue
import os
import re
from datetime import datetime
import pystray
from PIL import Image, ImageDraw
from plyer import notification

# Pydoll drives the browser through its Playwright-compatible layer, so the
# Playwright-style code below is unchanged; Pydoll adds a coherent browser
# fingerprint (replacing playwright_stealth) and Cloudflare Turnstile clicking.
# A .pyw has no console, so a missing package is reported in a dialog at start-up.
try:
    from pydoll.playwright.sync_api import (
        sync_playwright,
        Error as PlaywrightError,
        TimeoutError as PlaywrightTimeoutError,
    )
    PYDOLL_IMPORT_ERROR = None
except ImportError as _import_error:
    PYDOLL_IMPORT_ERROR = _import_error

    class PlaywrightError(Exception):
        pass

    class PlaywrightTimeoutError(PlaywrightError):
        pass


# --- Browser / CAPTCHA settings ---
# Turnstile usually passes headless from a home connection. If the log keeps
# saying "Blocked by Cloudflare challenge", set this to False: a real (visible)
# Edge window carries more trust, at the cost of windows popping up each round.
HEADLESS = True
TURNSTILE_WAIT_SECONDS = 8   # how long to look for a Turnstile box after each page load

EDGE_CANDIDATES = [
    os.path.join(os.environ.get('ProgramFiles(x86)', r'C:\Program Files (x86)'),
                 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    os.path.join(os.environ.get('ProgramFiles', r'C:\Program Files'),
                 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
    os.path.join(os.environ.get('LOCALAPPDATA', ''),
                 'Microsoft', 'Edge', 'Application', 'msedge.exe'),
]


def find_edge():
    """Pydoll ignores Playwright's channel="msedge", so point it at Edge directly.
    Returns None (= let Pydoll use Chrome) if Edge isn't found."""
    for path in EDGE_CANDIDATES:
        if path and os.path.isfile(path):
            return path
    return None


# Signs that a CAPTCHA is still blocking the page after Turnstile handling.
# Pydoll only clicks Turnstile checkboxes; image puzzles, reCAPTCHA and
# hCaptcha can't be solved, so these are reported rather than mistaken
# for "not delivered yet".
CHALLENGE_TITLE_WORDS = ['just a moment', 'attention required', 'verify you are human',
                         'security check', 'are you a robot']
CHALLENGE_SELECTORS = [
    ('Cloudflare challenge', '#challenge-form, #challenge-running, #cf-challenge-running'),
    ('reCAPTCHA', 'iframe[src*="recaptcha"][src*="bframe"], '
                  'iframe[src*="recaptcha"][src*="anchor"]:not([src*="size=invisible"])'),
    ('hCaptcha', 'iframe[src*="hcaptcha.com"]'),
]


# --- Files live next to the script, whatever the working directory is ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
POSTCODES_FILE = os.path.join(BASE_DIR, 'postcodes.txt')
URLS_FILE = os.path.join(BASE_DIR, 'urls.txt')

POLL_INTERVAL_SECONDS = 300
STATUS_CHECK_ATTEMPTS = 12
UI_QUEUE_INTERVAL_MS = 150
URL_SAVE_DEBOUNCE_MS = 1000

POSTCODE_SELECTOR = ('input[name*="postcode" i], input[id*="postcode" i], '
                     'input[placeholder*="postcode" i], input[name*="zip" i]')

# Words that suggest the postcode gate has opened onto a tracking page
TRACKING_PAGE_WORDS = ['in transit', 'out for delivery', 'delivered', 'collected', 'expected']

# A heading counts as "arrived" if it says delivered / ready to collect...
SUCCESS_RE = re.compile(
    r'\b(?:delivered|ready (?:to|for) collect(?:ion)?|ready to be collected'
    r'|available (?:to|for) collect(?:ion)?)\b'
)
# ...once phrases that use those words to mean "not yet" have been removed.
# ("undelivered" never matches SUCCESS_RE thanks to the word boundary.)
NEGATIVE_RE = re.compile(
    r"\b(?:not|never|be|being)\s+delivered\b"            # not / to be / will be / being delivered
    r"|n['’]t\s+(?:be\s+)?delivered\b"                    # wasn't / can't be delivered
    r"|\b(?:if|whether|when|once|until|after|before)\b[^.]*?\bdelivered\b"  # FAQ-style headings
)


def short_error(exc, limit=80):
    """First line of an exception message, trimmed (Playwright errors are multi-line)."""
    text = str(exc).strip().splitlines()[0] if str(exc).strip() else exc.__class__.__name__
    return text if len(text) <= limit else text[:limit] + '...'


def heading_says_delivered(text):
    text = ' '.join((text or '').lower().split())
    if not text or '?' in text:  # questions are FAQ headings, not statuses
        return False
    text = NEGATIVE_RE.sub(' ', text)
    return bool(SUCCESS_RE.search(text))


class ParcelTrackerWidget:
    def __init__(self, root):
        self.root = root
        self.root.title('Parcel Poller')
        self.root.geometry('450x450')
        self.root.attributes('-topmost', True)
        self.root.overrideredirect(True)

        # --- POSTCODE CONFIGURATION ---
        self.postcodes = []
        self.load_postcodes()

        # --- Buzz Gym Styling Variables ---
        self.bg_color = '#111111'
        self.panel_color = '#1f1f1f'
        self.text_color = '#ffffff'
        self.accent_color = '#FFEA00'
        self.accent_hover = '#FFD600'
        self.success_color = '#00E676'
        self.font_main = ('Segoe UI', 10, 'bold')
        self.font_log = ('Consolas', 9)

        self.root.configure(bg=self.bg_color)

        # --- State ---
        self.is_tracking = False       # only touched on the Tk (main) thread
        self.stop_event = None         # one Event per polling run
        self.tray_icon = None
        self.edge_path = None
        self._drag_x = None
        self._drag_y = None
        self._save_job = None

        # The URL list is shared with the polling thread, so guard it
        self._urls = []
        self._urls_lock = threading.Lock()

        # Background threads never touch Tk directly: they queue work here
        self.ui_queue = queue.Queue()

        # --- Custom Drag Header ---
        self.header = tk.Frame(root, bg=self.bg_color, relief='flat', bd=0)
        self.header.pack(fill='x', side='top', pady=(5, 0))

        self.header_label = tk.Label(
            self.header, text='/// PARCEL TRACKER',
            bg=self.bg_color, fg=self.accent_color, font=('Segoe UI', 11, 'bold italic')
        )
        self.header_label.pack(side='left', padx=20, pady=5)

        for widget in (self.header, self.header_label):
            widget.bind("<ButtonPress-1>", self.start_move)
            widget.bind("<ButtonRelease-1>", self.stop_move)
            widget.bind("<B1-Motion>", self.do_move)

        # --- UI Layout: URL Header & Clear Button ---
        self.url_header = tk.Frame(root, bg=self.bg_color)
        self.url_header.pack(fill='x', padx=20, pady=(10, 2))

        tk.Label(
            self.url_header, text='TRACKING URLS:',
            bg=self.bg_color, fg=self.text_color, font=self.font_main
        ).pack(side='left')

        self.clear_btn = tk.Button(
            self.url_header, text='[ CLEAR ALL ]', command=self.clear_urls,
            bg=self.bg_color, fg='#EF5350', relief='flat',
            font=('Segoe UI', 8, 'bold'), cursor='hand2',
            activebackground=self.bg_color, activeforeground='#D32F2F', borderwidth=0
        )
        self.clear_btn.pack(side='right')

        # --- UI Layout: Main Inputs ---
        self.url_input = scrolledtext.ScrolledText(
            root, height=4, width=50,
            bg=self.panel_color, fg=self.text_color,
            insertbackground=self.accent_color,
            relief='flat', font=('Segoe UI', 9), padx=10, pady=10
        )
        self.url_input.pack(pady=5, padx=20, fill='x')

        self.url_input.tag_config(
            'delivered_tag',
            background=self.success_color,
            foreground='#000000',
            font=('Segoe UI', 9, 'bold')
        )

        self.load_urls()
        self.url_input.edit_modified(False)
        self.url_input.bind('<<Modified>>', self._on_url_text_modified)

        self.start_btn = tk.Button(
            root, text='START POLLING', command=self.toggle_tracking,
            bg=self.accent_color, fg='black', relief='flat',
            font=('Segoe UI', 10, 'bold'), cursor='hand2',
            activebackground=self.accent_hover, activeforeground='black',
            borderwidth=0
        )
        self.start_btn.pack(pady=10, padx=20, fill='x', ipady=6)

        tk.Label(
            root, text='SYSTEM LOG:',
            bg=self.bg_color, fg=self.text_color, font=self.font_main
        ).pack(pady=(5, 2), anchor='w', padx=20)

        self.log_area = scrolledtext.ScrolledText(
            root, height=7, width=50, state='disabled',
            bg='#000000', fg=self.accent_color,
            relief='flat', font=self.font_log, padx=10, pady=10
        )
        self.log_area.pack(pady=(0, 10), padx=20, fill='both', expand=True)

        # --- Footer Action Buttons ---
        self.footer = tk.Frame(root, bg=self.bg_color)
        self.footer.pack(fill='x', padx=20, pady=(0, 20))

        self.dismiss_btn = tk.Button(
            self.footer, text='DISMISS (TO TRAY)', command=self.hide_window,
            bg=self.panel_color, fg=self.text_color, relief='flat',
            font=('Segoe UI', 9, 'bold'), cursor='hand2',
            activebackground='#333333', activeforeground='white', borderwidth=0
        )
        self.dismiss_btn.pack(side='left', fill='x', expand=True, padx=(0, 5), ipady=5)

        self.exit_btn = tk.Button(
            self.footer, text='EXIT ENTIRELY', command=self.quit_app,
            bg='#D32F2F', fg='white', relief='flat',
            font=('Segoe UI', 9, 'bold'), cursor='hand2',
            activebackground='#B71C1C', activeforeground='white', borderwidth=0
        )
        self.exit_btn.pack(side='right', fill='x', expand=True, padx=(5, 0), ipady=5)

        self.root.after(UI_QUEUE_INTERVAL_MS, self._drain_ui_queue)

    # --- Thread-safe UI plumbing ---
    def call_in_ui(self, func, *args):
        """Safe from any thread: run func(*args) on the Tk main thread."""
        self.ui_queue.put((func, args))

    def _drain_ui_queue(self):
        try:
            while True:
                func, args = self.ui_queue.get_nowait()
                try:
                    func(*args)
                except Exception as e:
                    try:
                        self._append_log(f'UI error: {short_error(e)}')
                    except Exception:
                        pass
        except queue.Empty:
            pass
        self.root.after(UI_QUEUE_INTERVAL_MS, self._drain_ui_queue)

    # --- Postcode / URL Persistence Mechanics ---
    def load_postcodes(self):
        try:
            with open(POSTCODES_FILE, 'r', encoding='utf-8') as f:
                self.postcodes = [line.strip() for line in f if line.strip()]
        except FileNotFoundError:
            self.postcodes = []
        except OSError:
            pass  # keep whatever was loaded before

    def load_urls(self):
        try:
            with open(URLS_FILE, 'r', encoding='utf-8') as f:
                saved_urls = f.read().strip()
                if saved_urls:
                    self.url_input.insert('1.0', saved_urls)
        except FileNotFoundError:
            pass
        except OSError as e:
            self.root.after(0, self.log_message, f'Could not read urls.txt: {short_error(e)}')
        self._refresh_urls_from_text()

    def _refresh_urls_from_text(self):
        raw_urls = self.url_input.get('1.0', tk.END).split('\n')
        urls = [url.strip() for url in raw_urls if url.strip()]
        with self._urls_lock:
            self._urls = urls
        return urls

    def get_urls_snapshot(self):
        with self._urls_lock:
            return list(self._urls)

    def save_urls(self):
        current_urls = self.url_input.get('1.0', tk.END).strip()
        try:
            with open(URLS_FILE, 'w', encoding='utf-8') as f:
                f.write(current_urls)
        except OSError as e:
            self.log_message(f'Could not save urls.txt: {short_error(e)}')

    def _on_url_text_modified(self, event=None):
        # <<Modified>> fires once per change of the flag, so reset it each time
        if not self.url_input.edit_modified():
            return
        self.url_input.edit_modified(False)
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
        self._save_job = self.root.after(URL_SAVE_DEBOUNCE_MS, self._commit_url_edits)

    def _commit_url_edits(self):
        """Save the box to disk and hand the new list to the poller (picked up next cycle)."""
        if self._save_job is not None:
            self.root.after_cancel(self._save_job)
            self._save_job = None
        self._refresh_urls_from_text()
        self.save_urls()

    def clear_urls(self):
        self.url_input.delete('1.0', tk.END)
        self._commit_url_edits()

    # --- UI & Alert Mechanics ---
    def start_move(self, event):
        self._drag_x = event.x
        self._drag_y = event.y

    def stop_move(self, event):
        self._drag_x = None
        self._drag_y = None

    def do_move(self, event):
        if self._drag_x is None or self._drag_y is None:
            return
        x = self.root.winfo_x() + event.x - self._drag_x
        y = self.root.winfo_y() + event.y - self._drag_y
        self.root.geometry(f"+{x}+{y}")

    def log_message(self, message):
        """Safe from any thread."""
        timestamp = datetime.now().strftime('%H:%M:%S')
        self.call_in_ui(self._append_log, f'[{timestamp}] {message}\n')

    def _append_log(self, line):
        self.log_area.config(state='normal')
        self.log_area.insert(tk.END, line)

        # Cap log at 100 lines so it doesn't grow forever
        lines = int(self.log_area.index('end-1c').split('.')[0])
        if lines > 100:
            self.log_area.delete('1.0', f'{lines - 100 + 1}.0')

        self.log_area.see(tk.END)
        self.log_area.config(state='disabled')

    def mark_delivered(self, domain, url):
        start_idx = self.url_input.search(url, '1.0', tk.END)
        if start_idx:
            end_idx = f"{start_idx}+{len(url)}c"
            self.url_input.tag_add('delivered_tag', start_idx, end_idx)

        def show_system_toast():
            try:
                notification.notify(
                    title="📦 Parcel Delivered!",
                    message=f"Update detected from {domain}",
                    app_name="Parcel Tracker",
                    timeout=8
                )
            except Exception:
                pass

        threading.Thread(target=show_system_toast, daemon=True).start()
        self.force_show_and_alert()

    # --- System Tray Logic ---
    def create_icon_image(self):
        image = Image.new('RGB', (64, 64), color='black')
        draw = ImageDraw.Draw(image)
        draw.rectangle((16, 16, 48, 48), fill=self.accent_color)
        return image

    def hide_window(self):
        self.root.withdraw()
        if self.tray_icon is not None:
            return
        # pystray calls these from its own thread, so they only queue work for Tk
        menu = pystray.Menu(
            pystray.MenuItem('Show', self._tray_show, default=True),
            pystray.MenuItem('Exit', self._tray_exit)
        )
        self.tray_icon = pystray.Icon("ParcelPoller", self.create_icon_image(), "Parcel Poller", menu)
        threading.Thread(target=self.tray_icon.run, daemon=True).start()

    def _tray_show(self, icon=None, item=None):
        self.call_in_ui(self.show_window)

    def _tray_exit(self, icon=None, item=None):
        self.call_in_ui(self.quit_app)

    def _remove_tray_icon(self):
        if self.tray_icon is not None:
            try:
                self.tray_icon.stop()
            except Exception:
                pass
            self.tray_icon = None

    def show_window(self):
        self._remove_tray_icon()
        self.root.deiconify()
        self.root.lift()

    def force_show_and_alert(self):
        self._remove_tray_icon()
        self.root.deiconify()
        self.root.lift()
        self.root.attributes('-topmost', True)
        self.root.bell()

    def quit_app(self):
        if self._save_job is not None:
            self._commit_url_edits()
        if self.stop_event is not None:
            self.stop_event.set()
        self.is_tracking = False
        self._remove_tray_icon()
        self.root.quit()

    # --- Tracking Logic (main thread side) ---
    def _set_button_running(self):
        self.start_btn.config(text='STOP POLLING', bg='#C62828', fg='white', activebackground='#B71C1C')

    def _set_button_idle(self):
        self.start_btn.config(text='START POLLING', bg=self.accent_color, fg='black',
                              activebackground=self.accent_hover)

    def toggle_tracking(self):
        if not self.is_tracking:
            self._commit_url_edits()
            if not self.get_urls_snapshot():
                messagebox.showwarning('Input Error', 'Please enter at least one URL.')
                return

            self.load_postcodes()  # pick up any edits to postcodes.txt
            self.url_input.tag_remove('delivered_tag', '1.0', tk.END)

            # A fresh Event per run: a previous run that is still winding down
            # keeps its own (already set) Event and can't be revived by this one.
            self.stop_event = threading.Event()
            delivered = set()
            self.is_tracking = True
            self._set_button_running()

            self.poll_thread = threading.Thread(
                target=self.poll_loop, args=(self.stop_event, delivered), daemon=True
            )
            self.poll_thread.start()
        else:
            self.is_tracking = False
            if self.stop_event is not None:
                self.stop_event.set()
            self._set_button_idle()
            self.log_message('Polling stopped.')

    def _on_poll_finished(self, stop):
        # Ignore a finished run if a newer one has already started
        if self.stop_event is stop:
            self.is_tracking = False
            self._set_button_idle()

    # --- Tracking Logic (background thread side) ---
    def poll_loop(self, stop, delivered):
        try:
            with sync_playwright() as p:
                self.edge_path = find_edge()
                engine = 'Edge' if self.edge_path else 'Chrome (Edge not found)'
                mode = 'headless' if HEADLESS else 'visible'
                self.log_message(f'Pydoll engine ready: {engine}, {mode}.')

                while not stop.is_set():
                    urls = self.get_urls_snapshot()
                    pending = [u for u in urls if u not in delivered]
                    if not pending:
                        if urls:
                            self.log_message('All parcels delivered/ready. Polling stopped.')
                        else:
                            self.log_message('No URLs left to poll. Polling stopped.')
                        break

                    try:
                        self.run_cycle(p, stop, pending, delivered)
                    except Exception as e:
                        # One bad cycle (e.g. Edge mid-update) shouldn't end polling for good
                        if not stop.is_set():
                            self.log_message(f'Cycle failed, retrying next round: {short_error(e)}')

                    # Browser is closed while we wait; wakes immediately on Stop
                    if stop.wait(POLL_INTERVAL_SECONDS):
                        break

        except Exception as e:
            if not stop.is_set():
                self.log_message(f'Engine error: {short_error(e)}')
        finally:
            self.call_in_ui(self._on_poll_finished, stop)

    def run_cycle(self, p, stop, urls, delivered):
        # No user-agent, viewport or stealth overrides: Pydoll builds a matching
        # fingerprint itself, and patching parts of it would make it inconsistent.
        browser = p.chromium.launch(executable_path=self.edge_path, headless=HEADLESS)
        try:
            context = browser.new_context()

            for url in urls:
                if stop.is_set():
                    break
                self.check_url(context, stop, url, delivered)
        finally:
            # Closing the browser also closes its context(s), even if new_context failed
            try:
                browser.close()
            except Exception:
                pass

    def check_url(self, context, stop, url, delivered):
        domain = url.split('//')[-1].split('/')[0]
        self.log_message(f'Checking {domain}...')

        page = None
        try:
            page = context.new_page()

            # Pydoll watches for a Turnstile box after the page's load event and
            # clicks it if one shows up. wait_until='load' matters: the block waits
            # for that load event before it exits, so goto must not return earlier.
            with page.tab.expect_cloudflare_turnstile(time_to_wait_captcha=TURNSTILE_WAIT_SECONDS):
                page.goto(url, wait_until='load', timeout=25000)
            page.wait_for_timeout(2000)

            self.handle_postcode_gate(page, stop)
            is_delivered = self.wait_for_delivered_status(page, stop)

            if stop.is_set():
                return
            if is_delivered:
                delivered.add(url)  # so it is never re-checked or re-alerted this run
                self.log_message(f'DELIVERED/READY: {domain}')
                self.call_in_ui(self.mark_delivered, domain, url)
                return

            blocker = self.detect_unsolved_captcha(page)
            if blocker == 'Cloudflare challenge' and HEADLESS:
                self.log_message(f'Blocked by {blocker}: {domain} (try HEADLESS = False)')
            elif blocker:
                self.log_message(f"Blocked by {blocker}: {domain} (can't be auto-solved)")
            else:
                self.log_message(f'Not delivered yet: {domain}')

        except PlaywrightTimeoutError:
            if not stop.is_set():
                self.log_message(f'Timeout fetching {domain}')
        except Exception as e:
            if not stop.is_set():
                self.log_message(f'Error fetching {domain}: {short_error(e, 40)}')
        finally:
            if page is not None:
                try:
                    page.close()
                except Exception:
                    pass

    def handle_postcode_gate(self, page, stop):
        if not self.postcodes:
            return
        pc_locator = page.locator(POSTCODE_SELECTOR).first
        try:
            if pc_locator.count() == 0 or not pc_locator.is_visible():
                return
        except PlaywrightError:
            return

        self.log_message('Postcode gate detected.')
        for pc in self.postcodes:
            if stop.is_set():
                return
            try:
                pc_locator.fill(pc, timeout=5000)
                pc_locator.press("Enter", timeout=5000)
                page.wait_for_timeout(3000)

                if not pc_locator.is_visible():
                    return

                # inner_text = what's rendered on screen; text_content would also
                # include <script> source, which often contains these very words
                body_text = (page.locator("body").inner_text(timeout=5000) or '').lower()
                if any(w in body_text for w in TRACKING_PAGE_WORDS):
                    return
            except PlaywrightError:
                return

    @staticmethod
    def detect_unsolved_captcha(page):
        """Name of the CAPTCHA still blocking the page, or None."""
        try:
            title = (page.title() or '').lower()
            if any(w in title for w in CHALLENGE_TITLE_WORDS):
                return 'Cloudflare challenge' if 'moment' in title or 'attention' in title else 'CAPTCHA'
            for label, selector in CHALLENGE_SELECTORS:
                loc = page.locator(selector).first
                if loc.count() > 0 and loc.is_visible():
                    return label
        except PlaywrightError:
            pass
        return None

    def wait_for_delivered_status(self, page, stop):
        """Check the title and h1-h3 headings once a second for up to ~12 seconds."""
        for _ in range(STATUS_CHECK_ATTEMPTS):
            if stop.is_set():
                return False
            page.wait_for_timeout(1000)
            try:
                # One snapshot call: no per-element waits if headings vanish mid-render
                headings = [page.title()] + page.locator('h1, h2, h3').all_text_contents()
            except PlaywrightError:
                continue  # page was mid-navigation; try again next second
            if any(heading_says_delivered(h) for h in headings):
                return True
        return False


if __name__ == '__main__':
    root = tk.Tk()
    if PYDOLL_IMPORT_ERROR is not None:
        root.withdraw()
        messagebox.showerror(
            'Parcel Tracker',
            'Pydoll is not installed (needs Python 3.10+).\n\n'
            'Run this in a terminal, then start the tracker again:\n\n'
            '    pip install pydoll-python\n\n'
            f'Details: {PYDOLL_IMPORT_ERROR}'
        )
        root.destroy()
        raise SystemExit(1)
    app = ParcelTrackerWidget(root)
    root.mainloop()
    try:
        root.destroy()
    except tk.TclError:
        pass
