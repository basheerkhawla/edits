
function animatePrice(el, targetPrice) {
  if (!el) return;
  var startPrice = parseFloat((el.textContent || '0').replace(/[^0-9.]/g, '')) || 0;
  var duration = 400; // ms
  var startTime = performance.now();
  
  function update(time) {
    var elapsed = time - startTime;
    var progress = Math.min(elapsed / duration, 1);
    var easeOutQuart = 1 - Math.pow(1 - progress, 4);
    var current = startPrice + (targetPrice - startPrice) * easeOutQuart;
    el.textContent = current.toFixed(2) + ' $';
    
    if (progress < 1) {
      requestAnimationFrame(update);
    }
  }
  requestAnimationFrame(update);
}
/* StarsHub v4 — app.js (fixed: language switching, defensive coding) */
"use strict";

/* ─── State ─────────────────────────────────────────────────────────────── */
var currentLang = "en";
var selectedStars = null;
var currentPaymentMethod = "cryptopay";
var tonOrderId = null;
var tonComment = null;
var tonExpiresAt = null;
var tonPollingTimer = null;
var tonCountdownTimer = null;
var resolveDebounce = null;
var roFilter = "last_24h";
var roSort = "newest";
var roTimer = null;
var PRICE_PER_STAR = 0.018;
var PACKAGES = [
  { stars: 50, delay: "0.05s" },
  { stars: 100, delay: "0.10s" },
  { stars: 200, delay: "0.15s" },
  { stars: 300, delay: "0.20s", best: true },
  { stars: 500, delay: "0.25s" },
];

/* ─── Translation helpers ────────────────────────────────────────────────── */
function t(key) {
  var T = window.TRANSLATIONS;
  if (!T) return key;
  var lang = T[currentLang] || T["en"] || {};
  var val = lang[key];
  if (val !== undefined && val !== null) return val;
  val = (T["en"] || {})[key];
  return val !== undefined && val !== null ? val : key;
}

function detectLang() {
  var T = window.TRANSLATIONS;
  if (!T) return "en";
  /* 1. localStorage */
  try {
    var s = localStorage.getItem("lang");
    if (s && T[s]) return s;
  } catch (e) {}
  /* 2. Telegram */
  try {
    var tgLang =
      window.Telegram &&
      window.Telegram.WebApp &&
      window.Telegram.WebApp.initDataUnsafe &&
      window.Telegram.WebApp.initDataUnsafe.user &&
      window.Telegram.WebApp.initDataUnsafe.user.language_code;
    if (tgLang) {
      var c = tgLang.split("-")[0];
      if (T[c]) return c;
    }
  } catch (e) {}
  /* 3. Browser */
  try {
    var bl = (navigator.language || "").split("-")[0];
    if (bl && T[bl]) return bl;
  } catch (e) {}
  return window.DEFAULT_LANG || "en";
}

function applyLang() {
  var T = window.TRANSLATIONS;
  if (!T) return;
  var lang = currentLang;
  var entry = T[lang] || T["en"];
  if (!entry) return;
  try {
    document.documentElement.lang = lang;
    document.documentElement.dir = entry.dir === "rtl" ? "rtl" : "ltr";
  } catch (e) {}
  var sel = document.getElementById("langSelect");
  if (sel) sel.value = lang;
  document.querySelectorAll("[data-t]").forEach(function (el) {
    var val = t(el.getAttribute("data-t"));
    if (el.tagName === "INPUT" || el.tagName === "TEXTAREA") {
      el.placeholder = val;
    } else {
      el.innerHTML = val;
    }
  });
  document.querySelectorAll("[data-tp]").forEach(function (el) {
    el.placeholder = t(el.getAttribute("data-tp"));
  });
  try {
    if (
      window.Telegram &&
      window.Telegram.WebApp &&
      window.Telegram.WebApp.MainButton
    ) {
      window.Telegram.WebApp.MainButton.setText(t("pay_now"));
    }
  } catch (e) {}
  renderPackages();
}

function setLang(lang) {
  var T = window.TRANSLATIONS;
  if (T && !T[lang]) return;
  currentLang = lang;
  try {
    localStorage.setItem("lang", lang);
  } catch (e) {}
  applyLang();
}
window.setLang = setLang;

/* ─── Packages ───────────────────────────────────────────────────────────── */
function renderPackages() {
  var sel = document.getElementById("packageSelect");
  if (!sel) return;
  sel.innerHTML =
    '<option value="" disabled selected>' + t("select_package") + "</option>";
  PACKAGES.forEach(function (pkg) {
    var price = (pkg.stars * PRICE_PER_STAR).toFixed(2);
    var opt = document.createElement("option");
    opt.value = pkg.stars;
    opt.textContent =
      pkg.stars.toLocaleString() +
      " ⭐  —  " +
      price +
      " USD" +
      (pkg.best ? "  🔥 " + t("best_seller") : "");
    sel.appendChild(opt);
  });
}

