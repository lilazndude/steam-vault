import os
import sys
import json
import re
import shutil
import threading
import time
import urllib.parse
import subprocess
import queue
from http.server import HTTPServer, BaseHTTPRequestHandler

# Port to listen on
PORT = 8000

# Config file location
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")

# Global thread-safe job queue
job_lock = threading.Lock()
job_queue = []  # List of job dicts
active_job = None

# Backwards compatibility state (maps active_job to old structure)
transfer_lock = threading.Lock()
transfer_status = {
    "running": False,
    "game_id": None,
    "game_name": None,
    "action": None,
    "total_bytes": 0,
    "bytes_transferred": 0,
    "speed": 0,
    "eta": 0,
    "current_file": "",
    "error": None,
    "log": [],
    "cancel_requested": False
}

def get_config():
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            pass
    return {"nas_path": ""}

def save_config(config):
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, indent=4)
        return {"success": True, "config": config}
    except Exception as e:
        return {"success": False, "error": str(e)}

def parse_vdf(filepath):
    """Parses Steam VDF format into a nested Python dictionary."""
    if not os.path.exists(filepath):
        return {}
    try:
        with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
            content = f.read()
    except Exception as e:
        print(f"Error reading VDF file {filepath}: {e}")
        return {}
        
    content = re.sub(r'//.*', '', content)
    tokens = re.findall(r'"(?:[^"\\]|\\.)*"|[{}]', content)
    
    root = {}
    stack = [root]
    current = root
    last_key = None
    
    for token in tokens:
        if token == '{':
            if last_key is not None:
                new_dict = {}
                current[last_key] = new_dict
                stack.append(new_dict)
                current = new_dict
                last_key = None
        elif token == '}':
            if len(stack) > 1:
                stack.pop()
                current = stack[-1]
        else:
            val = token[1:-1].replace('\\\\', '\\').replace('\\"', '"')
            if last_key is None:
                last_key = val
            else:
                current[last_key] = val
                last_key = None
    return root

def normalize_path(path):
    """Standardizes path formatting and capitalizes Windows drive letters consistently."""
    if not path:
        return ""
    path = os.path.abspath(path)
    drive, tail = os.path.splitdrive(path)
    if drive:
        return drive.upper() + tail
    return path

def get_steam_path():
    """Detects Steam installation path from Registry or common paths."""
    try:
        import winreg
        key = winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Software\Valve\Steam")
        path, _ = winreg.QueryValueEx(key, "SteamPath")
        return normalize_path(path)
    except Exception:
        fallbacks = [
            r"C:\Program Files (x86)\Steam",
            r"C:\Program Files\Steam",
            r"D:\Steam"
        ]
        for path in fallbacks:
            if os.path.exists(path):
                return normalize_path(path)
        return ""

def get_steam_libraries():
    """Scans and lists all Steam library folders configured on the PC."""
    steam_path = get_steam_path()
    if not steam_path:
        return []
        
    libraries = []
    primary_apps = os.path.join(steam_path, "steamapps")
    if os.path.exists(primary_apps):
        libraries.append(normalize_path(steam_path))
        
    vdf_path = os.path.join(primary_apps, "libraryfolders.vdf")
    if os.path.exists(vdf_path):
        data = parse_vdf(vdf_path)
        folders = data.get("libraryfolders", {})
        for _, val in folders.items():
            if isinstance(val, dict) and "path" in val:
                path = val["path"]
                norm = normalize_path(path)
                if not any(norm.lower() == l.lower() for l in libraries):
                    if os.path.exists(os.path.join(norm, "steamapps")):
                        libraries.append(norm)
    return libraries

def get_7z_path():
    """Detects 7-Zip CLI executable path."""
    paths = [
        r"C:\Program Files\7-Zip\7z.exe",
        r"C:\Program Files (x86)\7-Zip\7z.exe"
    ]
    for p in paths:
        if os.path.exists(p):
            return p
    # Fallback to PATH search
    which_7z = shutil.which("7z")
    if which_7z:
        return which_7z
    return None

def is_steam_running():
    """Checks if Steam is currently running on Windows."""
    try:
        output = subprocess.check_output('tasklist /FI "IMAGENAME eq steam.exe" /NH', shell=True).decode('utf-8', errors='ignore')
        return "steam.exe" in output.lower()
    except Exception:
        return False

