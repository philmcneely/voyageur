/* Web components with open shadow roots, standing in for Ionic / Salesforce style libraries.
   data-hit-inner="key" on a host marks the control inside its shadow root (the marker is removed, like data-hit).
   data-shadow-input="key" on a plain element gives that element a shadow root holding one input
   (data-shadow-input-attrs="id=a;name=b" are the input's attributes; data-shadow-wrap puts the input in a div inside the root). */
(function () {
  'use strict';
  var H = window.__hit;
  var STYLE = '<style>:host{display:block}input,textarea{box-sizing:border-box;width:100%;padding:6px 8px;border:1px solid #c9ccd3;border-radius:6px;font:inherit}' +
    '.row{display:flex;gap:8px;align-items:center}</style>';

  function take(host, attr) { var v = host.getAttribute(attr); host.removeAttribute(attr); return v; }

  function define(tag, render) {
    customElements.define(tag, class extends HTMLElement {
      connectedCallback() {
        if (this.shadowRoot) return;
        var key = take(this, 'data-hit-inner');
        var root = this.attachShadow({ mode: 'open' });
        var el = render(root, this);
        if (key && el) H.register(el, key);
      }
    });
  }

  define('ion-searchbar', function (root) {
    root.innerHTML = STYLE + '<div class="row"><input type="search" placeholder="Search"></div>';
    return root.querySelector('input');
  });
  define('ion-textarea', function (root) {
    root.innerHTML = STYLE + '<div class="row"><textarea rows="2" placeholder="Note"></textarea></div>';
    return root.querySelector('textarea');
  });
  customElements.define('lightning-input', class extends HTMLElement {
    connectedCallback() {
      if (this.shadowRoot) return;
      var key = take(this, 'data-hit-inner'), inner = take(this, 'data-inner') || 'lightning-primitive-input-toggle';
      var root = this.attachShadow({ mode: 'open' });
      root.innerHTML = STYLE + '<' + inner + '></' + inner + '>';
      var toggle = root.querySelector(inner);
      toggle.style.display = 'block';
      toggle.attachShadow({ mode: 'open' }).innerHTML = STYLE + '<label class="row"><input type="checkbox" role="switch"> <span>On</span></label>';
      if (key) H.register(toggle, key);
    }
  });

  document.addEventListener('DOMContentLoaded', function () {
    Array.prototype.forEach.call(document.querySelectorAll('[data-shadow-input]'), function (host) {
      var key = take(host, 'data-shadow-input'), attrs = take(host, 'data-shadow-input-attrs') || '', wrap = take(host, 'data-shadow-wrap');
      var root = host.attachShadow({ mode: 'open' });
      root.innerHTML = STYLE + (wrap ? '<div class="row"><input type="text"></div>' : '<input type="text">');
      var input = root.querySelector('input');
      attrs.split(';').forEach(function (kv) { var p = kv.split('='); if (p[0]) input.setAttribute(p[0], p[1]); });
      H.register(input, key);
    });
  }, { once: true });
})();