function selectPackage(stars) {
  var ci = document.getElementById("customStars");
  var lp = document.getElementById("livePrice");
  if (ci) ci.value = "";
  if (lp) lp.textContent = "0.00 $";
  setStars(stars);
}

document.addEventListener("DOMContentLoaded", function() {
  var tgInp = document.getElementById("tgUsername");
  if(tgInp) {
    tgInp.addEventListener("blur", function() {
      if(this.value && !this.value.startsWith("@")) {
        this.value = "@" + this.value;
        triggerResolve();
      }
    });
  }
});

function setStars(stars) {
  selectedStars = stars;
  var price = (stars * PRICE_PER_STAR).toFixed(2);
  var ss = document.getElementById("sumStars");
  var sp = document.getElementById("sumPrice");
  if (ss) ss.textContent = stars.toLocaleString() + " ⭐";
  if (sp) animatePrice(sp, parseFloat(price));
  if (currentPaymentMethod === "ton") {
    var td = document.getElementById("tonAmountDisplay");
    if (td) td.textContent = "...";
  }
}

/* ─── Account Preview ────────────────────────────────────────────────────── */
function triggerResolve() {
  clearTimeout(resolveDebounce);
  resolveDebounce = setTimeout(doResolveUsername, 700);
}

function doResolveUsername() {
  var inp = document.getElementById("tgUsername");
  var prev = document.getElementById("accountPreview");
  var err = document.getElementById("accountError");
  if (!inp || !prev || !err) return;
  var raw = inp.value.trim();
  prev.style.display = "none";
  err.style.display = "none";
  if (!raw.startsWith("@") || raw.length < 2) return;
  fetch("/api/resolve-username", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ username: raw }),
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (d) {
      if (d.found === false) {
        err.textContent = t("account_not_found");
        err.style.display = "block";
        return;
      }
      var av = document.getElementById("previewAvatar");
      var nm = document.getElementById("previewName");
      var un = document.getElementById("previewUsername");
      if (av) av.src = d.avatar_url || window.DEFAULT_AVATAR_SVG;
      if (nm) nm.textContent = d.full_name || raw;
      if (un) un.textContent = "@" + (d.username || raw.replace("@", ""));
      prev.style.display = "flex";
    })
    .catch(function () {
      err.textContent = t("resolve_error");
      err.style.display = "block";
    });
}
window.resolveUsername = doResolveUsername;
window.triggerResolve = triggerResolve;

/* ─── Payment Method ─────────────────────────────────────────────────────── */
function selectPaymentMethod(method) {
  currentPaymentMethod = method;
  var tu = document.getElementById("tonUI");
  var bc = document.getElementById("btnCryptomus");
  var bt = document.getElementById("btnTon");
  var bcp = document.getElementById("btnCryptoPay");
  
  if (bc) bc.classList.toggle("active", method === "cryptomus");
  if (bt) bt.classList.toggle("active", method === "ton");
  if (bcp) bcp.classList.toggle("active", method === "cryptopay");

  if (method === "ton") {
    if (tu) tu.style.display = "block";
  } else {
    if (tu) tu.style.display = "none";
    resetTonState();
  }
}
window.selectPaymentMethod = selectPaymentMethod;

/* ─── TON ────────────────────────────────────────────────────────────────── */
function createTonOrder() {
  var userId = (document.getElementById("tgUsername") || {}).value || "";
  userId = userId.trim();
  var emailRaw =
    ((document.getElementById("guestEmail") || {}).value || "").trim() || null;
  if (!selectedStars) return showToast("⚠️ " + t("err_select"), "error");
  if (!userId.startsWith("@") || userId.length < 2)
    return showToast("⚠️ " + t("err_username"), "error");
  var token = safeGet("sh_token");
  var refCode = safeGet("stars_ref");
  var headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = "Bearer " + token;
  var btn = document.getElementById("payBtn");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
  }
  fetch("/api/create-ton-order", {
    method: "POST",
    headers: headers,
    body: JSON.stringify({
      user_id: userId,
      stars_amount: selectedStars,
      ref_code: refCode || null,
      email: emailRaw,
    }),
  })
    .then(function (r) {
      return r.json().then(function (d) {
        return { ok: r.ok, d: d };
      });
    })
    .then(function (res) {
      if (!res.ok) throw new Error(res.d.detail || "Error");
      var d = res.d;
      tonOrderId = d.order_id;
      tonComment = d.comment;
      tonExpiresAt = new Date(
        (d.expires_at || "").replace(" ", "T") +
          (d.expires_at && d.expires_at.endsWith("Z") ? "" : "Z"),
      );
      var wa = document.getElementById("tonWalletAddress");
      var ta = document.getElementById("tonAmountDisplay");
      var tc = document.getElementById("tonCommentDisplay");
      if (wa) wa.textContent = d.wallet_address || "";
      if (ta) ta.textContent = (d.ton_amount || 0).toFixed(6) + " TON";
      if (tc) tc.textContent = d.comment || "";
      var qrImg = document.getElementById("tonWalletQrImg");
      if (qrImg)
        qrImg.src =
          "/api/ton-wallet-qr?order_id=" + d.order_id + "&t=" + Date.now();
      var det = document.getElementById("tonDetailsBox");
      if (det) { det.style.display = "block"; det.classList.add("show"); }
      if (btn) btn.style.display = "none";
      setTonStatus("waiting", t("ton_waiting"));
      startTonCountdown();
      startTonPolling();
    })
    .catch(function (e) {
      showToast("❌ " + e.message, "error");
      if (btn) btn.disabled = false;
    });
}
window.createTonOrder = createTonOrder;

