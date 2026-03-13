const state = {
  campgrounds: [],
  selectedIds: [],
  tripGroups: [],
  loadedTripGroups: [],
  savedUsers: [],
  activeTripIndex: null,
  monitorSha: null,
  usersSha: null,
  monitorPollSeconds: 60,
  previewImageCache: {},
  previewRequestId: 0,
};

const byId = {};
const STORAGE_KEYS = {
  githubToken: "recgovMonitorGithubToken",
  ridbKey: "recgovMonitorRidbKey",
  rememberGithubToken: "recgovMonitorRememberGithubToken",
  rememberRidbKey: "recgovMonitorRememberRidbKey",
  savedUsers: "recgovMonitorSavedUsers",
};

const el = (id) => document.getElementById(id);
const status = (msg) => {
  const node = el("status");
  if (!node) return;
  node.textContent = msg;
};
const setRidbStatus = (msg, level = "") => {
  const wrap = el("ridbFieldWrap");
  if (!wrap) return;
  wrap.classList.remove("ok", "error", "testing");
  if (level) wrap.classList.add(level);
  wrap.title = msg;
  const dot = el("ridbDot");
  if (dot) dot.title = msg;
};
const bindIfPresent = (id, eventName, handler) => {
  const node = el(id);
  if (!node) return;
  node.addEventListener(eventName, handler);
};

function githubApiBase() {
  const owner = el("owner").value.trim();
  const repo = el("repo").value.trim();
  return `https://api.github.com/repos/${owner}/${repo}/contents`;
}

