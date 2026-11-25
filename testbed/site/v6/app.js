(function () {
  'use strict';
  var KEY = 'signup';

  function load() {
    try { return JSON.parse(sessionStorage.getItem(KEY)) || {}; } catch (e) { return {}; }
  }
  function save(state) {
    try { sessionStorage.setItem(KEY, JSON.stringify(state)); } catch (e) { /* ignore */ }
  }
  function fmt(v) {
    if (v === undefined || v === null || v === '') return '—';
    if (v === true) return 'yes';
    if (v === false) return 'no';
    if (v instanceof Array) return v.length ? v.join(', ') : '—';
    return String(v);
  }

  /* ---------- validation ---------- */
  function fieldOf(el) { return el.closest('.field') || el.parentElement; }
  function labelOf(el) {
    var l = fieldOf(el).querySelector('legend, label, .label');
    return l ? l.textContent.replace(/\s+/g, ' ').trim() : el.dataset.key;
  }
  function isValid(el) {
    if (el.type === 'hidden') return !el.hasAttribute('data-required') || el.value.trim() !== '';
    if (typeof el.checkValidity !== 'function') return true;
    return el.checkValidity();
  }
  function describe(el) {
    var label = labelOf(el);
    if (el.type === 'hidden' || el.validity.valueMissing) return label + ' est obligatoire';
    return label + ': ' + (el.validationMessage || 'is invalid');
  }
  function clearErrors(form) {
    form.querySelectorAll('.error').forEach(function (p) { p.remove(); });
    form.querySelectorAll('[aria-invalid]').forEach(function (el) { el.removeAttribute('aria-invalid'); });
  }
  function reveal(el) {
    var d = el.closest('details');
    if (d) d.open = true;
    for (var i = 0; i < 5; i++) {
      var node = el.closest('[hidden]');
      if (!node) return;
      var ctl = node.id && document.querySelector('[aria-controls="' + node.id + '"]');
      if (ctl) ctl.click();
      if (node.hidden) node.hidden = false;
    }
  }
  function applyMatches(form) {
    form.querySelectorAll('[data-match]').forEach(function (el) {
      var other = form.querySelector('[data-key="' + el.dataset.match + '"]');
      el.setCustomValidity(other && el.value !== other.value ? 'Les mots de passe ne correspondent pas' : '');
    });
  }
  function validate(form) {
    clearErrors(form);
    applyMatches(form);
    var bad = [], seen = [];
    form.querySelectorAll('[data-key]').forEach(function (el) {
      if (el.disabled || isValid(el)) return;
      var field = fieldOf(el);
      if (seen.indexOf(field) >= 0) return;
      seen.push(field);
      reveal(el);
      var p = document.createElement('p');
      p.className = 'error';
      p.setAttribute('role', 'alert');
      p.textContent = describe(el);
      field.appendChild(p);
      el.setAttribute('aria-invalid', 'true');
      bad.push(el);
    });
    if (bad.length && bad[0].type !== 'hidden') { try { bad[0].focus(); } catch (e) { /* ignore */ } }
    return bad.length === 0;
  }

  /* ---------- collecting values ---------- */
  function collect(form) {
    var out = {}, multi = {};
    form.querySelectorAll('[data-key]').forEach(function (el) {
      var k = el.dataset.key;
      if (el.hasAttribute('data-nostore')) return;
      if (el.type === 'radio') {
        if (el.checked) out[k] = el.value;
      } else if (el.type === 'checkbox') {
        if (el.hasAttribute('data-multi')) {
          multi[k] = multi[k] || [];
          if (el.checked) multi[k].push(el.value);
        } else {
          out[k] = el.checked;
        }
      } else if (el.getAttribute('role') === 'switch') {
        out[k] = el.getAttribute('aria-checked') === 'true';
      } else if (el.hasAttribute('data-last4')) {
        out[k] = el.value ? '**** ' + el.value.replace(/\D/g, '').slice(-4) : '';
      } else {
        out[k] = el.value.trim();
      }
    });
    Object.keys(multi).forEach(function (k) { out[k] = multi[k]; });
    return out;
  }
  function renderLive(form) {
    var box = document.querySelector('[data-live]');
    if (!box) return;
    var v = collect(form);
    box.textContent = Object.keys(v).sort().map(function (k) { return k + ': ' + fmt(v[k]); }).join(' | ');
  }

  /* ---------- widgets ---------- */
  function activateTab(tab) {
    tab.closest('[role="tablist"]').querySelectorAll('[role="tab"]').forEach(function (t) {
      var on = t === tab;
      t.setAttribute('aria-selected', on ? 'true' : 'false');
      t.tabIndex = on ? 0 : -1;
      var panel = document.getElementById(t.getAttribute('aria-controls'));
      if (panel) panel.hidden = !on;
    });
  }
  document.querySelectorAll('[role="tab"]').forEach(function (tab) {
    tab.addEventListener('click', function () { activateTab(tab); });
  });

  document.querySelectorAll('[data-accordion] button[aria-expanded]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var open = btn.getAttribute('aria-expanded') === 'true';
      btn.setAttribute('aria-expanded', open ? 'false' : 'true');
      var panel = document.getElementById(btn.getAttribute('aria-controls'));
      if (panel) panel.hidden = open;
    });
  });

  document.querySelectorAll('[role="switch"]').forEach(function (sw) {
    sw.addEventListener('click', function () {
      sw.setAttribute('aria-checked', sw.getAttribute('aria-checked') === 'true' ? 'false' : 'true');
    });
  });

  document.querySelectorAll('.stepper').forEach(function (box) {
    var out = box.querySelector('output');
    var hidden = box.querySelector('input[type="hidden"]');
    var min = Number(box.dataset.min || 1), max = Number(box.dataset.max || 10);
    box.querySelectorAll('[data-step]').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var n = Math.max(min, Math.min(max, Number(hidden.value) + Number(btn.dataset.step)));
        hidden.value = String(n);
        out.textContent = String(n);
      });
    });
  });

  document.querySelectorAll('table.plans button[data-plan]').forEach(function (btn) {
    btn.addEventListener('click', function () {
      var table = btn.closest('table');
      table.querySelectorAll('tr').forEach(function (r) { r.classList.remove('selected'); });
      table.querySelectorAll('button[data-plan]').forEach(function (b) { b.setAttribute('aria-pressed', 'false'); });
      btn.setAttribute('aria-pressed', 'true');
      btn.closest('tr').classList.add('selected');
      var hidden = table.closest('.field').querySelector('input[type="hidden"]');
      hidden.value = btn.dataset.plan;
    });
  });

  document.querySelectorAll('input[type="range"]').forEach(function (r) {
    var out = document.querySelector('output[for="' + r.id + '"]');
    function sync() { if (out) out.textContent = r.value; }
    r.addEventListener('input', sync);
    sync();
  });

  document.querySelectorAll('[data-datepicker]').forEach(function (dp) {
    var open = dp.querySelector('.dp-open');
    var cal = dp.querySelector('.calendar');
    var out = dp.querySelector('.dp-value');
    var hidden = dp.querySelector('input[type="hidden"]');
    var month = dp.dataset.month;                 // YYYY-MM
    var y = Number(month.slice(0, 4)), m = Number(month.slice(5, 7));
    var names = ['janvier', 'février', 'mars', 'avril', 'mai', 'juin', 'juillet', 'août', 'septembre', 'octobre', 'novembre', 'décembre'];
    var first = new Date(y, m - 1, 1).getDay();
    var days = new Date(y, m, 0).getDate();
    var html = '<div class="cal-title">' + names[m - 1] + ' ' + y + '</div><div class="cal-grid">';
    ['Su', 'Mo', 'Tu', 'We', 'Th', 'Fr', 'Sa'].forEach(function (d) { html += '<span>' + d + '</span>'; });
    for (var i = 0; i < first; i++) html += '<span></span>';
    for (var d = 1; d <= days; d++) html += '<button type="button" class="day">' + d + '</button>';
    cal.innerHTML = html + '</div>';
    open.addEventListener('click', function () {
      cal.hidden = !cal.hidden;
      open.setAttribute('aria-expanded', cal.hidden ? 'false' : 'true');
    });
    cal.addEventListener('click', function (e) {
      var b = e.target.closest('button.day');
      if (!b) return;
      cal.querySelectorAll('.picked').forEach(function (x) { x.classList.remove('picked'); });
      b.classList.add('picked');
      var iso = month + '-' + (b.textContent.length < 2 ? '0' : '') + b.textContent;
      hidden.value = iso;
      out.textContent = iso;
      cal.hidden = true;
      open.setAttribute('aria-expanded', 'false');
    });
  });

  document.querySelectorAll('[data-combo]').forEach(function (box) {
    var input = box.querySelector('input');
    var list = box.querySelector('[role="listbox"]');
    var options = box.dataset.options.split(',');
    function close() { list.hidden = true; input.setAttribute('aria-expanded', 'false'); }
    input.addEventListener('input', function () {
      var q = input.value.trim().toLowerCase();
      var hits = q ? options.filter(function (o) { return o.toLowerCase().indexOf(q) === 0; }) : [];
      list.innerHTML = '';
      hits.forEach(function (o) {
        var li = document.createElement('li');
        li.setAttribute('role', 'option');
        li.textContent = o;
        li.addEventListener('click', function () {
          input.value = o;
          close();
          input.dispatchEvent(new Event('change', { bubbles: true }));
        });
        list.appendChild(li);
      });
      list.hidden = hits.length === 0;
      input.setAttribute('aria-expanded', hits.length ? 'true' : 'false');
    });
    input.addEventListener('keydown', function (e) { if (e.key === 'Escape') close(); });
  });

  var openTerms = document.getElementById('open-terms');
  if (openTerms) {
    var overlay = document.getElementById('terms-overlay');
    var agree = document.getElementById('agree');
    var status = document.querySelector('[data-terms-status]');
    var termsInput = document.querySelector('input[data-key="terms"]');
    openTerms.addEventListener('click', function () { overlay.hidden = false; });
    document.getElementById('close-terms').addEventListener('click', function () { overlay.hidden = true; });
    agree.addEventListener('change', function () {
      termsInput.value = agree.checked ? 'yes' : '';
      status.textContent = agree.checked ? 'Conditions acceptées' : 'Conditions pas encore acceptées';
    });
  }

  /* ---------- summaries (review, done) ---------- */
  var state = load();
  document.querySelectorAll('[data-review], [data-done]').forEach(function (dd) {
    dd.textContent = fmt(state[dd.dataset.review || dd.dataset.done]);
  });

  /* ---------- step form ---------- */
  var form = document.getElementById('step-form');
  if (form) {
    form.noValidate = true;
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      if (!validate(form)) return;
      var s = load();
      var v = collect(form);
      Object.keys(v).forEach(function (k) { s[k] = v[k]; });
      save(s);
      window.location.href = form.dataset.next;
    });
    ['input', 'change', 'click'].forEach(function (ev) {
      form.addEventListener(ev, function () { renderLive(form); });
    });
    renderLive(form);
  }
})();