function startTonCountdown() {
  clearInterval(tonCountdownTimer);
  var el = document.getElementById("tonTimer");
  if (!el || !tonExpiresAt) return;
  function tick() {
    var rem = Math.max(0, tonExpiresAt - Date.now());
    el.textContent =
      String(Math.floor(rem / 60000)).padStart(2, "0") +
      ":" +
      String(Math.floor((rem % 60000) / 1000)).padStart(2, "0");
    if (rem === 0) {
      clearInterval(tonCountdownTimer);
      stopTonPolling();
      setTonStatus("expired", t("ton_expired"));
    }
  }
  tick();
  tonCountdownTimer = setInterval(tick, 1000);
}

function startTonPolling() {
  clearInterval(tonPollingTimer);
  tonPollingTimer = setInterval(checkTonPayment, 10000);
}

function stopTonPolling() {
  clearInterval(tonPollingTimer);
  clearInterval(tonCountdownTimer);
}

function checkTonPayment() {
  if (!tonOrderId) return;
  fetch("/api/check-ton-payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order_id: tonOrderId }),
  })
    .then(function (r) {
      return r.json();
    })
    .then(function (d) {
      handleTonResult(d);
    })
    .catch(function () {});
}
window.checkTonPayment = checkTonPayment;

function handleTonResult(d) {
  if (d.expired || d.status === "expired") {
    stopTonPolling();
    setTonStatus("expired", t("ton_expired"));
    return;
  }
  if (d.testnet || d.status === "testnet_fraud") {
    stopTonPolling();
    setTonStatus("error", t("ton_testnet_reject"));
    return;
  }
  if (d.success || d.status === "completed") {
    stopTonPolling();
    setTonStatus("success", t("ton_confirmed"));
    showToast("✅ " + t("ton_confirmed"), "success");
    return;
  }
  if (d.status === "underpaid") {
    setTonStatus(
      "warning",
      t("ton_underpaid") +
        (d.missing ? " (" + Number(d.missing).toFixed(6) + " TON)" : ""),
    );
    return;
  }
  if (d.status === "not_found") {
    setTonStatus("waiting", t("ton_not_found"));
    return;
  }
  if (d.status === "failed") {
    stopTonPolling();
    setTonStatus("error", d.message || "Error");
    return;
  }
}

function setTonStatus(type, msg) {
  var el = document.getElementById("tonStatusMsg");
  if (!el) return;
  el.className = "ton-status ton-status--" + type + " show";
  el.textContent = msg;
}

function resetTonState() {
  stopTonPolling();
  tonOrderId = null;
  tonComment = null;
  tonExpiresAt = null;
  var det = document.getElementById("tonDetailsBox");
  var btn = document.getElementById("payBtn");
  var st = document.getElementById("tonStatusMsg");
  var qrBox = document.getElementById("tonWalletQrBox");
  var qrImg = document.getElementById("tonWalletQrImg");
  if (det) det.style.display = "none";
  if (btn) {
    btn.style.display = "";
    btn.disabled = false;
    btn.classList.remove("loading");
  }
  if (st) {
    st.className = "ton-status";
    st.textContent = "";
  }
  if (qrBox) qrBox.style.display = "none";
  if (qrImg) qrImg.src = "";
}

function toggleTonWalletQr() {
  var box = document.getElementById("tonWalletQrBox");
  var img = document.getElementById("tonWalletQrImg");
  if (!box || !img) return;
  var showing = box.style.display !== "none";
  if (showing) {
    box.style.display = "none";
    return;
  }
  if (!img.src || img.src.indexOf("/api/ton-wallet-qr") === -1) {
    // لا يوجد طلب TON نشط بعد → عرض QR لعنوان المحفظة فقط
    img.src = "/api/ton-wallet-qr?t=" + Date.now();
  }
  box.style.display = "block";
}
window.toggleTonWalletQr = toggleTonWalletQr;