function authHeaders() {
  const token = el("token").value.trim();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

function toDisplayDate(storageDate) {
  const [y, m, d] = storageDate.split("-");
  return `${m}-${d}-${y}`;
}

function toStorageDate(displayDate) {
  const [m, d, y] = displayDate.split("-");
  return `${y}-${m}-${d}`;
}

function toStorageDateSafe(displayDate) {
  const parts = String(displayDate || "").split("-");
  if (parts.length !== 3) return "";
  const [m, d, y] = parts;
  if (!/^\d{2}$/.test(m) || !/^\d{2}$/.test(d) || !/^\d{4}$/.test(y)) return "";
  const month = Number(m);
  const day = Number(d);
  const year = Number(y);
  if (month < 1 || month > 12 || day < 1 || day > 31 || year < 1900) return "";
  return `${y}-${m}-${d}`;
}

function renderLabel(item) {
  const location = [item.park || "", item.state || ""].filter(Boolean).join(" - ");
  return location ? `${item.name} (${item.id}) - ${location}` : `${item.name} (${item.id})`;
}

function currentDisplayDate(isoDate) {
  if (!isoDate) return "";
  const [y, m, d] = isoDate.split("-");
  return `${m}-${d}-${y}`;
}

function isoDateFromDisplay(displayDate) {
  if (!displayDate) return "";
  const [m, d, y] = displayDate.split("-");
  return `${y}-${m}-${d}`;
}

function normalizeDiscordUserId(raw) {
  const value = String(raw || "").trim();
  if (!value) return "";
  const mentionMatch = value.match(/^<@!?(\d{17,20})>$/);
  if (mentionMatch) return mentionMatch[1];
  const digitsMatch = value.match(/^@?(\d{17,20})$/);
  if (digitsMatch) return digitsMatch[1];
  return "";
}

function asDiscordMention(userId) {
  const id = normalizeDiscordUserId(userId);
  return id ? `<@${id}>` : "";
}

function displayUserFromTag(discordTag) {
  const id = normalizeDiscordUserId(discordTag);
  if (!id) return String(discordTag || "").trim();
  const user = state.savedUsers.find((u) => u.id === id);
  return user ? user.name : String(discordTag || "").trim();
}

function normalizeSavedUsers(raw) {
  if (!Array.isArray(raw)) return [];
  return raw
    .map((u) => ({
      name: String((u && u.name) || "").trim(),
      id: normalizeDiscordUserId((u && u.id) || ""),
    }))
    .filter((u) => u.name && u.id)
    .sort((a, b) => a.name.localeCompare(b.name));
}

function normalizeGroup(group) {
  const ids = Array.from(new Set(group.campground_ids.map((v) => Number(v)).filter((n) => Number.isInteger(n)))).sort((a, b) => a - b);
  const discordTag = String(group.discord_tag || "").trim();
  const fullMatchesOnly = !!group.full_matches_only;
  return {
    campground_ids: ids,
    check_in: String(group.check_in || "").trim(),
    check_out: String(group.check_out || "").trim(),
    discord_tag: discordTag,
    full_matches_only: fullMatchesOnly,
  };
}

function groupIdentity(group) {
  const g = normalizeGroup(group);
  return `${g.check_in}|${g.check_out}|${g.campground_ids.join(",")}|${g.discord_tag}|${g.full_matches_only ? 1 : 0}`;
}

function shortDate(displayDate) {
  const [m, d] = String(displayDate || "").split("-");
  const month = Number(m);
  const day = Number(d);
  if (!Number.isInteger(month) || !Number.isInteger(day)) return displayDate;
  return `${month}/${day}`;
}

function groupSummary(group) {
  const g = normalizeGroup(group);
  const firstId = g.campground_ids[0];
  const name = firstId && byId[firstId] ? byId[firstId].name : `Campground ${firstId || ""}`.trim();
  const range = `${shortDate(g.check_in)}-${shortDate(g.check_out)}`;
  return `${name} ${range} trip`;
}

function rangeKey(group) {
  const g = normalizeGroup(group);
  return `${g.check_in}|${g.check_out}`;
}

function campgroundNameById(campgroundId) {
  if (byId[campgroundId] && byId[campgroundId].name) return byId[campgroundId].name;
  return `Campground ${campgroundId}`;
}

function listNamesFromIds(ids) {
  return ids.map((id) => campgroundNameById(id)).join(", ");
}

function buildAutoCommitMessage() {
  const prev = state.loadedTripGroups.map(normalizeGroup);
  const next = state.tripGroups.map(normalizeGroup);
  const prevMap = new Map(prev.map((g) => [groupIdentity(g), g]));
  const nextMap = new Map(next.map((g) => [groupIdentity(g), g]));

  const added = [];
  const removed = [];
  for (const [k, g] of nextMap.entries()) {
    if (!prevMap.has(k)) added.push(g);
  }
  for (const [k, g] of prevMap.entries()) {
    if (!nextMap.has(k)) removed.push(g);
  }

  const prevByRange = new Map();
  const nextByRange = new Map();
  for (const g of prev) {
    const key = rangeKey(g);
    const arr = prevByRange.get(key) || [];
    arr.push(g);
    prevByRange.set(key, arr);
  }
  for (const g of next) {
    const key = rangeKey(g);
    const arr = nextByRange.get(key) || [];
    arr.push(g);
    nextByRange.set(key, arr);
  }

  const detailedUpdates = [];
  for (const [key, prevGroups] of prevByRange.entries()) {
    const nextGroups = nextByRange.get(key);
    if (!nextGroups || prevGroups.length !== 1 || nextGroups.length !== 1) continue;
    const prevGroup = prevGroups[0];
    const nextGroup = nextGroups[0];
    const prevIds = new Set(prevGroup.campground_ids);
    const nextIds = new Set(nextGroup.campground_ids);

    const addedIds = [...nextIds].filter((id) => !prevIds.has(id));
    const removedIds = [...prevIds].filter((id) => !nextIds.has(id));
    const tagChanged = prevGroup.discord_tag !== nextGroup.discord_tag;
    if (addedIds.length === 0 && removedIds.length === 0 && !tagChanged) continue;

    const range = `${shortDate(prevGroup.check_in)}-${shortDate(prevGroup.check_out)}`;
    const parts = [];
    if (addedIds.length > 0) parts.push(`Added ${listNamesFromIds(addedIds)}`);
    if (removedIds.length > 0) parts.push(`Removed ${listNamesFromIds(removedIds)}`);
    if (tagChanged) {
      const beforeTag = prevGroup.discord_tag || "(none)";
      const afterTag = nextGroup.discord_tag || "(none)";
      parts.push(`Tag ${beforeTag} -> ${afterTag}`);
    }
    detailedUpdates.push(`Updated ${range} trip group: ${parts.join("; ")}`);
  }

  if (detailedUpdates.length === 1) return detailedUpdates[0];
  if (detailedUpdates.length > 1) return `Updated ${detailedUpdates.length} trip groups`;

  if (added.length === 1 && removed.length === 0) return `Add ${groupSummary(added[0])}`;
  if (removed.length === 1 && added.length === 0) return `Remove ${groupSummary(removed[0])}`;
  if (added.length > 0 && removed.length === 0) return `Add ${added.length} trip group(s)`;
  if (removed.length > 0 && added.length === 0) return `Remove ${removed.length} trip group(s)`;
  if (added.length > 0 || removed.length > 0) return `Update trip groups (+${added.length} -${removed.length})`;
  return "Update monitor.json from web editor";
}

function selectedValues(select) {
  return Array.from(select.selectedOptions).map((o) => Number(o.value));
}

function refreshAvailableList() {
  const q = el("search").value.trim().toLowerCase();
  const available = el("availableList");
  available.innerHTML = "";
  for (const item of state.campgrounds) {
    if (state.selectedIds.includes(item.id)) continue;
    if (
      q &&
      !(
        String(item.name).toLowerCase().includes(q) ||
        String(item.id).includes(q) ||
        String(item.park || "").toLowerCase().includes(q) ||
        String(item.state || "").toLowerCase().includes(q)
      )
    ) {
      continue;
    }
    const opt = document.createElement("option");
    opt.value = String(item.id);
    opt.textContent = renderLabel(item);
    available.appendChild(opt);
  }
}

function refreshSelectedList() {
  const selected = el("selectedList");
  selected.innerHTML = "";
  for (const id of state.selectedIds) {
    const item = byId[id];
    const opt = document.createElement("option");
    opt.value = String(id);
    opt.textContent = item ? renderLabel(item) : `Unknown campground (${id})`;
    selected.appendChild(opt);
  }
  refreshAvailableList();
}

function refreshTripGroupsList() {
  const list = el("tripGroupsList");
  list.innerHTML = "";
  state.tripGroups.forEach((g, idx) => {
    const names = g.campground_ids.slice(0, 3).map((id) => (byId[id] ? byId[id].name : String(id)));
    const suffix = g.campground_ids.length > 3 ? `, +${g.campground_ids.length - 3} more` : "";
    const namesText = names.length ? `${names.join(", ")}${suffix}` : "(no campgrounds yet)";
    const rangeText = g.check_in && g.check_out ? `${g.check_in} to ${g.check_out}` : "dates not set";
    const opt = document.createElement("option");
    opt.value = String(idx);
    const tagLabel = displayUserFromTag(g.discord_tag);
    const tagPart = tagLabel ? ` | tag: ${tagLabel}` : "";
    const modePart = g.full_matches_only ? " | full-only" : "";
    opt.textContent = `Trip ${idx + 1}: ${rangeText} | ${namesText}${tagPart}${modePart}`;
    list.appendChild(opt);
  });
  const dynamicRows = Math.max(2, Math.min(8, state.tripGroups.length || 2));
  list.size = dynamicRows;
}

function currentGroupFromInputs() {
  const checkInIso = el("checkIn").value;
  const checkOutIso = el("checkOut").value;
  if (!checkInIso || !checkOutIso) return null;
  if (checkOutIso <= checkInIso) return null;
  if (state.selectedIds.length === 0) return null;
  const discordTag = el("discordTag").value.trim();
  const fullMatchesOnly = !!el("fullMatchesOnly").checked;
  return {
    campground_ids: [...state.selectedIds],
    check_in: currentDisplayDate(checkInIso),
    check_out: currentDisplayDate(checkOutIso),
    discord_tag: discordTag,
    full_matches_only: fullMatchesOnly,
  };
}

function autoUpdateActiveTripGroup() {
  if (state.activeTripIndex === null) return;
  if (state.activeTripIndex < 0 || state.activeTripIndex >= state.tripGroups.length) return;
  const next = currentGroupFromInputs();
  if (!next) return;
  state.tripGroups[state.activeTripIndex] = next;
  refreshTripGroupsList();
  el("tripGroupsList").value = String(state.activeTripIndex);
  status(`Updated trip group #${state.activeTripIndex + 1}.`);
}

function updatePreview(id) {
  state.previewRequestId += 1;
  const requestId = state.previewRequestId;
  const item = byId[id];
  if (!item) {
    el("preview").textContent = "";
    return;
  }
  const parts = [];
  parts.push(`<strong>${item.name} (${item.id})</strong>`);
  const location = [item.park || "", item.state || ""].filter(Boolean).join(" - ");
  if (location) parts.push(`<div>${location}</div>`);
  if (item.url) parts.push(`<div><a href="${item.url}" target="_blank" rel="noreferrer">Open campground page</a></div>`);
  parts.push('<div class="previewMuted">Loading image preview...</div>');
  el("preview").innerHTML = parts.join("");

  fetchPreviewImageUrl(item)
    .then((imageUrl) => {
      if (requestId !== state.previewRequestId) return;
      const nextParts = [];
      nextParts.push(`<strong>${item.name} (${item.id})</strong>`);
      if (location) nextParts.push(`<div>${location}</div>`);
      if (item.url) {
        nextParts.push(`<div><a href="${item.url}" target="_blank" rel="noreferrer">Open campground page</a></div>`);
      }
      if (imageUrl) {
        nextParts.push(`<img src="${imageUrl}" alt="Preview image for ${item.name}" loading="lazy" />`);
      } else {
        nextParts.push('<div class="previewMuted">No image found. Add RIDB API key above for better preview support.</div>');
      }
      el("preview").innerHTML = nextParts.join("");
    })
    .catch(() => {
      if (requestId !== state.previewRequestId) return;
      const nextParts = [];
      nextParts.push(`<strong>${item.name} (${item.id})</strong>`);
      if (location) nextParts.push(`<div>${location}</div>`);
      if (item.url) {
        nextParts.push(`<div><a href="${item.url}" target="_blank" rel="noreferrer">Open campground page</a></div>`);
      }
      nextParts.push('<div class="previewMuted">Image preview unavailable (RIDB request failed or blocked by browser CORS).</div>');
      el("preview").innerHTML = nextParts.join("");
    });
}

function saveUsersToStorage() {
  try {
    localStorage.setItem(STORAGE_KEYS.savedUsers, JSON.stringify(state.savedUsers));
  } catch (_) {
  }
}

function loadSavedUsersFromStorage() {
  try {
    const raw = localStorage.getItem(STORAGE_KEYS.savedUsers);
    if (!raw) return;
    const parsed = JSON.parse(raw);
    state.savedUsers = normalizeSavedUsers(parsed);
  } catch (_) {
  }
}

function refreshSavedUsersUi() {
  const list = el("savedUsersList");
  const tripUserSelect = el("tripUserSelect");
  if (list) {
    list.innerHTML = "";
    state.savedUsers.forEach((u, idx) => {
      const opt = document.createElement("option");
      opt.value = String(idx);
      opt.textContent = `${u.name} (${u.id})`;
      list.appendChild(opt);
    });
  }
  if (tripUserSelect) {
    const current = tripUserSelect.value;
    tripUserSelect.innerHTML = "";
    const custom = document.createElement("option");
    custom.value = "";
    custom.textContent = "(custom / none)";
    tripUserSelect.appendChild(custom);
    state.savedUsers.forEach((u) => {
      const opt = document.createElement("option");
      opt.value = u.id;
      opt.textContent = `${u.name} (${u.id})`;
      tripUserSelect.appendChild(opt);
    });
    tripUserSelect.value = state.savedUsers.some((u) => u.id === current) ? current : "";
  }
  refreshTripGroupsList();
}

function syncTripUserSelectFromDiscordTag(tag) {
  const tripUserSelect = el("tripUserSelect");
  if (!tripUserSelect) return;
  const id = normalizeDiscordUserId(tag);
  if (!id) {
    tripUserSelect.value = "";
    return;
  }
  tripUserSelect.value = state.savedUsers.some((u) => u.id === id) ? id : "";
}

function saveOrUpdateUser() {
  const name = el("userNameInput").value.trim();
  const id = normalizeDiscordUserId(el("userIdInput").value);
  if (!name || !id) {
    status("Enter both user name and a valid Discord numeric ID.");
    return;
  }
  const existingIndex = state.savedUsers.findIndex((u) => u.id === id || u.name.toLowerCase() === name.toLowerCase());
  if (existingIndex >= 0) {
    state.savedUsers[existingIndex] = { name, id };
  } else {
    state.savedUsers.push({ name, id });
    state.savedUsers.sort((a, b) => a.name.localeCompare(b.name));
  }
  saveUsersToStorage();
  refreshSavedUsersUi();
  syncTripUserSelectFromDiscordTag(el("discordTag").value.trim());
  status(`Saved user ${name}.`);
}

function removeSelectedUser() {
  const list = el("savedUsersList");
  const idx = Number(list ? list.value : "");
  if (!Number.isInteger(idx) || idx < 0 || idx >= state.savedUsers.length) return;
  const removed = state.savedUsers.splice(idx, 1)[0];
  saveUsersToStorage();
  refreshSavedUsersUi();
  syncTripUserSelectFromDiscordTag(el("discordTag").value.trim());
  status(`Removed user ${removed.name}.`);
}

async function saveUsersToRepo() {
  const usersPath = el("usersPath").value.trim();
  if (!usersPath) throw new Error("Users path is required.");
  const branch = el("branch").value.trim();
  const message = `Update saved users (${state.savedUsers.length})`;
  const content = btoa(unescape(encodeURIComponent(`${JSON.stringify(state.savedUsers, null, 2)}\n`)));
  const url = `${githubApiBase()}/${encodeURIComponent(usersPath)}`;
  const payload = {
    message,
    content,
    branch,
  };
  if (state.usersSha) payload.sha = state.usersSha;
  const resp = await fetch(url, {
    method: "PUT",
    headers: {
      ...authHeaders(),
      "Content-Type": "application/json",
      Accept: "application/vnd.github+json",
    },
    body: JSON.stringify(payload),
  });
  if (!resp.ok) {
    const body = await resp.text();
    throw new Error(`Save users failed (${resp.status}): ${body}`);
  }
  const saved = await resp.json();
  state.usersSha = saved.content?.sha || state.usersSha;
}

async function onSaveUsersRepo() {
  try {
    await saveUsersToRepo();
    status("Saved users file to GitHub.");
  } catch (err) {
    status(String(err.message || err));
  }
}

function loadSavedSecrets() {
  let autoLoadEligible = false;
  try {
    const rememberGithub = localStorage.getItem(STORAGE_KEYS.rememberGithubToken) === "1";
    const rememberRidb = localStorage.getItem(STORAGE_KEYS.rememberRidbKey) === "1";
    const rememberGithubNode = el("rememberGithubToken");
    const rememberRidbNode = el("rememberRidbKey");
    if (rememberGithubNode) rememberGithubNode.checked = rememberGithub;
    if (rememberRidbNode) rememberRidbNode.checked = rememberRidb;

    if (rememberGithub) {
      const savedGithub = localStorage.getItem(STORAGE_KEYS.githubToken) || "";
      const tokenNode = el("token");
      if (tokenNode) tokenNode.value = savedGithub;
      if (savedGithub.trim()) autoLoadEligible = true;
    }
    if (rememberRidb) {
      const savedRidb = localStorage.getItem(STORAGE_KEYS.ridbKey) || "";
      const ridbNode = el("ridbApiKey");
      if (ridbNode) ridbNode.value = savedRidb;
    }
  } catch (_) {
  }
  return autoLoadEligible;
}

function saveSecretsIfEnabled() {
  try {
    const rememberGithub = !!el("rememberGithubToken")?.checked;
    const rememberRidb = !!el("rememberRidbKey")?.checked;
    localStorage.setItem(STORAGE_KEYS.rememberGithubToken, rememberGithub ? "1" : "0");
    localStorage.setItem(STORAGE_KEYS.rememberRidbKey, rememberRidb ? "1" : "0");

    const tokenNode = el("token");
    const ridbNode = el("ridbApiKey");

    if (rememberGithub && tokenNode) {
      localStorage.setItem(STORAGE_KEYS.githubToken, tokenNode.value.trim());
    } else {
      localStorage.removeItem(STORAGE_KEYS.githubToken);
    }

    if (rememberRidb && ridbNode) {
      localStorage.setItem(STORAGE_KEYS.ridbKey, ridbNode.value.trim());
    } else {
      localStorage.removeItem(STORAGE_KEYS.ridbKey);
    }
  } catch (_) {
  }
}

function clearSavedSecrets() {
  try {
    localStorage.removeItem(STORAGE_KEYS.githubToken);
    localStorage.removeItem(STORAGE_KEYS.ridbKey);
    localStorage.setItem(STORAGE_KEYS.rememberGithubToken, "0");
    localStorage.setItem(STORAGE_KEYS.rememberRidbKey, "0");
  } catch (_) {
  }
  const tokenNode = el("token");
  const ridbNode = el("ridbApiKey");
  const rememberGithubNode = el("rememberGithubToken");
  const rememberRidbNode = el("rememberRidbKey");
  if (tokenNode) tokenNode.value = "";
  if (ridbNode) ridbNode.value = "";
  if (rememberGithubNode) rememberGithubNode.checked = false;
  if (rememberRidbNode) rememberRidbNode.checked = false;
  state.previewImageCache = {};
  setRidbStatus("RIDB: not tested");
  status("Cleared saved credentials.");
}

async function fetchPreviewImageUrl(item) {
  if (state.previewImageCache[item.id] !== undefined) return state.previewImageCache[item.id];
  const ridbNode = el("ridbApiKey");
  const ridbApiKey = ridbNode ? ridbNode.value.trim() : "";
  if (!ridbApiKey) {
    state.previewImageCache[item.id] = "";
    return "";
  }
  const url = `https://ridb.recreation.gov/api/v1/facilities/${item.id}/media?apikey=${encodeURIComponent(ridbApiKey)}`;
  const resp = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/json" },
  });
  if (!resp.ok) {
    throw new Error(`RIDB media request failed (${resp.status})`);
  }
  const payload = await resp.json();
  let imageUrl = "";
  const records = Array.isArray(payload?.RECDATA) ? payload.RECDATA : [];
  for (const record of records) {
    if (!record || typeof record !== "object") continue;
    if (String(record.MediaType || "").toLowerCase() !== "image") continue;
    for (const key of ["URL", "EntityMediaURL", "MediaURL"]) {
      const candidate = record[key];
      if (typeof candidate === "string" && candidate.startsWith("http")) {
        imageUrl = candidate;
        break;
      }
    }
    if (imageUrl) break;
  }
  state.previewImageCache[item.id] = imageUrl;
  return imageUrl;
}

