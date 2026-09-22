(function () {
  var ctx = document.createElement('canvas').getContext('2d');
  function syncWidth(table) {
    var sum = 0;
    table.querySelectorAll('colgroup col').forEach(function (c) {
      sum += c.getBoundingClientRect().width;
    });
    table.style.width = sum + 'px';
  }
  function autofit(table, col, idx) {
    var w = 30;
    table.querySelectorAll('tbody tr').forEach(function (tr) {
      var td = tr.children[idx + 1]; // +1 跳过行号 th
      if (!td || !td.textContent) return;
      var s = getComputedStyle(td);
      ctx.font = s.fontWeight + ' ' + s.fontSize + ' ' + s.fontFamily;
      w = Math.max(w, ctx.measureText(td.textContent).width + 14);
    });
    col.style.width = w + 'px';
    syncWidth(table);
  }
  document.querySelectorAll('table').forEach(function (table) {
    var cols = table.querySelectorAll('colgroup col:not(.row-header-col)');
    table.querySelectorAll('thead .col-header').forEach(function (th, i) {
      var col = cols[i];
      if (!col) return;
      var grip = document.createElement('span');
      grip.style.cssText =
        'position:absolute;right:-4px;top:0;width:9px;height:100%;cursor:col-resize;z-index:5';
      th.style.position = 'relative';
      th.appendChild(grip);
      grip.addEventListener('dblclick', function () { autofit(table, col, i); });
      grip.addEventListener('mousedown', function (e) {
        e.preventDefault();
        var startX = e.clientX;
        var startW = col.getBoundingClientRect().width;
        function move(ev) {
          col.style.width = Math.max(24, startW + ev.clientX - startX) + 'px';
          syncWidth(table);
        }
        function up() {
          document.removeEventListener('mousemove', move);
          document.removeEventListener('mouseup', up);
        }
        document.addEventListener('mousemove', move);
        document.addEventListener('mouseup', up);
      });
    });
  });
})();