function clipboardCopy(text, successMsg) {
  if (navigator.clipboard && window.isSecureContext) {
    navigator.clipboard
      .writeText(text)
      .then(function () {
        showToast(successMsg, "success");
      })
      .catch(function () {
        fallbackCopy(text, successMsg);
      });
  } else {
    fallbackCopy(text, successMsg);
  }
}
function fallbackCopy(text, successMsg) {
  var ta = document.createElement("textarea");
  ta.value = text;
  ta.style.cssText = "position:fixed;left:-9999px;top:-9999px;opacity:0";
  document.body.appendChild(ta);
  ta.select();
  try {
    document.execCommand("copy");
    showToast(successMsg, "success");
  } catch (e) {
    showToast("❌ Copy failed", "error");
  }
  document.body.removeChild(ta);
}
function copyTonWallet() {
  var el = document.getElementById("tonWalletAddress");
  if (!el) return;
  clipboardCopy(el.textContent.trim(), t("ton_copy_wallet"));
}
function copyTonComment() {
  var el = document.getElementById("tonCommentDisplay");
  if (!el) return;
  clipboardCopy(el.textContent.trim(), t("ton_copy_comment"));
}
window.copyTonWallet = copyTonWallet;
window.copyTonComment = copyTonComment;

/* ─── Pay ────────────────────────────────────────────────────────────────── */
var cryptoPayOrderId = null;
var cryptoPayPollingTimer = null;

function handlePay() {
  if (currentPaymentMethod === "ton") {
    createTonOrder();
    return;
  }
  var userId = ((document.getElementById("tgUsername") || {}).value || "").trim();
  var emailRaw = ((document.getElementById("guestEmail") || {}).value || "").trim() || null;
  if (!selectedStars) return showToast("⚠️ " + t("err_select"), "error");
  if (!userId.startsWith("@") || userId.length < 2)
    return showToast("⚠️ " + t("err_username"), "error");
  if (emailRaw && (!emailRaw.includes("@") || !emailRaw.includes("."))) {
    var ef = document.getElementById("guestEmail");
    if (ef) ef.classList.add("invalid");
    return showToast("⚠️ " + t("email_invalid"), "error");
  }
  var btn = document.getElementById("payBtn");
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
  }
  var token = safeGet("sh_token");
  var refCode = safeGet("stars_ref");
  var headers = { "Content-Type": "application/json" };
  if (token) headers["Authorization"] = "Bearer " + token;
  
  var endpoint = currentPaymentMethod === "cryptopay" ? "/api/create-cryptopay-invoice" : "/api/create-invoice";
  
  fetch(endpoint, {
    method: "POST",
    headers: headers,
    body: JSON.stringify({
      user_id: userId,
      stars_amount: selectedStars,
      ref_code: refCode || null,
      email: emailRaw,
      locale: currentLang
    }),
  })
    .then(function (r) {
      return r.json().then(function (d) {
        return { ok: r.ok, d: d };
      });
    })
    .then(function (res) {
      if (!res.ok) throw new Error(res.d.detail || "Server error");
      
      if (currentPaymentMethod === "cryptopay") {
          window.open(res.d.pay_url, "_blank");
          showToast(t("cryptopay_invoice_ok") || t("invoice_ok"), "success");
          cryptoPayOrderId = res.d.order_id;
          safeSet("cryptopay_pending_order", String(cryptoPayOrderId));
          startCryptoPayPolling();
      } else {
          window.open(res.d.invoice_url, "_blank");
          showToast(t("invoice_ok"), "success");
      }
    })
    .catch(function (e) {
      showToast("❌ " + e.message, "error");
    })
    .finally(function () {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("loading");
      }
    });
}
window.handlePay = handlePay;

function startCryptoPayPolling() {
  clearInterval(cryptoPayPollingTimer);
  showToast(t("cryptopay_checking") || "Checking...", "info");
  // تحقق فوري ثم كل 5 ثوانٍ
  checkCryptoPayPayment();
  cryptoPayPollingTimer = setInterval(checkCryptoPayPayment, 5000);
}

function stopCryptoPayPolling() {
  clearInterval(cryptoPayPollingTimer);
  cryptoPayOrderId = null;
  safeRemove("cryptopay_pending_order");
  // تنظيف URL من cryptopay_order إن وُجد
  try {
    var url = new URL(window.location.href);
    if (url.searchParams.has("cryptopay_order")) {
      url.searchParams.delete("cryptopay_order");
      window.history.replaceState({}, "", url.pathname + url.search + url.hash);
    }
  } catch(e) {}
}

