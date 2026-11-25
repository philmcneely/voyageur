/* Test hook of the real-locator pages. The source html marks the elements a test acts on with data-hit="key";
   at boot each marker is read and REMOVED, so the live DOM carries no trace of it (a locator, or a healer, sees only
   the markup a real app has). A click / input / change on a marked element (or inside it, through shadow roots and
   frames) is recorded as "hit.<key>: <value>" in the summary the tests read. Frames post their hits to the parent. */
(function () {
  'use strict';
  var map = new WeakMap(), state = {}, summary = null;

  function render() {
    if (!summary) return;
    summary.textContent = Object.keys(state).map(function (k) { return 'hit.' + k + ': ' + state[k]; }).join(' | ');
  }
  function record(name, val) {
    if (window.parent !== window) { window.parent.postMessage({ __hit: true, name: name, val: val }, '*'); return; }
    state[name] = val;
    render();
  }
  function register(el, name) { map.set(el, name); }
  function scan(root) {
    Array.prototype.forEach.call(root.querySelectorAll('[data-hit]'), function (el) {
      register(el, el.getAttribute('data-hit'));
      el.removeAttribute('data-hit');
    });
    Array.prototype.forEach.call(root.querySelectorAll('*'), function (el) { if (el.shadowRoot) scan(el.shadowRoot); });
  }
  function hit(e, val) {
    var path = e.composedPath ? e.composedPath() : [e.target];
    for (var i = 0; i < path.length; i++) {
      if (map.has(path[i])) { record(map.get(path[i]), val(path[i])); return; }
    }
  }
  document.addEventListener('click', function (e) { hit(e, function () { return 'click'; }); }, true);
  document.addEventListener('input', function (e) {
    var t = e.composedPath ? e.composedPath()[0] : e.target;
    if (t.type === 'checkbox' || t.type === 'radio') return;
    hit(e, function (el) { return el.isContentEditable && el.tagName !== 'INPUT' ? el.textContent : (t.value !== undefined ? t.value : ''); });
  }, true);
  document.addEventListener('change', function (e) {
    var t = e.composedPath ? e.composedPath()[0] : e.target;
    hit(e, function () { return t.type === 'checkbox' || t.type === 'radio' ? (t.checked ? 'checked' : 'unchecked') : t.value; });
  }, true);
  window.addEventListener('message', function (e) {
    var m = e.data;
    if (m && m.__hit) record(m.name, m.val);
  });
  window.__hit = { register: register, scan: scan };
  window.addEventListener('DOMContentLoaded', function () {
    summary = document.querySelector('[data-testid=summary]');
    scan(document);
    render();
  });
})();
