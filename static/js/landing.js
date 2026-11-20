/* Cassilion landing page: comparison table, both quadrant charts, copy button.
   Ported unchanged from the static draft in site/index.html. */
/* ------------------------------------------------------------------
   Single source for the comparison table and both quadrant charts.
   caps: 2 = core engine, 1 = partial / extension / separate service, 0 = no.
   Order of caps follows CAPS. Breadth = sum(caps) / 2.
   speed, backbone, ai are editorial scores (see captions on the page).
------------------------------------------------------------------- */
var CAPS = [
  ["Scale-out operational store", "partitioned, replicated across data centres"],
  ["Columnar analytics engine", "projections and aggregates on live data"],
  ["Full-text search with BM25", "lexical relevance ranking"],
  ["Vector search", "approximate nearest neighbours"],
  ["Fused hybrid ranking", "text and vectors in one ranked query"],
  ["Geospatial types and predicates", "geometry, distance, spatial filters"],
  ["Property graph traversal", "typed edges, paths"],
  ["Rule-based reasoning", "logic rules evaluated in the engine"]
];
var PRODUCTS = [
  {id:"cassilion", name:"Cassilion 1.0",          lab:"Cassilion",          caps:[2,2,2,2,2,2,2,2], speed:4.3, sl:"t", backbone:8.4, ai:8.8, al:"l", me:true},
  {id:"spanner",   name:"Google Spanner",         lab:"Google Spanner",     caps:[2,2,2,2,1,0,2,0], speed:4.2, sl:"r", backbone:9.4, ai:7.0, al:"l"},
  {id:"mongodb",   name:"MongoDB",                lab:"MongoDB",            caps:[2,0,2,2,2,2,1,0], speed:3.0, sl:"r", backbone:7.8, ai:6.0, al:"t"},
  {id:"surreal",   name:"SurrealDB 3",            lab:"SurrealDB",          caps:[1,0,2,2,2,2,2,0], speed:3.5, sl:"r", backbone:5.6, ai:7.2, al:"t"},
  {id:"elastic",   name:"Elasticsearch",          lab:"Elasticsearch",      caps:[1,1,2,2,2,2,0,0], speed:2.7, sl:"l", backbone:6.4, ai:5.6, al:"b"},
  {id:"neo4j",     name:"Neo4j",                  lab:"Neo4j",              caps:[1,0,2,2,1,1,2,0], speed:2.4, sl:"r", backbone:4.6, ai:6.8, al:"l"},
  {id:"vector",    name:"Milvus / Qdrant",        lab:"Vector DBs",         caps:[1,0,2,2,2,1,0,0], speed:4.0, sl:"l", backbone:5.2, ai:4.4, al:"b"},
  {id:"postgres",  name:"PostgreSQL + extensions",lab:"PostgreSQL + ext.",  caps:[1,1,1,1,1,1,1,0], speed:3.0, sl:"l", backbone:5.2, ai:5.8, al:"l"},
  {id:"scylla",    name:"ScyllaDB",               lab:"ScyllaDB",           caps:[2,0,2,2,1,0,0,0], speed:4.6, sl:"r", backbone:9.4, ai:4.0, al:"l"},
  {id:"cassandra", name:"Apache Cassandra 5.0",   lab:"Apache Cassandra 5", caps:[2,0,0,2,0,0,0,0], speed:2.7, sl:"l", backbone:9.2, ai:2.6, al:"l"}
];
PRODUCTS.forEach(function(p){ p.breadth = p.caps.reduce(function(a,b){return a+b;},0)/2; });

/* ---------- table ---------- */
(function(){
  var t = document.getElementById("matrix");
  var word = ["no","partial","yes"];
  var h = "<thead><tr><th scope=\"col\">Capability</th>";
  PRODUCTS.forEach(function(p){ h += "<th scope=\"col\""+(p.me?" class=\"me\"":"")+">"+p.name+"</th>"; });
  h += "</tr></thead><tbody>";
  CAPS.forEach(function(c,i){
    h += "<tr><th scope=\"row\">"+c[0]+"<small>"+c[1]+"</small></th>";
    PRODUCTS.forEach(function(p){
      var v = p.caps[i];
      h += "<td"+(p.me?" class=\"me\"":"")+"><i class=\"m m-"+v+"\"></i><span class=\"sr\">"+word[v]+"</span></td>";
    });
    h += "</tr>";
  });
  h += "<tr class=\"score\"><th scope=\"row\">Breadth score<small>of 8, partial counts half</small></th>";
  PRODUCTS.forEach(function(p){ h += "<td"+(p.me?" class=\"me\"":"")+">"+p.breadth.toFixed(1)+"</td>"; });
  h += "</tr></tbody>";
  t.insertAdjacentHTML("beforeend", h);
})();

