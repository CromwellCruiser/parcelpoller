# Parcel Poller

A small always-on-top Windows widget that watches courier tracking pages for you and tells you the moment a parcel is **delivered** or **ready to collect**.

Paste in your tracking links, press **Start Polling**, and dismiss it to the system tray. Every five minutes it opens each page in a hidden Microsoft Edge window and reads the status. When a parcel arrives, the link turns green, a Windows notification pops up and the widget jumps back out of the tray.

## Features

- **Works with most courier tracking pages.** It reads the page title and headings rather than relying on any one courier's layout.
- **Handles postcode gates.** If a tracking page asks for your postcode, it tries each one in `postcodes.txt` until the page opens.
- **Gets past Cloudflare Turnstile checkboxes** automatically, via [Pydoll](https://github.com/autoscrape-labs/pydoll).
- **Says when it's blocked.** A page stuck behind a CAPTCHA is logged as *blocked*, not quietly reported as "not delivered".
- **Alerts once per parcel.** After that it stops checking that link, and polling stops on its own once everything has arrived.
- **Lives in the tray**, with a draggable, borderless window that stays on top.

## Requirements

- Windows 10 or 11
- Python **3.10 or newer**
- Microsoft Edge, which comes with Windows. If Edge isn't found, it falls back to Google Chrome.

## Installation

```bat
git clone https://github.com/CromwellCruiser/parcelpoller.git
cd parcelpoller
python -m venv venv
venv\Scripts\python.exe -m pip install -r requirements.txt
```

Then double-click `ParcelTrack.pyw`.

If a `venv` folder sits next to the script, the script always relaunches itself with that environment's Python. Double-clicking, a desktop shortcut or `py ParcelTrack.pyw` all end up in the same place, with the right packages.

## Usage

1. Paste one tracking URL per line into the **Tracking URLs** box. The list saves itself to `urls.txt` about a second after you stop typing.
2. If any of your couriers ask for a postcode, put your postcode(s) in `postcodes.txt`, one per line.
3. Press **Start Polling**. Watch the system log, or press **Dismiss (to tray)** and forget about it.
4. When a parcel is delivered, you'll get a notification and the widget reappears with that link highlighted green.

To bring the window back from the tray, click the tray icon (or right-click it and choose **Show**). **Exit Entirely** saves your URL list and quits.

### What the log messages mean

| Message | Meaning |
|---|---|
| `DELIVERED/READY: example.com` | The page says delivered or ready to collect. You'll be alerted once. |
| `Not delivered yet: example.com` | The page loaded and showed no delivered status. It'll check again next round. |
| `Blocked by Cloudflare challenge … (try HEADLESS = False)` | Cloudflare's full-page check didn't clear. See [Settings](#settings). |
| `Blocked by reCAPTCHA / hCaptcha … (can't be auto-solved)` | The page wants an image puzzle solved, which this tool can't do. |
| `Timeout fetching example.com` | The page took longer than 25 seconds to load. It'll retry next round. |
| `Cycle failed, retrying next round` | The browser couldn't start (e.g. Edge was updating). Polling carries on. |

## Settings

These are constants near the top of `ParcelTrack.pyw`:

| Setting | Default | What it does |
|---|---|---|
| `POLL_INTERVAL_SECONDS` | `300` | Time between rounds of checks. |
| `HEADLESS` | `True` | Set to `False` to use a visible Edge window. Cloudflare trusts a visible window more, so try this if pages keep coming back "blocked". |
| `TURNSTILE_WAIT_SECONDS` | `8` | How long to look for a Turnstile checkbox after each page loads. Lower it if none of your couriers use Cloudflare. |
| `STATUS_CHECK_ATTEMPTS` | `12` | How many seconds to keep re-reading a page for its status, for pages that load their tracking info slowly. |

## How delivery is detected

Each page's title and `<h1>`–`<h3>` headings are checked for phrases such as *delivered*, *ready to collect* or *ready for collection*. Phrases that use those words to mean "not yet" are ignored, for example *to be delivered*, *will be delivered*, *not delivered*, *wasn't delivered*, *undelivered*, or FAQ questions such as *When will my parcel be delivered?*. Words like *collected* and *picked up* are deliberately not treated as delivered, because couriers also use them for the first scan, when they collect the parcel from the sender.

This is a heuristic tuned for English-language tracking pages. If a courier words things unusually, adjust `SUCCESS_RE` and `NEGATIVE_RE` near the top of the script.

## Keeping your details out of git

`urls.txt` and `postcodes.txt` are committed empty so the app works straight after cloning. If you fork or contribute, stop git tracking your own filled-in copies:

```bat
git update-index --skip-worktree urls.txt postcodes.txt
```

## Limitations and fair use

- CAPTCHA handling only covers Cloudflare Turnstile checkboxes. reCAPTCHA and hCaptcha image challenges are reported, not solved.
- This project isn't affiliated with any courier. It only reads tracking pages you could open yourself in a browser. Please keep the polling interval reasonable and respect each site's terms of use.

## Licence

[MIT](LICENSE)
