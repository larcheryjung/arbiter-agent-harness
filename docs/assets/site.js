// Arbiter Handbook — shared navigation and theme toggle. Pages work without it (plain links, system theme).
(function () {
  var PAGES = [
    ["start", "index.html", "เริ่มที่นี่"],
    ["learn", "concepts.html", "แนวคิดและบทบาท"],
    ["learn", "feature.html", "/feature ทีละขั้น"],
    ["learn", "spike.html", "/spike ทีละขั้น"],
    ["learn", "stuck.html", "เมื่องานติด"],
    ["deep", "safety.html", "อะไรบังคับจริง อะไรเป็นแค่กติกา"],
    ["deep", "cases.html", "กรณีศึกษาจากงานจริง"],
    ["ref", "reference.html", "อ้างอิง: คำสั่ง STATUS ไฟล์"],
    ["ref", "roadmap.html", "สถานะ เวอร์ชัน Roadmap"]
  ];
  var GROUPS = { start: "", learn: "เรียนตามลำดับ", deep: "เข้าใจลึก", ref: "อ้างอิง" };
  var here = location.pathname.split("/").pop() || "index.html";

  var nav = document.querySelector("nav.toc .links");
  if (nav) {
    var html = "", last = null;
    PAGES.forEach(function (p, i) {
      if (p[0] !== last && GROUPS[p[0]]) html += '<div class="group">' + GROUPS[p[0]] + "</div>";
      last = p[0];
      html += '<a href="' + p[1] + '"' + (p[1] === here ? ' class="active" aria-current="page"' : "") +
              '><span class="n">' + i + "</span>" + p[2] + "</a>";
    });
    nav.innerHTML = html;
  }

  var pager = document.querySelector(".pager");
  if (pager) {
    var idx = PAGES.findIndex(function (p) { return p[1] === here; });
    var prev = PAGES[idx - 1], next = PAGES[idx + 1];
    pager.innerHTML = (prev ? '<a href="' + prev[1] + '"><span>← ก่อนหน้า</span>' + prev[2] + "</a>" : "<i></i>") +
                      (next ? '<a href="' + next[1] + '" style="text-align:right"><span>ถัดไป →</span>' + next[2] + "</a>" : "");
  }

  var root = document.documentElement, KEY = "arbiter-theme";
  try { var saved = localStorage.getItem(KEY); if (saved) root.setAttribute("data-theme", saved); } catch (e) {}
  var btn = document.getElementById("themeBtn");
  if (btn) btn.addEventListener("click", function () {
    var dark = root.getAttribute("data-theme") ? root.getAttribute("data-theme") === "dark"
                                               : matchMedia("(prefers-color-scheme: dark)").matches;
    var next = dark ? "light" : "dark";
    root.setAttribute("data-theme", next);
    try { localStorage.setItem(KEY, next); } catch (e) {}
  });
})();
