/* Folio — frontend. Behaviour follows design/folio-mockup.html.
 *
 * Talks to server.py on the same origin. Answers stream as Server-Sent Events
 * and every finished turn is saved server-side (SQLite) with a payload that
 * re-renders it exactly. If the server has no key of its own, the visitor's key
 * lives only in memory for this tab and is sent per request in "x-api-key".
 */
(function () {
  "use strict";

  /* ------------------------------------------------------------ basics */
  var $ = function (id) { return document.getElementById(id); };
  var app = $("app"), side = $("side"), root = document.documentElement;
  var ICON = {
    check: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M20 6L9 17l-5-5"/></svg>',
    bang: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M12 7v6M12 17h.01"/></svg>',
    warn: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 9v4M12 17h.01"/><path d="M10.3 3.9L1.8 18a2 2 0 0 0 1.7 3h17a2 2 0 0 0 1.7-3L13.7 3.9a2 2 0 0 0-3.4 0z"/></svg>',
    copy: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><rect x="9" y="9" width="11" height="11" rx="2"/><path d="M5 15V5a2 2 0 0 1 2-2h10"/></svg>',
    chev: '<svg class="chev" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg>',
    file: '<svg class="fi" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.6"><path d="M14 3H7a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h10a2 2 0 0 0 2-2V8z"/><path d="M14 3v5h5"/></svg>',
    x: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M6 6l12 12M18 6L6 18"/></svg>',
    send: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.1" stroke-linecap="round"><path d="M12 19V5"/><path d="M5 12l7-7 7 7"/></svg>',
    stop: '<svg viewBox="0 0 24 24" fill="currentColor"><rect x="7" y="7" width="10" height="10" rx="2"/></svg>',
    ck: '<svg class="ck" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.6"><path d="M20 6L9 17l-5-5"/></svg>',
    plus: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"><path d="M12 5v14M5 12h14"/></svg>'
  };
  var OK = /\.(pdf|docx|txt|md|markdown|csv|xlsx|pptx)$/i;

  var state = {
    docs: [], uploads: [], ready: false, serverKey: false, apiKey: "", demo: null,
    models: [], model: "", scope: null, chat: null, chats: [], busy: null,
    k: 4, fast: false, query: ""
  };

  function esc(s) { return String(s == null ? "" : s).replace(/[&<>"']/g, function (ch) { return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch]; }); }
  function store(k, v) { try { localStorage.setItem("folio-" + k, v); } catch (e) { /* private mode */ } }
  function load(k) { try { return localStorage.getItem("folio-" + k); } catch (e) { return null; } }
  function short(n) { n = String(n || ""); return n.length > 34 ? n.slice(0, 32) + "…" : n; }
  function plural(n, w) { return n + " " + w + (n === 1 ? "" : "s"); }

  function toast(m, ms) {
    var t = $("toast"); t.textContent = m; t.classList.add("on");
    clearTimeout(toast.h); toast.h = setTimeout(function () { t.classList.remove("on"); }, ms || 1800);
  }
  function copy(text, msg) {
    function done() { toast(msg || "Copied"); }
    function fb() {
      var a = document.createElement("textarea"); a.value = text; a.style.position = "fixed"; a.style.opacity = "0";
      document.body.appendChild(a); a.select(); try { document.execCommand("copy"); } catch (e) { /* */ } a.remove(); done();
    }
    if (navigator.clipboard && navigator.clipboard.writeText) navigator.clipboard.writeText(text).then(done, fb); else fb();
  }

  function headers(json) {
    var h = {};
    if (json) h["Content-Type"] = "application/json";
    if (!state.serverKey && state.apiKey) h["x-api-key"] = state.apiKey;
    return h;
  }
  function api(path, opts) {
    opts = opts || {};
    return fetch(path, { method: opts.method || "GET", headers: headers(opts.json !== undefined), body: opts.json !== undefined ? JSON.stringify(opts.json) : undefined })
      .catch(function () { throw { code: "network", message: "Can't reach Folio's server. Is it still running?" }; })
      .then(function (res) {
        return res.json().catch(function () { return null; }).then(function (data) {
          if (!res.ok) throw errorFrom(res.status, data);
          return data;
        });
      });
  }
  function errorFrom(status, data) {
    var d = data && data.detail;
    if (d && typeof d === "object" && !Array.isArray(d)) return d;
    return { code: "error", message: "Request failed (" + status + ")." };
  }

  /* ------------------------------------------------------------ settings (persisted) */
  function isDark() { var t = root.getAttribute("data-theme"); return t ? t === "dark" : matchMedia("(prefers-color-scheme: dark)").matches; }
  function toggleTheme() { var n = isDark() ? "light" : "dark"; root.setAttribute("data-theme", n); store("theme", n); }
  state.fast = load("fast") === "1";
  var sk = +load("k"); if (sk >= 2 && sk <= 8) state.k = sk;

  /* ------------------------------------------------------------ sidebar modes */
  var phone = matchMedia("(max-width:639px)"), wide = matchMedia("(min-width:1100px)");
  function applyMode() {
    if (phone.matches) app.classList.remove("rail");
    else {
      app.classList.remove("open");
      var pref = load("sidebar");
      app.classList.toggle("rail", wide.matches ? pref === "rail" : pref !== "full");
    }
    syncSide();
  }
  function syncSide() {
    // Hidden sidebar content is inert; in rail mode the hidden parts are display:none.
    side.inert = phone.matches && !app.classList.contains("open");
    $("openside").setAttribute("aria-expanded", String(app.classList.contains("open")));
    var r = app.classList.contains("rail");
    $("sbtoggle").setAttribute("aria-label", r ? "Expand sidebar" : "Collapse sidebar");
    $("sbtoggle").title = r ? "Expand sidebar" : "Collapse sidebar";
  }
  function expand() { app.classList.remove("rail"); store("sidebar", "full"); syncSide(); }
  $("sbtoggle").onclick = function () { var r = !app.classList.contains("rail"); app.classList.toggle("rail", r); store("sidebar", r ? "rail" : "full"); syncSide(); };
  $("raildocs").onclick = function () { expand(); $("addfile").focus(); };
  $("railchats").onclick = function () { expand(); var f = document.querySelector("#chats .item"); if (f) f.focus(); };
  $("openside").onclick = function () { app.classList.add("open"); syncSide(); setTimeout(function () { $("newchat").focus(); }, 60); };
  function closeDrawer() { if (app.classList.contains("open")) { app.classList.remove("open"); syncSide(); $("openside").focus(); } }
  $("scrim").onclick = closeDrawer;
  phone.addEventListener("change", applyMode); wide.addEventListener("change", applyMode);
  applyMode();

  /* ------------------------------------------------------------ status line */
  function renderStatus() {
    var n = state.docs.length, p = state.docs.reduce(function (a, d) { return a + d.chunks; }, 0);
    $("statustx").textContent = !state.ready && n ? "Loading documents…" : plural(n, "doc") + " · " + plural(p, "passage");
  }

  /* ------------------------------------------------------------ documents */
  function renderDocs() {
    var box = $("docs"); box.innerHTML = "";
    if (!state.docs.length && !state.uploads.length) box.innerHTML = '<div class="hint-empty">No documents yet. Use + to add files.</div>';
    state.uploads.concat(state.docs).forEach(function (d) {
      var el = document.createElement("div");
      el.setAttribute("role", "listitem");
      var right, extra = "";
      if (d.uid && d.state === "error") {
        el.className = "item err";
        right = '<span class="st" title="' + esc(d.message) + '">' + esc(d.label) + '</span><span class="sr">: ' + esc(d.message) + '</span>';
      } else if (d.uid) {
        el.className = "item";
        right = '<span class="ct" aria-label="' + Math.round(d.p) + ' percent">' + Math.round(d.p) + '%</span>';
        extra = '<span class="pbar"><i style="width:' + d.p + '%"></i></span>';
      } else {
        el.className = "item";
        right = '<span class="ct" title="' + plural(d.chunks, "passage") + '">' + d.chunks + '</span>';
      }
      var removable = (!d.uid && !d.readonly) || d.state === "error";
      if (d.readonly) right += '<span class="sr"> (sample document)</span>';
      el.innerHTML = ICON.file + '<span class="nm" title="' + esc(d.name) + '">' + esc(d.name) + '</span>' + right +
        (removable ? '<button class="x" type="button" aria-label="' + (d.uid ? "Dismiss " : "Remove ") + esc(d.name) + '">' + ICON.x + '</button>' : "") + extra;
      if (removable) el.querySelector(".x").onclick = function () { d.uid ? dismissUpload(d) : removeDoc(d); };
      box.appendChild(el);
    });
    renderStatus();
  }
  function applyDocs(data) {
    var before = docKey();
    state.docs = data.docs || [];
    if (typeof data.ready === "boolean") state.ready = data.ready;
    if (state.scope && !state.docs.some(function (d) { return d.id === state.scope; })) setScope(null);
    renderDocs();
    if (docKey() !== before) loadSuggestions();
  }
  function docKey() { return state.docs.map(function (d) { return d.id + ":" + d.chunks; }).sort().join("|"); }
  function removeDoc(d) {
    api("/api/docs/" + encodeURIComponent(d.id), { method: "DELETE" })
      .then(function (data) { applyDocs(data); toast("Removed " + short(d.name)); })
      .catch(function (err) { toast(err.message); });
  }
  function dismissUpload(u) { state.uploads = state.uploads.filter(function (x) { return x !== u; }); renderDocs(); }

  /* uploads: one request per file so each row shows its own progress and result */
  var uploadSeq = 0, uploading = false;
  function pick() { $("file").click(); }
  $("addfile").onclick = pick; $("attach").onclick = pick;
  $("file").onchange = function () { addFiles(this.files); this.value = ""; };
  function addFiles(list) {
    if (app.classList.contains("rail")) expand();
    Array.prototype.forEach.call(list, function (f) {
      var u = { uid: ++uploadSeq, name: f.name, file: f, p: 0 };
      if (!OK.test(f.name)) {
        u.state = "error"; u.label = "Not supported"; u.message = "Use PDF, Word, Excel, PowerPoint, CSV, Markdown or text files.";
        toast(f.name + " isn't a supported file type");
      } else u.state = "queued";
      state.uploads.unshift(u);
    });
    renderDocs(); pump();
  }
  function pump() {
    if (uploading) return;
    var next = state.uploads.filter(function (u) { return u.state === "queued"; }).pop();
    if (!next) return;
    uploading = true;
    uploadOne(next).then(function () { uploading = false; pump(); });
  }
  var ERR_LABEL = { scanned: "Scanned", unsupported: "Not supported", encrypted: "Locked", too_large: "Too large", empty: "No text" };
  function uploadOne(u) {
    return new Promise(function (resolve) {
      u.state = "uploading"; renderDocs();
      var form = new FormData(); form.append("files", u.file, u.name);
      var xhr = new XMLHttpRequest(), creep = null;
      xhr.open("POST", "/api/upload"); xhr.responseType = "json";
      // Upload bytes fill the first 40%; indexing (server-side) then creeps toward 95%.
      xhr.upload.onprogress = function (e) { if (e.lengthComputable) { u.p = 40 * e.loaded / e.total; renderDocs(); } };
      xhr.upload.onload = function () {
        u.state = "indexing"; u.p = Math.max(u.p, 40);
        creep = setInterval(function () { u.p += (95 - u.p) * 0.08; renderDocs(); }, 300);
      };
      xhr.onload = function () {
        clearInterval(creep);
        var data = xhr.response || {};
        if (xhr.status === 200) {
          state.uploads = state.uploads.filter(function (x) { return x !== u; });
          applyDocs(data);
          toast(u.name + " is ready");
        } else {
          var r = (data.detail && data.detail.results && data.detail.results[0]) || data.detail || {};
          u.state = "error"; u.label = ERR_LABEL[r.code] || "Failed"; u.message = r.message || "Couldn't index this file.";
          toast(u.message, 3200); renderDocs();
        }
        delete u.file; resolve();
      };
      xhr.onerror = function () { clearInterval(creep); u.state = "error"; u.label = "Failed"; u.message = "Upload failed. Is the server running?"; renderDocs(); resolve(); };
      xhr.send(form);
    });
  }

  /* drag and drop anywhere on the page */
  var dragDepth = 0;
  function hasFiles(e) { return e.dataTransfer && Array.prototype.indexOf.call(e.dataTransfer.types, "Files") > -1; }
  document.addEventListener("dragenter", function (e) { if (!hasFiles(e)) return; e.preventDefault(); dragDepth++; $("docsec").classList.add("drop"); app.classList.add("dropzone-on"); });
  document.addEventListener("dragover", function (e) { if (hasFiles(e)) e.preventDefault(); });
  document.addEventListener("dragleave", function (e) { if (!hasFiles(e)) return; if (--dragDepth <= 0) { dragDepth = 0; $("docsec").classList.remove("drop"); app.classList.remove("dropzone-on"); } });
  document.addEventListener("drop", function (e) {
    if (!hasFiles(e)) return;
    e.preventDefault(); dragDepth = 0; $("docsec").classList.remove("drop"); app.classList.remove("dropzone-on");
    if (e.dataTransfer.files.length) addFiles(e.dataTransfer.files);
  });

  /* ------------------------------------------------------------ chats (Recents) */
  function renderChats() {
    var box = $("chats"); box.innerHTML = "";
    if (!state.chats.length) box.innerHTML = '<div class="hint-empty">' + (state.query ? "No chats match." : "No chats yet.") + '</div>';
    state.chats.forEach(function (ch) {
      var el = document.createElement("div");
      el.className = "item chat" + (ch.id === state.chat ? " on" : ""); el.tabIndex = 0;
      el.setAttribute("role", "listitem");
      if (ch.id === state.chat) el.setAttribute("aria-current", "true");
      el.innerHTML = '<span class="nm" title="' + esc(ch.title) + '">' + esc(ch.title) + '</span><button class="x" type="button" aria-label="Delete chat ' + esc(ch.title) + '">' + ICON.x + '</button>';
      el.onclick = function (e) { if (e.target.closest(".x")) return; openChat(ch.id); };
      el.onkeydown = function (e) { if ((e.key === "Enter" || e.key === " ") && e.target === el) { e.preventDefault(); openChat(ch.id); } };
      el.querySelector(".x").onclick = function () { deleteChat(ch); };
      box.appendChild(el);
    });
  }
  function loadChats() {
    var q = state.query;
    return api("/api/chats" + (q ? "?q=" + encodeURIComponent(q) : "")).then(function (d) {
      if (q !== state.query) return;
      state.chats = d.chats || []; renderChats();
      var cur = state.chats.find(function (c) { return c.id === state.chat; });
      if (cur) $("title").textContent = cur.title;
    }).catch(function () { /* keep the current list */ });
  }
  function deleteChat(ch) {
    api("/api/chats/" + encodeURIComponent(ch.id), { method: "DELETE" }).then(function () {
      toast("Chat deleted");
      if (state.chat === ch.id) newChat(); else loadChats();
      if (state.chat !== ch.id) return;
    }).catch(function (err) { toast(err.message); });
  }

  /* search chats */
  var searchTimer = null;
  $("searchbtn").onclick = function () {
    if (app.classList.contains("rail")) expand();
    var b = $("searchbox"); b.hidden = !b.hidden;
    this.setAttribute("aria-expanded", String(!b.hidden));
    if (!b.hidden) $("q").focus(); else { $("q").value = ""; state.query = ""; loadChats(); }
  };
  $("q").oninput = function () {
    clearTimeout(searchTimer);
    var v = this.value.trim();
    searchTimer = setTimeout(function () { state.query = v; loadChats(); }, 150);
  };
  $("q").onkeydown = function (e) { if (e.key === "Escape" && this.value) { e.stopPropagation(); this.value = ""; state.query = ""; loadChats(); } };

  function openChat(id) {
    if (state.busy) state.busy.stop();
    closeEv(); closePop();
    return api("/api/chats/" + encodeURIComponent(id)).then(function (chat) {
      state.chat = chat.id;
      $("title").textContent = chat.title;
      showThread();
      var th = $("thread"); th.innerHTML = ""; turns.clear();
      var pending = null;
      chat.messages.forEach(function (m) {
        if (m.role === "user") { pending = m.content; addQuestion(m.content); }
        else { renderStored(pending, m); pending = null; }
      });
      renderChats();
      $("scroll").scrollTop = 0;
      if (phone.matches) closeDrawer();
    }).catch(function (err) { toast(err.message); loadChats(); });
  }
  function newChat() {
    if (state.busy) state.busy.stop();
    state.chat = null; turns.clear();
    $("thread").innerHTML = ""; $("thread").hidden = true; $("empty").hidden = false; $("title").textContent = "";
    closeEv(); closePop(); renderChats();
    if (phone.matches) closeDrawer();
    $("ta").focus(); startSig();
  }
  $("newchat").onclick = newChat; $("newchat2").onclick = newChat;
  function showThread() { $("empty").hidden = true; $("thread").hidden = false; }

  /* ------------------------------------------------------------ popovers */
  var pop = null;
  function closePop() {
    if (!pop) return null;
    pop.el.remove(); pop.anchor.setAttribute("aria-expanded", "false");
    var a = pop.anchor; pop = null; return a;
  }
  function openPop(anchor, html, bind, role) {
    if (pop && pop.anchor === anchor) { closePop(); return; }
    closePop();
    var el = document.createElement("div"); el.className = "pop"; el.setAttribute("role", role || "menu"); el.innerHTML = html;
    if (role === "dialog") el.setAttribute("aria-label", anchor.title || "Settings");
    document.body.appendChild(el);
    var r = anchor.getBoundingClientRect(), w = el.offsetWidth, h = el.offsetHeight;
    var left = Math.min(Math.max(12, r.left), window.innerWidth - w - 12);
    var top = r.top - h - 8; if (top < 12) top = Math.min(r.bottom + 8, window.innerHeight - h - 12);
    el.style.left = left + "px"; el.style.top = top + "px";
    anchor.setAttribute("aria-expanded", "true"); pop = { el: el, anchor: anchor };
    bind(el);
    var f = el.querySelector('[aria-checked="true"]') || el.querySelector("button:not([disabled]),input"); if (f) f.focus();
    el.addEventListener("keydown", function (e) {  // arrow keys move through menu options
      if (e.key !== "ArrowDown" && e.key !== "ArrowUp") return;
      var opts = Array.prototype.slice.call(el.querySelectorAll(".opt:not([disabled])"));
      if (!opts.length) return;
      e.preventDefault();
      var i = opts.indexOf(document.activeElement);
      opts[(i + (e.key === "ArrowDown" ? 1 : -1) + opts.length) % opts.length].focus();
    });
  }
  document.addEventListener("mousedown", function (e) { if (pop && !pop.el.contains(e.target) && !pop.anchor.contains(e.target)) closePop(); });
  window.addEventListener("resize", function () { closePop(); });

  /* document scope picker */
  function setScope(id) {
    state.scope = id || null;
    var d = state.docs.find(function (x) { return x.id === id; });
    $("scopelbl").textContent = d ? short(d.name) : "All documents";
    $("scopebtn").classList.toggle("scoped", !!d);
    $("ta").placeholder = d ? "Ask about " + short(d.name) + "…" : "Ask about your documents…";
  }
  $("scopebtn").onclick = function () {
    var items = [{ v: null, l: "All documents" }].concat(state.docs.map(function (d) { return { v: d.id, l: d.name }; }));
    openPop(this, items.map(function (it, i) {
      return (i === 1 ? "<hr>" : "") + '<button class="opt" type="button" role="menuitemradio" data-i="' + i + '" aria-checked="' + (state.scope === it.v) + '">' + ICON.ck + '<span title="' + esc(it.l) + '">' + esc(it.l) + '</span></button>';
    }).join(""), function (el) {
      Array.prototype.forEach.call(el.querySelectorAll(".opt"), function (b) { b.onclick = function () { setScope(items[+b.dataset.i].v); closePop(); $("ta").focus(); }; });
    });
  };

  /* model picker */
  function modelLabel(id) { var m = state.models.find(function (x) { return x.id === id; }); return m ? m.label : ""; }
  function setModel(id) {
    state.model = id; store("model", id);
    $("modellbl").textContent = modelLabel(id) || "Model";
  }
  $("modelbtn").onclick = function () {
    if (!state.models.length) {
      openPop(this, '<button class="opt" type="button" disabled><span>' + (state.serverKey || state.apiKey ? "Models unavailable" : "Add an API key in Settings") + '<small>The model list loads once a key works.</small></span></button>', function () {});
      return;
    }
    openPop(this, state.models.map(function (m, i) {
      return '<button class="opt" type="button" role="menuitemradio" data-i="' + i + '" aria-checked="' + (state.model === m.id) + '">' + ICON.ck + '<span>' + esc(m.label) + '<small>' + esc(m.sub) + '</small></span></button>';
    }).join(""), function (el) {
      Array.prototype.forEach.call(el.querySelectorAll(".opt"), function (b) { b.onclick = function () { setModel(state.models[+b.dataset.i].id); closePop(); $("ta").focus(); }; });
    });
  };
  var modelsReq = 0;
  function loadModels() {
    var id = ++modelsReq;
    if (!state.serverKey && !state.apiKey) { state.models = []; $("modellbl").textContent = "Model"; return Promise.resolve(); }
    return api("/api/models").then(function (d) {
      if (id !== modelsReq) return;
      state.models = d.models || []; state.keyError = "";
      var saved = load("model");
      setModel(state.models.some(function (m) { return m.id === saved; }) ? saved : d.default);
      loadSuggestions();
    }).catch(function (err) {
      if (id !== modelsReq) return;
      state.models = []; state.keyError = err.message; $("modellbl").textContent = "Model";
      var kh = document.getElementById("keyhint"); if (kh) { kh.textContent = err.message; kh.className = "bad"; }
    });
  }

  /* settings popover */
  $("settingsbtn").onclick = function () {
    var keyRow = state.serverKey ? "" :
      '<hr><div class="keyrow"><label for="keyin">Groq API key</label><input class="keyin" id="keyin" type="password" autocomplete="off" spellcheck="false" placeholder="gsk_…" value="' + esc(state.apiKey) + '"' + (state.keyError ? ' aria-invalid="true"' : "") + ' aria-describedby="keyhint">' +
      '<small id="keyhint"' + (state.keyError ? ' class="bad">' + esc(state.keyError) : '>Kept in this tab only, never saved. Free at <a href="https://console.groq.com" target="_blank" rel="noopener">console.groq.com</a>.') + '</small></div>';
    openPop(this,
      '<div class="row"><div class="l">Dark mode</div><button class="sw" type="button" id="sw-theme" aria-label="Dark mode" aria-pressed="' + isDark() + '"></button></div>' +
      '<div class="row"><div class="l">Fast mode<small>Skips claim and conflict checks so free limits last longer.</small></div><button class="sw" type="button" id="sw-fast" aria-label="Fast mode" aria-pressed="' + state.fast + '"></button></div>' +
      '<hr><div class="row"><div class="l">Passages per answer</div><input type="range" id="sl-k" min="2" max="8" value="' + state.k + '" aria-label="Passages per answer"><span class="val" id="sl-v">' + state.k + '</span></div>' + keyRow,
      function (el) {
        el.querySelector("#sw-theme").onclick = function () { toggleTheme(); this.setAttribute("aria-pressed", String(isDark())); };
        el.querySelector("#sw-fast").onclick = function () { state.fast = !state.fast; store("fast", state.fast ? "1" : "0"); this.setAttribute("aria-pressed", String(state.fast)); };
        el.querySelector("#sl-k").oninput = function () { state.k = +this.value; store("k", this.value); el.querySelector("#sl-v").textContent = this.value; };
        var ki = el.querySelector("#keyin"), timer = null;
        if (ki) ki.oninput = function () {
          state.apiKey = this.value.trim(); state.keyError = ""; this.removeAttribute("aria-invalid");
          clearTimeout(timer); timer = setTimeout(loadModels, 500);
        };
      }, "dialog");
  };

  /* ------------------------------------------------------------ markdown (escape first: model output can't inject markup) */
  var RE_LIST = /^(\s*)([-*+•]|\d{1,9}[.)])\s+(.*)$/, RE_FENCE = /^\s*(`{3,}|~{3,})\s*([\w+#.-]*)\s*$/,
    RE_HEADING = /^\s{0,3}(#{1,6})\s+(.*?)\s*#*\s*$/, RE_HR = /^\s{0,3}([-*_])(\s*\1){2,}\s*$/, RE_QUOTE = /^\s{0,3}>\s?/,
    RE_TABLE_SEP = /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/, RE_CITE = /\[(\d+(?:\s*[,–-]\s*\d+)*)\]/g;
  function citeNumbers(group) {
    var out = [];
    group.split(/\s*,\s*/).forEach(function (part) {
      var r = part.split(/\s*[–-]\s*/);
      if (r.length === 2) { var a = +r[0], b = +r[1]; if (b >= a && b - a < 20) for (var n = a; n <= b; n++) out.push(n); else out.push(a, b); }
      else if (/^\d+$/.test(part.trim())) out.push(+part);
    });
    return out;
  }
  function inline(text, ctx) {
    var slots = [], hold = function (h) { return "\u0000" + (slots.push(h) - 1) + "\u0000"; };
    var s = esc(text);
    s = s.replace(/`([^`]+)`/g, function (_, c) { return hold("<code>" + c + "</code>"); });
    s = s.replace(/\[([^\]]+)\]\(([^)\s]+)\)/g, function (m, label, url) { return /^(https?:|mailto:)/i.test(url) ? hold('<a href="' + url + '" target="_blank" rel="noopener noreferrer">' + label + "</a>") : label; });
    s = s.replace(RE_CITE, function (_, g) {
      return hold(citeNumbers(g).map(function (n) {
        var occ = ctx.occ++, p = ctx.passages[n - 1];
        if (!p) return '<span class="cite bad" title="No source ' + n + '">' + n + "</span>";
        return '<button type="button" class="cite" data-n="' + n + '" data-occ="' + occ + '" aria-label="Source ' + n + ": " + esc(p.label) + '">' + n + "</button>";
      }).join(""));
    });
    s = s.replace(/\*\*(?=\S)([\s\S]*?\S)\*\*/g, "<strong>$1</strong>")
      .replace(/(^|[^\w])__(?=\S)([\s\S]*?\S)__(?!\w)/g, "$1<strong>$2</strong>")
      .replace(/(^|[^*\w])\*(?=[^\s*])([^*]*?[^\s*])\*(?!\*)/g, "$1<em>$2</em>")
      .replace(/(^|[^\w])_(?=[^\s_])([^_]*?[^\s_])_(?!\w)/g, "$1<em>$2</em>")
      .replace(/~~(?=\S)([\s\S]*?\S)~~/g, "<del>$1</del>");
    while (/\u0000\d+\u0000/.test(s)) s = s.replace(/\u0000(\d+)\u0000/g, function (_, i) { return slots[+i]; });
    return s;
  }
  function indentOf(l) { return l.match(/^\s*/)[0].replace(/\t/g, "    ").length; }
  function isBlock(l) { return RE_FENCE.test(l) || RE_HEADING.test(l) || RE_HR.test(l) || RE_QUOTE.test(l) || RE_LIST.test(l); }
  function splitRow(l) { return l.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map(function (c) { return c.trim(); }); }
  function parseList(lines, start, ctx) {
    var first = lines[start].match(RE_LIST), base = indentOf(lines[start]), ordered = /\d/.test(first[2]), items = [], i = start;
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) {
        var j = i + 1; while (j < lines.length && !lines[j].trim()) j++;
        if (lines[j] && RE_LIST.test(lines[j]) && indentOf(lines[j]) >= base) { i = j; continue; }
        break;
      }
      var m = line.match(RE_LIST), ind = indentOf(line);
      if (m && ind < base + 2) { if (/\d/.test(m[2]) !== ordered) break; items.push({ t: m[3], c: "" }); i++; }
      else if (m && items.length) { var r = parseList(lines, i, ctx); items[items.length - 1].c += r[0]; i = r[1]; }
      else if (!m && items.length && !isBlock(line)) { items[items.length - 1].t += "\n" + line.trim(); i++; }
      else break;
    }
    var tag = ordered ? "ol" : "ul", sn = ordered ? parseInt(first[2], 10) : 1;
    return ["<" + tag + (ordered && sn !== 1 ? ' start="' + sn + '"' : "") + ">" + items.map(function (it) { return "<li>" + inline(it.t, ctx).replace(/\n/g, "<br>") + it.c + "</li>"; }).join("") + "</" + tag + ">", i];
  }
  function markdown(src, ctx) {
    var lines = String(src).replace(/\u0000/g, "").replace(/\r\n?/g, "\n").split("\n"), out = [], i = 0, m;
    while (i < lines.length) {
      var line = lines[i];
      if (!line.trim()) { i++; continue; }
      if ((m = line.match(RE_FENCE))) {
        var code = []; i++;
        while (i < lines.length && lines[i].trim().indexOf(m[1]) !== 0) code.push(lines[i++]);
        i++; out.push("<pre><code>" + esc(code.join("\n")) + "</code></pre>");
      } else if ((m = line.match(RE_HEADING))) {
        var tag = ["h2", "h3", "h4", "h4", "h4", "h4"][m[1].length - 1]; out.push("<" + tag + ">" + inline(m[2], ctx) + "</" + tag + ">"); i++;
      } else if (RE_HR.test(line)) { out.push("<hr>"); i++; }
      else if (RE_QUOTE.test(line)) {
        var q = []; while (i < lines.length && lines[i].trim() && RE_QUOTE.test(lines[i])) q.push(lines[i++].replace(RE_QUOTE, ""));
        out.push("<blockquote>" + markdown(q.join("\n"), ctx) + "</blockquote>");
      } else if (line.indexOf("|") > -1 && i + 1 < lines.length && RE_TABLE_SEP.test(lines[i + 1])) {
        var head = splitRow(line), rows = []; i += 2;
        while (i < lines.length && lines[i].indexOf("|") > -1 && lines[i].trim()) rows.push(splitRow(lines[i++]));
        out.push('<div class="tablewrap"><table><thead><tr>' + head.map(function (c) { return "<th>" + inline(c, ctx) + "</th>"; }).join("") + "</tr></thead><tbody>" +
          rows.map(function (r) { return "<tr>" + head.map(function (_, k) { return "<td>" + inline(r[k] || "", ctx) + "</td>"; }).join("") + "</tr>"; }).join("") + "</tbody></table></div>");
      } else if (RE_LIST.test(line)) { var res = parseList(lines, i, ctx); out.push(res[0]); i = res[1]; }
      else {
        var para = [line.trim()]; i++;
        while (i < lines.length && lines[i].trim() && !isBlock(lines[i])) para.push(lines[i++].trim());
        out.push("<p>" + para.map(function (p) { return inline(p, ctx); }).join("<br>") + "</p>");
      }
    }
    return out.join("");
  }

  /* ------------------------------------------------------------ answers */
  var turns = new Map(), turnSeq = 0;

  function addQuestion(text) {
    var q = document.createElement("div"); q.className = "q"; q.textContent = text; $("thread").appendChild(q);
  }
  function makeTurn(question) {
    var id = "t" + (++turnSeq);
    var el = document.createElement("div"); el.className = "a"; el.dataset.turn = id; el.setAttribute("aria-busy", "true");
    el.innerHTML = '<div class="slot-think"></div><div class="slot-main"></div><div class="slot-table"></div><div class="slot-conflicts"></div><div class="slot-claims"></div><div class="slot-err"></div><div class="slot-meta"></div><div class="slot-sources"></div>';
    Array.prototype.forEach.call(el.children, function (c) { c.style.display = "contents"; });
    $("thread").appendChild(el);
    var t = { id: id, el: el, question: question, raw: "", passages: [], citations: [], mode: "search", scope: [], confidence: null,
      check: null, coverage: null, conflicts: [], table: null, error: null, ms: 0, model: state.model, modelLabel: modelLabel(state.model), stopped: false };
    turns.set(id, t);
    return t;
  }
  function slot(t, name) { return t.el.querySelector(".slot-" + name); }

  function thinking(t, text) {
    slot(t, "think").innerHTML = text ? '<div class="thinking"><span class="dots" aria-hidden="true"><i></i><i></i><i></i></span><span class="step">' + esc(text) + "</span></div>" : "";
  }
  function bodyHTML(t) { return markdown(t.raw, { passages: t.passages, occ: 0 }); }
  var raf = 0;
  function renderStreaming(t) {
    if (raf) return;
    raf = requestAnimationFrame(function () {
      raf = 0;
      slot(t, "main").innerHTML = '<div class="body streaming">' + bodyHTML(t) + "</div>";
      stickBottom();
    });
  }

  function renderMain(t) {
    var main = slot(t, "main"), g = t.coverage;
    if (g && g.not_covered && g.not_covered.length) {
      // Gap report: what the documents do and don't cover (mockup layout).
      var yes = t.raw.trim() ? '<div class="gaprow yes"><span class="dot">' + ICON.check + '</span><div class="tx"><b>In the documents</b><div class="body">' + bodyHTML(t) + "</div></div></div>" : "";
      var noList = g.not_covered.length === 1 ? esc(cap(g.not_covered[0])) + "." : "<ul>" + g.not_covered.map(function (x) { return "<li>" + esc(cap(x)) + "</li>"; }).join("") + "</ul>";
      main.innerHTML = '<div class="gap body">' + yes + '<div class="gaprow no"><span class="dot">' + ICON.bang + '</span><div class="tx"><b>Not in the documents</b>' + noList + "</div></div></div>";
    } else {
      main.innerHTML = t.raw.trim() ? '<div class="body">' + bodyHTML(t) + "</div>" : "";
    }
  }
  function cap(s) { s = String(s).trim(); return s.charAt(0).toUpperCase() + s.slice(1); }

  function splitValue(v) {
    var m = String(v).match(/^(.*?\S)\s*(±\s*[\d.\s]+%?)$/);
    return m ? esc(m[1]) + " <small>" + esc(m[2].replace(/\s+/g, " ")) + "</small>" : esc(v);
  }
  function renderTable(t) {
    var tb = t.table, box = slot(t, "table");
    if (!tb || !tb.rows) { box.innerHTML = ""; return; }
    box.innerHTML = '<div class="tablewrap"><table><thead><tr>' + tb.columns.map(function (c) { return '<th scope="col">' + esc(c) + "</th>"; }).join("") +
      '<th scope="col" style="text-align:right">Source</th></tr></thead><tbody>' + tb.rows.map(function (r, i) {
        return "<tr>" + tb.columns.map(function (_, j) {
          var c = r.cells[j] || { value: "", verbatim: null }, num = isValue(c.value);
          if (!num) return "<td>" + esc(c.value) + "</td>";
          return '<td class="v' + (c.verbatim === false ? " unv" : "") + '">' + splitValue(c.value) +
            (c.verbatim === false ? '<span class="flag" title="Not found word for word in the cited passage">not in text</span>' : "") + "</td>";
        }).join("") + '<td class="s"><button type="button" class="pg" data-row="' + i + '" title="' + esc(r.doc) + '">' + esc(locShort(r.location) || "source") + "</button></td></tr>";
      }).join("") + "</tbody></table></div>";
  }
  // A table cell is a "value" (mono, checked against the text) if it starts like a number: 12.5%, ±0.4, 0.984, +3.1%.
  function isValue(v) { return /^[+\-−~≈<>±]?\s*\d/.test(String(v || "").trim()); }
  function locShort(loc) { return String(loc || "").replace(/^page /, "p. ").replace(/^slide /, "slide ").replace(/^sheet /, ""); }

  function renderConflicts(t) {
    slot(t, "conflicts").innerHTML = (t.conflicts || []).map(function (c) {
      var side = function (s) { return "<div><b>" + esc(s.value) + "</b><span>" + esc(s.label || "") + (s.location ? ", " + esc(s.location) : "") + "</span></div>"; };
      var lead = c.contradiction ? "These figures disagree. " : "Not a contradiction. ";
      var cites = c.sides.map(function (s) { var p = t.passages[s.passage - 1]; return p ? '<button type="button" class="cite" data-n="' + s.passage + '" aria-label="Source ' + s.passage + ": " + esc(p.label) + '">' + s.passage + "</button>" : ""; }).join("");
      return '<div class="callout"><div class="hd">' + ICON.warn + esc(c.topic || "Two different figures") + '</div><div class="cmp">' + side(c.sides[0]) + side(c.sides[1]) + "</div><p>" + lead + esc(c.explanation || "") + cites + "</p></div>";
    }).join("");
  }

  function renderClaims(t) {
    var c = t.check, box = slot(t, "claims");
    if (!c || !c.ran || c.supported === c.total) { box.innerHTML = ""; return; }
    var bad = c.claims.filter(function (x) { return !x.supported; });
    box.innerHTML = '<div class="callout"><div class="hd">' + ICON.warn + (bad.length === 1 ? "1 claim isn't supported by the sources" : bad.length + " claims aren't supported by the sources") +
      "</div><ul>" + bad.map(function (x) { return "<li>" + esc(x.claim) + "</li>"; }).join("") + "</ul><p>Check these against the documents before relying on them.</p></div>";
  }

  var ERR_TITLE = { demo_busy: "Lots of questions right now", too_long: "That question is a bit long", demo_unconfigured: "The demo is resting",
    missing_key: "Add an API key to get answers", bad_key: "That API key didn't work", rate_limited: "Rate limit reached",
    no_docs: "No documents yet", bad_model: "That model isn't available", unreachable: "Can't reach the model provider", network: "Connection lost" };
  function renderError(t) {
    var e = t.error, box = slot(t, "err");
    if (!e) { box.innerHTML = ""; return; }
    var acts = [];
    if (e.code === "missing_key" || e.code === "bad_key") acts.push(["key", "Open settings"]);
    if (e.code === "rate_limited" && !state.fast) acts.push(["fast", "Turn on Fast mode"]);
    if (e.code === "no_docs") acts.push(["add", "Add files"]);
    if (e.code !== "no_docs" && t.live) acts.push(["retry", "Try again"]);
    var friendly = e.code === "demo_busy" || e.code === "too_long" || e.code === "demo_unconfigured";
    if (friendly) acts = acts.filter(function (a) { return a[0] !== "retry"; });
    box.innerHTML = '<div class="callout ' + (friendly ? "busy" : "err") + '" role="' + (friendly ? "status" : "alert") + '"><div class="hd">' + ICON.warn + esc(ERR_TITLE[e.code] || "Couldn't answer") + "</div><p>" + esc(e.message || "Something went wrong.") +
      "</p>" + (acts.length ? '<div class="acts">' + acts.map(function (a) { return '<button type="button" class="btn" data-act="' + a[0] + '">' + a[1] + "</button>"; }).join("") + "</div>" : "") + "</div>";
  }

  function renderMeta(t) {
    var pills = "", c = t.check, tb = t.table;
    if (tb && tb.rows) {
      var vals = 0, bad = 0;
      tb.rows.forEach(function (r) { r.cells.forEach(function (x) { if (isValue(x.value) && x.verbatim != null) { vals++; if (x.verbatim === false) bad++; } }); });
      if (vals) pills += bad ? '<span class="pill warn">' + ICON.bang + plural(bad, "value") + " not found in text</span>" : '<span class="pill ok">' + ICON.check + vals + " of " + vals + " values matched to text</span>";
    }
    if (c && c.ran && c.total > 0) {
      pills += c.supported === c.total ? '<span class="pill ok">' + ICON.check + c.supported + " of " + c.total + " claims supported</span>"
        : '<span class="pill warn">' + ICON.bang + (c.total - c.supported) + " of " + c.total + " claims unsupported</span>";
    }
    if (t.coverage && t.coverage.not_covered && t.coverage.not_covered.length) {
      pills += '<span class="pill warn">' + ICON.bang + (t.coverage.state === "none" ? "Not in the documents" : "Partly answered") + "</span>";
    }
    if (t.confidence) {
      var tip = t.mode === "whole" && t.scope.length ? "Read across the whole document" : "Top passage match " + Number(t.confidence.score).toFixed(2);
      pills += '<span class="pill plain" title="' + esc(tip) + '">' + esc(t.confidence.level) + " confidence</span>";
    }
    var muted = [t.modelLabel || modelLabel(t.model) || t.model || "Model"];
    if (t.ms) muted.push((t.ms / 1000).toFixed(1) + "s");
    if (t.stopped) muted.push("stopped");
    var extra = tb && tb.rows ? '<button type="button" class="btn" data-act="csv">Copy as CSV</button>' : "";
    slot(t, "meta").innerHTML = '<div class="meta">' + pills + '<span class="muted">' + esc(muted.join(" · ")) + '</span><span class="acts">' + extra +
      '<button type="button" class="ib" data-act="copy" aria-label="Copy answer" title="Copy answer">' + ICON.copy + "</button></span></div>";
  }

  function spanFor(t, n) {
    var fromCheck = ((t.check && t.check.claims) || []).map(function (c) { return c.evidence; }).find(function (e) { return e && e.n === n; });
    if (fromCheck) return [fromCheck.start, fromCheck.end];
    var fromCite = (t.citations || []).find(function (c) { return c.n === n && c.start != null; });
    return fromCite ? [fromCite.start, fromCite.end] : null;
  }
  function highlighted(text, span) {
    if (span && span[0] != null && span[1] > span[0] && span[1] <= text.length) {
      return esc(text.slice(0, span[0])) + "<mark>" + esc(text.slice(span[0], span[1])) + "</mark>" + esc(text.slice(span[1]));
    }
    return esc(text);
  }
  function renderSources(t) {
    var ps = t.passages, box = slot(t, "sources");
    if (!ps.length) { box.innerHTML = ""; return; }
    box.innerHTML = '<details class="sources"><summary>' + ICON.chev + plural(ps.length, "source") + "</summary><div>" + ps.map(function (p) {
      return '<details class="srow"><summary><span class="num">' + p.n + '</span><span class="src" title="' + esc(p.label) + '">' + esc(short(p.doc)) + (p.location ? " · " + esc(p.location) : "") +
        '</span><span class="rel" title="Match ' + Number(p.score).toFixed(2) + '">' + esc(p.level || "") + '</span></summary><div class="pass">' + highlighted(p.text, spanFor(t, p.n)) + "</div></details>";
    }).join("") + "</div></details>";
  }

  function finish(t) {
    t.el.removeAttribute("aria-busy");
    thinking(t, "");
    renderMain(t); renderTable(t); renderConflicts(t); renderClaims(t); renderError(t);
    if (t.raw.trim() || t.table || t.stopped) renderMeta(t);
    renderSources(t);
  }

  /* a saved turn, re-rendered from its payload */
  function renderStored(question, m) {
    var t = makeTurn(question), p = m.payload || {};
    var src = p.sources || {};
    t.passages = src.passages || []; t.mode = src.mode || "search"; t.scope = src.scope || []; t.confidence = src.confidence || null;
    t.raw = (p.answer && p.answer.text) || m.content || ""; t.citations = (p.answer && p.answer.citations) || [];
    t.coverage = p.coverage || null; t.check = p.check || null; t.conflicts = p.conflicts || []; t.table = p.table || null;
    t.error = p.error || null; t.ms = p.ms || 0; t.model = p.model || ""; t.modelLabel = p.model_label || ""; t.stopped = !!p.stopped;
    if (t.stopped && !t.raw.trim() && !t.error) t.raw = "_Stopped._";
    finish(t);
  }

  /* SSE over fetch (POST with a body, cancellable) */
  function ask(text) {
    text = text.trim();
    if (!text || state.busy) return;
    if (!state.docs.length) { toast("Add a file first"); pick(); return; }
    showThread();
    addQuestion(text);
    var t = makeTurn(text); t.live = true;
    var n = state.docs.length, scoped = state.docs.find(function (d) { return d.id === state.scope; });
    thinking(t, "Searching " + (scoped ? short(scoped.name) : plural(n, "document")) + "…");
    stick = true; stickBottom(true);
    var controller = new AbortController(), started = performance.now();
    setBusy({ stop: function () { controller.abort(); } });

    fetch("/api/chat", {
      method: "POST", headers: headers(true), signal: controller.signal,
      body: JSON.stringify({ question: text, chat_id: state.chat, model: state.model, k: state.k, fast: state.fast, doc: state.scope })
    }).then(function (res) {
      if (!res.ok) return res.json().catch(function () { return null; }).then(function (d) { throw errorFrom(res.status, d); });
      var reader = res.body.getReader(), dec = new TextDecoder(), buf = "";
      function pump() {
        return reader.read().then(function (r) {
          if (r.done) return;
          buf += dec.decode(r.value, { stream: true });
          var i;
          while ((i = buf.indexOf("\n\n")) !== -1) {
            var chunk = buf.slice(0, i); buf = buf.slice(i + 2);
            chunk.split("\n").forEach(function (line) { if (line.indexOf("data: ") === 0) onEvent(t, JSON.parse(line.slice(6))); });
          }
          return pump();
        });
      }
      return pump();
    }).catch(function (err) {
      if (err && err.name === "AbortError") { t.stopped = true; if (!t.raw.trim()) t.raw = "_Stopped._"; }
      else t.error = err && err.message ? err : { code: "network", message: "The connection dropped before the answer finished." };
    }).then(function () {
      if (!t.ms) t.ms = Math.round(performance.now() - started);
      finish(t);
      setBusy(null);
      stickBottom();
      loadChats();
      setTimeout(loadChats, 3500); // pick up the model-written title
      if (!phone.matches) $("ta").focus();
    });
  }

  function onEvent(t, e) {
    switch (e.type) {
      case "chat":
        if (state.chat !== e.id) { state.chat = e.id; $("title").textContent = e.title; loadChats(); }
        break;
      case "step":
        if (e.state === "active") thinking(t, e.label);
        break;
      case "sources":
        t.passages = e.passages; t.mode = e.mode; t.scope = e.scope; t.confidence = e.confidence;
        break;
      case "delta":
        t.raw += e.text; thinking(t, ""); renderStreaming(t);
        break;
      case "answer": t.raw = e.text; t.citations = e.citations || []; break;
      case "coverage": t.coverage = e; break;
      case "check": t.check = e; thinking(t, ""); break;
      case "conflicts": t.conflicts = e.items || []; break;
      case "table": t.table = e; break;
      case "error": t.error = e; break;
      case "done": t.ms = e.ms; break;
    }
  }

  /* clicks inside answers: citations, page buttons, actions */
  $("thread").addEventListener("click", function (e) {
    var node = e.target.closest("[data-turn]"), t = node && turns.get(node.dataset.turn);
    if (!t) return;
    var cite = e.target.closest(".cite[data-n]");
    if (cite) {
      var n = +cite.dataset.n, c = t.citations[+cite.dataset.occ];
      openEv(t, n, c && c.n === n && c.start != null ? [c.start, c.end] : spanFor(t, n), cite);
      return;
    }
    var row = e.target.closest("[data-row]");
    if (row) { var r = t.table.rows[+row.dataset.row]; openEv(t, r.passage, r.span, row, "The highlighted text is where this value comes from."); return; }
    var pg = e.target.closest(".pg[data-n]");
    if (pg) { openEv(t, +pg.dataset.n, spanFor(t, +pg.dataset.n), pg); return; }
    var act = e.target.closest("[data-act]");
    if (!act) return;
    var a = act.dataset.act;
    if (a === "copy") copyAnswer(t);
    else if (a === "csv") copyCsv(t);
    else if (a === "key") { if (phone.matches) { app.classList.add("open"); syncSide(); } else if (app.classList.contains("rail")) expand(); setTimeout(function () { $("settingsbtn").click(); }, 220); }
    else if (a === "fast") { state.fast = true; store("fast", "1"); toast("Fast mode is on"); }
    else if (a === "add") pick();
    else if (a === "retry") { var q = t.el.previousElementSibling; if (q) q.remove(); t.el.remove(); ask(t.question); }
  });
  function copyAnswer(t) {
    var used = [];
    (t.raw.match(RE_CITE) || []).forEach(function (m) { citeNumbers(m.slice(1, -1)).forEach(function (n) { if (used.indexOf(n) < 0) used.push(n); }); });
    used.sort(function (a, b) { return a - b; });
    var refs = used.map(function (n) { var p = t.passages[n - 1]; return p ? "[" + n + "] " + p.label : null; }).filter(Boolean);
    copy(t.raw + (refs.length ? "\n\nSources:\n" + refs.join("\n") : ""), "Answer copied");
  }
  function copyCsv(t) {
    var tb = t.table, q = function (x) { x = String(x == null ? "" : x); return /[",\n]/.test(x) ? '"' + x.replace(/"/g, '""') + '"' : x; };
    var rows = [tb.columns.concat(["Document", "Location", "Quote", "Found in text"])].concat(tb.rows.map(function (r) {
      return tb.columns.map(function (_, j) { return (r.cells[j] || {}).value || ""; }).concat([r.doc, r.location || "", r.quote || "", r.cells.some(function (c) { return c.verbatim === false; }) ? "no" : "yes"]);
    }));
    copy(rows.map(function (r) { return r.map(q).join(","); }).join("\n"), "Table copied as CSV");
  }

  /* ------------------------------------------------------------ evidence panel */
  var ev = $("ev"), lastFocus = null, evText = "";
  function openEv(t, n, span, from, why) {
    var p = t.passages[n - 1]; if (!p) return;
    lastFocus = from;
    var loc = p.location || "";
    $("evdoc").textContent = p.doc; $("evdoc").title = p.doc;
    $("evpg").textContent = loc ? cap(loc) : "Source " + n;
    $("evpn").textContent = (loc || "source " + n).toUpperCase();
    $("evrel").textContent = (p.level || "") + " match"; $("evrel").title = "Similarity " + Number(p.score).toFixed(2);
    $("evtext").innerHTML = highlighted(p.text, span);
    $("evwhy").textContent = span ? (why || "The highlighted sentence supports the claim you selected.") : "This passage was cited for the claim you selected.";
    evText = p.text;
    ev.classList.add("on"); ev.setAttribute("aria-hidden", "false"); ev.inert = false;
    var mk = $("evtext").querySelector("mark");
    setTimeout(function () { $("evclose").focus(); if (mk) mk.scrollIntoView({ block: "center" }); }, 50);
  }
  function closeEv() {
    if (!ev.classList.contains("on")) return;
    ev.classList.remove("on"); ev.setAttribute("aria-hidden", "true"); ev.inert = true;
    if (lastFocus && document.contains(lastFocus)) lastFocus.focus();
  }
  $("evclose").onclick = closeEv;
  $("evcopy").onclick = function () { copy(evText, "Passage copied"); };
  document.addEventListener("click", function (e) {
    if (ev.classList.contains("on") && !e.target.closest("#ev") && !e.target.closest(".cite,.pg")) closeEv();
  });
  document.addEventListener("keydown", function (e) {
    if (e.key !== "Escape") return;
    var a = closePop(); if (a) { a.focus(); return; }
    if (ev.classList.contains("on")) { closeEv(); return; }
    closeDrawer();
  });

  /* ------------------------------------------------------------ composer */
  var ta = $("ta"), send = $("send");
  send.innerHTML = ICON.send;
  function grow() { ta.style.height = "auto"; ta.style.height = Math.min(ta.scrollHeight, 150) + "px"; if (!state.busy) send.disabled = !ta.value.trim(); }
  ta.addEventListener("input", grow);
  function setBusy(b) {
    state.busy = b;
    if (b) { send.disabled = false; send.innerHTML = ICON.stop; send.setAttribute("aria-label", "Stop"); }
    else { send.innerHTML = ICON.send; send.setAttribute("aria-label", "Send"); send.disabled = !ta.value.trim(); }
  }
  $("form").onsubmit = function (e) {
    e.preventDefault();
    if (state.busy) { state.busy.stop(); return; }
    var v = ta.value; ta.value = ""; grow(); ask(v);
  };
  ta.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey && !e.isComposing) { e.preventDefault(); if (!state.busy) { var v = ta.value; ta.value = ""; grow(); ask(v); } }
  });
  var stick = true;
  $("scroll").addEventListener("scroll", function () { var s = $("scroll"); stick = s.scrollHeight - s.scrollTop - s.clientHeight < 140; }, { passive: true });
  function stickBottom(force) { if (force || stick) $("scroll").scrollTop = $("scroll").scrollHeight; }

  /* ------------------------------------------------------------ empty state: suggestions */
  var suggestReq = 0;
  function renderChips(qs) {
    var box = $("chips"); box.classList.remove("loading");
    if (!state.docs.length) {
      box.innerHTML = '<button class="chip add" type="button" id="getstarted">' + ICON.plus + "Add a file to get started</button>";
      $("getstarted").onclick = pick;
      return;
    }
    box.innerHTML = qs.map(function (q) { return '<button class="chip ex" type="button">' + esc(q) + "</button>"; }).join("");
    Array.prototype.forEach.call(box.querySelectorAll(".ex"), function (b) { b.onclick = function () { ask(b.textContent); }; });
  }
  function loadSuggestions() {
    var id = ++suggestReq;
    if (!state.docs.length) { renderChips([]); return; }
    $("chips").classList.add("loading");
    api("/api/suggestions").then(function (d) { if (id === suggestReq) renderChips((d.questions || []).slice(0, 3)); })
      .catch(function () { if (id === suggestReq) renderChips([]); });
  }

  /* ------------------------------------------------------------ signature: explorer and dog (rough.js, as in the mockup) */
  var sigReady = false, sigRun = false;
  function buildSig() {
    if (sigReady || !window.rough) return false;
    var svg = $("sig"), NS = "http://www.w3.org/2000/svg", rc = rough.svg(svg), F = "currentColor";
    function E(n, a, p) { var e = document.createElementNS(NS, n); for (var k in a) e.setAttribute(k, a[k]); (p || svg).appendChild(e); return e; }
    function limb(parent, jx, jy, len, w) { var g = E("g", {}, parent); g.appendChild(rc.line(0, 0, len, 0, { stroke: F, strokeWidth: w, roughness: 1.1 })); return { set: function (d) { g.setAttribute("transform", "translate(" + jx + " " + jy + ") rotate(" + d + ")"); } }; }
    E("line", { x1: 24, y1: 263, x2: 186, y2: 263, stroke: F, "stroke-width": 1.4, opacity: .35, "stroke-linecap": "round" });
    var dogWrap = E("g", { transform: "translate(98 262) scale(0.55) translate(-400 -262)" }), dog = E("g", {}, dogWrap), dbody = E("g", {}, dog);
    dbody.appendChild(rc.ellipse(444, 224, 86, 36, { stroke: F, strokeWidth: 2.2, fill: F, fillStyle: "hachure", hachureGap: 7, fillWeight: .6, roughness: 1.5 }));
    dbody.appendChild(rc.line(474, 212, 492, 200, { stroke: F, strokeWidth: 2.2, roughness: 1.2 }));
    dbody.appendChild(rc.ellipse(506, 198, 32, 26, { stroke: F, strokeWidth: 2.1, fill: F, fillStyle: "hachure", hachureGap: 6, fillWeight: .6, roughness: 1.4 }));
    dbody.appendChild(rc.path("M 518 196 C 532 196, 536 202, 532 208 C 526 210, 518 208, 516 204 Z", { stroke: F, strokeWidth: 1.8, fill: F, fillStyle: "solid", roughness: 1.2 }));
    dbody.appendChild(rc.path("M 494 188 L 489 172 L 502 184 Z", { stroke: F, strokeWidth: 1.6, fill: F, fillStyle: "hachure", hachureGap: 3, roughness: 1.2 }));
    dbody.appendChild(rc.path("M 508 186 L 510 170 L 519 184 Z", { stroke: F, strokeWidth: 1.6, fill: F, fillStyle: "hachure", hachureGap: 3, roughness: 1.2 }));
    E("circle", { cx: 510, cy: 195, r: 1.8, fill: F }, dbody); E("circle", { cx: 531, cy: 202, r: 2.2, fill: F }, dbody);
    var tail = E("g", {}, dog); tail.appendChild(rc.path("M 0 0 C -10 -2, -18 -12, -16 -26", { stroke: F, strokeWidth: 2.4, roughness: 1.3 }));
    var dFN = limb(dog, 470, 238, 22, 3.2), dFF = limb(dog, 460, 238, 22, 2.4), dBN = limb(dog, 420, 238, 22, 3.2), dBF = limb(dog, 430, 238, 22, 2.4);
    var expWrap = E("g", { transform: "translate(-700 0)" }), exp = E("g", {}, expWrap), shX = 752, shY = 200, hipX = 752, hipY = 232;
    var body = E("g", {}, exp);
    body.appendChild(rc.rectangle(735, 206, 15, 22, { stroke: F, strokeWidth: 1.8, fill: F, fillStyle: "hachure", hachureGap: 5, fillWeight: .6, roughness: 1.3 }));
    body.appendChild(rc.line(shX, shY, hipX, hipY, { stroke: F, strokeWidth: 3.4, roughness: 1.1 }));
    body.appendChild(rc.circle(752, 184, 22, { stroke: F, strokeWidth: 2.2, fill: F, fillStyle: "solid", roughness: 1.1 }));
    body.appendChild(rc.path("M 738 178 C 748 170, 758 170, 768 178", { stroke: F, strokeWidth: 2, roughness: 1.1 }));
    body.appendChild(rc.line(733, 179, 773, 179, { stroke: F, strokeWidth: 2.2, roughness: 1 }));
    var armB = limb(exp, shX, shY + 4, 22, 2.8), armF = limb(exp, shX, shY + 4, 22, 2.8), legA = limb(exp, hipX, hipY, 30, 3.4), legB = limb(exp, hipX, hipY, 30, 3.4);
    var stickEl = E("line", { stroke: F, "stroke-width": 2, "stroke-linecap": "round" }, exp);
    function pose(t) {
      var dp = t * 3.4, dsw = 16, dbob = 1.6 * Math.sin(dp * 2);
      dFN.set(90 + dsw * Math.sin(dp)); dBF.set(90 + dsw * Math.sin(dp)); dFF.set(90 + dsw * Math.sin(dp + Math.PI)); dBN.set(90 + dsw * Math.sin(dp + Math.PI));
      dbody.setAttribute("transform", "translate(0 " + dbob.toFixed(2) + ")");
      tail.setAttribute("transform", "translate(403 " + (214 + dbob).toFixed(2) + ") rotate(" + (18 * Math.sin(t * 7)).toFixed(1) + ")");
      var ep = t * 3, ebob = 1.6 * Math.sin(ep * 2), fa = 116 + 10 * Math.sin(ep + Math.PI);
      legA.set(90 + 22 * Math.sin(ep)); legB.set(90 + 22 * Math.sin(ep + Math.PI)); armB.set(90 - 18 * Math.sin(ep)); armF.set(fa);
      body.setAttribute("transform", "translate(0 " + ebob.toFixed(2) + ")");
      var r = fa * Math.PI / 180, hx = shX + 22 * Math.cos(r), hy = shY + 4 + 22 * Math.sin(r) + ebob;
      stickEl.setAttribute("x1", hx.toFixed(1)); stickEl.setAttribute("y1", hy.toFixed(1)); stickEl.setAttribute("x2", (hx + 10).toFixed(1)); stickEl.setAttribute("y2", 263);
    }
    sigReady = pose; return true;
  }
  var reduce = matchMedia("(prefers-reduced-motion: reduce)");
  function startSig() {
    var ok = false;
    try { ok = buildSig() || !!sigReady; } catch (e) { ok = false; }
    if (!ok) { $("sig").hidden = true; return; }   // rough.js missing: hide the drawing
    if (reduce.matches) { sigReady(0.4); return; } // still frame
    if (sigRun) return; sigRun = true;
    (function loop(now) {
      if ($("empty").hidden || document.hidden || reduce.matches) { sigRun = false; if (reduce.matches) sigReady(0.4); return; }
      sigReady(now / 1000); requestAnimationFrame(loop);
    })(performance.now());
  }
  document.addEventListener("visibilitychange", function () { if (!document.hidden && !$("empty").hidden) startSig(); });
  reduce.addEventListener("change", startSig);

  /* ------------------------------------------------------------ demo mode (public demo on Hugging Face) */
  function applyDemo(d) {
    var first = !state.demo;
    state.demo = d;
    if (!first) return;
    $("ta").maxLength = d.max_question;
    // Fast mode is on by default in the demo (keeps the shared quota going), unless the visitor chose otherwise.
    if (load("fast") === null) state.fast = true;
  }

  /* ------------------------------------------------------------ boot */
  function refreshState() {
    return api("/api/state").then(function (s) {
      state.serverKey = !!s.server_key;
      if (s.demo) applyDemo(s.demo);
      if (s.user) { $("who").textContent = s.user.name; $("av").textContent = s.user.initial; }
      applyDocs(s);
      return s;
    }).catch(function (err) { toast(err.message, 3000); return null; });
  }
  function boot() {
    grow(); setScope(null); startSig();
    renderChips([]);
    refreshState().then(function (s) {
      loadModels();
      loadSuggestions();
      if (s && !s.ready) {
        var poll = setInterval(function () { refreshState().then(function (n) { if (!n || n.ready) clearInterval(poll); }); }, 1500);
      }
    });
    loadChats();
  }
  boot();
})();