async function testRidbKey() {
  const ridbNode = el("ridbApiKey");
  const ridbApiKey = ridbNode ? ridbNode.value.trim() : "";
  if (!ridbApiKey) {
    setRidbStatus("RIDB: key missing", "error");
    return;
  }
  try {
    setRidbStatus("RIDB: testing...", "testing");
    const idForTest = state.campgrounds.length ? state.campgrounds[0].id : 256892;
    const url = `https://ridb.recreation.gov/api/v1/facilities/${idForTest}/media?apikey=${encodeURIComponent(ridbApiKey)}`;
    const resp = await fetch(url, { method: "GET", headers: { Accept: "application/json" } });
    if (!resp.ok) {
      setRidbStatus(`RIDB: failed (${resp.status})`, "error");
      return;
    }
    const payload = await resp.json();
    const records = Array.isArray(payload?.RECDATA) ? payload.RECDATA : [];
    const imageCount = records.filter((r) => r && String(r.MediaType || "").toLowerCase() === "image").length;
    setRidbStatus(`RIDB: OK (${imageCount} image records)`, "ok");
  } catch (_err) {
    setRidbStatus("RIDB: request blocked/failed", "error");
  }
}

async function loadJsonFromRepo(path) {
  const branch = encodeURIComponent(el("branch").value.trim());
  const url = `${githubApiBase()}/${encodeURIComponent(path)}?ref=${branch}`;
  const resp = await fetch(url, { headers: { ...authHeaders(), Accept: "application/vnd.github+json" } });
  if (!resp.ok) throw new Error(`GitHub API error (${resp.status}) loading ${path}`);
  const data = await resp.json();
  const content = atob((data.content || "").replace(/\n/g, ""));
  return { json: JSON.parse(content), sha: data.sha };
}