function checkCryptoPayPayment() {
  if (!cryptoPayOrderId) return;
  fetch("/api/check-cryptopay-payment", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ order_id: cryptoPayOrderId }),
  })
    .then(function (r) { return r.json(); })
    .then(function (d) {
      if (d.success || d.status === "completed") {
        stopCryptoPayPolling();
        showToast(t("cryptopay_paid") || "✅ Payment received!", "success");
      } else if (d.expired || d.status === "expired") {
        stopCryptoPayPolling();
        showToast(t("cryptopay_expired") || "⏰ Invoice expired.", "error");
      } else if (d.status === "failed" || d.status === "error") {
        stopCryptoPayPolling();
      }
    })
    .catch(function () {});
}
window.checkCryptoPayPayment = checkCryptoPayPayment;

/* ─── CryptoPay Resume ───────────────────────────────────────────────────── */
function resumeCryptoPayPolling() {
  // 1) تحقق من URL params (العميل عاد من CryptoBot بعد الدفع)
  var pendingId = null;
  try {
    var params = new URLSearchParams(window.location.search);
    var fromUrl = params.get("cryptopay_order");
    if (fromUrl && !isNaN(parseInt(fromUrl, 10))) {
      pendingId = parseInt(fromUrl, 10);
    }
  } catch(e) {}

  // 2) أو من localStorage (الصفحة أُعيد تحميلها أثناء الانتظار)
  if (!pendingId) {
    var stored = safeGet("cryptopay_pending_order");
    if (stored && !isNaN(parseInt(stored, 10))) {
      pendingId = parseInt(stored, 10);
    }
  }

  if (!pendingId) return;

  // استئناف polling
  cryptoPayOrderId = pendingId;
  safeSet("cryptopay_pending_order", String(pendingId));
  startCryptoPayPolling();
}
window.resumeCryptoPayPolling = resumeCryptoPayPolling;

/* ─── Auth ───────────────────────────────────────────────────────────────── */
function openModal(id) {
  var el = document.getElementById(id);
  if (el) el.classList.add("open");
}
function closeModal(id) {
  var el = document.getElementById(id);
  if (el) el.classList.remove("open");
}
function switchModal(f, t2) {
  closeModal(f);
  openModal(t2);
}
window.openModal = openModal;
window.closeModal = closeModal;
window.switchModal = switchModal;

function handleLogin() {
  var email = (
    (document.getElementById("loginEmail") || {}).value || ""
  ).trim();
  var pass = (document.getElementById("loginPassword") || {}).value || "";
  var errEl = document.getElementById("loginError");
  var btn = document.getElementById("loginBtn");
  if (errEl) errEl.style.display = "none";
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
  }
  fetch("/api/login", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ email: email, password: pass }),
  })
    .then(function (r) {
      return r.json().then(function (d) {
        return { ok: r.ok, d: d };
      });
    })
    .then(function (res) {
      if (!res.ok) throw new Error(res.d.detail || "Error");
      safeSet("sh_token", res.d.token);
      closeModal("loginModal");
      showToast(t("login_ok"), "success");
      loadUserState();
    })
    .catch(function (e) {
      if (errEl) {
        errEl.textContent = e.message;
        errEl.style.display = "block";
      }
    })
    .finally(function () {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("loading");
      }
    });
}
window.handleLogin = handleLogin;

function handleRegister() {
  var email = ((document.getElementById("regEmail") || {}).value || "").trim();
  var tgId =
    ((document.getElementById("regTelegramId") || {}).value || "").trim() ||
    null;
  var pass = (document.getElementById("regPassword") || {}).value || "";
  var refCode = safeGet("stars_ref");
  var errEl = document.getElementById("regError");
  var btn = document.getElementById("registerBtn");
  if (errEl) errEl.style.display = "none";
  if (btn) {
    btn.disabled = true;
    btn.classList.add("loading");
  }
  fetch("/api/register", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      email: email,
      password: pass,
      telegram_id: tgId,
      ref_code: refCode || null,
    }),
  })
    .then(function (r) {
      return r.json().then(function (d) {
        return { ok: r.ok, d: d };
      });
    })
    .then(function (res) {
      if (!res.ok) throw new Error(res.d.detail || "Error");
      safeSet("sh_token", res.d.token);
      closeModal("registerModal");
      showToast(t("reg_ok"), "success");
      loadUserState();
    })
    .catch(function (e) {
      if (errEl) {
        errEl.textContent = e.message;
        errEl.style.display = "block";
      }
    })
    .finally(function () {
      if (btn) {
        btn.disabled = false;
        btn.classList.remove("loading");
      }
    });
}
window.handleRegister = handleRegister;