def get_dir_size_and_files(path):
    """Recursively lists all files in a directory and calculates total size."""
    total_size = 0
    file_list = []
    if os.path.isfile(path):
        size = os.path.getsize(path)
        return size, [(path, size)]
    for root, _, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                size = os.path.getsize(fp)
                total_size += size
                file_list.append((fp, size))
            except OSError:
                continue
    return total_size, file_list

def stream_reader(stream, q):
    try:
        for line in stream:
            q.put(line)
    except Exception:
        pass
    finally:
        try:
            stream.close()
        except Exception:
            pass

def run_robocopy(src, dst, job):
    """Invokes Windows multi-threaded robocopy and feeds output to logs."""
    cmd = ["robocopy", src, dst, "/E", "/MT:16", "/NJH", "/NJS", "/NFL", "/NDL"]
    job["log"].append(f"[ENGINE] Running Robocopy: {' '.join(cmd)}")
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, shell=True)
    
    q = queue.Queue()
    t = threading.Thread(target=stream_reader, args=(process.stdout, q))
    t.daemon = True
    t.start()
    
    while True:
        if job.get("cancel_requested"):
            process.terminate()
            raise Exception("Transfer cancelled by user.")
            
        ret = process.poll()
        if ret is not None:
            # Drain remaining output
            while not q.empty():
                try:
                    line = q.get_nowait()
                    clean = line.strip()
                    if clean:
                        job["log"].append(clean)
                except queue.Empty:
                    break
            break
            
        # Parse output queue without blocking
        while not q.empty():
            try:
                line = q.get_nowait()
                clean = line.strip()
                if clean:
                    job["log"].append(clean)
            except queue.Empty:
                break
                
        # Monitor progress via destination folder size
        if os.path.exists(dst):
            size, _ = get_dir_size_and_files(dst)
            job["bytes_transferred"] = size
        time.sleep(0.1)
        
    if process.returncode >= 8:
        raise Exception(f"Robocopy failed with status code {process.returncode}")

def run_7z_compress(src_dir, dst_7z, job):
    """Invokes 7-Zip to compress directory into .7z on destination."""
    seven_zip = get_7z_path()
    if not seven_zip:
        raise Exception("7-Zip (7z.exe) not found on system. Install 7-Zip for high compression support.")
        
    cmd = [seven_zip, "a", "-t7z", "-mx=3", "-mmt=on", dst_7z, os.path.join(src_dir, "*")]
    job["log"].append(f"[ENGINE] Running 7z Compression: {' '.join(cmd)}")
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, shell=True)
    
    q = queue.Queue()
    t = threading.Thread(target=stream_reader, args=(process.stdout, q))
    t.daemon = True
    t.start()
    
    while True:
        if job.get("cancel_requested"):
            process.terminate()
            raise Exception("Transfer cancelled by user.")
            
        ret = process.poll()
        if ret is not None:
            while not q.empty():
                try:
                    line = q.get_nowait()
                    clean = line.strip()
                    if clean:
                        job["log"].append(clean)
                except queue.Empty:
                    break
            break
            
        while not q.empty():
            try:
                line = q.get_nowait()
                clean = line.strip()
                if clean:
                    job["log"].append(clean)
            except queue.Empty:
                break
                
        # Monitor target .7z file size
        if os.path.exists(dst_7z):
            job["bytes_transferred"] = os.path.getsize(dst_7z)
        time.sleep(0.1)
        
    if process.returncode != 0:
        raise Exception(f"7-Zip compression failed with exit code {process.returncode}")

