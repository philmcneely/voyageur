// Account settings page. The status line carries machine values only (never what was typed, never copy), so a test can
// assert it in every language and every variant.
(function () {
  var summary = document.getElementById('summary');
  function say(text) { summary.textContent = text; }
  document.addEventListener('click', function (event) {
    var button = event.target.closest('button');
    if (!button) return;
    var action = button.getAttribute('data-action') || button.id;
    if (action === 'save') {
      var name = document.getElementById('display-name').value;
      if (name.trim().length < 3) { say('error: display name needs at least 3 characters'); return; }
      var box = document.querySelector('[data-pref=newsletter]');
      say('saved: profile newsletter=' + (box && box.checked ? 'yes' : 'no'));
    } else if (action === 'save-notifications') {
      say('saved: notifications');
    } else if (action === 'delete') {
      say('deleted: account');
    }
  });
})();