function logout() {
  safeRemove("sh_token");
  var ug = document.getElementById("userGreeting");
  var gn = document.getElementById("guestNav");
  var mb = document.getElementById("memberBadge");
  var ef = document.getElementById("emailFieldWrap");
  var cb = document.getElementById("checkoutBox");
  if (ug) ug.style.display = "none";
  if (gn) gn.style.display = "flex";
  if (mb) mb.style.display = "none";
  if (ef) ef.style.display = "block";
  if (cb) cb.classList.remove("logged-in");
}
window.logout = logout;

function loadUserState() {
  var token = safeGet("sh_token");
  if (!token) return;
  fetch("/api/me", { headers: { Authorization: "Bearer " + token } })
    .then(function (r) {
      if (!r.ok) {
        safeRemove("sh_token");
        return null;
      }
      return r.json();
    })
    .then(function (me) {
      if (!me) return;
      var eg = document.getElementById("greetEmail");
      var ug = document.getElementById("userGreeting");
      var gn = document.getElementById("guestNav");
      var mb = document.getElementById("memberBadge");
      var ef = document.getElementById("emailFieldWrap");
      var cb = document.getElementById("checkoutBox");
      if (eg) eg.textContent = me.email || "";
      if (ug) ug.style.display = "flex";
      if (gn) gn.style.display = "none";
      if (mb) mb.style.display = "block";
      if (ef) ef.style.display = "none";
      if (cb) cb.classList.add("logged-in");
    })
    .catch(function () {});
}

/* ─── Recent Orders ──────────────────────────────────────────────────────── */
function timeAgo(iso) {
  if (!iso) return "";
  var diff = Math.floor(
    (Date.now() - new Date(iso.replace(" ", "T") + "Z").getTime()) / 60000,
  );
  if (diff < 60) return diff + " " + t("ago_minutes");
  if (diff < 1440) return Math.floor(diff / 60) + " " + t("ago_hours");
  return Math.floor(diff / 1440) + " " + t("ago_days");
}

function fetchRecentOrders() {
  var cont = document.getElementById("recentOrdersList");
  if (!cont) return;
  fetch(
    "/api/recent-orders?filter=" + roFilter + "&sort=" + roSort + "&limit=20",
  )
    .then(function (r) {
      return r.json();
    })
    .then(function (rows) {
      if (!rows.length) {
        cont.innerHTML = '<div class="ro-empty">—</div>';
        return;
      }
      cont.innerHTML = rows
        .map(function (o) {
          var pm = o.payment_method === "ton_transfer" ? "💎" : "💳";
          return (
            '<div class="ro-card">' +
            '<div class="ro-user">' +
            (o.user_id || "—") +
            "</div>" +
            '<div class="ro-stars">⭐ ' +
            (o.stars_amount || 0).toLocaleString() +
            "</div>" +
            '<div class="ro-price">$' +
            parseFloat(o.price_usd || 0).toFixed(2) +
            "</div>" +
            '<div class="ro-meta"><span class="ro-time">' +
            timeAgo(o.created_at) +
            "</span>" +
            "<span>" +
            pm +
            "</span></div>" +
            '<div class="ro-status">✅ ' +
            t("order_completed") +
            "</div></div>"
          );
        })
        .join("");
    })
    .catch(function () {
      if (cont) cont.innerHTML = '<div class="ro-empty">—</div>';
    });
}

function setROFilter(f) {
  roFilter = f;
  document.querySelectorAll(".ro-filter-btn").forEach(function (b) {
    b.classList.toggle("active", b.dataset.filter === f);
  });
  fetchRecentOrders();
}

function setROSort(s) {
  roSort = s;
  document.querySelectorAll(".ro-sort-btn").forEach(function (b) {
    b.classList.toggle("active", b.dataset.sort === s);
  });
  fetchRecentOrders();
}

function startROPolling() {
  fetchRecentOrders();
  clearInterval(roTimer);
  roTimer = setInterval(fetchRecentOrders, 30000);
}
window.setROFilter = setROFilter;
window.setROSort = setROSort;
window.startROPolling = startROPolling;
window.fetchRecentOrders = fetchRecentOrders;