async function onLoad() {
  try {
    status("Loading campgrounds and monitor config...");
    const campgroundsPath = el("campgroundsPath").value.trim();
    const monitorPath = el("monitorPath").value.trim();
    const usersPath = el("usersPath").value.trim();
    const campResult = await loadJsonFromRepo(campgroundsPath);
    const monitorResult = await loadJsonFromRepo(monitorPath);

    if (!Array.isArray(campResult.json)) throw new Error("campgrounds.json is not an array.");
    state.campgrounds = campResult.json
      .filter((x) => x && Number.isInteger(x.id) && typeof x.name === "string")
      .map((x) => ({
        id: Number(x.id),
        name: String(x.name).trim(),
        url: typeof x.url === "string" ? x.url : "",
        park: typeof x.park === "string" ? x.park : "",
        state: typeof x.state === "string" ? x.state : "",
      }))
      .sort((a, b) => a.name.localeCompare(b.name) || a.id - b.id);
    Object.keys(byId).forEach((k) => delete byId[k]);
    state.campgrounds.forEach((c) => { byId[c.id] = c; });

    if (!monitorResult.json || typeof monitorResult.json !== "object") throw new Error("monitor.json must be an object.");
    const monitor = monitorResult.json;
    state.monitorPollSeconds = Number.isInteger(monitor.poll_seconds) ? Number(monitor.poll_seconds) : 60;
    state.monitorSha = monitorResult.sha;
    state.usersSha = null;

    state.tripGroups = Array.isArray(monitor.monitors)
      ? monitor.monitors
          .filter((m) => m && Array.isArray(m.campground_ids) && typeof m.check_in === "string" && typeof m.check_out === "string")
          .map((m) => ({
            campground_ids: m.campground_ids.map((v) => Number(v)).filter((n) => Number.isInteger(n)),
            check_in: toDisplayDate(m.check_in),
            check_out: toDisplayDate(m.check_out),
            discord_tag: typeof m.discord_tag === "string" ? m.discord_tag.trim() : "",
            full_matches_only: !!m.full_matches_only,
          }))
          .filter((m) => m.campground_ids.length > 0)
      : [];
    state.loadedTripGroups = state.tripGroups.map((g) => normalizeGroup(g));

    if (usersPath) {
      try {
        const usersResult = await loadJsonFromRepo(usersPath);
        state.savedUsers = normalizeSavedUsers(usersResult.json);
        state.usersSha = usersResult.sha;
        saveUsersToStorage();
      } catch (_err) {
      }
    }
    refreshSavedUsersUi();

    state.activeTripIndex = null;
    state.selectedIds = state.tripGroups.length ? [...state.tripGroups[0].campground_ids] : [];
    if (state.tripGroups.length) {
      el("checkIn").value = isoDateFromDisplay(state.tripGroups[0].check_in);
      el("checkOut").value = isoDateFromDisplay(state.tripGroups[0].check_out);
      el("discordTag").value = state.tripGroups[0].discord_tag || "";
      el("fullMatchesOnly").checked = !!state.tripGroups[0].full_matches_only;
      syncTripUserSelectFromDiscordTag(state.tripGroups[0].discord_tag || "");
    } else {
      el("checkIn").value = "";
      el("checkOut").value = "";
      el("discordTag").value = "";
      el("fullMatchesOnly").checked = false;
      syncTripUserSelectFromDiscordTag("");
    }

    refreshSelectedList();
    refreshTripGroupsList();
    status(`Loaded ${state.campgrounds.length} campgrounds and ${state.tripGroups.length} trip group(s).`);
  } catch (err) {
    status(String(err.message || err));
  }
}

