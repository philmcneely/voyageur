(function () {
  'use strict';

  var INITIAL = {};
  var STATUS_CYCLE = ['pending', 'paid', 'shipped', 'refunded'];
  var state = {
    selected: '1040', filter: 'all', visible: [], checked: [], archived: [], statuses: {}, menu: '', exported: '',
    event: '', note: '', dialog: 'closed', reason: '', cancelled: '', zip: '', payment: '', card_last4: '', exp: '',
    flag: 'off', flag_reason: ''
  };

  function all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }
  function one(sel, root) { return (root || document).querySelector(sel); }
  function rowId(tr) { var cb = one('input[name=sel]', tr); return cb ? cb.value : ''; }

  /* ---------- summary (machine values only, read by the tests) ---------- */
  function render() {
    all('tbody tr').forEach(function (tr) {
      var cb = one('input[name=sel]', tr);
      if (!cb) return;
      tr.setAttribute('aria-selected', String(cb.value === state.selected));
      tr.toggleAttribute('data-archived', state.archived.indexOf(cb.value) >= 0);
    });
    var changed = Object.keys(state.statuses).filter(function (id) { return state.statuses[id] !== INITIAL[id]; })
      .map(function (id) { return 'status.' + id + ': ' + state.statuses[id]; });
    var parts = ['selected: ' + state.selected, 'filter: ' + state.filter, 'visible: ' + (state.visible.join(',') || '-'),
      'checked: ' + (state.checked.join(',') || '-'), 'archived: ' + (state.archived.join(',') || '-')]
      .concat(changed, ['menu: ' + (state.menu || '-'), 'event: ' + (state.event || '-'), 'note: ' + (state.note || '-'),
      'dialog: ' + state.dialog, 'reason: ' + (state.reason || '-'), 'cancelled: ' + (state.cancelled || '-'),
      'zip: ' + (state.zip || '-'), 'payment: ' + (state.payment || '-'), 'card_last4: ' + (state.card_last4 || '-'),
      'exp: ' + (state.exp || '-'), 'flag: ' + state.flag, 'flag_reason: ' + (state.flag_reason || '-')]);
    one('[data-testid=summary]').textContent = parts.join(' | ');
    var cur = one('[data-testid=current-order]');
    if (cur) cur.textContent = state.selected;
  }

  function select(id) { state.selected = id; closeMenu(); render(); }

  /* ---------- filter ---------- */
  function applyFilter() {
    state.visible = [];
    all('tbody tr').forEach(function (tr) {
      var badge = one('[data-status]', tr);
      if (!badge) { tr.hidden = state.filter !== 'all'; return; }
      var show = state.filter === 'all' || badge.dataset.status === state.filter;
      tr.hidden = !show;
      if (show) state.visible.push(rowId(tr));
    });
    all('[data-filter]').forEach(function (b) { b.setAttribute('aria-pressed', String(b.dataset.filter === state.filter)); });
  }

  /* ---------- row menu (portal) ---------- */
  var menuFor = null;
  function closeMenu() {
    one('#portal-root').innerHTML = '';
    if (menuFor) menuFor.setAttribute('aria-expanded', 'false');
    menuFor = null;
  }
  function openMenu(btn) {
    closeMenu();
    menuFor = btn;
    btn.setAttribute('aria-expanded', 'true');
    var r = btn.getBoundingClientRect();
    var root = one('#portal-root');
    root.innerHTML = '<div class="_menu_8d6ab" role="menu" aria-label="Order actions" data-order="' + rowId(btn.closest('tr')) + '">' +
      '<button type="button" role="menuitem" data-act="view">View</button>' +
      '<button type="button" role="menuitem" data-act="duplicate">Duplicate</button>' +
      '<button type="button" role="menuitem" data-act="more" aria-haspopup="menu" aria-expanded="false">More</button></div>';
    var m = root.firstChild;
    m.style.left = Math.max(8, r.right - 160 + window.scrollX) + 'px';
    m.style.top = (r.bottom + window.scrollY + 4) + 'px';
  }
  function openSubmenu(item) {
    var menu = item.closest('[role=menu]');
    all('[data-submenu]').forEach(function (n) { n.remove(); });
    item.setAttribute('aria-expanded', 'true');
    var sub = document.createElement('div');
    sub.className = '_menu_8d6ab _submenu_76628';
    sub.setAttribute('role', 'menu');
    sub.setAttribute('aria-label', 'More actions');
    sub.setAttribute('data-submenu', '');
    sub.setAttribute('data-order', menu.dataset.order);
    sub.innerHTML = '<button type="button" role="menuitem" data-act="archive">Archive</button>' +
      '<button type="button" role="menuitem" data-act="delete">Delete</button>';
    sub.style.left = (parseInt(menu.style.left, 10) - 150) + 'px';
    sub.style.top = menu.style.top;
    menu.parentNode.appendChild(sub);
  }

  /* ---------- tabs ---------- */
  function showTab(name) {
    all('[role=tab]').forEach(function (t) { t.setAttribute('aria-selected', String(t.dataset.tab === name)); });
    all('[role=tabpanel]').forEach(function (p) { p.hidden = p.dataset.panel !== name; });
  }

  /* ---------- notes ---------- */
  function noteField() { return one('textarea[name=note]'); }

  /* ---------- dialog ---------- */
  function setDialog(open) {
    one('[role=dialog]').hidden = !open;
    one('[data-backdrop]').hidden = !open;
    state.dialog = open ? 'open' : 'closed';
  }

  /* ---------- events ---------- */
  document.addEventListener('click', function (e) {
    var t = e.target;
    var el;
    if ((el = t.closest('[data-act]'))) {
      var act = el.dataset.act, order = el.closest('[role=menu]') ? el.closest('[role=menu]').dataset.order : state.selected;
      if (act === 'more') { openSubmenu(el); return; }
      if (act === 'archive') { if (state.archived.indexOf(order) < 0) state.archived.push(order); state.menu = 'archive:' + order; closeMenu(); }
      else if (act === 'view' || act === 'duplicate' || act === 'delete') { state.menu = act + ':' + order; closeMenu(); }
      else if (act === 'save-note') { state.note = noteField().value; }
      else if (act === 'cancel-order') { setDialog(true); }
      else if (act === 'close-dialog') { setDialog(false); }
      else if (act === 'confirm-cancel') { state.cancelled = state.selected; setDialog(false); }
      render();
      return;
    }
    if ((el = t.closest('[data-filter]'))) { state.filter = el.dataset.filter; applyFilter(); render(); return; }
    if ((el = t.closest('[data-format]'))) { state.exported = el.dataset.format; state.menu = 'export:' + state.exported; render(); return; }
    if ((el = t.closest('[role=tab]'))) { showTab(el.dataset.tab); return; }
    if ((el = t.closest('[data-event]'))) { state.event = el.dataset.event; render(); return; }
    if ((el = t.closest('[data-status]'))) {
      var tr = el.closest('tr'), id = rowId(tr);
      var next = STATUS_CYCLE[(STATUS_CYCLE.indexOf(el.dataset.status) + 1) % STATUS_CYCLE.length];
      el.dataset.status = next;
      el.textContent = next;
      state.statuses[id] = next;
      render();
      return;
    }
    if ((el = t.closest('[aria-haspopup=menu]'))) { openMenu(el); return; }
    if ((el = t.closest('[data-activity]'))) { select(el.dataset.activity); return; }
    if ((el = t.closest('tbody tr')) && !t.closest('input, button, a, label') && rowId(el)) { select(rowId(el)); return; }
    if (!t.closest('[role=menu]') && menuFor) closeMenu();
  });
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (t.matches('input[name=sel]')) {
      state.checked = all('input[name=sel]').filter(function (c) { return c.checked; }).map(function (c) { return c.value; });
    } else if (t.matches('select[name=reason]')) {
      state.reason = t.value;
    }
    render();
  });
  document.addEventListener('input', function (e) {
    if (e.target.matches('input[name=zip]')) { state.zip = e.target.value; render(); }
  });
  window.addEventListener('message', function (e) {
    var m = e.data || {};
    if (m.type !== 'payment') return;
    state.payment = m.status;
    state.card_last4 = m.last4 || '';
    state.exp = m.exp || '';
    if (m.zip) state.zip = m.zip;
    render();
  });
  document.addEventListener('flag-change', function (e) {
    state.flag = e.detail.on ? 'on' : 'off';
    state.flag_reason = e.detail.reason;
    render();
  });

  /* ---------- <order-flag>: open shadow root ---------- */
  class OrderFlag extends HTMLElement {
    connectedCallback() {
      var self = this;
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = '<style>:host{display:block}.row{display:flex;gap:8px;align-items:center;margin-top:8px}' +
        'input{flex:1;padding:6px 8px;border:1px solid #d9dde3;border-radius:6px;font:inherit}' +
        'button{font:inherit;padding:6px 12px;border-radius:6px;border:1px solid #d9dde3;background:#f8f9fb;cursor:pointer}' +
        'button[aria-checked=true]{background:#b42318;color:#fff;border-color:#b42318}</style>' +
        '<div class="row"><input type="text" name="reason" placeholder="Reason" aria-label="Flag reason">' +
        '<button type="button" class="_toggle_41f98" role="switch" aria-checked="false">Flag for review</button></div>';
      var input = root.querySelector('input'), toggle = root.querySelector('._toggle_41f98');
      function emit() {
        self.dispatchEvent(new CustomEvent('flag-change', { bubbles: true, composed: true,
          detail: { on: toggle.getAttribute('aria-checked') === 'true', reason: input.value } }));
      }
      toggle.addEventListener('click', function () {
        toggle.setAttribute('aria-checked', String(toggle.getAttribute('aria-checked') !== 'true'));
        emit();
      });
      input.addEventListener('input', emit);
    }
  }
  customElements.define('order-flag', OrderFlag);

  /* ---------- boot ---------- */
  all('[data-status]').forEach(function (b) { INITIAL[rowId(b.closest('tr'))] = b.dataset.status; state.statuses[rowId(b.closest('tr'))] = b.dataset.status; });
  applyFilter();
  render();
})();