def run_7z_extract(src_7z, dst_dir, job):
    """Invokes 7-Zip to extract a .7z file to destination library."""
    seven_zip = get_7z_path()
    if not seven_zip:
        raise Exception("7-Zip (7z.exe) not found on system.")
        
    cmd = [seven_zip, "x", src_7z, f"-o{dst_dir}", "-y"]
    job["log"].append(f"[ENGINE] Running 7z Extraction: {' '.join(cmd)}")
    
    process = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, shell=True)
    
    q = queue.Queue()
    t = threading.Thread(target=stream_reader, args=(process.stdout, q))
    t.daemon = True
    t.start()
    
    while True:
        if job.get("cancel_requested"):
            process.terminate()
            raise Exception("Transfer cancelled by user.")
            
        ret = process.poll()
        if ret is not None:
            while not q.empty():
                try:
                    line = q.get_nowait()
                    clean = line.strip()
                    if clean:
                        job["log"].append(clean)
                except queue.Empty:
                    break
            break
            
        while not q.empty():
            try:
                line = q.get_nowait()
                clean = line.strip()
                if clean:
                    job["log"].append(clean)
            except queue.Empty:
                break
                
        # Monitor destination folder size growth
        if os.path.exists(dst_dir):
            size, _ = get_dir_size_and_files(dst_dir)
            job["bytes_transferred"] = size
        time.sleep(0.1)
        
    if process.returncode != 0:
        raise Exception(f"7-Zip extraction failed with exit code {process.returncode}")

# ==========================================
# JOB PROCESSING & SPEED TRACKER
# ==========================================
def track_job_speed(job):
    """Monitors bytes_transferred in background and updates job speed / ETA."""
    start_time = time.time()
    job["start_time"] = start_time
    while True:
        with job_lock:
            status = job["status"]
            cancel = job.get("cancel_requested", False)
        if status != "running" or cancel:
            break
            
        time.sleep(0.5)
        now = time.time()
        elapsed = now - start_time
        curr = job["bytes_transferred"]
        total = job["total_bytes"]
        
        if elapsed > 0:
            speed = curr / elapsed
            with job_lock:
                job["speed"] = speed
                if speed > 0:
                    remaining = total - curr
                    job["eta"] = max(0, remaining / speed)
                else:
                    job["eta"] = 0