async function onSave() {
  try {
    if (!state.monitorSha) throw new Error("Load monitor.json first.");
    if (state.tripGroups.length === 0) throw new Error("Add at least one trip group.");
    const poll = Number(state.monitorPollSeconds);
    if (!Number.isInteger(poll) || poll < 0) throw new Error("poll_seconds must be a non-negative integer.");

    const monitors = state.tripGroups
      .map((g) => {
        const checkIn = toStorageDateSafe(g.check_in);
        const checkOut = toStorageDateSafe(g.check_out);
        if (!checkIn || !checkOut || checkOut <= checkIn || !Array.isArray(g.campground_ids) || g.campground_ids.length === 0) {
          return null;
        }
        return {
          campground_ids: g.campground_ids,
          check_in: checkIn,
          check_out: checkOut,
          ...(g.discord_tag ? { discord_tag: g.discord_tag } : {}),
          ...(g.full_matches_only ? { full_matches_only: true } : {}),
        };
      })
      .filter((m) => m !== null);
    if (monitors.length === 0) {
      throw new Error("Add at least one complete trip group (dates + campground) before saving.");
    }
    const payload = { monitors, poll_seconds: poll };

    const monitorPath = el("monitorPath").value.trim();
    const branch = el("branch").value.trim();
    const message = buildAutoCommitMessage();
    const content = btoa(unescape(encodeURIComponent(`${JSON.stringify(payload, null, 2)}\n`)));
    const url = `${githubApiBase()}/${encodeURIComponent(monitorPath)}`;
    const resp = await fetch(url, {
      method: "PUT",
      headers: {
        ...authHeaders(),
        "Content-Type": "application/json",
        Accept: "application/vnd.github+json",
      },
      body: JSON.stringify({
        message,
        content,
        sha: state.monitorSha,
        branch,
      }),
    });
    if (!resp.ok) {
      const body = await resp.text();
      throw new Error(`Save failed (${resp.status}): ${body}`);
    }
    const saved = await resp.json();
    state.monitorSha = saved.content?.sha || state.monitorSha;
    state.loadedTripGroups = state.tripGroups.map((g) => normalizeGroup(g));
    status("Saved monitor.json to GitHub.");
  } catch (err) {
    status(String(err.message || err));
  }
}

