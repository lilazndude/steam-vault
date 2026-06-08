// Global State
let games = [];
let drives = {};
let config = { nas_path: "" };
let transferStatus = { running: false };
let statusInterval = null;

// DOM Elements
const elements = {
  btnSettings: document.getElementById("btn-settings"),
  modalSettings: document.getElementById("modal-settings"),
  btnSaveSettings: document.getElementById("btn-save-settings"),
  btnCancelSettings: document.getElementById("btn-cancel-settings"),
  btnCloseModal: document.getElementById("btn-close-modal"),
  nasPathInput: document.getElementById("nas-path-input"),
  settingsStatusMsg: document.getElementById("settings-status-msg"),
  
  drivesContainer: document.getElementById("drives-container"),
  
  activeTransferPanel: document.getElementById("active-transfer-panel"),
  transferGameName: document.getElementById("transfer-game-name"),
  transferActionType: document.getElementById("transfer-action-type"),
  transferProgressFill: document.getElementById("transfer-progress-fill"),
  transferPercentage: document.getElementById("transfer-percentage"),
  transferDetails: document.getElementById("transfer-details"),
  transferSpeed: document.getElementById("transfer-speed"),
  transferEta: document.getElementById("transfer-eta"),
  transferFile: document.getElementById("transfer-file"),
  btnCancelTransfer: document.getElementById("btn-cancel-transfer"),
  
  searchInput: document.getElementById("search-input"),
  filterStatus: document.getElementById("filter-status"),
  sortBy: document.getElementById("sort-by"),
  
  btnRefresh: document.getElementById("btn-refresh"),
  libraryCount: document.getElementById("library-count"),
  gamesGrid: document.getElementById("games-grid"),
  
  consoleToggle: document.getElementById("console-toggle"),
  consoleChevron: document.getElementById("console-chevron"),
  consoleBody: document.getElementById("console-body"),
  consoleLog: document.getElementById("console-log"),
  steamStatusBanner: document.getElementById("steam-status-banner")
};

// ==========================================
// INITIALIZATION
// ==========================================
document.addEventListener("DOMContentLoaded", () => {
  setupEventListeners();
  initializeApp();
});

function initializeApp() {
  fetchConfig();
  refreshData();
  // Poll Steam status every 5 seconds
  setInterval(checkSteamStatus, 5000);
}

function setupEventListeners() {
  // Settings Modal
  elements.btnSettings.addEventListener("click", () => openModal());
  elements.btnCloseModal.addEventListener("click", () => closeModal());
  elements.btnCancelSettings.addEventListener("click", () => closeModal());
  elements.btnSaveSettings.addEventListener("click", saveSettings);
  
  // Refresh & Search
  elements.btnRefresh.addEventListener("click", refreshData);
  elements.searchInput.addEventListener("input", renderGames);
  elements.filterStatus.addEventListener("change", renderGames);
  elements.sortBy.addEventListener("change", renderGames);
  
  // Active Transfer
  elements.btnCancelTransfer.addEventListener("click", cancelTransfer);
  
  // Console panel toggle
  elements.consoleToggle.addEventListener("click", () => {
    elements.consoleBody.classList.toggle("collapsed");
    elements.consoleChevron.classList.toggle("collapsed");
  });
}

// ==========================================
// UTILITY FUNCTIONS
// ==========================================
function formatBytes(bytes, decimals = 2) {
  if (bytes === 0) return "0 Bytes";
  const k = 1024;
  const dm = decimals < 0 ? 0 : decimals;
  const sizes = ["Bytes", "KB", "MB", "GB", "TB"];
  const i = Math.floor(Math.log(bytes) / Math.log(k));
  return parseFloat((bytes / Math.pow(k, i)).toFixed(dm)) + " " + sizes[i];
}

function formatSpeed(bytesPerSec) {
  return formatBytes(bytesPerSec, 1) + "/s";
}

function formatETA(seconds) {
  if (isNaN(seconds) || seconds === Infinity || seconds <= 0) return "--:--:--";
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  return [
    h.toString().padStart(2, '0'),
    m.toString().padStart(2, '0'),
    s.toString().padStart(2, '0')
  ].join(':');
}

