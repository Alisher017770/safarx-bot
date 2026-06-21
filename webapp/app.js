(function () {
  "use strict";

  var tg = window.Telegram ? window.Telegram.WebApp : null;
  if (tg) {
    tg.ready();
    tg.expand();
    try {
      tg.setHeaderColor("#0a0d12");
      tg.setBackgroundColor("#0a0d12");
    } catch (e) {
      /* older client, ignore */
    }
  }

  var state = {
    tab: "all",
    from: "",
    to: "",
    cities: [],
    botUsername: "",
    sheetField: null, // "from" | "to"
  };

  var listEl = document.getElementById("list");
  var fromValueEl = document.getElementById("fromValue");
  var toValueEl = document.getElementById("toValue");
  var filterResetEl = document.getElementById("filterReset");
  var hintEl = document.getElementById("hint");
  var sheetBackdrop = document.getElementById("sheetBackdrop");
  var sheet = document.getElementById("citySheet");
  var sheetTitle = document.getElementById("sheetTitle");
  var sheetOptions = document.getElementById("sheetOptions");

  var CAR_COLORS = {
    "oq": "#cfd4da", "qora": "#22252b", "kulrang": "#8a93a0", "kumush": "#b9c0c8",
    "qizil": "#e2483a", "ko'k": "#2f6fed", "moviy": "#2f9fed", "yashil": "#1fb564",
    "sariq": "#f2b705", "jigarrang": "#8a5a33", "to'q sariq": "#ef7d1a",
  };

  function carColor(name) {
    if (!name) return "#3a4250";
    var key = String(name).trim().toLowerCase();
    return CAR_COLORS[key] || "#3a4250";
  }

  function escapeHtml(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (ch) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[ch];
    });
  }

  function dateLabel(dateStr) {
    if (!dateStr) return "";
    var parts = String(dateStr).split(" - ");
    return parts[0];
  }

  function authHeaders() {
    var headers = {};
    if (tg && tg.initData) headers["X-Telegram-Init-Data"] = tg.initData;
    return headers;
  }

  function fetchJson(url, opts) {
    return fetch(url, opts).then(function (res) {
      if (!res.ok) throw new Error("http_" + res.status);
      return res.json();
    });
  }

  function loadMeta() {
    return fetchJson("/api/meta")
      .then(function (data) {
        state.cities = data.cities || [];
        state.botUsername = data.bot_username || "";
      })
      .catch(function () {
        /* meta failing shouldn't block the list */
      });
  }

  function showSkeletons() {
    var html = "";
    for (var i = 0; i < 3; i++) html += '<div class="skeleton"></div>';
    listEl.innerHTML = html;
  }

  function showEmpty(title, sub) {
    listEl.innerHTML =
      '<div class="state-block">' +
      '<div class="state-emoji">🚗</div>' +
      '<div class="state-title">' + escapeHtml(title) + "</div>" +
      '<div class="state-sub">' + escapeHtml(sub) + "</div>" +
      "</div>";
  }

  function badgesFor(trip) {
    var html = '<div class="badges">';
    if (trip.status === "active" && trip.available_seats > 0) {
      html += '<span class="badge badge--live"><span class="badge-dot"></span>JONLI</span>';
    } else {
      html += '<span class="badge badge--full">TO\'LGAN</span>';
    }
    if (trip.is_urgent) {
      html += '<span class="badge badge--urgent">🔥 TEZKOR</span>';
    }
    html += "</div>";
    return html;
  }

  function cardHtml(trip) {
    var canBook = trip.status === "active" && trip.available_seats > 0;
    var commentHtml = trip.comment
      ? '<div class="comment">💬 ' + escapeHtml(trip.comment) + "</div>"
      : "";
    return (
      '<article class="card">' +
      '<div class="card-top">' +
      '<div class="card-thumb" style="background:' + carColor(trip.car_color) + '">🚗</div>' +
      '<div class="card-info">' +
      badgesFor(trip) +
      '<div class="route">' + escapeHtml(trip.from_city) +
      '<span class="route-arrow">→</span>' + escapeHtml(trip.to_city) + "</div>" +
      '<div class="meta-row">' +
      '<span class="meta-item">📅 <b>' + escapeHtml(dateLabel(trip.date)) + "</b></span>" +
      '<span class="meta-item">🕐 <b>' + escapeHtml(trip.time) + "</b></span>" +
      '<span class="meta-item">💺 <b>' + trip.available_seats + " ta</b></span>" +
      "</div>" +
      "</div>" +
      "</div>" +
      commentHtml +
      '<div class="card-footer">' +
      '<div class="price">' + Number(trip.price_per_person || 0).toLocaleString("ru-RU") + " <span>so'm</span></div>" +
      '<button class="book-btn" data-trip-id="' + trip.id + '"' + (canBook ? "" : " disabled") + ">" +
      (canBook ? "Joy band qilish" : "Joy yo'q") +
      "</button>" +
      "</div>" +
      "</article>"
    );
  }

  function render(trips) {
    if (!trips || !trips.length) {
      if (state.tab === "mine") {
        showEmpty("Hali e'loningiz yo'q", "Haydovchi sifatida yo'nalish qo'shsangiz, shu yerda chiqadi.");
      } else {
        showEmpty("Mos e'lon topilmadi", "Filtrni o'zgartirib ko'ring yoki birozdan keyin qayta urinib ko'ring.");
      }
      return;
    }
    listEl.innerHTML = trips.map(cardHtml).join("");
  }

  function bookTrip(tripId) {
    if (!state.botUsername) return;
    var url = "https://t.me/" + state.botUsername + "?start=trip_" + tripId;
    if (tg && tg.openTelegramLink) {
      tg.openTelegramLink(url);
    } else {
      window.open(url, "_blank");
    }
  }

  listEl.addEventListener("click", function (e) {
    var btn = e.target.closest(".book-btn");
    if (!btn || btn.disabled) return;
    bookTrip(btn.getAttribute("data-trip-id"));
  });

  function refresh() {
    showSkeletons();
    if (state.tab === "mine") {
      hintEl.textContent = "Bu yerda siz haydovchi sifatida qo'shgan yo'nalishlar va faol buyurtmalaringiz ko'rinadi.";
      fetchJson("/api/trips/mine", { headers: authHeaders() })
        .then(function (data) {
          render(data.trips);
        })
        .catch(function () {
          showEmpty(
            "Bog'lanib bo'lmadi",
            "Bu bo'lim faqat Telegram ilovasi ichida ishlaydi. Botga qaytib, \"Mening yo'nalishlarim\" bo'limidan foydalaning."
          );
        });
      return;
    }
    hintEl.textContent = "Joy band qilish uchun e'londagi tugmani bosing — bot suhbatida davom etadi.";
    var params = new URLSearchParams();
    if (state.from) params.set("from", state.from);
    if (state.to) params.set("to", state.to);
    fetchJson("/api/trips?" + params.toString())
      .then(function (data) {
        render(data.trips);
      })
      .catch(function () {
        showEmpty("Yuklab bo'lmadi", "Internetni tekshirib, qayta urinib ko'ring.");
      });
  }

  // ---- Tabs ----
  document.querySelectorAll(".tab").forEach(function (tabEl) {
    tabEl.addEventListener("click", function () {
      document.querySelectorAll(".tab").forEach(function (t) { t.classList.remove("active"); });
      tabEl.classList.add("active");
      state.tab = tabEl.getAttribute("data-tab");
      refresh();
    });
  });

  // ---- Filters / bottom sheet ----
  function openSheet(field) {
    state.sheetField = field;
    sheetTitle.textContent = field === "from" ? "Qayerdan" : "Qayerga";
    var current = field === "from" ? state.from : state.to;
    var options = ["Barchasi"].concat(state.cities);
    sheetOptions.innerHTML = options
      .map(function (city) {
        var value = city === "Barchasi" ? "" : city;
        var selected = value === current ? " selected" : "";
        return (
          '<button class="sheet-option' + selected + '" data-value="' + escapeHtml(value) + '">' +
          escapeHtml(city) +
          "</button>"
        );
      })
      .join("");
    sheetBackdrop.classList.add("show");
    sheet.classList.add("show");
  }

  function closeSheet() {
    sheetBackdrop.classList.remove("show");
    sheet.classList.remove("show");
    state.sheetField = null;
  }

  document.getElementById("filterFrom").addEventListener("click", function () { openSheet("from"); });
  document.getElementById("filterTo").addEventListener("click", function () { openSheet("to"); });
  sheetBackdrop.addEventListener("click", closeSheet);

  sheetOptions.addEventListener("click", function (e) {
    var opt = e.target.closest(".sheet-option");
    if (!opt) return;
    var value = opt.getAttribute("data-value");
    if (state.sheetField === "from") {
      state.from = value;
      fromValueEl.textContent = value || "Barchasi";
    } else if (state.sheetField === "to") {
      state.to = value;
      toValueEl.textContent = value || "Barchasi";
    }
    filterResetEl.hidden = !(state.from || state.to);
    closeSheet();
    refresh();
  });

  filterResetEl.addEventListener("click", function () {
    state.from = "";
    state.to = "";
    fromValueEl.textContent = "Barchasi";
    toValueEl.textContent = "Barchasi";
    filterResetEl.hidden = true;
    refresh();
  });

  // ---- Bottom nav ----
  document.querySelectorAll(".nav-item").forEach(function (item) {
    item.addEventListener("click", function () {
      var nav = item.getAttribute("data-nav");
      if (nav === "home" || nav === "listings") {
        document.querySelectorAll(".nav-item").forEach(function (n) { n.classList.remove("active"); });
        item.classList.add("active");
        window.scrollTo({ top: 0, behavior: "smooth" });
        return;
      }
      // post / messages / profile: hand off to the bot chat itself
      if (!state.botUsername) return;
      var url = "https://t.me/" + state.botUsername;
      if (tg && tg.openTelegramLink) {
        tg.openTelegramLink(url);
      } else {
        window.open(url, "_blank");
      }
    });
  });

  // ---- Pull-style manual refresh ----
  document.getElementById("refreshBtn").addEventListener("click", function (btn) {
    var el = btn.currentTarget || btn.target;
    el.classList.add("spinning");
    refresh();
    setTimeout(function () { el.classList.remove("spinning"); }, 700);
  });

  loadMeta().then(refresh);
})();