function addSelected() {
  const ids = selectedValues(el("availableList"));
  for (const id of ids) {
    if (!state.selectedIds.includes(id)) state.selectedIds.push(id);
  }
  refreshSelectedList();
  autoUpdateActiveTripGroup();
}

function removeSelected() {
  const toRemove = new Set(selectedValues(el("selectedList")));
  state.selectedIds = state.selectedIds.filter((id) => !toRemove.has(id));
  refreshSelectedList();
  autoUpdateActiveTripGroup();
}

function newTripGroup() {
  state.tripGroups.push({
    campground_ids: [],
    check_in: "",
    check_out: "",
    discord_tag: "",
    full_matches_only: false,
  });
  state.activeTripIndex = state.tripGroups.length - 1;
  state.selectedIds = [];
  el("checkIn").value = "";
  el("checkOut").value = "";
  el("discordTag").value = "";
  el("fullMatchesOnly").checked = false;
  syncTripUserSelectFromDiscordTag("");
  refreshSelectedList();
  refreshTripGroupsList();
  el("tripGroupsList").value = String(state.activeTripIndex);
  status(`Created blank trip group #${state.tripGroups.length}.`);
}

function loadTripGroup() {
  const idx = Number(el("tripGroupsList").value);
  if (!Number.isInteger(idx) || idx < 0 || idx >= state.tripGroups.length) return;
  state.activeTripIndex = idx;
  const group = state.tripGroups[idx];
  state.selectedIds = [...group.campground_ids];
  el("checkIn").value = isoDateFromDisplay(group.check_in);
  el("checkOut").value = isoDateFromDisplay(group.check_out);
  el("discordTag").value = group.discord_tag || "";
  el("fullMatchesOnly").checked = !!group.full_matches_only;
  syncTripUserSelectFromDiscordTag(group.discord_tag || "");
  refreshSelectedList();
  status(`Loaded trip group #${idx + 1}.`);
}

