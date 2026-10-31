/* AI Animal Lab — instructor upload console. Vanilla JS, same-origin admin API. */
"use strict";

(function () {
  const PREVIEW_LIMIT = 12;
  const FETCH_TIMEOUT_MS = 20000;
  const BYTE = 1;

  const els = {
    category: document.getElementById("category"),
    categoryMsg: document.getElementById("category-msg"),
    dropzone: document.getElementById("dropzone"),
    fileInput: document.getElementById("file-input"),
    fileInfo: document.getElementById("file-info"),
    uploadBtn: document.getElementById("upload-btn"),
    uploadLabel: document.querySelector("#upload-btn .btn-label"),
    result: document.getElementById("upload-result"),
    countsGrid: document.getElementById("counts-grid"),
    totalPill: document.getElementById("catalog-total"),
    previewGrid: document.getElementById("preview-grid"),
    catalogMsg: document.getElementById("catalog-msg"),
  };

  let categories = [];
  let chosenFiles = [];
  let uploading = false;

  function esc(value) {
    return String(value).replace(/[&<>"']/g, (ch) => ({
      "&": "&amp;",
      "<": "&lt;",
      ">": "&gt;",
      '"': "&quot;",
      "'": "&#39;",
    }[ch]));
  }

  async function fetchJSON(url, options) {
    const ctrl = new AbortController();
    const timer = setTimeout(() => ctrl.abort(), FETCH_TIMEOUT_MS);
    try {
      const res = await fetch(url, Object.assign({}, options, { signal: ctrl.signal }));
      let data = null;
      try { data = await res.json(); } catch (err) { /* non-JSON body */ }
      if (!res.ok) {
        const detail = data && data.detail ? String(data.detail) : "HTTP " + res.status;
        throw new Error(detail);
      }
      return data;
    } finally {
      clearTimeout(timer);
    }
  }

  function fmtBytes(bytes) {
    if (bytes >= 1048576) return (bytes / 1048576).toFixed(1) + " MB";
    if (bytes >= 1024) return Math.round(bytes / 1024) + " KB";
    return Math.round(bytes * BYTE) + " B";
  }

  /* --------------------------------------------------------------------------
     Categories
     -------------------------------------------------------------------------- */

  async function loadCategories() {
    // Category source strategy:
    //   1) GET /api/admin/config — same-origin mirror of the public /api/config.
    //   2) Fallback: derive the category keys from GET /api/admin/images -> "counts".
    let list = null;
    try {
      const cfg = await fetchJSON("/api/admin/config");
      if (cfg && Array.isArray(cfg.categories) && cfg.categories.length > 0) list = cfg.categories;
    } catch (err) { /* older backend — try the fallback below. */ }
    if (!list) {
      try {
        const inv = await fetchJSON("/api/admin/images");
        const keys = inv && inv.counts ? Object.keys(inv.counts) : [];
        if (keys.length > 0) list = keys;
      } catch (err) { /* both sources failed */ }
    }
    categories = Array.isArray(list) ? list.slice() : [];
    renderCategorySelect();
  }

  function renderCategorySelect() {
    els.category.textContent = "";
    els.categoryMsg.hidden = true;
    els.categoryMsg.textContent = "";

    if (categories.length === 0) {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.disabled = true;
      placeholder.selected = true;
      placeholder.textContent = "No categories available";
      els.category.appendChild(placeholder);
      els.categoryMsg.hidden = false;
      const text = document.createTextNode("Could not load categories. ");
      const retry = document.createElement("button");
      retry.type = "button";
      retry.textContent = "Retry";
      retry.addEventListener("click", () => {
        retry.disabled = true;
        loadCategories();
      });
      els.categoryMsg.appendChild(text);
      els.categoryMsg.appendChild(retry);
    } else {
      const placeholder = document.createElement("option");
      placeholder.value = "";
      placeholder.disabled = true;
      placeholder.selected = true;
      placeholder.textContent = "Choose a category…";
      els.category.appendChild(placeholder);
      categories.forEach((name) => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.textContent = name;
        els.category.appendChild(opt);
      });
    }
    updateUploadState();
  }

  /* --------------------------------------------------------------------------
     File picker / dropzone
     -------------------------------------------------------------------------- */

  function setChosen(fileList) {
    chosenFiles = Array.from(fileList || []);
    if (chosenFiles.length === 0) {
      els.fileInfo.hidden = true;
      els.fileInfo.textContent = "";
    } else {
      const totalBytes = chosenFiles.reduce((sum, f) => sum + (Number(f.size) || 0), 0);
      const names = chosenFiles.map((f) => f.name).join(", ");
      els.fileInfo.hidden = false;
      els.fileInfo.textContent = "";
      const strong = document.createElement("strong");
      strong.textContent = chosenFiles.length + (chosenFiles.length === 1 ? " file · " : " files · ") + fmtBytes(totalBytes);
      els.fileInfo.appendChild(strong);
      els.fileInfo.appendChild(document.createTextNode(" — " + names));
      els.fileInfo.title = names;
    }
    updateUploadState();
  }

  function updateUploadState() {
    els.uploadBtn.disabled = uploading || chosenFiles.length === 0 || !els.category.value;
  }

  function wireDropzone() {
    ["dragenter", "dragover"].forEach((type) => {
      els.dropzone.addEventListener(type, (e) => {
        e.preventDefault();
        els.dropzone.classList.add("is-over");
      });
    });
    ["dragleave", "dragend"].forEach((type) => {
      els.dropzone.addEventListener(type, () => {
        els.dropzone.classList.remove("is-over");
      });
    });
    els.dropzone.addEventListener("drop", (e) => {
      e.preventDefault();
      els.dropzone.classList.remove("is-over");
      if (e.dataTransfer && e.dataTransfer.files) setChosen(e.dataTransfer.files);
    });
    els.dropzone.addEventListener("click", () => els.fileInput.click());
    els.dropzone.addEventListener("keydown", (e) => {
      if (e.key === "Enter" || e.key === " ") {
        e.preventDefault();
        els.fileInput.click();
      }
    });
    els.fileInput.addEventListener("change", () => setChosen(els.fileInput.files));
    els.category.addEventListener("change", updateUploadState);
  }

  /* --------------------------------------------------------------------------
     Upload
     -------------------------------------------------------------------------- */

  async function doUpload() {
    if (uploading || chosenFiles.length === 0 || !els.category.value) return;
    uploading = true;
    els.uploadBtn.disabled = true;
    els.uploadBtn.classList.add("is-busy");
    els.uploadLabel.textContent = "Uploading…";

    const form = new FormData();
    form.append("category", els.category.value);
    chosenFiles.forEach((f) => form.append("files", f));
    const batch = chosenFiles.slice(); // kept for result rendering before clearing

    try {
      const res = await fetchJSON("/api/admin/upload", { method: "POST", body: form });
      renderResult(res, batch);
      setChosen([]);
      els.fileInput.value = ""; // allow re-picking the same files
      await refreshCatalog();
    } catch (err) {
      els.result.hidden = false;
      els.result.className = "upload-result err";
      els.result.innerHTML = "";
      const box = document.createElement("div");
      box.className = "result-box";
      const strong = document.createElement("strong");
      strong.textContent = "Upload failed. ";
      box.appendChild(strong);
      box.appendChild(document.createTextNode(err && err.message ? err.message : "Network error."));
      els.result.appendChild(box);
    } finally {
      uploading = false;
      els.uploadBtn.classList.remove("is-busy");
      els.uploadLabel.textContent = "Upload to lab";
      updateUploadState();
    }
  }

  function renderResult(res, batch) {
    // The response lists uploaded entries WITHOUT original names
    // ({image_id, filename, category}), so we recover display names by matching the
    // failed entries' "name" field against the local batch.
    const uploaded = Array.isArray(res && res.uploaded) ? res.uploaded : [];
    const failed = Array.isArray(res && res.failed) ? res.failed : [];
    const failedNames = new Set(failed.map((f) => String(f.name)));
    const succeeded = batch.filter((f) => !failedNames.has(f.name));

    els.result.hidden = false;
    els.result.className = uploaded.length > 0 ? "upload-result ok" : "upload-result err";
    els.result.innerHTML = "";

    const box = document.createElement("div");
    box.className = "result-box";
    const strong = document.createElement("strong");
    strong.textContent = uploaded.length > 0
      ? "Upload complete — " + uploaded.length + (uploaded.length === 1 ? " image" : " images") + " added" + (failed.length ? ", " + failed.length + " rejected." : ".")
      : "Nothing was uploaded.";
    box.appendChild(strong);

    if (succeeded.length > 0 || failed.length > 0) {
      const list = document.createElement("ul");
      list.className = "result-list";
      succeeded.forEach((f) => {
        const li = document.createElement("li");
        const name = document.createElement("span");
        name.textContent = f.name;
        const tag = document.createElement("span");
        tag.className = "tag";
        tag.textContent = els.category.value;
        li.appendChild(name);
        li.appendChild(tag);
        list.appendChild(li);
      });
      failed.forEach((f) => {
        const li = document.createElement("li");
        li.className = "bad";
        const name = document.createElement("span");
        name.textContent = String(f.name) + " — " + String(f.reason);
        li.appendChild(name);
        list.appendChild(li);
      });
      box.appendChild(list);
    }
    els.result.appendChild(box);
  }

  /* --------------------------------------------------------------------------
     Catalog panel
     -------------------------------------------------------------------------- */

  async function refreshCatalog() {
    try {
      const inv = await fetchJSON("/api/admin/images");
      renderCatalog(inv);
      els.catalogMsg.hidden = true;
      els.catalogMsg.textContent = "";
      els.catalogMsg.classList.remove("err");
    } catch (err) {
      els.catalogMsg.hidden = false;
      els.catalogMsg.classList.add("err");
      els.catalogMsg.textContent = "Could not load catalog — " + (err && err.message ? err.message : "network error");
    }
  }

  function renderCatalog(inv) {
    const counts = inv && inv.counts && typeof inv.counts === "object" ? inv.counts : {};
    const total = Number(inv && inv.total) || 0;
    const images = Array.isArray(inv && inv.images) ? inv.images : [];

    els.totalPill.textContent = total + (total === 1 ? " image" : " images");

    els.countsGrid.textContent = "";
    const totalCard = document.createElement("div");
    totalCard.className = "count-card total";
    totalCard.innerHTML =
      '<span class="count-name">All images</span><span class="count-num">' + total + "</span>";
    els.countsGrid.appendChild(totalCard);
    Object.keys(counts).forEach((key) => {
      const card = document.createElement("div");
      card.className = "count-card";
      card.innerHTML =
        '<span class="count-name">' + esc(key) + '</span><span class="count-num">' + (Number(counts[key]) || 0) + "</span>";
      els.countsGrid.appendChild(card);
    });

    els.previewGrid.textContent = "";
    if (images.length === 0) {
      const empty = document.createElement("div");
      empty.className = "catalog-empty";
      empty.textContent = "No images yet — upload the first batch to get started.";
      els.previewGrid.appendChild(empty);
      return;
    }

    images.slice(0, PREVIEW_LIMIT).forEach((img) => {
      const tile = document.createElement("figure");
      tile.className = "tile";
      tile.title = img.original_name ? String(img.original_name) : String(img.filename || "");
      const pic = document.createElement("img");
      pic.loading = "lazy";
      pic.alt = tile.title;
      pic.src = "/api/admin/files/" + encodeURIComponent(String(img.filename || ""));
      const badge = document.createElement("span");
      badge.className = "cat-badge";
      badge.textContent = String(img.category || "");
      const votes = document.createElement("span");
      votes.className = "vote-chip";
      const vc = Number(img.vote_count) || 0;
      votes.textContent = vc + (vc === 1 ? " vote" : " votes");
      tile.appendChild(pic);
      tile.appendChild(badge);
      tile.appendChild(votes);
      els.previewGrid.appendChild(tile);
    });
  }

  /* --------------------------------------------------------------------------
     Init
     -------------------------------------------------------------------------- */

  wireDropzone();
  els.uploadBtn.addEventListener("click", doUpload);
  loadCategories();
  refreshCatalog();
})();