// Generate unique HSL gradient based on AppID hash
function getUniqueGradient(appid) {
  let hash = 0;
  for (let i = 0; i < appid.length; i++) {
    hash = appid.charCodeAt(i) + ((hash << 5) - hash);
  }
  const hue1 = Math.abs(hash % 360);
  const hue2 = (hue1 + 40) % 360;
  return `linear-gradient(135deg, hsl(${hue1}, 75%, 45%), hsl(${hue2}, 75%, 25%))`;
}

// ==========================================
// API CLIENT CALLS
// ==========================================
async function refreshData() {
  showGamesLoading();
  await Promise.all([
    fetchDrives(),
    fetchGames(),
    checkSteamStatus()
  ]);
  checkTransferStatus();
}

async function fetchConfig() {
  try {
    const res = await fetch("/api/config");
    config = await res.json();
    elements.nasPathInput.value = config.nas_path || "";
  } catch (err) {
    console.error("Error fetching config:", err);
  }
}

async function fetchDrives() {
  try {
    const res = await fetch("/api/drives");
    drives = await res.json();
    renderDrives();
  } catch (err) {
    console.error("Error fetching drives:", err);
  }
}

async function fetchGames() {
  try {
    const res = await fetch("/api/games");
    games = await res.json();
    renderGames();
  } catch (err) {
    console.error("Error fetching games:", err);
    showGamesError();
  }
}

async function checkSteamStatus() {
  try {
    const res = await fetch("/api/steam-status");
    const data = await res.json();
    const banner = elements.steamStatusBanner;
    if (data.steam_running) {
      banner.className = "steam-warning-banner running";
      banner.innerHTML = `
        <svg class="warning-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="12" y1="8" x2="12" y2="12"></line>
          <line x1="12" y1="16" x2="12.01" y2="16"></line>
        </svg>
        <span><strong>Steam is Running!</strong> Please exit the Steam Client fully (Steam ➔ Exit) before archiving or restoring games to prevent manifest conflicts.</span>
      `;
    } else {
      banner.className = "steam-warning-banner closed";
      banner.innerHTML = `
        <svg class="warning-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
          <path d="M22 11.08V12a10 10 0 1 1-5.93-9.14"></path>
          <polyline points="22 4 12 14.01 9 11.01"></polyline>
        </svg>
        <span><strong>Steam is Closed.</strong> It is safe to archive and restore games.</span>
      `;
    }
  } catch (err) {
    console.error("Error checking Steam status:", err);
  }
}

async function saveSettings() {
  const path = elements.nasPathInput.value.trim();
  
  elements.btnSaveSettings.disabled = true;
  elements.settingsStatusMsg.className = "status-msg hidden";
  
  try {
    const res = await fetch("/api/config", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ nas_path: path })
    });
    const result = await res.json();
    
    if (result.success) {
      config = result.config;
      showSettingsMsg("Settings saved successfully!", "success");
      setTimeout(() => closeModal(), 1000);
      refreshData();
    } else {
      showSettingsMsg("Error: " + result.error, "error");
    }
  } catch (err) {
    showSettingsMsg("Failed to save settings: " + err, "error");
  } finally {
    elements.btnSaveSettings.disabled = false;
  }
}

async function archiveGame(appid, libraryPath) {
  try {
    const res = await fetch("/api/archive", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ appid, library_path: libraryPath })
    });
    const result = await res.json();
    if (result.success) {
      // Instantly mark game as busy in frontend
      updateLocalGameStatus(appid, "archiving");
      checkTransferStatus();
    } else {
      alert("Archive failed to start: " + result.error);
    }
  } catch (err) {
    alert("API error: " + err);
  }
}

async function restoreGame(appid) {
  // Find local library path to restore to
  // If we have multiple, default to the first one.
  const localLibs = Object.values(drives).filter(d => !d.is_nas && d.path);
  if (localLibs.length === 0) {
    alert("No local Steam libraries detected to restore to!");
    return;
  }
  
  const targetLibrary = localLibs[0].path;
  
  try {
    const res = await fetch("/api/restore", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ appid, target_library_path: targetLibrary })
    });
    const result = await res.json();
    if (result.success) {
      // Instantly mark game as busy in frontend
      updateLocalGameStatus(appid, "restoring");
      checkTransferStatus();
    } else {
      alert("Restore failed to start: " + result.error);
    }
  } catch (err) {
    alert("API error: " + err);
  }
}

