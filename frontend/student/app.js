/* AI Animal Lab — student labeling flow.
 * Vanilla JS, no dependencies. Talks only to the public API (API_CONTRACT.md):
 *   GET  /api/config
 *   POST /api/session                  {student_id}
 *   GET  /api/next?student_id=...
 *   POST /api/vote                     {student_id, image_id, choice}
 *   GET  /api/images/{image_id}/file
 */
(function () {
  "use strict";

  var LS_API_KEY = "animal-lab.api-base";
  var LS_STUDENT_KEY = "animal-lab.student-id";
  var STUDENT_ID_PATTERN = /^[A-Za-z0-9_-]{6,64}$/;
  var FETCH_TIMEOUT_MS = 10000;
  var SAVED_FEEDBACK_MS = 350;
  var ANSWER_STAGGER_MS = 70;

  var state = {
    apiBase: "",
    studentId: "",
    labeled: 0,
    target: 0,
    current: null,   // /api/next payload for the image on screen
    busy: false,     // a vote is in flight or a transition is running
    renderToken: 0   // invalidates stale async image loads
  };

  var screens = {
    loading: document.getElementById("screen-loading"),
    welcome: document.getElementById("screen-welcome"),
    quiz: document.getElementById("screen-quiz"),
    done: document.getElementById("screen-done"),
    error: document.getElementById("screen-error")
  };

  var els = {
    btnStart: document.getElementById("btn-start"),
    btnBootRetry: document.getElementById("btn-retry-boot"),
    welcomeMission: document.getElementById("welcome-mission"),
    welcomeEmpty: document.getElementById("welcome-empty"),
    progress: document.getElementById("progress"),
    progressFill: document.getElementById("progress-fill"),
    progressCount: document.getElementById("progress-count"),
    quizBody: document.getElementById("quiz-body"),
    banner: document.getElementById("banner"),
    bannerText: document.getElementById("banner-text"),
    bannerRetry: document.getElementById("banner-retry"),
    bannerClose: document.getElementById("banner-close"),
    photoLoading: document.getElementById("photo-loading"),
    photoSpinner: document.getElementById("photo-spinner"),
    photoLoadingText: document.getElementById("photo-loading-text"),
    photoRetry: document.getElementById("photo-retry"),
    photo: document.getElementById("photo"),
    savedPill: document.getElementById("saved-pill"),
    question: document.getElementById("quiz-question"),
    answers: document.getElementById("answers"),
    doneCopy: document.getElementById("done-copy"),
    errorCopy: document.getElementById("error-copy")
  };

  var reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
  var bannerRetryAction = null;

  /* ---------------- utilities ---------------- */

  function ApiError(message) {
    this.name = "ApiError";
    this.message = message;
  }
  ApiError.prototype = Object.create(Error.prototype);

  function friendlyError(err) {
    return err instanceof ApiError ? err.message : "Something unexpected went wrong. Please try again.";
  }

  function plural(n, word) {
    return n === 1 ? word : word + "s";
  }

  function capitalize(text) {
    return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
  }

  function delay(ms) {
    return new Promise(function (resolve) { setTimeout(resolve, ms); });
  }

  function makeUuid() {
    if (window.crypto && typeof window.crypto.randomUUID === "function") {
      return window.crypto.randomUUID();
    }
    var bytes = new Uint8Array(16);
    window.crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    var hex = [];
    for (var i = 0; i < bytes.length; i++) {
      hex.push((bytes[i] + 0x100).toString(16).slice(1));
    }
    return hex.slice(0, 4).join("") + "-" + hex.slice(4, 6).join("") + "-" +
      hex.slice(6, 8).join("") + "-" + hex.slice(8, 10).join("") + "-" +
      hex.slice(10, 16).join("");
  }

  /* ---------------- configuration ---------------- */

  function resolveApiBase() {
    var base = "";
    var params = new URLSearchParams(window.location.search);
    if (params.has("api")) {
      base = (params.get("api") || "").trim().replace(/\/+$/, "");
      try {
        if (base) { window.localStorage.setItem(LS_API_KEY, base); }
        else { window.localStorage.removeItem(LS_API_KEY); }
      } catch (e) { /* storage unavailable — config value still applies */ }
      return base;
    }
    try {
      var saved = window.localStorage.getItem(LS_API_KEY);
      if (saved) { return saved.replace(/\/+$/, ""); }
    } catch (e) { /* ignore */ }
    var config = window.ANIMAL_LAB_CONFIG || {};
    return String(config.API_BASE || "").replace(/\/+$/, "");
  }

  function ensureStudentId() {
    var id = "";
    try { id = window.localStorage.getItem(LS_STUDENT_KEY) || ""; } catch (e) { /* ignore */ }
    if (!STUDENT_ID_PATTERN.test(id)) {
      id = "s-" + makeUuid();
      try { window.localStorage.setItem(LS_STUDENT_KEY, id); } catch (e) { /* ignore */ }
    }
    return id;
  }

  /* ---------------- API access ---------------- */

  function api(path, options) {
    options = options || {};
    var controller = new AbortController();
    var timer = setTimeout(function () { controller.abort(); }, FETCH_TIMEOUT_MS);

    var init = {
      method: options.method || "GET",
      cache: "no-store",
      signal: controller.signal
    };
    if (options.body) {
      init.headers = { "Content-Type": "application/json" };
      init.body = options.body;
    }

    return fetch(state.apiBase + path, init).then(function (res) {
      clearTimeout(timer);
      if (!res.ok) {
        throw new ApiError("The server had trouble (error " + res.status + ").");
      }
      return res.json().catch(function () {
        throw new ApiError("The server sent a response we couldn't read.");
      });
    }, function (err) {
      clearTimeout(timer);
      if (err && err.name === "AbortError") {
        throw new ApiError("The request timed out.");
      }
      throw new ApiError("Can't reach the server — check your connection.");
    });
  }

  function imageUrl(imageId) {
    return state.apiBase + "/api/images/" + imageId + "/file";
  }

  function nextUrl() {
    return "/api/next?student_id=" + encodeURIComponent(state.studentId);
  }

  var imageCache = new Map();

  function preloadImage(url) {
    var pending = imageCache.get(url);
    if (!pending) {
      pending = new Promise(function (resolve, reject) {
        var img = new Image();
        img.onload = function () { resolve(img); };
        img.onerror = function () {
          imageCache.delete(url); // allow a later retry to try again
          reject(new Error("image load failed"));
        };
        img.src = url;
      });
      imageCache.set(url, pending);
    }
    return pending;
  }

  /* ---------------- screen switching ---------------- */

  function showScreen(name) {
    Object.keys(screens).forEach(function (key) {
      screens[key].hidden = key !== name;
    });
  }

  /* ---------------- progress ---------------- */

  function setProgress(labeled, target) {
    // "labeled" only ever grows for a student, so Math.max guards against a
    // stale read racing ahead of a vote insert on the server.
    if (typeof labeled === "number") {
      state.labeled = Math.max(state.labeled, labeled);
    }
    if (typeof target === "number" && target > 0) {
      state.target = target;
    }
    var total = Math.max(state.target, 1);
    var pct = Math.max(0, Math.min(100, Math.round((state.labeled / total) * 100)));
    els.progressFill.style.width = pct + "%";
    els.progressCount.textContent = state.labeled + " / " + state.target;
    els.progress.setAttribute("aria-valuemax", String(state.target));
    els.progress.setAttribute("aria-valuenow", String(state.labeled));
    els.progress.setAttribute("aria-valuetext", state.labeled + " of " + state.target + " photos labeled");
  }

  /* ---------------- banner + saved pill ---------------- */

  function showBanner(text, onRetry) {
    els.bannerText.textContent = text;
    bannerRetryAction = onRetry || null;
    els.banner.hidden = false;
  }

  function hideBanner() {
    els.banner.hidden = true;
    bannerRetryAction = null;
  }

  function showSaved() {
    els.savedPill.hidden = false;
  }

  /* ---------------- answers ---------------- */

  function clearAnswers() {
    while (els.answers.firstChild) {
      els.answers.removeChild(els.answers.firstChild);
    }
  }

  function buildAnswers(choices) {
    clearAnswers();
    choices.forEach(function (choice, index) {
      var btn = document.createElement("button");
      btn.type = "button";
      btn.className = "answer";
      btn.textContent = capitalize(choice);
      btn.setAttribute("aria-label", "Answer: " + capitalize(choice));
      btn.style.animationDelay = (index * ANSWER_STAGGER_MS) + "ms";
      btn.addEventListener("click", function () { onAnswer(choice, btn); });
      els.answers.appendChild(btn);
    });
  }

  function lockAnswers(pickedBtn) {
    Array.prototype.forEach.call(els.answers.children, function (btn) {
      btn.disabled = true;
      if (pickedBtn && btn === pickedBtn) {
        btn.classList.add("answer--picked");
        btn.setAttribute("aria-pressed", "true");
      }
    });
  }

  function unlockAnswers() {
    Array.prototype.forEach.call(els.answers.children, function (btn) {
      btn.disabled = false;
      btn.classList.remove("answer--picked");
      btn.removeAttribute("aria-pressed");
    });
  }

  /* ---------------- question rendering ---------------- */

  function loadImage(imageId) {
    var token = ++state.renderToken;
    els.photo.hidden = true;
    els.photo.classList.remove("photo--loaded");
    els.photo.removeAttribute("src");
    els.photoRetry.hidden = true;
    els.photoLoadingText.hidden = true;
    els.photoSpinner.hidden = false;
    els.photoLoading.hidden = false;

    var url = imageUrl(imageId);
    preloadImage(url).then(function () {
      if (token !== state.renderToken) { return; }
      els.photo.src = url;
      els.photo.hidden = false;
      els.photoLoading.hidden = true;
      requestAnimationFrame(function () {
        if (token === state.renderToken) {
          els.photo.classList.add("photo--loaded");
        }
      });
    }).catch(function () {
      if (token !== state.renderToken) { return; }
      els.photoSpinner.hidden = true;
      els.photoLoadingText.hidden = false;
      els.photoRetry.hidden = false;
    });
  }

  function renderQuestion(data) {
    hideBanner();
    els.savedPill.hidden = true;
    loadImage(data.image_id);
    buildAnswers(data.choices);
  }

  /* ---------------- transitions ---------------- */

  function waitTransition(el, fallbackMs) {
    return new Promise(function (resolve) {
      var finished = false;
      function finish() {
        if (finished) { return; }
        finished = true;
        el.removeEventListener("transitionend", finish);
        resolve();
      }
      el.addEventListener("transitionend", finish);
      setTimeout(finish, fallbackMs);
    });
  }

  function slideOut() {
    if (reduceMotion.matches) { return Promise.resolve(); }
    els.quizBody.classList.add("quiz-body--leave");
    return waitTransition(els.quizBody, 450).then(function () {
      els.quizBody.classList.remove("quiz-body--leave");
    });
  }

  function slideIn() {
    if (reduceMotion.matches) {
      els.question.focus({ preventScroll: true });
      return Promise.resolve();
    }
    els.quizBody.classList.add("quiz-body--enter");
    els.quizBody.getBoundingClientRect(); // force reflow so the offset applies
    els.quizBody.classList.remove("quiz-body--enter");
    return waitTransition(els.quizBody, 450).then(function () {
      els.question.focus({ preventScroll: true });
    });
  }

  /* ---------------- screens ---------------- */

  function showWelcome(empty) {
    els.btnStart.hidden = Boolean(empty);
    els.btnStart.disabled = false;
    els.btnStart.textContent = "Start labeling";
    if (empty) {
      els.welcomeMission.hidden = true;
      els.welcomeEmpty.hidden = false;
    } else {
      els.welcomeMission.textContent = "You'll label about " + state.target + " " +
        plural(state.target, "photo") +
        ". For each one, pick the animal you see — your answers become part of the dataset we'll train a model on.";
      els.welcomeMission.hidden = false;
      els.welcomeEmpty.hidden = true;
    }
    showScreen("welcome");
  }

  function showDone(count) {
    hideBanner();
    els.savedPill.hidden = true;
    var n = typeof count === "number" ? count : state.labeled;
    els.doneCopy.textContent = n > 0
      ? "You labeled " + n + " " + plural(n, "photo") +
        ". Your answers are now part of the training dataset — thanks for helping out."
      : "There are no photos to label right now — thanks for stopping by!";
    showScreen("done");
  }

  function showFatal(message) {
    els.errorCopy.textContent = message;
    showScreen("error");
  }

  /* ---------------- quiz flow ---------------- */

  function fetchAndShowNext() {
    return api(nextUrl()).then(function (next) {
      if (next.done) {
        showDone(next.labeled);
        return;
      }
      state.current = next;
      setProgress(next.labeled, next.target);
      renderQuestion(next);
    });
  }

  function advance(next) {
    return slideOut().then(function () {
      if (next.done) {
        showDone(next.labeled);
        return null;
      }
      if (state.current && next.image_id === state.current.image_id) {
        // Our vote may have been counted just after the server picked "next";
        // ask once more so the same photo is never shown twice in a row.
        return api(nextUrl()).then(function (again) {
          if (again.done) {
            showDone(again.labeled);
            return null;
          }
          return again;
        }).catch(function () {
          return next; // keep the original payload; voting again is safe (deduped)
        });
      }
      return next;
    }).then(function (usable) {
      if (!usable) { return null; } // completion screen already shown
      state.current = usable;
      setProgress(usable.labeled, usable.target);
      renderQuestion(usable);
      return slideIn();
    });
  }

  function onAnswer(choice, pickedBtn) {
    if (state.busy || !state.current) { return; }
    state.busy = true;
    hideBanner();
    lockAnswers(pickedBtn);

    var imageId = state.current.image_id;
    var settle = function (promise) {
      return promise.then(
        function (data) { return { ok: true, data: data }; },
        function (err) { return { ok: false, err: err }; }
      );
    };

    var votePromise = settle(api("/api/vote", {
      method: "POST",
      body: JSON.stringify({
        student_id: state.studentId,
        image_id: imageId,
        choice: choice
      })
    }));
    var nextPromise = settle(api(nextUrl()));

    // Prefetch the next photo while the vote is being submitted.
    nextPromise.then(function (out) {
      if (out.ok && out.data && out.data.done === false) {
        preloadImage(imageUrl(out.data.image_id)).catch(function () { /* retried on render */ });
      }
    });

    Promise.all([votePromise, nextPromise]).then(function (results) {
      var vote = results[0];
      var next = results[1];

      if (!vote.ok) {
        state.busy = false;
        unlockAnswers();
        showBanner("Couldn't save that answer. Tap Retry — resending is always safe.", function () {
          retryAnswer(choice);
        });
        return;
      }

      setProgress(vote.data.labeled, vote.data.target);

      if (!next.ok) {
        state.busy = false;
        unlockAnswers();
        showBanner("Your answer was saved, but the next photo didn't load.", retryNext);
        return;
      }

      showSaved();
      delay(SAVED_FEEDBACK_MS).then(function () {
        return advance(next.data);
      }).then(function () {
        state.busy = false;
      });
    });
  }

  function retryAnswer(choice) {
    if (state.busy || !state.current) { return; }
    state.busy = true;
    hideBanner();
    lockAnswers(null);

    api("/api/vote", {
      method: "POST",
      body: JSON.stringify({
        student_id: state.studentId,
        image_id: state.current.image_id,
        choice: choice
      })
    }).then(function (vote) {
      setProgress(vote.labeled, vote.target);
      showSaved();
      return api(nextUrl());
    }).then(function (next) {
      return delay(SAVED_FEEDBACK_MS).then(function () {
        return advance(next);
      });
    }).then(function () {
      state.busy = false;
    }).catch(function () {
      state.busy = false;
      unlockAnswers();
      showBanner("Still couldn't save that answer — check your connection and try again.", function () {
        retryAnswer(choice);
      });
    });
  }

  function retryNext() {
    if (state.busy) { return; }
    state.busy = true;
    hideBanner();
    lockAnswers(null);
    api(nextUrl()).then(function (next) {
      return advance(next);
    }).then(function () {
      state.busy = false;
    }).catch(function () {
      state.busy = false;
      unlockAnswers();
      showBanner("Still couldn't load the next photo.", retryNext);
    });
  }

  /* ---------------- boot ---------------- */

  function resumeIntoQuiz() {
    showScreen("quiz");
    setProgress(state.labeled, state.target);
    return fetchAndShowNext().catch(function () {
      showBanner("Couldn't load your next photo. Tap Retry to pick up where you left off.", function () {
        if (state.busy) { return; }
        state.busy = true;
        fetchAndShowNext().then(function () {
          state.busy = false;
        }).catch(function () {
          state.busy = false;
          showBanner("Still couldn't load a photo — try again in a moment.", function () {
            state.busy = false;
            resumeIntoQuiz();
          });
        });
      });
    });
  }

  // GitHub Pages serves config.js with a 10-minute browser cache, so right
  // after a redeploy a browser may still hold a stale API URL. Before giving
  // up, re-fetch config.js bypassing the cache and try once more.
  function refreshCachedConfig() {
    return fetch("config.js", { cache: "reload" })
      .then(function (res) {
        if (!res.ok) { throw new Error("config.js HTTP " + res.status); }
        return res.text();
      })
      .then(function (code) {
        new Function(code)(); // re-executes config.js -> window.ANIMAL_LAB_CONFIG
        // a stale "?api=" override (remembered in localStorage) must not beat
        // the freshly fetched config when the boot just failed
        var fresh = (window.ANIMAL_LAB_CONFIG || {}).API_BASE || "";
        try {
          var saved = window.localStorage.getItem(LS_API_KEY);
          if (saved && saved !== fresh) {
            window.localStorage.removeItem(LS_API_KEY);
          }
        } catch (e) { /* storage unavailable — nothing to clear */ }
      })
      .catch(function () { /* keep whatever config we already had */ });
  }

  function boot(freshConfigTried) {
    state.apiBase = resolveApiBase();
    state.studentId = ensureStudentId();
    state.labeled = 0;
    state.target = 0;
    state.current = null;
    state.busy = true;
    hideBanner();
    showScreen("loading");

    var configPromise = api("/api/config");
    var sessionPromise = api("/api/session", {
      method: "POST",
      body: JSON.stringify({ student_id: state.studentId })
    });

    Promise.all([configPromise, sessionPromise]).then(function (results) {
      var config = results[0];
      var session = results[1];

      state.studentId = session.student_id || state.studentId;
      state.target = typeof session.target === "number" ? session.target : 0;
      state.labeled = typeof session.labeled === "number" ? session.labeled : 0;

      var emptyCatalog = state.target === 0 || Number(config.image_count) === 0;

      if (session.done) {
        showDone(state.labeled);
      } else if (state.labeled > 0) {
        // Restore-after-refresh: skip the welcome card.
        return resumeIntoQuiz();
      } else if (emptyCatalog) {
        showWelcome(true);
      } else {
        showWelcome(false);
      }
      return null;
    }).catch(function () {
      if (!freshConfigTried) {
        return refreshCachedConfig().then(function () { boot(true); });
      }
      showFatal("We couldn't reach AI Animal Lab just now. Check your connection and try again.");
    }).then(function () {
      state.busy = false;
    });
  }

  /* ---------------- events ---------------- */

  els.btnStart.addEventListener("click", function () {
    if (state.busy) { return; }
    state.busy = true;
    els.btnStart.disabled = true;
    els.btnStart.textContent = "Loading photos…";
    showScreen("quiz"); // show the labeling chrome while the first photo loads
    setProgress(state.labeled, state.target);
    fetchAndShowNext().catch(function (err) {
      showFatal(friendlyError(err));
    }).then(function () {
      els.btnStart.disabled = false;
      els.btnStart.textContent = "Start labeling";
      state.busy = false;
    });
  });

  els.btnBootRetry.addEventListener("click", function () {
    boot();
  });

  els.bannerRetry.addEventListener("click", function () {
    if (bannerRetryAction) { bannerRetryAction(); }
  });

  els.bannerClose.addEventListener("click", function () {
    hideBanner();
  });

  els.photoRetry.addEventListener("click", function () {
    if (!state.current) { return; }
    loadImage(state.current.image_id);
  });

  boot();
})();
