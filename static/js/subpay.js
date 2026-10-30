/* ChamaPay payment page. External file because the site CSP blocks inline scripts. */
(function () {
  'use strict';
  var csrf = (document.querySelector('meta[name="csrf-token"]') || {}).content || '';
  function $(sel, root) { return (root || document).querySelector(sel); }
  function kes(cents) { return 'KES ' + Math.round(cents / 100).toLocaleString('en-KE'); }

  // Accepts 07XXXXXXXX, 01XXXXXXXX, 7XXXXXXXX, 2547XXXXXXXX, +2547XXXXXXXX
  function normPhone(v) {
    var d = String(v || '').replace(/\D/g, '');
    if (/^0[17]\d{8}$/.test(d)) d = '254' + d.slice(1);
    else if (/^[17]\d{8}$/.test(d)) d = '254' + d;
    return /^254[17]\d{8}$/.test(d) ? d : null;
  }

  // Poll a payment until it leaves PENDING, then reload so the server shows the final state.
  function poll(url, onTick) {
    var started = Date.now(), delay = 3000;
    (function tick() {
      fetch(url, { credentials: 'same-origin', headers: { 'Accept': 'application/json' }, cache: 'no-store' })
        .then(function (r) { return r.ok ? r.json() : Promise.reject(); })
        .then(function (j) {
          if (j.done) { window.location.reload(); return; }
          if (onTick) onTick(Math.round((Date.now() - started) / 1000));
          if (Date.now() - started < 10 * 60 * 1000) setTimeout(tick, delay = Math.min(delay + 500, 6000));
        })
        .catch(function () { setTimeout(tick, 6000); });
    })();
  }

  // ---- status page ----
  var st = $('[data-pp-status]');
  if (st && st.getAttribute('data-status') === 'PENDING') {
    var note = $('[data-pp-note]', st);
    poll(st.getAttribute('data-status-url'), function (secs) {
      if (note) note.textContent = secs < 45 ? 'Checking with Paystack\u2026' :
        'Still waiting. If no prompt appeared, check the number is on Safaricom and try again.';
    });
  }

  // ---- checkout card ----
  var box = $('[data-pp]');
  if (!box) return;
  var form = $('[data-pp-form]', box), plans = {};
  try { JSON.parse(box.getAttribute('data-plans') || '[]').forEach(function (p) { plans[p.id] = p; }); } catch (e) {}
  var out = { plan: $('[data-pp-plan]', box), period: $('[data-pp-period]', box), total: $('[data-pp-total]', box) };
  var phoneIn = $('input[name="phone"]', box), phoneWrap = $('.pp-phone', box), phoneMsg = $('[data-pp-phone-msg]', box);
  var errBox = $('[data-pp-error]', box), submitBtn = $('[data-pp-submit]', box), label = $('[data-pp-label]', box);
  var idleText = phoneMsg ? phoneMsg.textContent : '';

  function selected() {
    var r = $('input[name="plan_id"]:checked', form), m = $('input[name="months"]:checked', form);
    return { plan: r ? plans[r.value] : null, months: m ? parseInt(m.value, 10) : 1 };
  }
  function update() {
    var s = selected();
    if (!s.plan) return;
    out.plan.textContent = s.plan.name;
    out.period.textContent = s.months + ' month' + (s.months === 1 ? '' : 's');
    out.total.textContent = kes(s.plan.price * s.months);
  }
  function showError(msg) { errBox.textContent = msg; errBox.hidden = !msg; }
  function checkPhone(show) {
    var ok = !!normPhone(phoneIn.value);
    phoneWrap.classList.toggle('bad', show && !ok);
    phoneMsg.classList.toggle('bad', show && !ok);
    phoneMsg.textContent = (show && !ok) ? 'Enter a valid Safaricom number, e.g. 0712 345 678.' : idleText;
    return ok;
  }

  form.addEventListener('change', update);
  phoneIn.addEventListener('blur', function () { if (phoneIn.value) checkPhone(true); });
  phoneIn.addEventListener('input', function () { showError(''); if (phoneWrap.classList.contains('bad')) checkPhone(true); });

  // "Upgrade" buttons in the plan list above jump here with the plan preselected.
  document.addEventListener('click', function (e) {
    var b = e.target.closest('[data-pick-plan]');
    if (!b) return;
    var r = $('#payment-plan-' + b.getAttribute('data-pick-plan'), form);
    if (r && !r.disabled) { r.checked = true; update(); }
    box.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });

  form.addEventListener('submit', function (e) {
    var mode = (e.submitter && e.submitter.value) || 'stk';
    if (!window.fetch || !window.FormData) return; // plain form post still works without JS
    e.preventDefault();
    showError('');
    if (!checkPhone(true)) { phoneIn.focus(); return; }
    var s = selected();
    if (!s.plan) { showError('Choose a plan to continue.'); return; }
    var data = new FormData(form);
    data.set('mode', mode);
    var original = label.textContent;
    submitBtn.disabled = true; label.textContent = 'Please wait\u2026';
    fetch(form.getAttribute('action'), {
      method: 'POST', body: data, credentials: 'same-origin',
      headers: { 'X-Requested-With': 'fetch', 'Accept': 'application/json', 'X-CSRF-Token': csrf }
    }).then(function (r) {
      return r.json().catch(function () { return { ok: false, error: 'Something went wrong. Please try again.' }; });
    }).then(function (j) {
      if (!j.ok) { showError(j.error || 'Payment could not be started.'); submitBtn.disabled = false; label.textContent = original; return; }
      if (j.mode === 'checkout' && j.redirect_url) { window.location.assign(j.redirect_url); return; }
      // M-Pesa prompt sent: swap the form for the waiting panel and watch for the result.
      var wait = $('[data-pp-wait]', box);
      form.hidden = true; $('.pp-head', box).hidden = true; wait.hidden = false;
      $('[data-pp-wait-total]', wait).textContent = Math.round(s.plan.price * s.months / 100).toLocaleString('en-KE');
      var link = $('[data-pp-wait-link]', wait); link.href = j.status_url;
      wait.scrollIntoView({ behavior: 'smooth', block: 'center' });
      var wn = $('[data-pp-wait-note]', wait);
      poll(j.status_url, function (secs) {
        if (secs > 45 && wn) wn.textContent = 'Still waiting. Did the prompt arrive? You can open the status page or go back and try again.';
      });
    }).catch(function () {
      showError('Could not reach ChamaPay. Check your connection and try again.');
      submitBtn.disabled = false; label.textContent = original;
    });
  });
  update();
})();
