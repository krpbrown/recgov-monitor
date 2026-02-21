const state = {
  campgrounds: [],
  selectedIds: [],
  tripGroups: [],
  activeTripIndex: null,
  monitorSha: null,
  monitorPollSeconds: 60,
  previewImageCache: {},
  previewRequestId: 0,
};

const byId = {};

const el = (id) => document.getElementById(id);
const status = (msg) => {
  const node = el("status");
  if (!node) return;
  node.textContent = msg;
};
const setRidbStatus = (msg, level = "") => {
  const node = el("ridbStatus");
  if (!node) return;
  node.textContent = msg;
  node.classList.remove("ok", "error");
  if (level) node.classList.add(level);
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
    const opt = document.createElement("option");
    opt.value = String(idx);
    opt.textContent = `Trip ${idx + 1}: ${g.check_in} to ${g.check_out} | ${names.join(", ")}${suffix}`;
    list.appendChild(opt);
  });
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

function loadSavedRidbKey() {
  try {
    const saved = localStorage.getItem("recgovMonitorRidbKey");
    const input = el("ridbApiKey");
    if (saved && input) input.value = saved;
  } catch (_) {
  }
}

function saveRidbKey() {
  try {
    const input = el("ridbApiKey");
    if (!input) return;
    localStorage.setItem("recgovMonitorRidbKey", input.value.trim());
  } catch (_) {
  }
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
    setRidbStatus("RIDB: testing...");
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

    state.tripGroups = Array.isArray(monitor.monitors)
      ? monitor.monitors
          .filter((m) => m && Array.isArray(m.campground_ids) && typeof m.check_in === "string" && typeof m.check_out === "string")
          .map((m) => ({
            campground_ids: m.campground_ids.map((v) => Number(v)).filter((n) => Number.isInteger(n)),
            check_in: toDisplayDate(m.check_in),
            check_out: toDisplayDate(m.check_out),
          }))
          .filter((m) => m.campground_ids.length > 0)
      : [];

    state.activeTripIndex = null;
    state.selectedIds = state.tripGroups.length ? [...state.tripGroups[0].campground_ids] : [];
    if (state.tripGroups.length) {
      el("checkIn").value = isoDateFromDisplay(state.tripGroups[0].check_in);
      el("checkOut").value = isoDateFromDisplay(state.tripGroups[0].check_out);
    } else {
      el("checkIn").value = "";
      el("checkOut").value = "";
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

    const monitors = state.tripGroups.map((g) => ({
      campground_ids: g.campground_ids,
      check_in: toStorageDate(g.check_in),
      check_out: toStorageDate(g.check_out),
    }));
    const payload = { monitors, poll_seconds: poll };

    const monitorPath = el("monitorPath").value.trim();
    const branch = el("branch").value.trim();
    const message = el("commitMessage").value.trim() || "Update monitor.json from web editor";
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
}

function removeSelected() {
  const toRemove = new Set(selectedValues(el("selectedList")));
  state.selectedIds = state.selectedIds.filter((id) => !toRemove.has(id));
  refreshSelectedList();
}

function newTripGroup() {
  state.activeTripIndex = null;
  state.selectedIds = [];
  el("checkIn").value = "";
  el("checkOut").value = "";
  refreshSelectedList();
  status("Ready to create a new trip group.");
}

function upsertTripGroup() {
  const checkInIso = el("checkIn").value;
  const checkOutIso = el("checkOut").value;
  if (!checkInIso || !checkOutIso) return status("Set both check-in and check-out dates.");
  if (state.selectedIds.length === 0) return status("Select at least one campground.");
  if (checkOutIso <= checkInIso) return status("Check-out must be after check-in.");

  const group = {
    campground_ids: [...state.selectedIds],
    check_in: currentDisplayDate(checkInIso),
    check_out: currentDisplayDate(checkOutIso),
  };
  if (state.activeTripIndex === null) {
    state.tripGroups.push(group);
    state.activeTripIndex = state.tripGroups.length - 1;
    status(`Added trip group #${state.tripGroups.length}.`);
  } else {
    state.tripGroups[state.activeTripIndex] = group;
    status(`Updated trip group #${state.activeTripIndex + 1}.`);
  }
  refreshTripGroupsList();
  el("tripGroupsList").value = String(state.activeTripIndex);
}

function loadTripGroup() {
  const idx = Number(el("tripGroupsList").value);
  if (!Number.isInteger(idx) || idx < 0 || idx >= state.tripGroups.length) return;
  state.activeTripIndex = idx;
  const group = state.tripGroups[idx];
  state.selectedIds = [...group.campground_ids];
  el("checkIn").value = isoDateFromDisplay(group.check_in);
  el("checkOut").value = isoDateFromDisplay(group.check_out);
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
  loadSavedRidbKey();
  bindIfPresent("loadBtn", "click", onLoad);
  bindIfPresent("saveBtn", "click", onSave);
  bindIfPresent("testRidbBtn", "click", testRidbKey);
  bindIfPresent("ridbApiKey", "change", () => {
    state.previewImageCache = {};
    saveRidbKey();
    setRidbStatus("RIDB: key updated");
  });
  bindIfPresent("search", "input", refreshAvailableList);
  bindIfPresent("addBtn", "click", addSelected);
  bindIfPresent("removeBtn", "click", removeSelected);
  bindIfPresent("newTripBtn", "click", newTripGroup);
  bindIfPresent("upsertTripBtn", "click", upsertTripGroup);
  bindIfPresent("loadTripBtn", "click", loadTripGroup);
  bindIfPresent("removeTripBtn", "click", removeTripGroup);
  bindIfPresent("availableList", "change", () => {
    const ids = selectedValues(el("availableList"));
    if (ids.length) updatePreview(ids[0]);
  });
  bindIfPresent("selectedList", "change", () => {
    const ids = selectedValues(el("selectedList"));
    if (ids.length) updatePreview(ids[0]);
  });
}

bindEvents();