async function compressGame(appid) {
  try {
    const res = await fetch("/api/compress", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ appid })
    });
    const result = await res.json();
    if (result.success) {
      // Instantly mark game as busy in frontend
      updateLocalGameStatus(appid, "compressing");
      checkTransferStatus();
    } else {
      alert("Compression failed to start: " + result.error);
    }
  } catch (err) {
    alert("API error: " + err);
  }
}

async function cancelTransfer() {
  if (confirm("Are you sure you want to cancel the transfer? Partially copied files will be cleaned up.")) {
    try {
      const res = await fetch("/api/cancel", { method: "POST" });
      const result = await res.json();
      if (!result.success) {
        alert("Cancellation error: " + result.error);
      }
    } catch (err) {
      console.error(err);
    }
  }
}

async function cancelQueueJob(jobId) {
  try {
    const res = await fetch("/api/cancel", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ job_id: jobId })
    });
    const result = await res.json();
    if (result.success) {
      checkTransferStatus();
    } else {
      alert("Failed to cancel job: " + result.error);
    }
  } catch (err) {
    alert("Error cancelling job: " + err);
  }
}

// ==========================================
// POLLING TRANSFERS & QUEUE
// ==========================================
async function checkTransferStatus() {
  try {
    const [statusRes, queueRes] = await Promise.all([
      fetch("/api/status"),
      fetch("/api/queue")
    ]);
    const status = await statusRes.json();
    const queueData = await queueRes.json();
    
    const oldGameId = transferStatus.game_id;
    const oldRunning = transferStatus.running;
    
    renderQueueUI(queueData);
    updateTransferUI(status, queueData.pending.length > 0);
    
    if (oldRunning !== status.running || oldGameId !== status.game_id) {
      fetchGames();
      fetchDrives();
    }
    
    if (status.running || queueData.pending.length > 0) {
      startStatusPolling();
    } else {
      stopStatusPolling();
    }
  } catch (err) {
    console.error("Error checking transfer status:", err);
  }
}

function startStatusPolling() {
  if (statusInterval) return;
  statusInterval = setInterval(async () => {
    try {
      const [statusRes, queueRes] = await Promise.all([
        fetch("/api/status"),
        fetch("/api/queue")
      ]);
      const status = await statusRes.json();
      const queueData = await queueRes.json();
      
      const oldGameId = transferStatus.game_id;
      const oldRunning = transferStatus.running;
      
      renderQueueUI(queueData);
      updateTransferUI(status, queueData.pending.length > 0);
      
      if (oldRunning !== status.running || oldGameId !== status.game_id) {
        fetchGames();
        fetchDrives();
      }
      
      if (!status.running && queueData.pending.length === 0) {
        stopStatusPolling();
      }
    } catch (err) {
      console.error("Polling error:", err);
    }
  }, 1000);
}

function stopStatusPolling() {
  if (statusInterval) {
    clearInterval(statusInterval);
    statusInterval = null;
  }
}

function renderQueueUI(queueData) {
  const container = document.getElementById("queue-container");
  const list = document.getElementById("queue-list");
  const count = document.getElementById("queue-count");
  
  if (!container || !list || !count) return;
  
  const pending = queueData.pending || [];
  count.textContent = pending.length;
  
  if (pending.length === 0) {
    container.classList.add("hidden");
    list.innerHTML = "";
    return;
  }
  
  container.classList.remove("hidden");
  list.innerHTML = "";
  
  pending.forEach(job => {
    const item = document.createElement("div");
    item.className = "queue-item";
    
    let actionLabel = "";
    if (job.action === "archive") {
      actionLabel = "Archive";
    } else if (job.action === "restore") {
      actionLabel = "Restore";
    } else if (job.action === "compress") {
      actionLabel = "Compress 7z";
    }
    
    item.innerHTML = `
      <div class="queue-item-info">
        <span class="queue-item-name" title="${job.game_name}">${job.game_name}</span>
        <span class="queue-item-action badge-${job.action}">${actionLabel}</span>
      </div>
      <button class="btn-cancel-queue-item" onclick="cancelQueueJob('${job.id}')" title="Remove from queue">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:14px;height:14px;">
          <line x1="18" y1="6" x2="6" y2="18"></line>
          <line x1="6" y1="6" x2="18" y2="18"></line>
        </svg>
      </button>
    `;
    list.appendChild(item);
  });
}