/* ─── TWA ────────────────────────────────────────────────────────────────── */
function initTWA() {
  var tg;
  try {
    tg = window.Telegram && window.Telegram.WebApp;
  } catch (e) {
    return;
  }
  if (!tg) return;
  try {
    tg.ready();
  } catch (e) {}
  try {
    tg.expand();
  } catch (e) {}
  try {
    var tp = tg.themeParams || {};
    if (tp.bg_color)
      document.documentElement.style.setProperty("--bg", tp.bg_color);
    if (tp.text_color)
      document.documentElement.style.setProperty("--text", tp.text_color);
  } catch (e) {}
  try {
    var user = tg.initDataUnsafe && tg.initDataUnsafe.user;
    if (user) {
      var wel = document.getElementById("twaWelcome");
      if (wel) {
        wel.textContent =
          t("twa_welcome") +
          ", " +
          (user.first_name || user.username || "") +
          "! 👋";
        wel.style.display = "block";
      }
      var inp = document.getElementById("tgUsername");
      if (inp && !inp.value && user.username) {
        inp.value = "@" + user.username;
        triggerResolve();
      }
      if (user.language_code && !safeGet("lang")) {
        var lc = user.language_code.split("-")[0];
        if (window.TRANSLATIONS && window.TRANSLATIONS[lc]) currentLang = lc;
      }
    }
  } catch (e) {}
  try {
    if (tg.MainButton) {
      tg.MainButton.setText(t("pay_now"));
      tg.MainButton.onClick(function () {
        handlePay();
      });
    }
  } catch (e) {}
}

/* ─── Ref code ───────────────────────────────────────────────────────────── */
function initRefCode() {
  try {
    var ref = new URLSearchParams(window.location.search).get("ref");
    if (ref) {
      safeSet("stars_ref", ref);
      var ln = document.getElementById("loginRefNote");
      var rn = document.getElementById("regRefNote");
      if (ln) ln.style.display = "block";
      if (rn) rn.style.display = "block";
    }
  } catch (e) {}
}

/* ─── Utilities ──────────────────────────────────────────────────────────── */
function safeGet(k) {
  try {
    return localStorage.getItem(k);
  } catch (e) {
    return null;
  }
}
function safeSet(k, v) {
  try {
    localStorage.setItem(k, v);
  } catch (e) {}
}
function safeRemove(k) {
  try {
    localStorage.removeItem(k);
  } catch (e) {}
}

function showToast(msg, type) {
  var w = document.getElementById("toastWrap");
  if (!w) return;
  var el = document.createElement("div");
  el.className = "toast" + (type ? " " + type : "");
  el.textContent = msg;
  w.appendChild(el);
  setTimeout(function () {
    if (el.parentNode) el.parentNode.removeChild(el);
  }, 4200);
}
window.showToast = showToast;

function buildStarsBg() {
  var bg = document.getElementById("starsBg");
  if (!bg) return;
  for (var i = 0; i < 60; i++) {
    var s = document.createElement("span");
    s.style.left = Math.random() * 100 + "vw";
    s.style.top = Math.random() * 100 + "vh";
    s.style.setProperty("--d", 3 + Math.random() * 6 + "s");
    s.style.setProperty("--delay", Math.random() * 8 + "s");
    s.style.setProperty("--op", (0.3 + Math.random() * 0.5).toFixed(2));
    bg.appendChild(s);
  }
}

function animateSoldCounter() {
  var el = document.getElementById("soldCounter");
  if (!el) return;
  var target = Math.floor(Math.random() * 40000) + 10000;
  var cur = 0;
  var step = Math.ceil(target / 60);
  var timer = setInterval(function () {
    cur += step;
    if (cur >= target) {
      cur = target;
      clearInterval(timer);
    }
    el.textContent = cur.toLocaleString();
  }, 20);
}

