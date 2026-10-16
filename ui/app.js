const AVATAR_SVG = '<svg class="fluent-svg" width="16" height="16" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a4 4 0 100 8 4 4 0 000-8zm-2 4a2 2 0 114 0 2 2 0 01-4 0zm-3 8a3 3 0 013-3h4a3 3 0 013 3v2a1 1 0 01-1 1H6a1 1 0 01-1-1v-2zm3-1a1 1 0 00-1 1v1h8v-1a1 1 0 00-1-1H8z"/></svg>';
const gamepadIcon = (size) => `<svg class="fluent-svg" width="${size}" height="${size}" viewBox="0 0 20 20" fill="currentColor"><path d="M6.5 4A4.5 4.5 0 002 8.5v4A3.5 3.5 0 005.5 16h.28a3.5 3.5 0 003.35-2.5h1.74A3.5 3.5 0 0014.22 16h.28a3.5 3.5 0 003.5-3.5v-4A4.5 4.5 0 0013.5 4h-7zm0 1h7A3.5 3.5 0 0117 8.5v4a2.5 2.5 0 01-2.5 2.5h-.28a2.5 2.5 0 01-2.4-1.8A1 1 0 0010.86 12.5H9.14a1 1 0 00-.96.7A2.5 2.5 0 015.78 15H5.5A2.5 2.5 0 013 12.5v-4A3.5 3.5 0 016.5 5zM7 7.5a.5.5 0 01.5.5V9H9a.5.5 0 010 1H7.5v1.5a.5.5 0 01-1 0V10H5a.5.5 0 010-1h1.5V8a.5.5 0 01.5-.5zm6.5 1a1 1 0 110 2 1 1 0 010-2zm-1.5 2.5a1 1 0 110 2 1 1 0 010-2z"/></svg>`;

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sortGames(games) {
  return [...(games || [])].sort((a, b) =>
    (a.name || "").localeCompare(b.name || "", undefined, { sensitivity: "base", numeric: true })
  );
}

function fuzzyMatch(query, text) {
  if (!query) return true;
  query = query.toLowerCase().trim();
  text = (text || "").toLowerCase();

  // Direct substring match
  if (text.includes(query)) return true;

  // Acronym match (e.g. "fh2" -> "Forza Horizon 2")
  const words = text.split(/[\s:_\-\/\\]+/).filter(Boolean);
  const acronym = words.map(w => w[0]).join("");
  if (acronym.includes(query)) return true;

  // Subsequence match (characters in sequence)
  let qIdx = 0;
  for (let tIdx = 0; tIdx < text.length && qIdx < query.length; tIdx++) {
    if (text[tIdx] === query[qIdx]) {
      qIdx++;
    }
  }
  return qIdx === query.length;
}