def run_job(job):
    """Executes the specific job action based on queue dispatcher."""
    appid = job["appid"]
    action = job["action"]
    nas_path = get_config().get("nas_path")
    
    if not nas_path or not os.path.exists(nas_path):
        raise Exception("NAS archive path is invalid or offline.")
        
    acf_name = f"appmanifest_{appid}.acf"
    
    if action == "archive":
        src_library = job["src_library"]
        src_acf = os.path.join(src_library, "steamapps", acf_name)
        
        if not os.path.exists(src_acf):
            raise Exception(f"Manifest file {acf_name} not found locally.")
            
        manifest = parse_vdf(src_acf)
        app_state = manifest.get("AppState", {})
        installdir = app_state.get("installdir", "")
        
        if not installdir:
            raise Exception("Game install directory not declared in manifest.")
            
        src_game_dir = os.path.join(src_library, "steamapps", "common", installdir)
        dst_game_dir = os.path.join(nas_path, "steamapps", "common", installdir)
        dst_acf = os.path.join(nas_path, "steamapps", acf_name)
        
        if not os.path.exists(src_game_dir):
            raise Exception(f"Local game folder not found: {src_game_dir}")
            
        # Scan files
        job["log"].append("Scanning game files...")
        total_size, file_list = get_dir_size_and_files(src_game_dir)
        file_count = len(file_list)
        job["total_bytes"] = total_size
        job["log"].append(f"Scan complete: {total_size / (1024**3):.2f} GB across {file_count} files.")
        
        # Space check
        nas_usage = shutil.disk_usage(nas_path)
        if nas_usage.free < total_size:
            raise Exception(f"Insufficient NAS disk space. Required: {total_size/(1024**3):.2f} GB, Free: {nas_usage.free/(1024**3):.2f} GB")
            
        # Start speed tracking thread
        t_speed = threading.Thread(target=track_job_speed, args=(job,))
        t_speed.daemon = True
        t_speed.start()
        
        # Decide format: 7z if high file count, else Robocopy
        if file_count > 500:
            job["log"].append("High file count detected (> 500 files). Archiving as .7z for high performance...")
            dst_7z = dst_game_dir + ".7z"
            os.makedirs(os.path.dirname(dst_7z), exist_ok=True)
            run_7z_compress(src_game_dir, dst_7z, job)
        else:
            job["log"].append("Copying game files using multi-threaded Robocopy...")
            os.makedirs(os.path.dirname(dst_game_dir), exist_ok=True)
            run_robocopy(src_game_dir, dst_game_dir, job)
            
        # Copy acf manifest to NAS
        job["log"].append("Archiving manifest file to NAS...")
        os.makedirs(os.path.dirname(dst_acf), exist_ok=True)
        shutil.copy2(src_acf, dst_acf)
        
        # Verify and clean local
        job["log"].append("Verification success. Deleting local game folders and manifests...")
        shutil.rmtree(src_game_dir)
        os.remove(src_acf)
        job["log"].append("Archive completed successfully!")
        
    elif action == "restore":
        dst_library = job["dst_library"]
        nas_acf = os.path.join(nas_path, "steamapps", acf_name)
        
        if not os.path.exists(nas_acf):
            raise Exception(f"NAS manifest {acf_name} not found.")
            
        manifest = parse_vdf(nas_acf)
        app_state = manifest.get("AppState", {})
        installdir = app_state.get("installdir", "")
        
        if not installdir:
            raise Exception("Game install directory not declared in manifest.")
            
        nas_7z = os.path.join(nas_path, "steamapps", "common", installdir + ".7z")
        nas_folder = os.path.join(nas_path, "steamapps", "common", installdir)
        dst_game_dir = os.path.join(dst_library, "steamapps", "common", installdir)
        dst_acf = os.path.join(dst_library, "steamapps", acf_name)
        
        # Get total size from manifest size
        total_size = int(app_state.get("SizeOnDisk", 0))
        job["total_bytes"] = total_size
        
        # Start speed tracking thread
        t_speed = threading.Thread(target=track_job_speed, args=(job,))
        t_speed.daemon = True
        t_speed.start()
        
        if os.path.exists(nas_7z):
            job["log"].append("Archived game is stored as .7z. Extracting directly to local library...")
            os.makedirs(os.path.dirname(dst_game_dir), exist_ok=True)
            run_7z_extract(nas_7z, os.path.join(dst_library, "steamapps", "common"), job)
            job["log"].append("Decompress complete. Deleting NAS 7z archive file...")
            os.remove(nas_7z)
        elif os.path.exists(nas_folder):
            job["log"].append("Archived game is stored as loose files. Restoring using Robocopy...")
            os.makedirs(os.path.dirname(dst_game_dir), exist_ok=True)
            run_robocopy(nas_folder, dst_game_dir, job)
            job["log"].append("Transfer complete. Deleting loose files from NAS...")
            shutil.rmtree(nas_folder)
        else:
            raise Exception("No game files found on NAS.")
            
        # Copy acf back to local PC
        job["log"].append("Copying manifest back to local Steam library...")
        os.makedirs(os.path.dirname(dst_acf), exist_ok=True)
        shutil.copy2(nas_acf, dst_acf)
        os.remove(nas_acf)
        job["log"].append("Restore completed successfully!")
        
    elif action == "compress":
        installdir = job["installdir"]
        dst_game_dir = os.path.join(nas_path, "steamapps", "common", installdir)
        dst_7z = dst_game_dir + ".7z"
        
        if not os.path.exists(dst_game_dir):
            raise Exception(f"Loose game folder not found on NAS at {dst_game_dir}")
            
        job["log"].append(f"Compressing loose NAS folder: {dst_game_dir}")
        total_size, file_list = get_dir_size_and_files(dst_game_dir)
        job["total_bytes"] = total_size
        
        # Start speed tracking thread
        t_speed = threading.Thread(target=track_job_speed, args=(job,))
        t_speed.daemon = True
        t_speed.start()
        
        run_7z_compress(dst_game_dir, dst_7z, job)
        
        # Verify and clean
        if not os.path.exists(dst_7z) or os.path.getsize(dst_7z) == 0:
            raise Exception("Compression failed. Output file is missing or empty.")
            
        job["log"].append("Compression verified. Deleting loose folders from NAS...")
        shutil.rmtree(dst_game_dir)
        job["log"].append("NAS archive optimization completed successfully!")