// Helper to update state locally immediately
function updateLocalGameStatus(appid, status) {
  const game = games.find(g => g.appid === appid);
  if (game) {
    game.status = status;
    renderGames();
  }
}

// ==========================================
// RENDER UI ELEMENTS
// ==========================================
function renderDrives() {
  elements.drivesContainer.innerHTML = "";
  
  const driveKeys = Object.keys(drives);
  if (driveKeys.length === 0) {
    elements.drivesContainer.innerHTML = `
      <div class="empty-state" style="grid-column: 1/-1; padding: 2rem;">
        <p>No Steam Library drives found. Open Steam to verify your storage path.</p>
      </div>
    `;
    return;
  }
  
  driveKeys.forEach(key => {
    const drive = drives[key];
    const isNAS = drive.is_nas;
    
    const card = document.createElement("div");
    card.className = `drive-card ${isNAS ? "is-nas" : ""}`;
    
    let contents = "";
    if (drive.error) {
      contents = `
        <div class="drive-header">
          <div class="drive-info">
            <svg class="drive-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
              <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
              <line x1="6" y1="6" x2="6.01" y2="6"></line>
              <line x1="6" y1="18" x2="6.01" y2="18"></line>
            </svg>
            <span class="drive-name">${isNAS ? "NAS Share" : `Drive (${key})`}</span>
          </div>
          <span class="drive-badge">${isNAS ? "NAS" : "Local"}</span>
        </div>
        <div class="drive-error-msg">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" style="width:1rem;height:1rem;">
            <circle cx="12" cy="12" r="10"></circle>
            <line x1="12" y1="8" x2="12" y2="12"></line>
            <line x1="12" y1="16" x2="12.01" y2="16"></line>
          </svg>
          ${drive.error}
        </div>
      `;
    } else {
      const pct = drive.total > 0 ? ((drive.used / drive.total) * 100).toFixed(0) : 0;
      const freeStr = formatBytes(drive.free);
      const totalStr = formatBytes(drive.total);
      
      contents = `
        <div class="drive-header">
          <div class="drive-info">
            <svg class="drive-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <rect x="2" y="2" width="20" height="8" rx="2" ry="2"></rect>
              <rect x="2" y="14" width="20" height="8" rx="2" ry="2"></rect>
              <line x1="6" y1="6" x2="6.01" y2="6"></line>
              <line x1="6" y1="18" x2="6.01" y2="18"></line>
            </svg>
            <span class="drive-name">${isNAS ? "NAS Storage" : `Local Drive (${key})`}</span>
          </div>
          <span class="drive-badge ${isNAS ? "nas-active" : ""}">${isNAS ? "NAS" : "Local"}</span>
        </div>
        <div class="drive-meter">
          <div class="meter-bar-bg">
            <div class="meter-bar-fill" style="width: ${pct}%"></div>
          </div>
        </div>
        <div class="drive-stats">
          <span>${freeStr} free of ${totalStr}</span>
          <span>${pct}% used</span>
        </div>
      `;
    }
    
    card.innerHTML = contents;
    elements.drivesContainer.appendChild(card);
  });
}

