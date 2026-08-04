/* ============================================================
   Shared mobile behavior — Ikai Production
   Include on EVERY page, right before </body>:
     <script src="{{ url_for('static', filename='js/mobile.js') }}"></script>
   Pairs with static/css/mobile.css.
   No template markup changes required — this injects the hamburger
   button, backdrop, and table-scroll wrappers automatically.
   ============================================================ */
(function () {
  function ready(fn) {
    if (document.readyState !== 'loading') fn();
    else document.addEventListener('DOMContentLoaded', fn);
  }

  ready(function () {
    var sidebar = document.querySelector('.layout > .sidebar');
    if (!sidebar) return;

    // Pull the brand name out of the sidebar for the mobile topbar
    var brandEl = sidebar.querySelector('.brand');
    var brandText = 'Menu';
    if (brandEl) {
      // .brand's first text node is the product name; the <span> child is the subtitle
      var firstNode = brandEl.childNodes[0];
      if (firstNode && firstNode.textContent.trim()) {
        brandText = firstNode.textContent.trim();
      }
    }

    // Build the mobile topbar (hamburger + brand)
    var topbar = document.createElement('div');
    topbar.className = 'mobile-topbar';
    topbar.innerHTML =
      '<button type="button" class="hamburger" aria-label="Open menu">&#9776;</button>' +
      '<span>' + brandText + '</span>';
    sidebar.parentNode.insertBefore(topbar, sidebar);

    // Build the backdrop
    var overlay = document.createElement('div');
    overlay.className = 'sidebar-overlay';
    document.body.appendChild(overlay);

    function openSidebar() {
      sidebar.classList.add('mobile-open');
      overlay.classList.add('open');
    }
    function closeSidebar() {
      sidebar.classList.remove('mobile-open');
      overlay.classList.remove('open');
    }

    topbar.querySelector('.hamburger').addEventListener('click', openSidebar);
    overlay.addEventListener('click', closeSidebar);

    // Tapping a nav link closes the drawer (page navigates anyway)
    sidebar.querySelectorAll('nav a').forEach(function (a) {
      a.addEventListener('click', closeSidebar);
    });

    // If the window is resized back up to desktop width, make sure the
    // drawer state doesn't linger open behind the now-visible sidebar
    window.addEventListener('resize', function () {
      if (window.innerWidth > 880) closeSidebar();
    });

    // Wrap any bare <table> not already inside a horizontal-scroll
    // container, so wide tables scroll inside their own card instead of
    // stretching the whole page (the original horizontal-scroll bug).
    document.querySelectorAll('.main table').forEach(function (table) {
      if (table.closest('.table-scroll') || table.closest('.receipt-table-scroll')) return;
      var wrap = document.createElement('div');
      wrap.className = 'table-scroll';
      table.parentNode.insertBefore(wrap, table);
      wrap.appendChild(table);
    });
  });
})();