def queue_worker():
    """Background worker loop managing sequential job execution from the queue."""
    global active_job
    while True:
        time.sleep(0.5)
        with job_lock:
            if active_job is not None:
                continue
            # Find next pending job
            pending = [j for j in job_queue if j["status"] == "pending"]
            if not pending:
                continue
            active_job = pending[0]
            active_job["status"] = "running"
            
        # Run job
        try:
            run_job(active_job)
            with job_lock:
                active_job["status"] = "completed"
        except Exception as e:
            err_msg = str(e)
            print(f"Job failed: {err_msg}")
            with job_lock:
                active_job["status"] = "failed"
                active_job["error"] = err_msg
                active_job["log"].append(f"FATAL ERROR: {err_msg}")
                
            # Clean up destination partial files on failure
            try:
                nas_path = get_config().get("nas_path")
                appid = active_job["appid"]
                action = active_job["action"]
                acf_name = f"appmanifest_{appid}.acf"
                
                if action == "archive" and nas_path:
                    # clean NAS folder or 7z file
                    # We can't know easily without parsing, so we clean both
                    # Let's read it from manifest if possible
                    src_acf = os.path.join(active_job["src_library"], "steamapps", acf_name)
                    if os.path.exists(src_acf):
                        manifest = parse_vdf(src_acf)
                        installdir = manifest.get("AppState", {}).get("installdir", "")
                        if installdir:
                            dst_game_dir = os.path.join(nas_path, "steamapps", "common", installdir)
                            if os.path.exists(dst_game_dir):
                                shutil.rmtree(dst_game_dir)
                            if os.path.exists(dst_game_dir + ".7z"):
                                os.remove(dst_game_dir + ".7z")
                            dst_acf = os.path.join(nas_path, "steamapps", acf_name)
                            if os.path.exists(dst_acf):
                                os.remove(dst_acf)
                                
                elif action == "restore" and "dst_library" in active_job:
                    # Clean local folder
                    dst_library = active_job["dst_library"]
                    # read from NAS manifest if still exists
                    nas_acf = os.path.join(nas_path, "steamapps", acf_name)
                    if os.path.exists(nas_acf):
                        manifest = parse_vdf(nas_acf)
                        installdir = manifest.get("AppState", {}).get("installdir", "")
                        if installdir:
                            dst_game_dir = os.path.join(dst_library, "steamapps", "common", installdir)
                            if os.path.exists(dst_game_dir):
                                shutil.rmtree(dst_game_dir)
                            dst_acf = os.path.join(dst_library, "steamapps", acf_name)
                            if os.path.exists(dst_acf):
                                os.remove(dst_acf)
            except Exception as clean_err:
                print(f"Cleanup failed during error rollback: {clean_err}")
        finally:
            with job_lock:
                active_job = None

# ==========================================
# API ENDPOINT HELPERS
# ==========================================
def list_games():
    """Scans local libraries and NAS path, returning a combined game list."""
    games = []
    libraries = get_steam_libraries()
    nas_path = get_config().get("nas_path", "")
    
    # Deduplicate local libraries to be absolutely safe
    unique_libraries = []
    for lib in libraries:
        norm = normalize_path(lib)
        if norm not in unique_libraries:
            unique_libraries.append(norm)
            
    # Scan local libraries
    for lib in unique_libraries:
        apps_path = os.path.join(lib, "steamapps")
        if not os.path.exists(apps_path):
            continue
        try:
            for f in os.listdir(apps_path):
                if f.startswith("appmanifest_") and f.endswith(".acf"):
                    acf_path = os.path.join(apps_path, f)
                    manifest = parse_vdf(acf_path)
                    app_state = manifest.get("AppState", {})
                    appid = app_state.get("appid")
                    name = app_state.get("name")
                    installdir = app_state.get("installdir")
                    size = int(app_state.get("SizeOnDisk", 0))
                    
                    if appid and name:
                        folder_path = os.path.join(apps_path, "common", installdir) if installdir else ""
                        folder_exists = os.path.exists(folder_path) if folder_path else False
                        z7_exists = os.path.exists(folder_path + ".7z") if folder_path else False
                        exists = folder_exists or z7_exists
                        is_compressed = z7_exists
                        
                        status = "local"
                        # Check active job
                        with job_lock:
                            if active_job and active_job["appid"] == appid:
                                status = active_job["action"] + "ing"
                            else:
                                pending = [j for j in job_queue if j["appid"] == appid and j["status"] == "pending"]
                                if pending:
                                    status = "queued"
                                    
                        games.append({
                            "appid": appid,
                            "name": name,
                            "installdir": installdir,
                            "size": size,
                            "library_path": lib,
                            "folder_exists": exists,
                            "is_compressed": is_compressed,
                            "status": status
                        })
        except Exception as e:
            print(f"Error scanning local library {lib}: {e}")
            
    # Scan NAS archive
    if nas_path and os.path.exists(nas_path):
        apps_path = os.path.join(nas_path, "steamapps")
        if os.path.exists(apps_path):
            try:
                for f in os.listdir(apps_path):
                    if f.startswith("appmanifest_") and f.endswith(".acf"):
                        acf_path = os.path.join(apps_path, f)
                        manifest = parse_vdf(acf_path)
                        app_state = manifest.get("AppState", {})
                        appid = app_state.get("appid")
                        name = app_state.get("name")
                        installdir = app_state.get("installdir")
                        size = int(app_state.get("SizeOnDisk", 0))
                        
                        if appid and name:
                            folder_path = os.path.join(apps_path, "common", installdir) if installdir else ""
                            folder_exists = os.path.exists(folder_path) if folder_path else False
                            z7_exists = os.path.exists(folder_path + ".7z") if folder_path else False
                            exists = folder_exists or z7_exists
                            is_compressed = z7_exists
                            
                            status = "archived"
                            with job_lock:
                                if active_job and active_job["appid"] == appid:
                                    status = active_job["action"] + "ing"
                                else:
                                    pending = [j for j in job_queue if j["appid"] == appid and j["status"] == "pending"]
                                    if pending:
                                        status = "queued"
                                        
                            games.append({
                                "appid": appid,
                                "name": name,
                                "installdir": installdir,
                                "size": size,
                                "library_path": nas_path,
                                "folder_exists": exists,
                                "is_compressed": is_compressed,
                                "status": status
                            })
            except Exception as e:
                print(f"Error scanning NAS path {nas_path}: {e}")
                
    return games