function removeTripGroup() {
  const idx = Number(el("tripGroupsList").value);
  if (!Number.isInteger(idx) || idx < 0 || idx >= state.tripGroups.length) return;
  state.tripGroups.splice(idx, 1);
  state.activeTripIndex = null;
  refreshTripGroupsList();
  status(`Removed trip group #${idx + 1}.`);
}

function bindEvents() {
  loadSavedUsersFromStorage();
  refreshSavedUsersUi();
  const shouldAutoLoad = loadSavedSecrets();
  bindIfPresent("loadBtn", "click", onLoad);
  bindIfPresent("saveBtn", "click", onSave);
  bindIfPresent("testRidbBtn", "click", testRidbKey);
  bindIfPresent("clearSavedSecretsBtn", "click", clearSavedSecrets);
  bindIfPresent("rememberGithubToken", "change", saveSecretsIfEnabled);
  bindIfPresent("rememberRidbKey", "change", saveSecretsIfEnabled);
  bindIfPresent("token", "change", saveSecretsIfEnabled);
  bindIfPresent("token", "input", saveSecretsIfEnabled);
  bindIfPresent("ridbApiKey", "input", saveSecretsIfEnabled);
  bindIfPresent("ridbApiKey", "change", () => {
    state.previewImageCache = {};
    saveSecretsIfEnabled();
    setRidbStatus("RIDB: key updated", "testing");
  });
  bindIfPresent("search", "input", refreshAvailableList);
  bindIfPresent("addBtn", "click", addSelected);
  bindIfPresent("removeBtn", "click", removeSelected);
  bindIfPresent("newTripBtn", "click", newTripGroup);
  bindIfPresent("loadTripBtn", "click", loadTripGroup);
  bindIfPresent("removeTripBtn", "click", removeTripGroup);
  bindIfPresent("saveUserBtn", "click", saveOrUpdateUser);
  bindIfPresent("deleteUserBtn", "click", removeSelectedUser);
  bindIfPresent("saveUsersRepoBtn", "click", onSaveUsersRepo);
  bindIfPresent("savedUsersList", "change", () => {
    const list = el("savedUsersList");
    const idx = Number(list ? list.value : "");
    if (!Number.isInteger(idx) || idx < 0 || idx >= state.savedUsers.length) return;
    const selected = state.savedUsers[idx];
    el("userNameInput").value = selected.name;
    el("userIdInput").value = selected.id;
  });
  bindIfPresent("tripUserSelect", "change", () => {
    const id = el("tripUserSelect").value;
    el("discordTag").value = id ? asDiscordMention(id) : "";
    autoUpdateActiveTripGroup();
  });
  bindIfPresent("checkIn", "change", autoUpdateActiveTripGroup);
  bindIfPresent("checkOut", "change", autoUpdateActiveTripGroup);
  bindIfPresent("discordTag", "input", () => {
    syncTripUserSelectFromDiscordTag(el("discordTag").value.trim());
    autoUpdateActiveTripGroup();
  });
  bindIfPresent("fullMatchesOnly", "change", autoUpdateActiveTripGroup);
  bindIfPresent("availableList", "change", () => {
    const ids = selectedValues(el("availableList"));
    if (ids.length) updatePreview(ids[0]);
  });
  bindIfPresent("selectedList", "change", () => {
    const ids = selectedValues(el("selectedList"));
    if (ids.length) updatePreview(ids[0]);
  });
  if (shouldAutoLoad) {
    status("Saved credentials found. Loading from GitHub...");
    onLoad();
  }
}

bindEvents();
