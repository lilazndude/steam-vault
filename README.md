# Steam Game Archiver & Manager

A lightweight, premium web-based application designed to help you archive your Steam games to a Network Attached Storage (NAS) or secondary drive, freeing up local disk space while keeping games easily restorable.

Developed in pure **Python 3** (backend) and **Vanilla HTML5/CSS3/JavaScript** (frontend) with **zero external dependencies**. No `npm install`, no `pip install`, no complex setup.

---

## Features

-   **Zero-Dependency Architecture**: Runs out-of-the-box on Windows using standard libraries.
-   **Sequential Batch Queue**: Queue up multiple game archives, restores, or compressions. The background worker processes them one-by-one to avoid disk thrashing and network saturation.
-   **Smart Hybrid Transfer Engine**:
    -   Uses Windows multi-threaded **Robocopy** for fast raw copy of games with large files.
    -   Uses **7-Zip** (`7z.exe`) automatically to compress games with high file counts (e.g. Geometry Dash with 9,000+ files) into `.7z` archives, bypassing SMB network handshaking overhead.
-   **Retroactive Archive Compression**: Compresses existing loose NAS game directories into space-efficient `.7z` archives directly from the UI.
-   **Live Terminal Console**: Live status logs, speeds, progress bars, and ETAs stream directly to a collapsable console panel in the UI.
-   **Storage Pools Metering**: Visually monitors free/used space of your local library drives and NAS folder in real-time.
-   **Active Steam Lock Check**: Warns you in the UI if the Steam client is open to prevent file manifest lock conflicts.
-   **Premium Cyberpunk Aesthetic**: Modern responsive glassmorphic dark UI with animated gradients and glow effects.

---

## How It Works

1.  **Detection**: The backend checks the Windows Registry to locate your default Steam install and parses Steam's `libraryfolders.vdf` file to detect game manifests (`appmanifest_*.acf`).
2.  **Archive**: Copies/Compresses the game files from your PC's `common/` folder to the target NAS directory, copies the manifest file, and removes the local versions.
3.  **Restore**: Decompresses or copies files back to a local Steam library and restores the manifest file, prompting Steam to immediately recognize the game as installed.

---

## Setup & Running

### Prerequisites
1.  **Python 3.x** installed.
2.  **7-Zip** installed (default path: `C:\Program Files\7-Zip\7z.exe`). Used for fast compression.
3.  A **NAS Network Share** or secondary drive writable from your PC.

### Installation
Clone or download this folder onto your PC:
```bash
git clone https://github.com/your-username/steam-archiver.git
cd steam-archiver
```

### Running the App
Double-click the `server.py` file or run the following command in your terminal:
```bash
python server.py
```

Open your browser and navigate to:
[http://localhost:8000/](http://localhost:8000/)

---

## Configuration

1.  Click the **Configure NAS** button in the top right.
2.  Enter the absolute local path or SMB network share path (e.g. `\\YOUR-NAS\share\SteamArchive`).
3.  Save configuration. The dashboard will now scan both your local and NAS libraries.