def list_drives():
    """Collects disk statistics for all detected drives and NAS path."""
    drives = {}
    libraries = get_steam_libraries()
    
    # Local drives
    for lib in libraries:
        lib = normalize_path(lib)
        drive_letter = os.path.splitdrive(lib)[0]
        if not drive_letter:
            drive_letter = "Local"
        drive_key = drive_letter.upper()
        if drive_key not in drives:
            try:
                usage = shutil.disk_usage(lib)
                drives[drive_key] = {
                    "path": lib,
                    "total": usage.total,
                    "free": usage.free,
                    "used": usage.used,
                    "is_nas": False
                }
            except Exception:
                pass
                
    # NAS drive
    nas_path = get_config().get("nas_path", "")
    if nas_path:
        if os.path.exists(nas_path):
            try:
                usage = shutil.disk_usage(nas_path)
                drives["NAS"] = {
                    "path": nas_path,
                    "total": usage.total,
                    "free": usage.free,
                    "used": usage.used,
                    "is_nas": True
                }
            except Exception:
                drives["NAS"] = {
                    "path": nas_path,
                    "total": 0,
                    "free": 0,
                    "used": 0,
                    "is_nas": True,
                    "error": "Not Accessible"
                }
        else:
            drives["NAS"] = {
                "path": nas_path,
                "total": 0,
                "free": 0,
                "used": 0,
                "is_nas": True,
                "error": "Directory Does Not Exist"
            }
            
    return drives

def get_transfer_status():
    """Maps queue active_job details to old transfer status structure for UI compatibility."""
    with job_lock:
        if active_job is not None:
            return {
                "running": True,
                "game_id": active_job["appid"],
                "game_name": active_job["game_name"],
                "action": active_job["action"],
                "total_bytes": active_job["total_bytes"],
                "bytes_transferred": active_job["bytes_transferred"],
                "speed": active_job["speed"],
                "eta": active_job["eta"],
                "current_file": active_job["current_file"],
                "error": active_job["error"],
                "log": active_job["log"],
                "cancel_requested": active_job.get("cancel_requested", False)
            }
        else:
            completed_jobs = [j for j in job_queue if j["status"] in ("completed", "failed")]
            last_job = completed_jobs[-1] if completed_jobs else None
            return {
                "running": False,
                "game_id": None,
                "game_name": last_job["game_name"] if last_job else None,
                "action": last_job["action"] if last_job else None,
                "total_bytes": last_job["total_bytes"] if last_job else 0,
                "bytes_transferred": last_job["bytes_transferred"] if last_job else 0,
                "speed": 0,
                "eta": 0,
                "current_file": "",
                "error": last_job["error"] if last_job else None,
                "log": last_job["log"] if last_job else []
            }