/* ─── DOM Init ───────────────────────────────────────────────────────────── */
document.addEventListener("DOMContentLoaded", function () {
  /* TWA (may set language) */
  initTWA();
  /* Language */
  currentLang = detectLang();
  applyLang();
  /* Visual */
  buildStarsBg();
  animateSoldCounter();
  /* Functional */
  initRefCode();
  loadUserState();
  /* Default payment method */
  currentPaymentMethod = "cryptopay";
  selectPaymentMethod("cryptopay");
  /* استئناف تحقق CryptoPay بعد إعادة تحميل الصفحة */
  resumeCryptoPayPolling();
  /* Recent orders */
  startROPolling();
  /* fetch channel URL */
  fetch("/api/config")
    .then(function (r) {
      return r.json();
    })
    .then(function (cfg) {
      var btn = document.getElementById("btnChannel");
            if (cfg && cfg.price_per_star) {
        PRICE_PER_STAR = cfg.price_per_star;
        if (typeof renderPackages === 'function') renderPackages();
        var customInp = document.getElementById("customStars");
        if (customInp && customInp.value) {
            customInp.dispatchEvent(new Event("input"));
        } else {
            var sel = document.getElementById("packageSelect");
            if (sel && sel.value) sel.dispatchEvent(new Event("change"));
        }
      }
      if (btn && cfg && cfg.telegram_channel_url)
        btn.href = cfg.telegram_channel_url;
    })
    .catch(function () {});
  /* Language selector */
  var ls = document.getElementById("langSelect");
  if (ls)
    ls.addEventListener("change", function () {
      setLang(this.value);
    });
  /* Nav buttons */
  _wire("btnOpenLogin", function () {
    openModal("loginModal");
  });
  _wire("btnOpenRegister", function () {
    openModal("registerModal");
  });
  _wire("btnLogout", function () {
    logout();
  });
  /* Modal close */
  _wire("btnCloseLogin", function () {
    closeModal("loginModal");
  });
  _wire("btnCloseRegister", function () {
    closeModal("registerModal");
  });
  /* Modal switch */
  _wire("linkToRegister", function () {
    switchModal("loginModal", "registerModal");
  });
  _wire("linkToLogin", function () {
    switchModal("registerModal", "loginModal");
  });
  /* Auth submit */
  _wire("loginBtn", function () {
    handleLogin();
  });
  _wire("registerBtn", function () {
    handleRegister();
  });
  /* Enter key in modals */
  ["loginEmail", "loginPassword"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el)
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter") handleLogin();
      });
  });
  ["regEmail", "regPassword", "regTelegramId"].forEach(function (id) {
    var el = document.getElementById(id);
    if (el)
      el.addEventListener("keydown", function (e) {
        if (e.key === "Enter") handleRegister();
      });
  });
  /* Pay */
  _wire("payBtn", function () {
    handlePay();
  });
  _wire("btnCheckTon", function () {
    checkTonPayment();
  });
  _wire("btnCopyWallet", function () {
    copyTonWallet();
  });
  _wire("btnCopyComment", function () {
    copyTonComment();
  });
  _wire("btnShowWalletQr", function () {
    toggleTonWalletQr();
  });
  /* Support */
  _wire("btnSupport", function () {
    window.open("https://t.me/YourSupportBot", "_blank");
  });
  /* Package select */
  var ps = document.getElementById("packageSelect");
  if (ps)
    ps.addEventListener("change", function () {
      var val = parseInt(this.value, 10);
      if (!isNaN(val)) selectPackage(val);
    });
  /* Custom stars */
  var ci = document.getElementById("customStars");
  if (ci)
    ci.addEventListener("input", function () {
      var val = parseInt(this.value, 10);
      var psel = document.getElementById("packageSelect");
      if (psel) psel.selectedIndex = 0;
      if (!isNaN(val) && val >= 50) {
        var lp = document.getElementById("livePrice");
        if (lp) lp.textContent = (val * PRICE_PER_STAR).toFixed(2) + " $";
        setStars(val);
      } else {
        selectedStars = null;
        var ss = document.getElementById("sumStars");
        var sp = document.getElementById("sumPrice");
        if (ss) ss.textContent = "—";
        if (sp) sp.textContent = "—";
      }
    });
  /* Username field — auto-prepend @ */
  var tgi = document.getElementById("tgUsername");
  if (tgi) {
    tgi.addEventListener("blur", function () {
      var v = this.value.trim();
      if (v && !v.startsWith("@")) {
        this.value = "@" + v;
      }
      triggerResolve();
    });
    tgi.addEventListener("input", function () {
      var p = document.getElementById("accountPreview");
      var e = document.getElementById("accountError");
      var hint = document.getElementById("atHint");
      if (p) p.style.display = "none";
      if (e) e.style.display = "none";
      var v = this.value.trim();
      if (hint) hint.style.display = v && !v.startsWith("@") ? "block" : "none";
      triggerResolve();
    });
    tgi.addEventListener("keydown", function (e) {
      if (e.key === "Enter") doResolveUsername();
    });
  }
  /* Email validation */
  var ge = document.getElementById("guestEmail");
  if (ge)
    ge.addEventListener("blur", function () {
      var v = this.value.trim();
      if (v && (!v.includes("@") || !v.includes("."))) {
        this.classList.add("invalid");
        showToast("⚠️ " + t("email_invalid"), "error");
      } else {
        this.classList.remove("invalid");
      }
    });
  /* Recent orders filter/sort */
  document.querySelectorAll(".ro-filter-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      setROFilter(this.dataset.filter);
    });
  });
  document.querySelectorAll(".ro-sort-btn").forEach(function (b) {
    b.addEventListener("click", function () {
      setROSort(this.dataset.sort);
    });
  });
  /* Close overlays on backdrop */
  document.querySelectorAll(".overlay").forEach(function (ov) {
    ov.addEventListener("click", function (e) {
      if (e.target === ov) ov.classList.remove("open");
    });
  });
  /* Escape key */
  document.addEventListener("keydown", function (e) {
    if (e.key === "Escape")
      document.querySelectorAll(".overlay.open").forEach(function (ov) {
        ov.classList.remove("open");
      });
  });
});

function _wire(id, fn) {
  var el = document.getElementById(id);
  if (el) el.addEventListener("click", fn);
}