const app = {
  state: {
    signedIn: false,
    gamertag: "Not Signed In",
    xuid: "",
    gamerpic: "",
    games: [],
    selectedGame: null,
    lastDownloadedFolder: ""
  },

  init() {
    this.bindEvents();
    this.checkPywebviewReady();
  },

  bindEvents() {
    // Search Input with Fuzzy Match and Clear Button
    const searchInput = document.getElementById("search-input");
    const clearBtn = document.getElementById("search-clear-btn");

    if (searchInput) {
      searchInput.addEventListener("input", (e) => {
        const val = e.target.value;
        if (clearBtn) clearBtn.style.display = val ? "flex" : "none";
        this.filterGames(val);
      });

      searchInput.addEventListener("keydown", (e) => {
        if (e.key === "Escape") {
          searchInput.value = "";
          if (clearBtn) clearBtn.style.display = "none";
          this.filterGames("");
          searchInput.blur();
        }
      });
    }

    if (clearBtn) {
      clearBtn.addEventListener("click", () => {
        if (searchInput) {
          searchInput.value = "";
          clearBtn.style.display = "none";
          this.filterGames("");
          searchInput.focus();
        }
      });
    }

    // Ctrl+F shortcut to focus search
    window.addEventListener("keydown", (e) => {
      if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "f") {
        e.preventDefault();
        if (searchInput) {
          searchInput.focus();
          searchInput.select();
        }
      }
    });
  },

  checkPywebviewReady() {
    if (window.pywebview && window.pywebview.api) {
      this.onBridgeReady();
    } else {
      window.addEventListener("pywebviewready", () => {
        this.onBridgeReady();
      });
    }
  },

  async onBridgeReady() {
    try {
      // Must return immediately from disk with 0 network calls
      const initData = await window.pywebview.api.get_initial_state();
      if (initData && initData.signed_in) {
        this.setSignedIn(true, initData.gamertag, initData.xuid, initData.cached_gamerpic);

        // Instant render of cached library (0ms on startup)
        if (initData.cached_games && initData.cached_games.length > 0) {
          this.setGames(initData.cached_games);
        } else {
          // Show loading state only if no cache exists yet
          const loadingState = document.getElementById("games-loading-state");
          if (loadingState) loadingState.style.display = "block";
        }

        // Dispatch asynchronous background refresh after initial frame settles
        setTimeout(() => {
          this.startProgress(20);
          window.pywebview.api.request_profile();
          window.pywebview.api.request_games();
        }, 150);
      } else {
        this.setSignedIn(false);
      }
    } catch (e) {
      console.error("[Init Error]", e);
      this.setSignedIn(false);
    }
  },

  onUserCardClick(event) {
    if (event) {
      event.preventDefault();
      event.stopPropagation();
    }
    if (!this.state.signedIn) {
      this.startLogin();
      return;
    }
    this.toggleProfileMenu();
  },

  toggleProfileMenu() {
    const menu = document.getElementById("profile-menu");
    if (!menu) return;
    if (menu.style.display === "none" || !menu.style.display) {
      this.openProfileMenu();
    } else {
      this.closeProfileMenu();
    }
  },

  openProfileMenu() {
    const menu = document.getElementById("profile-menu");
    const tag = document.getElementById("profile-menu-gamertag");
    if (tag) tag.innerText = this.state.gamertag || "Xbox User";
    const menuAvatar = document.getElementById("profile-menu-avatar");
    const mainAvatar = document.getElementById("user-avatar");
    if (menuAvatar && mainAvatar) menuAvatar.innerHTML = mainAvatar.innerHTML;
    if (menu) menu.style.display = "flex";
  },

  closeProfileMenu() {
    const menu = document.getElementById("profile-menu");
    if (menu) menu.style.display = "none";
  },

  refreshLibrary() {
    this.closeProfileMenu();
    this.startProgress(15);
    const countEl = document.getElementById("games-count");
    if (countEl) countEl.innerText = "Syncing...";
    window.pywebview.api.refresh_library();
  },

  clearCacheAndRefresh() {
    this.closeProfileMenu();
    this.startProgress(15);
    // Clear in-memory games list & state
    this.state.games = [];
    this.state.gamerpic = "";
    const userAvatar = document.getElementById("user-avatar");
    if (userAvatar) {
      userAvatar.innerHTML = AVATAR_SVG;
    }
    const grid = document.getElementById("games-grid");
    if (grid) grid.innerHTML = "";
    const loadingState = document.getElementById("games-loading-state");
    if (loadingState) loadingState.style.display = "block";
    const countEl = document.getElementById("games-count");
    if (countEl) countEl.innerText = "Refreshing...";

    window.pywebview.api.clear_cache_and_refresh();
  },

  signOut() {
    this.closeProfileMenu();
    this.showConfirmModal({
      title: "Sign Out",
      message: `Are you sure you want to sign out of <strong>${escapeHtml(this.state.gamertag)}</strong>?<br><br>You will need to sign back in with your Microsoft account to access your library and saves.`,
      actionText: "Sign Out",
      actionStyle: "danger",
      icon: "signout",
      onConfirm: () => {
        window.pywebview.api.sign_out().then(() => {
          this.setSignedIn(false);
          this.setGames([]);
        });
      }
    });
  },

  showConfirmModal({ title, message, actionText = "Confirm", actionStyle = "primary", icon = "info", onConfirm }) {
    const modal = document.getElementById("confirm-modal");
    const titleEl = document.getElementById("confirm-modal-title");
    const msgEl = document.getElementById("confirm-modal-message");
    const actionBtn = document.getElementById("confirm-modal-action-btn");
    const cancelBtn = document.getElementById("confirm-modal-cancel-btn");
    const iconEl = document.getElementById("confirm-modal-icon");

    if (titleEl) titleEl.innerText = title;
    if (msgEl) msgEl.innerHTML = message;
    if (actionBtn) {
      actionBtn.innerText = actionText;
      actionBtn.className = actionStyle === "danger" ? "winui-btn winui-btn-danger" : "winui-btn winui-btn-primary";
      actionBtn.onclick = () => {
        this.closeConfirmModal();
        if (onConfirm) onConfirm();
      };
    }

    if (iconEl) {
      if (icon === "signout") {
        iconEl.innerHTML = `<svg class="fluent-svg" width="18" height="18" viewBox="0 0 20 20" fill="currentColor"><path d="M3 4.5A1.5 1.5 0 014.5 3h6a.75.75 0 010 1.5h-6a.5.5 0 00-.5.5v10a.5.5 0 00.5.5h6a.75.75 0 010 1.5h-6A1.5 1.5 0 013 15.5v-11zm10.72 2.22a.75.75 0 011.06 0l3 3a.75.75 0 010 1.06l-3 3a.75.75 0 11-1.06-1.06l1.72-1.72H8.75a.75.75 0 010-1.5h7.69l-1.72-1.72a.75.75 0 010-1.06z"/></svg>`;
      } else {
        iconEl.innerHTML = `<svg class="fluent-svg" width="18" height="18" viewBox="0 0 20 20" fill="currentColor"><path d="M10 2a8 8 0 100 16 8 8 0 000-16zm.75 4.75a.75.75 0 00-1.5 0v4.5a.75.75 0 001.5 0v-4.5zm-.75 7.75a1 1 0 110-2 1 1 0 010 2z"/></svg>`;
      }
    }

    if (modal) modal.classList.add("active");
  },

  closeConfirmModal() {
    const modal = document.getElementById("confirm-modal");
    if (modal) modal.classList.remove("active");
  },

  // WinUI Divider Progress Bar Methods
  startProgress(initialPct = 15) {
    const bar = document.getElementById("header-progress-bar");
    const indicator = document.getElementById("header-progress-indicator");
    if (!bar || !indicator) return;

    if (this._progressTimer) {
      clearInterval(this._progressTimer);
      this._progressTimer = null;
    }

    bar.classList.add("active");
    indicator.style.transition = "width 0.3s cubic-bezier(0.1, 0.9, 0.2, 1)";
    indicator.style.width = initialPct + "%";
    this.currentProgress = initialPct;

    // Smooth forward creep while waiting for network responses
    this._progressTimer = setInterval(() => {
      if (this.currentProgress < 85) {
        this.currentProgress += Math.random() * 2 + 0.8;
        indicator.style.width = this.currentProgress.toFixed(1) + "%";
      }
    }, 250);
  },

  onProgressUpdate(pct) {
    const bar = document.getElementById("header-progress-bar");
    const indicator = document.getElementById("header-progress-indicator");
    if (!bar || !indicator) return;

    bar.classList.add("active");
    if (pct > this.currentProgress) {
      this.currentProgress = pct;
      indicator.style.transition = "width 0.3s cubic-bezier(0.1, 0.9, 0.2, 1)";
      indicator.style.width = pct + "%";
    }
  },

  finishProgress() {
    const bar = document.getElementById("header-progress-bar");
    const indicator = document.getElementById("header-progress-indicator");
    if (!bar || !indicator) return;

    if (this._progressTimer) {
      clearInterval(this._progressTimer);
      this._progressTimer = null;
    }

    this.currentProgress = 100;
    indicator.style.transition = "width 0.2s cubic-bezier(0.1, 0.9, 0.2, 1)";
    indicator.style.width = "100%";

    setTimeout(() => {
      bar.classList.remove("active");
      setTimeout(() => {
        indicator.style.transition = "none";
        indicator.style.width = "0%";
        this.currentProgress = 0;
      }, 300);
    }, 350);
  },

  setSignedIn(signedIn, gamertag = "Not Signed In", xuid = "", gamerpic = "") {
    this.state.signedIn = signedIn;
    this.state.gamertag = gamertag;
    this.state.xuid = xuid;
    if (gamerpic) this.state.gamerpic = gamerpic;

    const pageTitle = document.getElementById("page-title");
    if (pageTitle) {
      pageTitle.textContent = signedIn && gamertag ? `${gamertag}'s Library` : "Sign in";
    }

    const userCard = document.getElementById("user-card");
    const userAvatar = document.getElementById("user-avatar");
    const prompt = document.getElementById("library-login-prompt");
    const grid = document.getElementById("games-grid");

    if (signedIn) {
      if (userAvatar) {
        if (this.state.gamerpic) {
          userAvatar.innerHTML = `<img src="${this.state.gamerpic}" class="user-avatar-img" alt="Avatar" />`;
        } else {
          userAvatar.innerHTML = AVATAR_SVG;
        }
      }
      if (userCard) userCard.title = "Account options (Click to open menu)";
      if (prompt) prompt.style.display = "none";
    } else {
      this.closeProfileMenu();
      if (userAvatar) {
        userAvatar.innerHTML = AVATAR_SVG;
      }
      if (userCard) userCard.title = "Click to Sign In";
      if (prompt) prompt.style.display = "flex";
      if (grid) grid.style.display = "none";
      const loadingState = document.getElementById("games-loading-state");
      if (loadingState) loadingState.style.display = "none";
    }
  },

  startLogin() {
    window.pywebview.api.start_login();
  },

  // Python background worker callbacks
  onLoginSuccess(payload) {
    this.setSignedIn(true, payload.gamertag, payload.xuid);
    this.startProgress(20);
    const loadingState = document.getElementById("games-loading-state");
    if (loadingState) loadingState.style.display = "block";
  },

  onLoginFailed(errMsg) {
    this.finishProgress();
    alert(`Authentication failed:\n${errMsg}`);
  },

  onLoginCancelled() {
    this.finishProgress();
  },

  onGamerpicLoaded(gamerpicUrl) {
    this.state.gamerpic = gamerpicUrl;
    const userAvatar = document.getElementById("user-avatar");
    if (userAvatar && gamerpicUrl) {
      userAvatar.innerHTML = `<img src="${gamerpicUrl}" class="user-avatar-img" alt="Avatar" />`;
    }
  },

  onGamesLoaded(gamesList) {
    this.finishProgress();
    const loadingState = document.getElementById("games-loading-state");
    if (loadingState) loadingState.style.display = "none";

    // Diff check: if the fresh list matches what is already rendered, do not re-render DOM
    gamesList = sortGames(gamesList);
    if (this.areGamesEqual(this.state.games, gamesList)) {
      return;
    }
    this.setGames(gamesList);
  },

  areGamesEqual(a, b) {
    if (!a || !b) return false;
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i++) {
      if (a[i].title_id !== b[i].title_id) return false;
      if (a[i].display_image !== b[i].display_image) return false;
      if (a[i].name !== b[i].name) return false;
    }
    return true;
  },

  onGamesError(errMsg) {
    this.finishProgress();
    const loadingState = document.getElementById("games-loading-state");
    if (loadingState) loadingState.style.display = "none";
    console.error("[Games Error]", errMsg);
    if (/sign in again|sign out and sign in/i.test(String(errMsg))) {
      this.showConfirmModal({
        title: "Session Expired",
        message: escapeHtml(errMsg),
        actionText: "OK",
        actionStyle: "primary",
        icon: "error"
      });
    }
  },

  onIconCached(titleIdHex, localPath) {
    if (!titleIdHex || !localPath) return;
    const rawTid = titleIdHex.replace(/^0x/i, "").toUpperCase();

    // Update in-memory game object
    const game = this.state.games.find(g => {
      const gHex = (g.title_id_hex || (g.title_id ? "0x" + g.title_id.toString(16) : "")).replace(/^0x/i, "").toUpperCase();
      return gHex === rawTid;
    });
    if (game) {
      game.local_icon = localPath;
    }

    // Update rendered poster card if present in DOM
    const cards = document.querySelectorAll(".game-poster-card");
    for (const card of cards) {
      const cardTid = (card.getAttribute("data-title-id") || "").replace(/^0x/i, "").toUpperCase();
      if (cardTid === rawTid) {
        const fallback = card.querySelector(".game-poster-fallback");
        if (fallback) fallback.remove();
        let img = card.querySelector(".game-poster-img");
        if (!img) {
          img = document.createElement("img");
          img.className = "game-poster-img";
          card.insertBefore(img, card.firstChild);
        }
        img.src = localPath;
        img.style.visibility = "visible";
        img.style.display = "block";
        break;
      }
    }

    // Update modal preview if currently open for this game
    if (this.state.selectedGame) {
      const selHex = (this.state.selectedGame.title_id_hex || (this.state.selectedGame.title_id ? "0x" + this.state.selectedGame.title_id.toString(16) : "")).replace(/^0x/i, "").toUpperCase();
      if (selHex === rawTid) {
        const thumb = document.getElementById("modal-game-thumb");
        if (thumb) {
          thumb.innerHTML = `<img src="${escapeHtml(localPath)}" alt="${escapeHtml(this.state.selectedGame.name)}" />`;
        }
      }
    }
  },

  setGames(games) {
    this.state.games = sortGames(games);
    const searchInput = document.getElementById("search-input");
    this.filterGames(searchInput ? searchInput.value : "");
  },

  filterGames(searchQuery = "") {
    const q = (searchQuery || "").trim();
    const grid = document.getElementById("games-grid");
    const noGames = document.getElementById("no-games-state");
    const prompt = document.getElementById("library-login-prompt");

    if (!this.state.signedIn) {
      if (prompt) prompt.style.display = "flex";
      if (grid) grid.style.display = "none";
      if (noGames) noGames.style.display = "none";
      return;
    }

    const filtered = this.state.games.filter(g => {
      if (!q) return true;
      return (
        fuzzyMatch(q, g.name) ||
        (g.title_id_hex && g.title_id_hex.toLowerCase().includes(q.toLowerCase())) ||
        (g.title_id && String(g.title_id).includes(q))
      );
    });

    if (filtered.length === 0) {
      if (grid) grid.style.display = "none";
      if (noGames) noGames.style.display = "flex";
      return;
    }

    if (noGames) noGames.style.display = "none";
    if (grid) {
      grid.style.display = "grid";
      grid.innerHTML = "";

      const fragment = document.createDocumentFragment();
      const gamepadSvg = gamepadIcon(32);

      filtered.forEach(game => {
        const card = document.createElement("div");
        card.className = "game-poster-card";
        card.title = game.name;
        const tidAttr = (game.title_id_hex || (game.title_id ? "0x" + Number(game.title_id).toString(16) : "")).toUpperCase();
        card.setAttribute("data-title-id", tidAttr);
        card.onclick = () => app.openDownloadModal(game.title_id);

        if (game.local_icon) {
          card.innerHTML = `
            <img src="${escapeHtml(game.local_icon)}" class="game-poster-img" alt="${escapeHtml(game.name)}" />
            <div class="game-poster-overlay">${escapeHtml(game.name)}</div>
          `;
        } else {
          card.innerHTML = `
            <div class="game-poster-fallback">${gamepadSvg}</div>
            <div class="game-poster-overlay">${escapeHtml(game.name)}</div>
          `;
          // Download and compress in background first; do not make dual uncompressed network requests
          if (game.display_image && game.title_id_hex) {
            try {
              window.pywebview.api.request_icon_cache(game.title_id_hex, game.display_image);
            } catch (e) { }
          }
        }

        fragment.appendChild(card);
      });

      grid.appendChild(fragment);
    }
  },

  async openDownloadModal(titleId, gameOverride = null) {
    const game = gameOverride || this.state.games.find(g => g.title_id === titleId);
    if (!game) return;
    this.state.selectedGame = game;

    const modal = document.getElementById("download-modal");
    const gameName = document.getElementById("modal-game-name");
    const gameTitleId = document.getElementById("modal-game-title-id");
    const gameScid = document.getElementById("modal-game-scid");
    const gameThumb = document.getElementById("modal-game-thumb");
    const destFolder = document.getElementById("modal-dest-folder");

    if (gameName) gameName.textContent = game.name;
    if (gameTitleId) gameTitleId.textContent = `Title ID: ${game.title_id_hex || ('0x' + (game.title_id ? game.title_id.toString(16).toUpperCase() : '0'))}`;
    if (gameScid) gameScid.textContent = `SCID: ${game.scid || 'Default'}`;

    // Only show Azimuth export format option for Forza Horizon 2
    const isFH2 = Boolean(
      game.title_id === 446059611 ||
      (game.title_id_hex && game.title_id_hex.toUpperCase() === "0X1A96545B") ||
      (game.name && game.name.toLowerCase().includes("forza horizon 2"))
    );

    const azimuthContainer = document.getElementById("modal-azimuth-container");
    const azimuthCheck = document.getElementById("modal-export-azimuth");
    if (azimuthContainer) {
      azimuthContainer.style.display = isFH2 ? "flex" : "none";
    }
    if (azimuthCheck) {
      azimuthCheck.checked = isFH2;
    }

    if (gameThumb) {
      const imgSrc = game.local_icon;
      if (imgSrc) {
        gameThumb.innerHTML = `<img src="${escapeHtml(imgSrc)}" alt="${escapeHtml(game.name)}" />`;
      } else {
        gameThumb.innerHTML = gamepadIcon(24);
      }
    }

    // Default download folder if empty
    if (destFolder && !destFolder.value) {
      try {
        const defaultDir = await window.pywebview.api.get_default_download_dir();
        if (defaultDir) {
          destFolder.value = defaultDir;
        }
      } catch (e) { }
    }

    // Reset modal UI
    const progSec = document.getElementById("modal-progress-section");
    const progBar = document.getElementById("modal-progress-bar");
    const startBtn = document.getElementById("modal-start-btn");
    const cancelBtn = document.getElementById("modal-cancel-btn");
    const openBtn = document.getElementById("modal-open-folder-btn");

    if (progSec) progSec.style.display = "none";
    if (progBar) progBar.style.width = "0%";
    if (startBtn) {
      startBtn.style.display = "inline-flex";
      startBtn.disabled = false;
    }
    if (cancelBtn) {
      cancelBtn.disabled = false;
      cancelBtn.textContent = "Cancel";
    }
    if (openBtn) openBtn.style.display = "none";

    if (modal) modal.classList.add("active");
  },

  closeDownloadModal() {
    // While a download is running, Cancel aborts it instead of closing
    if (this.state.downloading) {
      const progressStatus = document.getElementById("modal-progress-status");
      const cancelBtn = document.getElementById("modal-cancel-btn");
      if (progressStatus) progressStatus.textContent = "Cancelling...";
      if (cancelBtn) cancelBtn.disabled = true;
      window.pywebview.api.cancel_download();
      return;
    }
    const modal = document.getElementById("download-modal");
    if (modal) modal.classList.remove("active");
  },

  onDialogOverlayClick(e, modalId) {
    if (e.target.id === modalId) {
      if (modalId === "download-modal" && !this.state.downloading) this.closeDownloadModal();
      if (modalId === "confirm-modal") this.closeConfirmModal();
    }
  },

  async browseDownloadFolder() {
    try {
      const current = document.getElementById("modal-dest-folder").value || "";
      const folder = await window.pywebview.api.select_folder(current);
      if (folder) {
        document.getElementById("modal-dest-folder").value = folder;
      }
    } catch (e) {
      console.error(e);
    }
  },

  executeDownload() {
    const folder = document.getElementById("modal-dest-folder").value.trim();
    if (!folder) {
      document.getElementById("modal-dest-folder").focus();
      return;
    }

    const game = this.state.selectedGame;
    if (!game) return;

    const isFH2 = Boolean(
      game.title_id === 446059611 ||
      (game.title_id_hex && game.title_id_hex.toUpperCase() === "0X1A96545B") ||
      (game.name && game.name.toLowerCase().includes("forza horizon 2"))
    );
    const exportAzimuth = isFH2 && (document.getElementById("modal-export-azimuth")?.checked ?? false);
    const playerId = "User_9000000000000";

    const progressSec = document.getElementById("modal-progress-section");
    const progressBar = document.getElementById("modal-progress-bar");
    const progressStatus = document.getElementById("modal-progress-status");
    const progressPercent = document.getElementById("modal-progress-percent");
    const startBtn = document.getElementById("modal-start-btn");
    const cancelBtn = document.getElementById("modal-cancel-btn");

    if (progressSec) progressSec.style.display = "flex";
    if (progressBar) progressBar.style.width = "5%";
    if (progressStatus) progressStatus.textContent = "Connecting to Xbox Live Title Storage...";
    if (progressPercent) progressPercent.textContent = "5%";
    if (startBtn) startBtn.disabled = true;
    if (cancelBtn) cancelBtn.disabled = false;
    this.state.downloading = true;

    // Dispatches download in a background thread
    window.pywebview.api.download_save({
      title_id: game.title_id,
      game_name: game.name,
      scid: game.scid,
      pfn: game.pfn,
      output_dir: folder,
      export_azimuth: exportAzimuth,
      player_id: playerId
    }).then(res => {
      // Immediate validation failures come back as the return value
      if (res && res.success === false) this.onDownloadResult(res);
    }).catch(e => this.onDownloadResult({ success: false, error: String(e) }));
  },

  onDownloadProgress(statusText, percent) {
    const progressStatus = document.getElementById("modal-progress-status");
    const progressBar = document.getElementById("modal-progress-bar");
    const progressPercent = document.getElementById("modal-progress-percent");

    if (progressStatus) progressStatus.textContent = statusText;
    if (progressBar) progressBar.style.width = `${percent}%`;
    if (progressPercent) progressPercent.textContent = `${percent}%`;
  },

  onDownloadResult(res) {
    const progressBar = document.getElementById("modal-progress-bar");
    const progressStatus = document.getElementById("modal-progress-status");
    const progressPercent = document.getElementById("modal-progress-percent");
    const startBtn = document.getElementById("modal-start-btn");
    const cancelBtn = document.getElementById("modal-cancel-btn");

    this.state.downloading = false;
    if (res.cancelled) {
      if (progressStatus) progressStatus.textContent = "Download cancelled.";
      if (progressBar) progressBar.style.width = "0%";
      if (progressPercent) progressPercent.textContent = "0%";
      if (startBtn) startBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = false;
      return;
    }

    if (res.success) {
      if (progressBar) progressBar.style.width = "100%";
      if (progressStatus) progressStatus.textContent = `Extraction complete! Unpacked ${res.container_count} container(s).`;
      if (progressPercent) progressPercent.textContent = "100%";
      if (startBtn) startBtn.style.display = "none";
      if (cancelBtn) {
        cancelBtn.disabled = false;
        cancelBtn.textContent = "Done";
      }

      this.state.lastDownloadedFolder = res.folder;
      const openBtn = document.getElementById("modal-open-folder-btn");
      if (openBtn) openBtn.style.display = "inline-flex";
    } else {
      if (progressStatus) progressStatus.textContent = `Download failed: ${res.error}`;
      if (startBtn) startBtn.disabled = false;
      if (cancelBtn) cancelBtn.disabled = false;
      this.showConfirmModal({
        title: "Download Failed",
        message: `An error occurred while downloading save data:<br><br><code>${escapeHtml(res.error)}</code>`,
        actionText: "OK",
        actionStyle: "primary",
        icon: "error"
      });
    }
  },

  openFolderAndClose() {
    if (this.state.lastDownloadedFolder) {
      window.pywebview.api.open_folder(this.state.lastDownloadedFolder);
    }
    this.closeDownloadModal();
  },
};

window.addEventListener("DOMContentLoaded", () => {
  app.init();

  // Close profile context menu on click outside or Escape
  document.addEventListener("click", (e) => {
    const menu = document.getElementById("profile-menu");
    const card = document.getElementById("user-card");
    if (menu && menu.style.display !== "none") {
      if (!menu.contains(e.target) && !card.contains(e.target)) {
        app.closeProfileMenu();
      }
    }
  });

  document.addEventListener("keydown", (e) => {
    if (e.key === "Escape") {
      app.closeProfileMenu();
      app.closeDownloadModal();
      app.closeConfirmModal();
    }
  });
});