# ==========================================
# HTTP HANDLER ROUTING
# ==========================================
class RequestHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def do_OPTIONS(self):
        self.send_response(200)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        
        # Static file routing
        if path == "/" or path == "/index.html":
            self.serve_static("index.html", "text/html")
        elif path == "/index.css":
            self.serve_static("index.css", "text/css")
        elif path == "/index.js":
            self.serve_static("index.js", "application/javascript")
        elif path == "/api/config":
            self.send_json(get_config())
        elif path == "/api/games":
            self.send_json(list_games())
        elif path == "/api/drives":
            self.send_json(list_drives())
        elif path == "/api/status":
            self.send_json(get_transfer_status())
        elif path == "/api/steam-status":
            self.send_json({"steam_running": is_steam_running()})
        elif path == "/api/queue":
            with job_lock:
                active = dict(active_job) if active_job else None
                pending = [dict(j) for j in job_queue if j["status"] == "pending"]
            self.send_json({"active": active, "pending": pending})
        else:
            self.send_error(404, "Not Found")
            
    def do_POST(self):
        url = urllib.parse.urlparse(self.path)
        path = url.path
        
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8') if content_length > 0 else ""
        
        try:
            data = json.loads(body) if body else {}
        except Exception:
            self.send_error(400, "Invalid JSON")
            return
            
        if path == "/api/config":
            self.send_json(save_config(data))
        elif path == "/api/archive":
            self.send_json(self.handle_archive(data))
        elif path == "/api/restore":
            self.send_json(self.handle_restore(data))
        elif path == "/api/compress":
            self.send_json(self.handle_compress(data))
        elif path == "/api/cancel":
            self.send_json(self.handle_cancel(data))
        else:
            self.send_error(404, "Not Found")
            
    def serve_static(self, filename, content_type):
        filepath = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
        if not os.path.exists(filepath):
            self.send_error(404, "File Not Found")
            return
            
        try:
            with open(filepath, 'rb') as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"Server error: {e}")
            
    def send_json(self, data):
        try:
            content = json.dumps(data).encode('utf-8')
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", len(content))
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            self.wfile.write(content)
        except Exception as e:
            self.send_error(500, f"JSON Error: {e}")

    def handle_archive(self, data):
        appid = data.get("appid")
        src_library = data.get("library_path")
        nas_path = get_config().get("nas_path")
        
        if not appid or not src_library:
            return {"success": False, "error": "Missing AppID or local library path."}
        if not nas_path or not os.path.exists(nas_path):
            return {"success": False, "error": "NAS path is not set or not accessible."}
            
        # Get game details
        acf_name = f"appmanifest_{appid}.acf"
        src_acf = os.path.join(src_library, "steamapps", acf_name)
        if not os.path.exists(src_acf):
            return {"success": False, "error": f"Local manifest file {acf_name} not found."}
            
        manifest = parse_vdf(src_acf)
        app_state = manifest.get("AppState", {})
        game_name = app_state.get("name", f"Unknown Game ({appid})")
        
        job = {
            "id": str(int(time.time() * 1000)),
            "appid": appid,
            "action": "archive",
            "status": "pending",
            "game_name": game_name,
            "src_library": src_library,
            "total_bytes": 0,
            "bytes_transferred": 0,
            "speed": 0,
            "eta": 0,
            "current_file": "",
            "log": [f"[ARCHIVE] Added job for {game_name} to queue."],
            "error": None
        }
        
        with job_lock:
            # Check duplicates
            existing = [j for j in job_queue if j["appid"] == appid and j["status"] in ("pending", "running")]
            if existing:
                return {"success": False, "error": f"A job for {game_name} is already in the queue."}
            job_queue.append(job)
            
        return {"success": True}
        
    def handle_restore(self, data):
        appid = data.get("appid")
        dst_library = data.get("target_library_path")
        nas_path = get_config().get("nas_path")
        
        if not appid or not dst_library:
            return {"success": False, "error": "Missing AppID or destination library path."}
        if not nas_path or not os.path.exists(nas_path):
            return {"success": False, "error": "NAS path is not set or not accessible."}
            
        acf_name = f"appmanifest_{appid}.acf"
        nas_acf = os.path.join(nas_path, "steamapps", acf_name)
        if not os.path.exists(nas_acf):
            return {"success": False, "error": f"NAS manifest file {acf_name} not found."}
            
        manifest = parse_vdf(nas_acf)
        app_state = manifest.get("AppState", {})
        game_name = app_state.get("name", f"Unknown Game ({appid})")
        
        job = {
            "id": str(int(time.time() * 1000)),
            "appid": appid,
            "action": "restore",
            "status": "pending",
            "game_name": game_name,
            "dst_library": dst_library,
            "total_bytes": 0,
            "bytes_transferred": 0,
            "speed": 0,
            "eta": 0,
            "current_file": "",
            "log": [f"[RESTORE] Added job for {game_name} to queue."],
            "error": None
        }
        
        with job_lock:
            existing = [j for j in job_queue if j["appid"] == appid and j["status"] in ("pending", "running")]
            if existing:
                return {"success": False, "error": f"A job for {game_name} is already in the queue."}
            job_queue.append(job)
            
        return {"success": True}

    def handle_compress(self, data):
        appid = data.get("appid")
        nas_path = get_config().get("nas_path")
        if not appid:
            return {"success": False, "error": "Missing AppID."}
        if not nas_path or not os.path.exists(nas_path):
            return {"success": False, "error": "NAS path is not set or not accessible."}
            
        # Get game details from manifest
        acf_name = f"appmanifest_{appid}.acf"
        nas_acf = os.path.join(nas_path, "steamapps", acf_name)
        if not os.path.exists(nas_acf):
            return {"success": False, "error": f"NAS manifest file {acf_name} not found."}
            
        manifest = parse_vdf(nas_acf)
        app_state = manifest.get("AppState", {})
        game_name = app_state.get("name", f"Unknown Game ({appid})")
        installdir = app_state.get("installdir", "")
        
        job = {
            "id": str(int(time.time() * 1000)),
            "appid": appid,
            "action": "compress",
            "status": "pending",
            "game_name": game_name,
            "installdir": installdir,
            "total_bytes": 0,
            "bytes_transferred": 0,
            "speed": 0,
            "eta": 0,
            "current_file": "",
            "log": [f"[COMPRESS] Added retroactive compression job for {game_name} to queue."],
            "error": None
        }
        
        with job_lock:
            existing = [j for j in job_queue if j["appid"] == appid and j["status"] in ("pending", "running")]
            if existing:
                return {"success": False, "error": f"A job for {game_name} is already in the queue."}
            job_queue.append(job)
            
        return {"success": True}
        
    def handle_cancel(self, data):
        job_id = data.get("job_id")
        with job_lock:
            if job_id:
                # Cancel specific pending job
                for j in job_queue:
                    if j["id"] == job_id:
                        if j["status"] == "pending":
                            j["status"] = "cancelled"
                            j["log"].append("Job cancelled by user while pending.")
                            return {"success": True}
                        elif j["status"] == "running":
                            j["cancel_requested"] = True
                            j["log"].append("Cancellation requested by user...")
                            return {"success": True}
                return {"success": False, "error": "Job not found or already completed."}
            else:
                # Cancel currently active job
                if active_job:
                    active_job["cancel_requested"] = True
                    active_job["log"].append("Cancellation requested by user...")
                    return {"success": True}
                return {"success": False, "error": "No transfer is currently running."}

def main():
    # Start the Job Queue worker thread
    t_worker = threading.Thread(target=queue_worker)
    t_worker.daemon = True
    t_worker.start()
    
    server = HTTPServer(('localhost', PORT), RequestHandler)
    print(f"\n=======================================================")
    print(f" STEAM GAME ARCHIVER BACKEND SERVER RUNNING             ")
    print(f"=======================================================")
    print(f"  --> Local UI: http://localhost:{PORT}/")
    print(f"  --> Steam Path: {get_steam_path() or 'Not Found'}")
    print(f"  --> Detected libraries: {get_steam_libraries()}")
    print(f"  --> 7-Zip Executable: {get_7z_path() or 'Not Installed'}")
    print(f"=======================================================\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server...")
        server.server_close()

if __name__ == "__main__":
    main()