function renderGames() {
  const searchTerm = elements.searchInput.value.toLowerCase();
  const statusFilter = elements.filterStatus.value;
  const sortOption = elements.sortBy.value;
  
  // Filter games
  let filtered = games.filter(game => {
    const matchesSearch = game.name.toLowerCase().includes(searchTerm) || game.appid.includes(searchTerm);
    
    let matchesStatus = true;
    if (statusFilter === "local") {
      matchesStatus = game.status === "local" || game.status === "archiving";
    } else if (statusFilter === "archived") {
      matchesStatus = game.status === "archived" || game.status === "restoring";
    }
    
    return matchesSearch && matchesStatus;
  });
  
  // Sort games
  filtered.sort((a, b) => {
    if (sortOption === "size-desc") return b.size - a.size;
    if (sortOption === "size-asc") return a.size - b.size;
    if (sortOption === "name-asc") return a.name.localeCompare(b.name);
    if (sortOption === "status-asc") return a.status.localeCompare(b.status);
    return 0;
  });
  
  // Update header count
  elements.libraryCount.textContent = `Games Detected (${filtered.length})`;
  
  // Render grid
  elements.gamesGrid.innerHTML = "";
  if (filtered.length === 0) {
    elements.gamesGrid.innerHTML = `
      <div class="empty-state">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:3.5rem;height:3.5rem;color:var(--text-dim);">
          <circle cx="12" cy="12" r="10"></circle>
          <line x1="8" y1="12" x2="16" y2="12"></line>
        </svg>
        <p>No games match your search criteria.</p>
      </div>
    `;
    return;
  }
  
  filtered.forEach(game => {
    const card = document.createElement("div");
    card.className = "game-card";
    
    // Status settings
    let badgeText = "Local PC";
    let badgeClass = "badge-local";
    let actionBtn = "";
    const isBusy = ["archiving", "restoring", "compressing", "queued"].includes(game.status);
    
    if (game.status === "archived") {
      badgeText = "NAS Archived";
      badgeClass = "badge-archived";
      
      let compressBtn = "";
      if (!game.is_compressed) {
        compressBtn = `
          <button class="btn btn-secondary btn-sm" onclick="compressGame('${game.appid}')">
            <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
              <path d="M21 16V8a2 2 0 0 0-1-1.73l-7-4a2 2 0 0 0-2 0l-7 4A2 2 0 0 0 3 8v8a2 2 0 0 0 1 1.73l7 4a2 2 0 0 0 2 0l7-4A2 2 0 0 0 21 16z"></path>
              <polyline points="3.27 6.96 12 12.01 20.73 6.96"></polyline>
              <line x1="12" y1="22.08" x2="12" y2="12"></line>
            </svg>
            Compress to 7z
          </button>
        `;
      }
      
      actionBtn = `
        <button class="btn btn-primary btn-sm" onclick="restoreGame('${game.appid}')">
          <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="17 11 12 6 7 11"></polyline>
            <line x1="12" y1="18" x2="12" y2="6"></line>
          </svg>
          Restore to PC
        </button>
        ${compressBtn}
      `;
    } else if (game.status === "local") {
      actionBtn = `
        <button class="btn btn-secondary btn-sm" onclick="archiveGame('${game.appid}', '${game.library_path.replace(/\\/g, '\\\\')}')">
          <svg class="btn-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2">
            <polyline points="7 13 12 18 17 13"></polyline>
            <line x1="12" y1="6" x2="12" y2="18"></line>
          </svg>
          Archive to NAS
        </button>
      `;
    } else if (isBusy) {
      if (game.status === "archiving") {
        badgeText = "Archiving...";
      } else if (game.status === "restoring") {
        badgeText = "Restoring...";
      } else if (game.status === "compressing") {
        badgeText = "Compressing...";
      } else {
        badgeText = "Queued";
      }
      badgeClass = "badge-busy";
      
      let btnLabel = "Transferring...";
      if (game.status === "compressing") btnLabel = "Compressing...";
      if (game.status === "queued") btnLabel = "Queued in Line...";
      
      actionBtn = `
        <button class="btn btn-secondary btn-sm" disabled>
          <div class="spinner" style="width:12px;height:12px;border-width:2px;display:inline-block;margin-right:4px;"></div>
          ${btnLabel}
        </button>
      `;
    }
    
    const sizeStr = formatBytes(game.size);
    const gradient = getUniqueGradient(game.appid);
    
    let compressedBadge = "";
    if (game.is_compressed) {
      compressedBadge = `<span class="game-badge badge-compressed">7z Compressed</span>`;
    }
    
    card.innerHTML = `
      <div class="game-cover-placeholder">
        <div class="game-artwork-bg" style="background: ${gradient}"></div>
        <span class="game-badge ${badgeClass}">${badgeText}</span>
        ${compressedBadge}
      </div>
      <div class="game-details">
        <h3 class="game-name" title="${game.name}">${game.name}</h3>
        <div class="game-meta-row">
          <span>AppID: ${game.appid}</span>
          <span style="font-weight: 600; color: var(--text-main);">${sizeStr}</span>
        </div>
      </div>
      <div class="game-actions">
        ${actionBtn}
      </div>
    `;
    
    elements.gamesGrid.appendChild(card);
  });
}

