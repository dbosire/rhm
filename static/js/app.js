/* RHM Extractor – UI behaviour (no external dependencies) */
(function () {
  "use strict";
  const $ = (s, el = document) => el.querySelector(s);
  const $$ = (s, el = document) => Array.from(el.querySelectorAll(s));

  function getCookie(name) {
    const m = document.cookie.match(new RegExp("(^|;\\s*)" + name + "=([^;]*)"));
    return m ? decodeURIComponent(m[2]) : "";
  }

  /* ---------------- theme ---------------- */
  const root = document.documentElement;
  function applyThemeIcon() {
    const dark = root.getAttribute("data-theme") === "dark";
    $$("[data-theme-toggle]").forEach((b) => {
      b.querySelector(".i-sun")?.classList.toggle("hidden", !dark);
      b.querySelector(".i-moon")?.classList.toggle("hidden", dark);
      b.setAttribute("aria-label", dark ? "Switch to light theme" : "Switch to dark theme");
    });
  }
  $$("[data-theme-toggle]").forEach((btn) =>
    btn.addEventListener("click", () => {
      const next = root.getAttribute("data-theme") === "dark" ? "light" : "dark";
      root.setAttribute("data-theme", next);
      try { localStorage.setItem("rhm-theme", next); } catch (e) {}
      applyThemeIcon();
      document.dispatchEvent(new CustomEvent("themechange"));
    })
  );
  applyThemeIcon();

  /* ---------------- sidebar ---------------- */
  $$("[data-nav-toggle]").forEach((b) => b.addEventListener("click", () => document.body.classList.toggle("nav-open")));
  $(".scrim")?.addEventListener("click", () => document.body.classList.remove("nav-open"));

  /* ---------------- toasts ---------------- */
  $$(".toast").forEach((t, i) => {
    t.querySelector(".x")?.addEventListener("click", () => t.remove());
    if (!t.classList.contains("error")) setTimeout(() => t.remove(), 6000 + i * 800);
  });

  /* ---------------- loader ---------------- */
  const loader = $("#loader");
  function showLoader(text) {
    if (!loader) return;
    if (text) $("#loader-text").textContent = text;
    loader.classList.add("open");
  }
  window.RHM = { showLoader, hideLoader: () => loader?.classList.remove("open") };
  $$("form[data-loading]").forEach((f) =>
    f.addEventListener("submit", (e) => {
      if (e.defaultPrevented) return;
      showLoader(f.dataset.loading);
      $$("button[type=submit]", f).forEach((b) => (b.disabled = false));
    })
  );
  window.addEventListener("pageshow", () => loader?.classList.remove("open"));

  /* ---------------- confirm ---------------- */
  document.addEventListener("click", (e) => {
    const el = e.target.closest("[data-confirm]");
    if (el && !window.confirm(el.dataset.confirm)) {
      e.preventDefault();
      e.stopImmediatePropagation();
    }
  }, true);

  /* ---------------- selection ---------------- */
  $$("[data-select-scope]").forEach((scope) => {
    const all = $("[data-select-all]", scope);
    const boxes = () => $$("input[data-row-select]", scope);
    const counter = $$("[data-sel-count]", scope);
    const update = () => {
      const n = boxes().filter((b) => b.checked).length;
      counter.forEach((c) => (c.textContent = n));
      $$("[data-min-select]", scope).forEach((b) => (b.disabled = n < parseInt(b.dataset.minSelect, 10)));
      if (all) all.checked = n && n === boxes().length;
    };
    all?.addEventListener("change", () => { boxes().forEach((b) => (b.checked = all.checked)); update(); });
    scope.addEventListener("change", (e) => { if (e.target.matches("input[data-row-select]")) update(); });
    update();
  });

  /* ---------------- grid amount auto-calc ---------------- */
  $$("tr[data-txn]").forEach((tr) => {
    const q = $("[data-f=quantity]", tr), u = $("[data-f=unit_cost]", tr), a = $("[data-f=amount]", tr);
    if (!q || !u || !a) return;
    const calc = () => {
      const qv = parseFloat(q.value), uv = parseFloat(u.value), av = parseFloat(a.value);
      if (!isNaN(qv) && !isNaN(uv)) {
        const c = qv * uv;
        if (!a.value) a.value = c.toFixed(2);
        a.classList.toggle("flag", !isNaN(av) && Math.abs(av - c) > 0.01);
        a.title = !isNaN(av) && Math.abs(av - c) > 0.01 ? "Differs from Quantity × Unit Cost = " + c.toFixed(2) : "";
      } else { a.classList.remove("flag"); a.title = ""; }
    };
    [q, u, a].forEach((el) => el.addEventListener("input", calc));
  });
  // mark edited rows
  $$("tr[data-txn] input, tr[data-txn] select").forEach((el) =>
    el.addEventListener("change", () => el.closest("tr").classList.add("edited"))
  );

  /* ---------------- dropzone ---------------- */
  $$(".dropzone").forEach((dz) => {
    const input = $("input[type=file]", dz), label = $(".dz-file", dz);
    const show = () => { label.textContent = input.files.length ? input.files[0].name : ""; };
    input.addEventListener("change", show);
    ["dragenter", "dragover"].forEach((ev) => dz.addEventListener(ev, (e) => { e.preventDefault(); dz.classList.add("drag"); }));
    ["dragleave", "drop"].forEach((ev) => dz.addEventListener(ev, () => dz.classList.remove("drag")));
    dz.addEventListener("drop", (e) => { e.preventDefault(); if (e.dataTransfer.files.length) { input.files = e.dataTransfer.files; show(); } });
  });

  /* ---------------- live SMS counter ---------------- */
  const smsBox = $("#id_sms_text");
  if (smsBox) {
    const out = $("#sms-count");
    const count = () => {
      const t = smsBox.value.trim();
      if (!t) { out.textContent = "0 messages"; return; }
      const starts = t.match(/(^|\s)([A-Z0-9]{8,12}\s+Confirmed|Dear\s)/g) || [];
      const blocks = t.split(/\n\s*\n/).filter((x) => x.trim()).length;
      const n = Math.max(starts.length, blocks, 1);
      out.textContent = "≈ " + n + " message" + (n === 1 ? "" : "s");
    };
    smsBox.addEventListener("input", count);
    count();
  }

  /* ---------------- secure download modal ---------------- */
  const modal = $("#dl-modal");
  if (modal) {
    const form = $("form", modal), pw = $("#dl-password"), err = $(".modal-error", modal), btn = $("button[type=submit]", modal);
    let url = null;
    const open = (u, name) => {
      url = u; err.textContent = ""; pw.value = "";
      $("#dl-name").textContent = name || "report";
      modal.classList.add("open"); setTimeout(() => pw.focus(), 50);
    };
    const close = () => { modal.classList.remove("open"); url = null; pw.value = ""; };
    document.addEventListener("click", (e) => {
      const t = e.target.closest("[data-download]");
      if (t) { e.preventDefault(); open(t.dataset.download, t.dataset.name); }
    });
    $$("[data-close]", modal).forEach((b) => b.addEventListener("click", close));
    modal.addEventListener("click", (e) => { if (e.target === modal) close(); });
    document.addEventListener("keydown", (e) => { if (e.key === "Escape" && modal.classList.contains("open")) close(); });
    $("[data-toggle-pw]", modal)?.addEventListener("click", () => { pw.type = pw.type === "password" ? "text" : "password"; });
    form.addEventListener("submit", async (e) => {
      e.preventDefault();
      if (!url || !pw.value) { err.textContent = "Enter the download password."; return; }
      btn.disabled = true; const label = btn.innerHTML; btn.innerHTML = '<span class="spinner"></span> Verifying…';
      try {
        const body = new FormData(); body.append("password", pw.value);
        const resp = await fetch(url, { method: "POST", body, headers: { "X-CSRFToken": getCookie("csrftoken") }, credentials: "same-origin" });
        pw.value = "";
        if (!resp.ok) {
          let msg = "Download failed.";
          try { msg = (await resp.json()).error || msg; } catch (x) {}
          err.textContent = msg; $(".modal", modal).classList.remove("shake"); void modal.offsetWidth; $(".modal", modal).classList.add("shake");
          return;
        }
        const blob = await resp.blob();
        const cd = resp.headers.get("Content-Disposition") || "";
        const m = cd.match(/filename\*?=(?:UTF-8'')?"?([^";]+)"?/i);
        const a = document.createElement("a");
        a.href = URL.createObjectURL(blob);
        a.download = m ? decodeURIComponent(m[1]) : "report.xlsx";
        document.body.appendChild(a); a.click(); a.remove();
        setTimeout(() => URL.revokeObjectURL(a.href), 4000);
        close();
      } catch (x) {
        err.textContent = "Network error – please try again.";
      } finally { btn.disabled = false; btn.innerHTML = label; }
    });
  }

  /* ---------------- generate page: mode + template info ---------------- */
  const genForm = $("#generate-form");
  if (genForm) {
    const sync = () => {
      const mode = $("input[name=mode]:checked", genForm)?.value || "new";
      $$("[data-mode]", genForm).forEach((el) => el.classList.toggle("hidden", el.dataset.mode !== mode));
    };
    $$("input[name=mode]", genForm).forEach((r) => r.addEventListener("change", sync));
    sync();
    const tplSel = $("#id_template"), sheetSel = $("#id_template_sheet"), mapSel = $("#id_mapping"), hdr = $("#id_header_row"), info = $("#tpl-info");
    async function loadInfo(keepSheet) {
      if (!tplSel || !tplSel.value) { info && (info.innerHTML = ""); return; }
      const params = new URLSearchParams();
      if (keepSheet && sheetSel.value) params.set("sheet", sheetSel.value);
      if (hdr && hdr.value) params.set("header_row", hdr.value);
      const r = await fetch(tplSel.dataset.infoUrl.replace("0", tplSel.value) + "?" + params, { credentials: "same-origin" });
      if (!r.ok) return;
      const d = await r.json();
      sheetSel.innerHTML = d.sheets.map((s) => `<option${s === d.sheet ? " selected" : ""}></option>`).join("");
      $$("option", sheetSel).forEach((o, i) => { o.value = d.sheets[i]; o.textContent = d.sheets[i]; });
      mapSel.innerHTML = '<option value="">Auto-map by column headers</option>';
      d.mappings.forEach((m) => { const o = document.createElement("option"); o.value = m.id; o.textContent = m.name + " (" + m.sheet + ")"; mapSel.appendChild(o); });
      if (hdr && !hdr.value) hdr.placeholder = d.header_row;
      const mapped = Object.entries(d.auto_mapping);
      const esc = (s) => String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
      if (info) info.innerHTML = mapped.length
        ? '<div class="subtle">Auto-mapping (header row ' + d.header_row + "):</div>" + mapped.map(([k, c]) => `<span class="badge info">${esc(c)} ← ${esc(k.replace(/_/g, " "))}</span>`).join(" ")
        : '<div class="alert warn">No headers matched the standard parameters on this row. Choose a saved mapping or create one.</div>';
      const link = $("#mapping-link");
      if (link) link.href = link.dataset.base.replace("0", tplSel.value) + "?sheet=" + encodeURIComponent(d.sheet);
    }
    tplSel?.addEventListener("change", () => loadInfo(false));
    sheetSel?.addEventListener("change", () => loadInfo(true));
    hdr?.addEventListener("change", () => loadInfo(true));
    if (tplSel && tplSel.value) loadInfo(true);
  }

  /* ---------------- charts ---------------- */
  const chartData = $("#chart-data");
  if (chartData && window.Chart) {
    const data = JSON.parse(chartData.textContent);
    const palette = ["#6366f1", "#10b981", "#0ea5e9", "#f59e0b", "#ec4899", "#8b5cf6", "#14b8a6", "#f43f5e"];
    const charts = [];
    const css = (v) => getComputedStyle(root).getPropertyValue(v).trim();
    function draw() {
      charts.forEach((c) => c.destroy()); charts.length = 0;
      Chart.defaults.color = css("--text-2");
      Chart.defaults.font.family = css("--font");
      Chart.defaults.borderColor = css("--line");
      const mk = (id, cfg) => { const el = document.getElementById(id); if (el) charts.push(new Chart(el, cfg)); };
      const opts = { responsive: true, maintainAspectRatio: false, plugins: { legend: { display: false } } };
      mk("chart-daily", {
        type: "line",
        data: { labels: data.daily.labels, datasets: [{ data: data.daily.values, fill: true, tension: .35, borderColor: "#6366f1", pointRadius: 3,
          backgroundColor: (ctx) => { const g = ctx.chart.ctx.createLinearGradient(0, 0, 0, 260); g.addColorStop(0, "rgba(99,102,241,.35)"); g.addColorStop(1, "rgba(99,102,241,0)"); return g; } }] },
        options: { ...opts, scales: { y: { beginAtZero: true, ticks: { callback: (v) => "KES " + Number(v).toLocaleString() } }, x: { grid: { display: false } } } },
      });
      const donut = (id, d) => mk(id, { type: "doughnut", data: { labels: d.labels, datasets: [{ data: d.values, backgroundColor: palette, borderWidth: 0 }] },
        options: { responsive: true, maintainAspectRatio: false, cutout: "68%", plugins: { legend: { position: "bottom", labels: { boxWidth: 10, usePointStyle: true } } } } });
      donut("chart-bank", data.bank);
      donut("chart-workflow", data.workflow);
      mk("chart-channel", { type: "bar", data: { labels: data.channel.labels, datasets: [{ data: data.channel.values, backgroundColor: palette, borderRadius: 8 }] },
        options: { ...opts, indexAxis: "y", scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } } } });
    }
    draw();
    document.addEventListener("themechange", draw);
  }
})();