/* ---------- charts ---------- */
var NS = "http://www.w3.org/2000/svg";
function S(tag, attrs, text){
  var e = document.createElementNS(NS, tag);
  for (var k in attrs) e.setAttribute(k, attrs[k]);
  if (text != null) e.textContent = text;
  return e;
}
function drawChart(id, cfg){
  var svg = document.getElementById(id);
  var L = 58, R = 540, T = 22, B = 404;
  function X(v){ return L + (v - cfg.x.min) / (cfg.x.max - cfg.x.min) * (R - L); }
  function Y(v){ return B - (v - cfg.y.min) / (cfg.y.max - cfg.y.min) * (B - T); }
  var mx = X(cfg.x.mid), my = Y(cfg.y.mid);

  svg.appendChild(S("rect", {x:L, y:T, width:R-L, height:B-T, rx:8, "class":"c-frame"}));
  svg.appendChild(S("rect", {x:mx, y:T+1, width:R-mx-1, height:my-T-1, "class":"c-tint"}));
  svg.appendChild(S("line", {x1:mx, y1:T, x2:mx, y2:B, "class":"c-mid"}));
  svg.appendChild(S("line", {x1:L, y1:my, x2:R, y2:my, "class":"c-mid"}));

  svg.appendChild(S("text", {x:L+10, y:T+17, "class":"c-q"}, cfg.q[0]));
  svg.appendChild(S("text", {x:mx+10, y:T+17, "class":"c-q"}, cfg.q[1]));
  svg.appendChild(S("text", {x:L+10, y:B-10, "class":"c-q"}, cfg.q[2]));
  svg.appendChild(S("text", {x:mx+10, y:B-10, "class":"c-q"}, cfg.q[3]));

  (cfg.x.ticks || []).forEach(function(v){
    svg.appendChild(S("text", {x:X(v), y:B+16, "text-anchor":"middle", "class":"c-tick"}, String(v)));
  });
  svg.appendChild(S("text", {x:(L+R)/2, y:B+38, "text-anchor":"middle", "class":"c-axis"}, cfg.x.title));
  svg.appendChild(S("text", {x:20, y:(T+B)/2, "text-anchor":"middle", transform:"rotate(-90 20 "+((T+B)/2)+")", "class":"c-axis"}, cfg.y.title));

  var pts = PRODUCTS.slice().sort(function(a,b){ return (a.me?1:0) - (b.me?1:0); });
  pts.forEach(function(p){
    var x = X(cfg.px(p)), y = Y(cfg.py(p)), pos = cfg.pos(p);
    var g = S("g", {});
    g.appendChild(S("title", {}, p.name + ": " + cfg.tip(p)));
    if (p.me){
      g.appendChild(S("circle", {cx:x, cy:y, r:17, "class":"c-me-halo"}));
      g.appendChild(S("circle", {cx:x, cy:y, r:8.5, "class":"c-me-ring"}));
      g.appendChild(S("circle", {cx:x, cy:y, r:2.8, "class":"c-me-core"}));
      if (pos === "t"){
        g.appendChild(S("text", {x:x+17, y:y-36, "text-anchor":"end", "class":"c-me-lab"}, p.lab));
        g.appendChild(S("text", {x:x+17, y:y-23, "text-anchor":"end", "class":"c-me-sub"}, "design target"));
      } else {
        g.appendChild(S("text", {x:x-22, y:y+1, "text-anchor":"end", "class":"c-me-lab"}, p.lab));
        g.appendChild(S("text", {x:x-22, y:y+14, "text-anchor":"end", "class":"c-me-sub"}, "design target"));
      }
    } else {
      g.appendChild(S("circle", {cx:x, cy:y, r:6, "class":"c-dot"}));
      var a = {"class":"c-lab"};
      if (pos === "r"){ a.x = x+11; a.y = y+4; a["text-anchor"] = "start"; }
      else if (pos === "l"){ a.x = x-11; a.y = y+4; a["text-anchor"] = "end"; }
      else if (pos === "t"){ a.x = x; a.y = y-12; a["text-anchor"] = "middle"; }
      else { a.x = x; a.y = y+21; a["text-anchor"] = "middle"; }
      g.appendChild(S("text", a, p.lab));
    }
    svg.appendChild(g);
  });
}

drawChart("chart-speed", {
  x:{min:0, max:8.4, mid:4, ticks:[0,2,4,6,8], title:"Capabilities in one engine (of 8) →"},
  y:{min:0.8, max:5.2, mid:3.25, title:"Expected speed (performance architecture) →"},
  q:["Specialised speed","Breadth at speed","Single purpose","Breadth with trade-offs"],
  px:function(p){ return p.breadth; }, py:function(p){ return p.speed; }, pos:function(p){ return p.sl; },
  tip:function(p){ return "breadth " + p.breadth.toFixed(1) + " of 8"; }
});
drawChart("chart-ai", {
  x:{min:0, max:10, mid:5, title:"Operational backbone: ingest, scale, resilience →"},
  y:{min:0, max:10, mid:5, title:"AI capability: retrieve, relate, reason, trace →"},
  q:["AI features first","AI-ready system of record","Niche","Scale first"],
  px:function(p){ return p.backbone; }, py:function(p){ return p.ai; }, pos:function(p){ return p.al; },
  tip:function(p){ return "our assessment, November 2026"; }
});

/* ---------- copy address ---------- */
(function(){
  var b = document.getElementById("copy-mail");
  if (!b) return;
  b.addEventListener("click", function(){
    var done = function(msg){ b.textContent = msg; setTimeout(function(){ b.textContent = "Copy address"; }, 1800); };
    try {
      navigator.clipboard.writeText(b.getAttribute("data-copy")).then(function(){ done("Copied"); }, function(){ done("Select the address to copy"); });
    } catch (e) { done("Select the address to copy"); }
  });
})();

/* ---------- hero background video ----------
   Plays only while the hero is on screen, and not at all when the visitor asked for
   reduced motion or data saving; then the poster frame stays. */
(function(){
  var v = document.getElementById("hero-globe");
  if (!v || !v.play) return;
  var calm = window.matchMedia ? window.matchMedia("(prefers-reduced-motion: reduce)") : null;
  var saving = navigator.connection && navigator.connection.saveData;
  var visible = true;
  function sync(){
    if (saving || (calm && calm.matches) || !visible || document.hidden) { v.pause(); return; }
    var p = v.play();
    if (p && p.catch) p.catch(function(){});
  }
  if ("IntersectionObserver" in window) {
    new IntersectionObserver(function(entries){ visible = entries[0].isIntersecting; sync(); }).observe(v);
  }
  if (calm && calm.addEventListener) calm.addEventListener("change", sync);
  document.addEventListener("visibilitychange", sync);
  sync();
})();
