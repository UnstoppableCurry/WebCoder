(function () {
  var header = document.querySelector(".site-header");
  var toggle = document.querySelector(".nav-toggle");
  var nav = document.getElementById("site-nav");
  if (header && toggle && nav) {
    toggle.addEventListener("click", function () {
      var open = header.classList.toggle("is-open");
      toggle.setAttribute("aria-expanded", open ? "true" : "false");
    });
  }

  var path = location.pathname.replace(/\/+$/, "");
  var file = path.split("/").pop() || "index.html";
  if (file === "WebCoder" || file === "") file = "index.html";
  document.querySelectorAll('.site-nav a[href]').forEach(function (link) {
    var href = link.getAttribute("href");
    if (href === file || (file === "index.html" && href === "./")) {
      link.setAttribute("aria-current", "page");
    }
  });
})();