function showGamesLoading() {
  elements.gamesGrid.innerHTML = `
    <div class="loading-state">
      <div class="spinner"></div>
      <p>Refreshing library manifests...</p>
    </div>
  `;
}

function showGamesError() {
  elements.gamesGrid.innerHTML = `
    <div class="empty-state">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5" style="width:3.5rem;height:3.5rem;color:var(--color-danger);">
        <circle cx="12" cy="12" r="10"></circle>
        <line x1="12" y1="8" x2="12" y2="12"></line>
        <line x1="12" y1="16" x2="12.01" y2="16"></line>
      </svg>
      <p>Error listing games. Check backend server console logs.</p>
    </div>
  `;
}

// ==========================================
// TRANSFER PROGRESS UI
// ==========================================
function updateTransferUI(status, hasPending = false) {
  transferStatus = status;
  
  if (!status.running && !hasPending) {
    elements.activeTransferPanel.classList.add("hidden");
    
    // Clear log if ended and clean
    if (status.error) {
      elements.consoleLog.innerHTML = `<span style="color:var(--color-danger)">[ERROR] Transfer failed: ${status.error}</span>\n\n` + status.log.join("\n");
      // Open console on error so user sees it
      elements.consoleBody.classList.remove("collapsed");
      elements.consoleChevron.classList.remove("collapsed");
    }
    return;
  }
  
  // Show transfer panel
  elements.activeTransferPanel.classList.remove("hidden");
  
  if (status.running) {
    elements.transferGameName.textContent = status.game_name || "Scanning files...";
    
    let actionLabel = "";
    if (status.action === "archive") {
      actionLabel = "Archiving to NAS (PC ➔ NAS)";
    } else if (status.action === "restore") {
      actionLabel = "Restoring to PC (NAS ➔ PC)";
    } else if (status.action === "compress") {
      actionLabel = "Compressing NAS Archive (Optimizing to .7z)";
    }
    elements.transferActionType.textContent = actionLabel;
    
    // Calculate percentage
    const pct = status.total_bytes > 0 ? ((status.bytes_transferred / status.total_bytes) * 100).toFixed(1) : 0;
    elements.transferProgressFill.style.width = pct + "%";
    elements.transferPercentage.textContent = pct + "%";
    
    elements.transferDetails.textContent = `${formatBytes(status.bytes_transferred)} / ${formatBytes(status.total_bytes)}`;
    elements.transferSpeed.textContent = formatSpeed(status.speed);
    elements.transferEta.textContent = formatETA(status.eta);
    elements.transferFile.textContent = status.current_file || "Preparing files...";
    
    elements.btnCancelTransfer.classList.remove("hidden");
    
    // Update log console
    updateConsoleLogs(status.log);
  } else {
    // There are pending jobs but none is active yet (transition state)
    elements.transferGameName.textContent = "Queue Waiting...";
    elements.transferActionType.textContent = "Waiting for queue worker to start...";
    elements.transferProgressFill.style.width = "0%";
    elements.transferPercentage.textContent = "0%";
    elements.transferDetails.textContent = "-- / --";
    elements.transferSpeed.textContent = "0 B/s";
    elements.transferEta.textContent = "--:--:--";
    elements.transferFile.textContent = "Idle";
    elements.btnCancelTransfer.classList.add("hidden");
  }
}

function updateConsoleLogs(logs) {
  if (!logs || logs.length === 0) return;
  elements.consoleLog.textContent = logs.join("\n");
  
  // Auto scroll to bottom
  elements.consoleBody.scrollTop = elements.consoleBody.scrollHeight;
}

// ==========================================
// MODAL CONTROLS
// ==========================================
function openModal() {
  elements.modalSettings.classList.remove("hidden");
  elements.settingsStatusMsg.className = "status-msg hidden";
}

function closeModal() {
  elements.modalSettings.classList.add("hidden");
}

function showSettingsMsg(text, type) {
  elements.settingsStatusMsg.textContent = text;
  elements.settingsStatusMsg.className = `status-msg ${type}`;
}

// Global exports for inline HTML onclick handlers
window.compressGame = compressGame;
window.cancelQueueJob = cancelQueueJob;
