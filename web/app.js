/* CampusPulse front-end. Plain JS, no build step, no framework, no image assets.
   Light mint-on-cream UI. The app only ever receives seat states and counts -
   never a frame, never a face. Nothing pops up unless the user taps something. */

(function () {
  "use strict";

  var LEVEL = { low: "quiet", medium: "filling", high: "busy", packed: "packed" };
  var LEVEL_TONE = { low: "tone-good", medium: "tone-warn", high: "tone-warn", packed: "tone-bad" };
  var LEVEL_COLOUR = { low: "#cfdcc9", medium: "#eddfc4", high: "#e6cdb2", packed: "#e0bfc2" };
  var LEVEL_RANK = { low: 0, medium: 1, high: 2, packed: 3 };
  var DAY_FACTOR = { Mon: 1.0, Tue: 0.98, Wed: 1.02, Thu: 0.97, Fri: 0.95, Sat: 0.72, Sun: 0.5 };

  var state = {
    snap: null,
    screen: "home",
    mode: "student",
    venueId: null,
    groupSize: null,
    matters: ["closest"],
    started: false,
    showAll: false,
    lastAt: {},
    menuOpen: false,
    chatBusy: false,
    chat: [],
    chips: [],
    squad: null,
    notify: { rush: true, ghost: true },
    tm: {},
    dragging: false,
    sheetOpen: false,
    insightVenue: null,
    pending: false,
  };

  function $(sel, root) { return (root || document).querySelector(sel); }
  function $(all, root) { return Array.prototype.slice.call((root || document).querySelectorAll(all)); }
  function el(id) { return document.getElementById(id); }
  function esc(value) {
    return String(value == null ? "" : value).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function pct(a, b) { return b > 0 ? Math.round((a / b) * 100) : 0; }

  function api(path, body) {
    var opts = body
      ? { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }
      : undefined;
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (data) {
        if (!r.ok) {
          throw new Error((data && data.error) ? data.error : "request failed (" + r.status + ")");
        }
        return data;
      });
    });
  }

  /* ------------------------------------------------------------------ */
  /* group finder: one table first, then the tightest cluster            */
  /* ------------------------------------------------------------------ */
  function dist(a, b) { return Math.hypot(a.x - b.x, a.y - b.y); }

  function findGroup(venue, n) {
    if (!n || !venue) return null;
    var avail = venue.seats.filter(function (s) {
      return s.state === "free" || s.state === "probably_free";
    });
    if (avail.length < n) return null;

    var byTable = {};
    avail.forEach(function (s) { (byTable[s.table] = byTable[s.table] || []).push(s); });

    var whole = null;
    Object.keys(byTable).forEach(function (t) {
      var list = byTable[t];
      if (list.length < n) return;
      var picked = list.slice(0, n);
      var spread = 0;
      picked.forEach(function (a) { picked.forEach(function (b) { spread = Math.max(spread, dist(a, b)); }); });
      if (!whole || spread < whole.spread) whole = { table: t, seats: picked, spread: spread, same: true };
    });
    if (whole) return whole;

    var best = null;
    avail.forEach(function (anchor) {
      var picked = avail.slice().sort(function (p, q) { return dist(anchor, p) - dist(anchor, q); }).slice(0, n);
      var spread = 0;
      picked.forEach(function (s) { spread = Math.max(spread, dist(anchor, s)); });
      if (!best || spread < best.spread) best = { table: null, seats: picked, spread: spread, same: false };
    });
    return best;
  }

  function rushHint(venue) {
    var s = state.snap;
    if (!venue.forecast || venue.forecast.length < 2 || !s) return "";
    for (var i = 1; i < venue.forecast.length; i += 1) {
      var f = venue.forecast[i];
      if (f.free <= Math.max(1, venue.free * 0.6)) {
        var mins = Math.round((f.t - s.clock.seconds) / 60);
        if (mins > 4) return "fills up in about " + mins + " min";
        break;
      }
    }
    return "";
  }

  function wants(key) { return state.matters.indexOf(key) >= 0; }

  /* Every selected preference counts. "Closest and quietest" must not silently
     behave like "closest", which is what a chain of sort() calls did. */
  function scoreVenue(v) {
    var score = v.free * 0.6;
    if (wants("closest")) score -= v.walk_min * 3.0;
    if (wants("quiet")) {
      score -= (LEVEL_RANK[v.crowd_level] || 0) * 6;
      score -= (1 - (v.free / Math.max(1, v.capacity))) * 4;
    }
    if (wants("queue")) score -= v.wait_minutes * 1.6 + v.queue * 0.3;
    return score;
  }

  function sortedVenues(list) {
    return list.slice().sort(function (a, b) {
      return (scoreVenue(b) - scoreVenue(a)) || (b.free - a.free) || (a.walk_min - b.walk_min);
    });
  }

  function bestVenue() {
    var s = state.snap;
    if (!s) return null;
    var pool = s.venues.filter(function (v) { return v.kind !== "Library"; });
    if (state.groupSize) {
      var fits = pool.filter(function (v) { return findGroup(v, state.groupSize); });
      if (fits.length) pool = fits;
    }
    return sortedVenues(pool)[0] || null;
  }

  /* ------------------------------------------------------------------ */
  /* small soft graphics                                                 */
  /* ------------------------------------------------------------------ */
  function freeRing(free, total) {
    var r = 62, c = 2 * Math.PI * r;
    var ratio = total ? free / total : 0;
    var colour = ratio > 0.5 ? "#b9cfb2" : ratio > 0.28 ? "#e3cfa9" : ratio > 0.1 ? "#dcbfa4" : "#dcb0b6";
    return '<svg viewBox="0 0 150 150" aria-hidden="true">' +
      '<circle cx="75" cy="75" r="' + r + '" fill="none" stroke="#efe9dd" stroke-width="12"/>' +
      '<circle cx="75" cy="75" r="' + r + '" fill="none" stroke="' + colour + '" stroke-width="12" stroke-linecap="round" ' +
      'stroke-dasharray="' + c.toFixed(1) + '" stroke-dashoffset="' + (c * (1 - ratio)).toFixed(1) + '"/></svg>';
  }

  function forecastChart(points, highlight) {
    if (!points || points.length < 2) return "";
    var W = 560, H = 110, pad = 10;
    var max = Math.max.apply(null, points.map(function (p) { return p.free; }).concat([1]));
    var xs = points.map(function (_, i) { return pad + (i / (points.length - 1)) * (W - pad * 2); });
    var ys = points.map(function (p) { return H - pad - (p.free / max) * (H - pad * 2); });
    var line = xs.map(function (x, i) { return (i ? "L" : "M") + x.toFixed(1) + "," + ys[i].toFixed(1); }).join(" ");
    var area = line + " L" + xs[xs.length - 1].toFixed(1) + "," + (H - pad) + " L" + xs[0].toFixed(1) + "," + (H - pad) + " Z";
    var dots = points.map(function (_, i) {
      var on = i === highlight;
      return '<circle cx="' + xs[i].toFixed(1) + '" cy="' + ys[i].toFixed(1) + '" r="' + (on ? 5 : 3) +
        '" fill="' + (on ? "#7d6039" : "#ffffff") + '" stroke="#c3ab84" stroke-width="1.8"/>';
    }).join("");
    var marker = highlight >= 0
      ? '<line x1="' + xs[highlight].toFixed(1) + '" y1="' + pad + '" x2="' + xs[highlight].toFixed(1) +
        '" y2="' + (H - pad) + '" stroke="#e0d8c8" stroke-dasharray="4 4"/>'
      : "";
    return '<svg class="forecast" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<defs><linearGradient id="fg" x1="0" y1="0" x2="0" y2="1">' +
      '<stop offset="0%" stop-color="#efe3cf"/><stop offset="100%" stop-color="#ffffff" stop-opacity="0"/>' +
      "</linearGradient></defs>" +
      '<path d="' + area + '" fill="url(#fg)"/>' +
      '<path d="' + line + '" fill="none" stroke="#c3ab84" stroke-width="3" stroke-linejoin="round" stroke-linecap="round"/>' +
      marker + dots + "</svg>";
  }

  function seatMap(venue, groupIds) {
    var W = 720, H = 420;
    var plan = venue.plan || {};
    var cols = plan.cols || 4, rows = plan.rows || 3;
    var unit = Math.min(W / cols, H / rows);

    var fill = {
      free: "#e7efe4", busy: "#eee9e0", held: "#f7ead6", probably_free: "#e7ecf4", unknown: "#efe9dd",
    };
    var stroke = {
      free: "#b9cfb2", busy: "#cfc5b4", held: "#dcc09a", probably_free: "#b9c7db", unknown: "#d8cfbf",
    };

    var byTable = {};
    venue.seats.forEach(function (s) { (byTable[s.table] = byTable[s.table] || []).push(s); });

    var tables = Object.keys(byTable).map(function (t) {
      var seats = byTable[t];
      var cx = (seats.reduce(function (a, s) { return a + s.x; }, 0) / seats.length) * W;
      var cy = (seats.reduce(function (a, s) { return a + s.y; }, 0) / seats.length) * H;
      var size = unit * 0.44;
      return '<rect x="' + (cx - size / 2).toFixed(1) + '" y="' + (cy - size / 2).toFixed(1) +
        '" width="' + size.toFixed(1) + '" height="' + size.toFixed(1) + '" rx="' + (size * 0.3).toFixed(1) +
        '" fill="#faf7f0" stroke="#ece6da"/>';
    }).join("");

    var seats = venue.seats.map(function (s) {
      var cx = s.x * W, cy = s.y * H, r = Math.max(7, unit * 0.16);
      var inGroup = groupIds && groupIds.indexOf(s.id) >= 0;
      var state_ = fill[s.state] ? s.state : "unknown";
      var ghost = s.state === "held" || s.state === "probably_free";
      return '<g class="seat" data-act="seat" data-id="' + esc(s.id) + '">' +
        (inGroup ? '<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="' + (r * 2).toFixed(1) +
          '" fill="#e7efe4" stroke="#b9cfb2" stroke-width="2"/>' : "") +
        '<circle cx="' + cx.toFixed(1) + '" cy="' + cy.toFixed(1) + '" r="' + r.toFixed(1) +
        '" fill="' + fill[state_] + '" stroke="' + stroke[state_] + '" stroke-width="2">' +
        "<title>" + esc(s.id + " - " + s.state.replace("_", " ") +
          (ghost ? " for " + Math.round(s.ghost_age / 60) + " min" : "") +
          (s.live ? " - watched by a camera" : "")) + "</title></circle>" +
        (s.live ? '<circle cx="' + (cx + r * 1.2).toFixed(1) + '" cy="' + (cy - r * 1.2).toFixed(1) +
          '" r="' + Math.max(3.4, r * 0.34).toFixed(1) + '" fill="#b6c6db"/>' : "") +
        "</g>";
    }).join("");

    return '<svg class="plan" viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="xMidYMid meet" role="img" ' +
      'aria-label="Seat map: green is free, pink is taken">' +
      '<rect x="1" y="1" width="' + (W - 2) + '" height="' + (H - 2) + '" rx="16" fill="#fdfcf9" stroke="#ece6da"/>' +
      tables + seats + "</svg>";
  }

  /* ------------------------------------------------------------------ */
  /* HOME                                                                */
  /* ------------------------------------------------------------------ */
  function prefsPanel() {
    var sizes = [1, 2, 3, 4, 5, 6, 7, 8].map(function (n) {
      return '<button class="seat-num' + (state.groupSize === n ? " is-on" : "") +
        '" data-act="group" data-n="' + n + '" aria-pressed="' + (state.groupSize === n) + '">' + n + "</button>";
    }).join("");
    var matters = [
      ["closest", "Closest to me"],
      ["quiet", "Quietest"],
      ["queue", "No queue"],
    ].map(function (pair) {
      var on = wants(pair[0]);
      return '<button class="chip' + (on ? " is-on" : "") + '" data-act="matters" data-key="' +
        pair[0] + '" aria-pressed="' + on + '">' + pair[1] + "</button>";
    }).join("");

    var ready = !!state.groupSize;
    return '<div class="prefs">' +
      '<p class="field-label">How many of you?</p>' +
      '<div class="seat-grid">' + sizes + "</div>" +
      '<p class="field-label">What matters most?</p>' +
      '<div class="chip-row">' + matters + "</div>" +
      '<div class="cta-row"><button class="btn primary" data-act="prefs-done"' +
      (ready ? "" : " disabled") + ">Show me where to go</button>" +
      '<button class="btn ghost" data-act="prefs-skip">Just browsing</button></div>' +
      "</div>";
  }

  function ghostSection(venues) {
    var rows = [];
    venues.forEach(function (v) {
      v.ghosts.forEach(function (g) {
        var mins = Math.floor(g.ghost_age / 60);
        var secs = Math.floor(g.ghost_age % 60);
        var ripe = g.state === "probably_free";
        rows.push('<div class="ghost-row">' +
          '<span class="chip ghost">' + esc(g.id) + "</span>" +
          "<div><div style=\"font-weight:600;color:var(--ink)\">" + esc(v.name) + "</div>" +
          '<div class="note">bag or laptop left behind, nobody nearby</div></div>' +
          '<div class="spacer"></div>' +
          '<div class="ghost-timer' + (ripe ? " ripe" : "") + '">' + mins + "m " +
          String(secs).padStart(2, "0") + "s</div>" +
          "</div>");
      });
    });
    if (!rows.length) {
      return '<p class="section-title">Ghost seats</p><div class="empty">' +
        "None right now. When a bag or laptop sits alone for 20+ minutes, it appears here with a live timer " +
        "and we mark it probably free.</div>";
    }
    return '<p class="section-title">Ghost seats, live timers</p><div class="panel">' + rows.join("") +
      '<p class="note">A seat counts as abandoned only after the item sits alone past the venue\'s ghost ' +
      "threshold (20 minutes in real use). Until then it is held, not free.</p></div>";
  }

  function venueCard(v) {
    var group = state.groupSize ? findGroup(v, state.groupSize) : null;
    var bits = [esc(v.kind), v.walk_min + " min walk"];
    bits.push(v.queue ? v.queue + " in queue" : "no queue");
    bits.push(LEVEL[v.crowd_level]);
    if (state.groupSize && group && group.same) bits.push("a table fits " + state.groupSize);
    var rush = rushHint(v);
    if (rush) bits.push(rush);

    return '<button class="row" data-act="venue" data-id="' + esc(v.id) + '">' +
      '<i class="row-dot accent-' + esc(v.accent) + '"></i>' +
      '<span class="row-main"><span class="row-title">' + esc(v.name) + "</span>" +
      '<span class="row-sub">' + bits.join(" &middot; ") + "</span></span>" +
      '<span class="row-value"><b>' + v.free + "</b><span>free</span></span>" +
      '<span class="row-chev">&rsaquo;</span></button>';
  }

  function sparkline(points) {
    if (!points || points.length < 3) return "";
    var W = 300, H = 40, top = 6;
    var max = Math.max.apply(null, points.concat([1]));
    var d = points.map(function (p, i) {
      var x = (i / (points.length - 1)) * W;
      var y = H - top - (p / max) * (H - top * 2);
      return (i ? "L" : "M") + x.toFixed(1) + "," + y.toFixed(1);
    }).join(" ");
    return '<svg viewBox="0 0 ' + W + " " + H + '" preserveAspectRatio="none" aria-hidden="true">' +
      '<path d="' + d + '" fill="none" stroke="#c3ab84" stroke-width="2.4" ' +
      'stroke-linejoin="round" stroke-linecap="round"/></svg>';
  }

  function prefLabel() {
    var names = { closest: "closest", quiet: "quietest", queue: "no queue" };
    return state.matters.map(function (k) { return names[k]; }).filter(Boolean).join(" + ") || "anything";
  }

  function moreSection() {
    var s = state.snap;
    var food = s.venues.filter(function (v) { return v.kind !== "Library"; });
    var study = s.venues.filter(function (v) { return v.kind === "Library"; });
    var totalFree = s.venues.reduce(function (a, v) { return a + v.free; }, 0);
    var totalSeats = s.venues.reduce(function (a, v) { return a + v.capacity; }, 0);

    return '<div class="more">' +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Campus right now</span>' +
      '<div class="spacer"></div><span class="note">' + esc(s.clock.label) + " - " +
      pct(totalFree, totalSeats) + "% free</span></div>" +
      '<div class="rec-stats" style="margin:4px 0 0">' +
      '<div class="metric"><b>' + totalFree + "</b><span>free seats</span></div>" +
      '<div class="metric"><b>' + s.venues.length + "</b><span>places live</span></div>" +
      "</div></div>" +
      '<p class="section-title">Food and drink near you</p>' +
      sortedVenues(food).map(venueCard).join("") +
      (study.length ? '<p class="section-title">Study spaces</p>' + sortedVenues(study).map(venueCard).join("") : "") +
      ghostSection(s.venues) +
      '<div class="more-actions">' +
      '<button class="btn soft" data-act="nav" data-screen="map">Campus map</button>' +
      '<button class="btn soft" data-act="nav" data-screen="alerts">Alerts</button></div>' +
      '<p class="note" style="margin-top:26px">Cameras see the room; this app never does. Frames are read on the ' +
      "device and thrown away in the same instant, so only seat states, timers and counts reach this screen.</p>" +
      "</div>";
  }

  function microLine(v, s) {
    var bw = v.best_window;
    var later = bw && bw.in_minutes >= 5 && bw.free > v.free + 1;
    return "Today at " + esc(s.clock.day) + ": " +
      (later ? "quietest around <b>" + esc(bw.label) + "</b>" : "steady for the next two hours") +
      " - " + v.counts.busy + " of " + v.capacity + " seats taken right now";
  }

  /* The snapshot arrives every second. Replacing the whole subtree each time
     made the page repaint (and re-run entrance animations) at 1 Hz, which read
     as blinking. The skeleton is built once; only the numbers move. */
  function patchRecommend(holder, v, bits) {
    var s = state.snap;
    var line = holder.querySelector('[data-live="line"]');
    if (line) line.textContent = bits.join(" - ");
    var free = holder.querySelector('[data-live="free"]');
    if (free) free.textContent = v.free;
    var wait = holder.querySelector('[data-live="wait"]');
    if (wait) wait.textContent = v.queue ? Math.round(v.wait_minutes) + "m" : "0m";
    var walk = holder.querySelector('[data-live="walk"]');
    if (walk) walk.textContent = v.walk_min + "m";
    var micro = holder.querySelector('[data-live="micro"]');
    if (micro) micro.innerHTML = microLine(v, s);

    var row = holder.querySelector(".spark-row");
    if (row) {
      var fresh = sparkline(v.sparkline);
      var old = row.querySelector("svg");
      if (old && fresh) old.outerHTML = fresh;
      else if (fresh) row.insertAdjacentHTML("afterbegin", fresh);
    }
    var more = holder.querySelector(".more");
    if (more) more.outerHTML = moreSection();
  }

  function renderRecommend() {
    var s = state.snap;
    if (!s) return;
    var v = bestVenue();
    if (!v) {
      el("recommend-body").innerHTML = '<div class="err">No places are open right now.</div>';
      return;
    }

    var group = state.groupSize ? findGroup(v, state.groupSize) : null;
    var bits = [v.free + " seats free"];
    bits.push(v.queue ? "queue " + Math.round(v.wait_minutes) + " min" : "no queue");
    bits.push(v.walk_min + " min walk");
    if (state.groupSize) {
      bits.push(group ? "table for " + state.groupSize : "no table for " + state.groupSize + " yet");
    }

    var holder = el("recommend-body");
    var key = [v.id, state.groupSize || 0, state.showAll ? 1 : 0,
      state.matters.join(","), s.clock.day].join("|");
    if (holder.dataset.key === key) {
      patchRecommend(holder, v, bits);
      return;
    }
    holder.dataset.key = key;
    holder.innerHTML = '<div class="rec">' +
      '<p class="rec-kicker">' + (state.groupSize ? "For " + state.groupSize + (state.groupSize > 1 ? " of you" : "") : "For you") +
      " - " + esc(prefLabel()) + "</p>" +
      '<h1 class="rec-name">' + esc(v.name) + "</h1>" +
      '<p class="rec-line" data-live="line">' + esc(bits.join(" - ")) + "</p>" +
      '<div class="rec-stats">' +
      '<div class="metric"><b data-live="free">' + v.free + "</b><span>seats free</span></div>" +
      '<div class="metric"><b data-live="wait">' + (v.queue ? Math.round(v.wait_minutes) + "m" : "0m") +
      "</b><span>queue wait</span></div>" +
      '<div class="metric"><b data-live="walk">' + v.walk_min + "m</b><span>walk</span></div>" +
      "</div>" +
      '<div class="cta-row rec-actions">' +
      '<button class="btn primary" data-act="venue" data-id="' + esc(v.id) + '">See the seat map</button>' +
      '<button class="btn ghost" data-act="ask-why" data-id="' + esc(v.id) + '">Ask why</button></div>' +
      '<p class="rec-micro" data-live="micro">' + microLine(v, s) + "</p>" +
      '<div class="spark-row">' + sparkline(v.sparkline) +
      '<span class="note">last few minutes</span></div>' +
      '<button class="expand' + (state.showAll ? " is-open" : "") + '" data-act="expand">' +
      '<span class="arrow"><svg viewBox="0 0 24 24" width="15" height="15" fill="none" ' +
      'stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round">' +
      '<path d="M12 5v14M6 13l6 6 6-6"/></svg></span>' +
      (state.showAll ? "Hide the rest of campus" : "Click to see more available seats on campus right now") +
      "</button>" +
      (state.showAll ? moreSection() : "") +
      "</div>";
  }

  function renderHome() {
    el("home-body").innerHTML = prefsPanel();
  }

  /* ------------------------------------------------------------------ */
  /* CAMPUS MAP                                                          */
  /* ------------------------------------------------------------------ */
  function renderMap() {
    var s = state.snap;
    var pins = s.venues.map(function (v) {
      var x = (v.map.x || 50) * 4, y = (v.map.y || 50) * 3;
      var colour = LEVEL_COLOUR[v.crowd_level];
      return '<g class="map-pin" data-act="venue" data-id="' + esc(v.id) + '" transform="translate(' + x.toFixed(1) +
        ", " + y.toFixed(1) + ')">' +
        '<circle r="20" fill="' + colour + '" opacity="0.16"/>' +
        '<circle r="11" fill="' + colour + '"/>' +
        '<text y="-26" text-anchor="middle" font-size="13" font-weight="800" fill="#22302e">' + v.free + "</text>" +
        '<text y="32" text-anchor="middle" font-size="9.5" font-weight="700" fill="#93a09c">' +
        esc(v.name.split(",")[0].split(" ").slice(0, 2).join(" ")) + "</text></g>";
    }).join("");

    var key = Object.keys(LEVEL).map(function (k) {
      return '<span><i style="background:' + LEVEL_COLOUR[k] + '"></i>' + LEVEL[k] + "</span>";
    }).join("");

    el("map-body").innerHTML =
      '<div class="map-card"><svg viewBox="0 0 400 300" width="100%" role="img" ' +
      'aria-label="Campus map with live free-seat counts">' +
      '<defs><pattern id="grid" width="20" height="20" patternUnits="userSpaceOnUse">' +
      '<path d="M20 0H0v20" fill="none" stroke="#ece6da" stroke-width="0.8"/></pattern></defs>' +
      '<rect width="400" height="300" rx="14" fill="#fdfcf9"/>' +
      '<rect width="400" height="300" rx="14" fill="url(#grid)"/>' +
      '<path d="M0 128h400" stroke="#f4efe5" stroke-width="16"/>' +
      '<path d="M172 0v300" stroke="#f4efe5" stroke-width="14"/>' +
      '<g fill="#faf7f0" stroke="#ece6da">' +
      '<rect x="26" y="34" width="112" height="66" rx="10"/>' +
      '<rect x="204" y="26" width="150" height="74" rx="10"/>' +
      '<rect x="206" y="150" width="132" height="66" rx="10"/>' +
      '<rect x="36" y="152" width="104" height="58" rx="10"/>' +
      '<rect x="192" y="234" width="160" height="48" rx="10"/></g>' +
      '<g fill="#93a09c" font-size="10" font-weight="700">' +
      '<text x="38" y="54">Academic Block</text><text x="216" y="46">Engineering</text>' +
      '<text x="218" y="168">Market Road</text><text x="48" y="172">Library</text>' +
      '<text x="204" y="252">Sports Ground</text></g>' +
      '<g fill="#e6e0d0"><circle cx="150" cy="222" r="7"/><circle cx="120" cy="238" r="5"/>' +
      '<circle cx="352" cy="132" r="6"/><circle cx="164" cy="70" r="6"/></g>' +
      pins + "</svg></div>" +
      '<div class="map-key">' + key + "</div>" +
      '<p class="section-title">Nearest first</p>' +
      '<div class="pin-list">' + sortedVenues(s.venues).map(function (v) {
        return '<button class="row" data-act="venue" data-id="' + esc(v.id) + '">' +
          '<i class="row-dot accent-' + esc(v.accent) + '"></i>' +
          '<span class="row-main"><span class="row-title">' + esc(v.name) + "</span>" +
          '<span class="row-sub">' + v.walk_min + " min walk &middot; " + v.distance_m + " m &middot; open " +
          esc(v.open) + "</span></span>" +
          '<span class="row-value"><b>' + v.free + "</b><span>free</span></span>" +
          '<span class="row-chev">&rsaquo;</span></button>';
      }).join("") + "</div>";
  }

  /* ------------------------------------------------------------------ */
  /* VENUE                                                               */
  /* ------------------------------------------------------------------ */
  function renderVenue() {
    var s = state.snap;
    var v = s.venues.filter(function (x) { return x.id === state.venueId; })[0] || s.venues[0];
    if (!v) return;
    state.venueId = v.id;

    var group = state.groupSize ? findGroup(v, state.groupSize) : null;
    var groupIds = group ? group.seats.map(function (x) { return x.id; }) : [];
    var index = Math.min(state.tm[v.id] || 0, v.forecast.length - 1);
    var point = v.forecast[index] || v.forecast[0];

    var ghostRows = v.ghosts.length ? v.ghosts.map(function (g) {
      var ripe = g.state === "probably_free";
      return '<div class="ghost-row">' +
        '<span class="chip ghost">' + esc(g.id) + "</span>" +
        "<div><div style=\"font-weight:600;color:var(--ink)\">" + (ripe ? "Probably free" : "Held by an item") + "</div></div>" +
        '<div class="spacer"></div>' +
        '<div class="ghost-timer' + (ripe ? " ripe" : "") + '">' + Math.floor(g.ghost_age / 60) + "m " +
        String(Math.floor(g.ghost_age % 60)).padStart(2, "0") + "s</div></div>";
    }).join("") : '<p class="note">No abandoned seats here right now.</p>';

    el("venue-body").innerHTML =
      '<button class="back" data-act="nav" data-screen="recommend">&lsaquo; Back</button>' +
      '<div class="venue-head"><div>' +
      '<p class="eyebrow">' + esc(v.kind) + " - " + esc(v.area) + "</p>" +
      "<h2>" + esc(v.name) + "</h2>" +
      '<p class="note">' + v.walk_min + " min walk - " + v.distance_m + " m - open " + esc(v.open) + "</p>" +
      '<div class="vc-tags">' +
      (v.live_seats ? '<span class="chip live">' + v.live_seats + " of " + v.capacity + " seats from a live camera</span>" : "") +
      (v.ghosts.length ? '<span class="chip ghost">' + v.ghosts.length + " ghost</span>" : "") +
      '<span class="chip ' + (v.crowd_level === "low" ? "good" : "warn") + '">' + LEVEL[v.crowd_level] + "</span></div>" +
      '<div class="stat-grid" style="margin-top:22px">' +
      '<div class="stat"><b>' + v.queue + "</b><span>in the queue</span></div>" +
      '<div class="stat"><b>' + (v.queue ? Math.round(v.wait_minutes) + " min" : "none") + "</b><span>wait now</span></div>" +
      '<div class="stat"><b>' + v.counts.busy + " of " + v.capacity + "</b><span>seats taken</span></div>" +
      "</div></div>" +
      '<div class="ring-wrap">' + freeRing(v.free, v.capacity) +
      '<div class="ring-label"><b>' + v.free + "</b><span>seats free</span></div></div></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Best time to go</span>' +
      '<div class="spacer"></div><span class="chip good">' +
      (v.best_window ? "quietest around " + esc(v.best_window.label) : "steady today") + "</span></div>" +
      forecastChart(v.forecast, index) +
      '<input type="range" min="0" max="' + Math.max(0, v.forecast.length - 1) + '" value="' + index +
      '" data-act="tm" data-id="' + esc(v.id) + '" aria-label="Projected free seats over the next two hours"/>' +
      '<div class="tm-row"><span>' + (point ? esc(point.label) + " - " + point.free + " free seats" : "") +
      "</span><span>drag to look ahead</span></div></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Seats together</span>' +
      (group ? '<div class="spacer"></div><span class="note">' +
        (group.same ? "all at one table" : "closest cluster") + "</span>" : "") +
      "</div><div class=\"chip-row\">" + [1, 2, 3, 4, 5, 6, 8].map(function (n) {
        return '<button class="chip' + (state.groupSize === n ? " is-on" : "") + '" data-act="group" data-n="' + n +
          '">' + n + "</button>";
      }).join("") + "</div>" +
      (state.groupSize
        ? (group
          ? '<p class="note">Seats <b>' + group.seats.map(function (x) { return esc(x.id); }).join(", ") +
            "</b> are free together" + (group.same ? " at table " + esc(group.table) : "") +
            ". They are circled on the map below.</p>" +
            '<div class="cta-row"><button class="btn primary" data-act="squad" data-id="' + esc(v.id) +
            '">Hold these seats</button></div>'
          : '<p class="note">No ' + state.groupSize + " seats together this moment" +
            (v.best_window ? ". " + esc(v.best_window.label) + " is when it frees up." : ".") + "</p>")
        : "") +
      "</div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Live seat map</span>' +
      '<div class="spacer"></div><span class="note">' + v.free + " free of " + v.capacity + "</span></div>" +
      seatMap(v, groupIds) +
      '<div class="legend-seat">' +
      '<span><i class="free"></i>free</span>' +
      '<span><i class="taken"></i>someone sitting</span>' +
      '<span><i class="bag"></i>bag, owner nearby</span>' +
      '<span><i class="abandoned"></i>abandoned</span>' +
      '<span><i class="camera"></i>seen by a camera</span></div>' +
      '<p class="note">' + (v.camera_seats
        ? v.live_seats + " of " + v.capacity + " seats are measured by a live camera; no image leaves the device."
        : "Demo seat data - no camera is attached here.") + "</p></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Ghost seats</span></div>' +
      ghostRows + "</div>" +

      "";
  }

  /* ------------------------------------------------------------------ */
  /* ALERTS                                                              */
  /* ------------------------------------------------------------------ */
  function renderAlerts() {
    var s = state.snap;
    var alerts = s.alerts || [];

    el("alerts-body").innerHTML =
      '<div class="panel"><div class="panel-head"><span class="panel-title">Notify me about</span></div>' +
      '<div class="chip-row">' +
      '<button class="chip' + (state.notify.rush ? " is-on" : "") + '" data-act="pref" data-key="rush">' +
      "Rush about to start</button>" +
      '<button class="chip' + (state.notify.ghost ? " is-on" : "") + '" data-act="pref" data-key="ghost">' +
      "Ghost seat freed up</button></div></div>" +

      '<p class="section-title">Alerts - ' + esc(s.clock.label) + "</p>" +
      (alerts.length ? alerts.map(function (a) {
        return '<button class="alert" data-act="venue" data-id="' + esc(a.venue_id) + '">' +
          '<span class="ic">' + (a.kind === "rush" ? "&#9203;" : a.kind === "ghost" ? "&#127890;" : "&#10022;") + "</span>" +
          "<div><b>" + esc(a.title) + "</b><p>" + esc(a.body) + "</p>" +
          '<div class="when">' + esc(a.when) + "</div></div></button>";
      }).join("") : '<div class="empty">Nothing urgent right now. Alerts appear when a counter is about to ' +
        "back up or a seat frees up.</div>") +

      (state.squad ? '<div class="panel" style="margin-top:18px"><div class="panel-head">' +
        '<span class="panel-title">Seats held for your group</span><div class="spacer"></div>' +
        '<span class="chip good">' + esc(state.squad.code) + "</span></div>" +
        '<p class="note">Seats ' + state.squad.seats.map(esc).join(", ") + " are held for " +
        state.squad.valid_minutes + " minutes. Share the code so your friends find you.</p></div>" : "");
  }

  /* ------------------------------------------------------------------ */
  /* STAFF VIEW                                                          */
  /* ------------------------------------------------------------------ */
  function renderInsights() {
    var s = state.snap;
    var staff = s.staff;
    if (!staff) return;
    var current = state.insightVenue || staff.venues[0].venue_id;
    var row = staff.venues.filter(function (r) { return r.venue_id === current; })[0] || staff.venues[0];
    var maxHeat = Math.max.apply(null, row.heat.concat([0.01]));

    var heatWeek = Object.keys(DAY_FACTOR).map(function (d) {
      var cells = row.heat.map(function (h) {
        var val = Math.min(1, h * DAY_FACTOR[d]);
        var colour = val > 0.75 ? "#e0bfc2" : val > 0.5 ? "#e6cdb2" : val > 0.3 ? "#f0e3cb" : "#e7efe4";
        return '<div class="heat-cell" style="background:' + colour + '" title="' + d + " " + val.toFixed(2) +
          '"></div>';
      }).join("");
      return '<div style="display:grid;grid-template-columns:36px 1fr;gap:8px;align-items:center;' +
        'font-size:13px;color:var(--label-3)"><span>' + d + "</span>" +
        '<div class="heat" style="grid-template-columns:repeat(' + row.heat.length + ',1fr)">' + cells + "</div></div>";
    }).join("");

    el("insights-body").innerHTML =
      '<div class="staff-banner"><span>Staff view</span><span style="font-weight:700;opacity:.85">' +
      "Counts and timings only. No footage, no faces.</span></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Today at a glance</span></div>' +
      '<div class="stat-grid">' +
      '<div class="stat"><b>' + staff.headline.est_served_today + "</b><span>meals served (estimated)</span></div>" +
      '<div class="stat"><b>' + Math.round(staff.headline.minutes_saved / 60) + "h</b><span>queueing avoided</span></div>" +
      '<div class="stat"><b>' + esc(staff.headline.peak_window) + "</b><span>peak window</span></div>" +
      "</div></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Peak hours</span>' +
      '<div class="spacer"></div><span class="note">' + staff.hours[0] + ":00 to " +
      staff.hours[staff.hours.length - 1] + ":00</span></div>" +
      '<div class="chip-row">' + staff.venues.map(function (v) {
        return '<button class="chip' + (v.venue_id === current ? " is-on" : "") + '" data-act="insight" data-id="' +
          esc(v.venue_id) + '">' + esc(v.name.split(",")[0].split(" ").slice(0, 2).join(" ")) + "</button>";
      }).join("") + "</div>" +
      '<div class="bars">' + row.heat.map(function (h) {
        return '<i class="' + (h > 0.7 ? "hot" : "") + '" style="height:' + Math.max(8, (h / maxHeat) * 100) +
          '%"></i>';
      }).join("") + "</div>" +
      '<div class="hours">' + staff.hours.map(function (h) { return "<span>" + h + "</span>"; }).join("") + "</div>" +
      '<div class="kv"><span>Busiest</span><b>' + esc(row.peak_label) + " at " + row.peak_occupancy + "</b></div>" +
      '<div class="kv"><span>Quietest</span><b>' + esc(row.quietest_hour) + "</b></div>" +
      '<div class="kv"><span>Counters to staff at peak</span><b>' + row.suggested_staff + "</b></div>" +
      '<div class="kv"><span>Food waste risk</span><b>' + esc(row.food_waste_risk) + "</b></div></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Week at a glance</span></div>' +
      '<div style="display:grid;gap:6px">' + heatWeek + "</div>" +
      '<p class="note">Staffing suggestion: ' + esc(staff.headline.staff_suggestion) + "</p>" +
      '<p class="note">Food prep: ' + esc(staff.headline.waste_note) + "</p></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">What the detection layer is doing</span></div>' +
      '<div class="kv"><span>Cameras attached</span><b>' + (s.cameras.length || 0) + "</b></div>" +
      '<div class="kv"><span>Mode</span><b>' + (s.mode === "live-cv" ? "live camera" : "demo dataset") + "</b></div>" +
      '<div class="kv"><span>Seats backed by real detection</span><b>' +
      s.venues.reduce(function (a, v) { return a + v.live_seats; }, 0) + "</b></div>" +
      '<div class="kv"><span>Frames sent to the app</span><b>0</b></div>' +
      '<p class="note">' + esc(staff.note) + "</p></div>";
  }

  /* ------------------------------------------------------------------ */
  /* ASK                                                                 */
  /* ------------------------------------------------------------------ */
  function openChat(question, focusId) {
    closeMenu();
    var panel = el("chat");
    panel.classList.add("is-open");
    document.body.classList.add("chat-open");
    panel.style.display = "block";        // belt and braces: never depend on a single CSS rule
    panel.setAttribute("aria-hidden", "false");
    if (!state.chat.length) {
      state.chat.push({ role: "bot", text: "Hi! Ask me where to eat, when it gets quiet, or for seats together." });
    }
    renderChat();
    if (question) sendChat(question, { venueId: focusId });
    else setTimeout(function () { el("chat-input").focus(); }, 120);
  }

  function closeChat() {
    var panel = el("chat");
    panel.classList.remove("is-open");
    document.body.classList.remove("chat-open");
    panel.style.display = "none";
    panel.setAttribute("aria-hidden", "true");
  }

  function renderChat() {
    var log = el("chat-log");
    log.innerHTML = state.chat.map(function (m) {
      var text = esc(m.text).replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
      return '<div class="msg ' + (m.role === "me" ? "me" : "bot") + '">' + text + "</div>";
    }).join("") + (state.chatBusy ? '<div class="msg bot typing">...</div>' : "");
    log.scrollTop = log.scrollHeight;
    el("chat-chips").innerHTML = state.chatBusy ? "" : (state.chips || []).map(function (c) {
      return '<button class="chip" data-act="chat-chip">' + esc(c) + "</button>";
    }).join("");
    var send = $(".chat-form .send");
    if (send) send.disabled = state.chatBusy;
  }

  function sendChat(text, opts) {
    var input = el("chat-input");
    var msg = (text || (input ? input.value : "") || "").trim();
    if (!msg) { if (input) input.focus(); return; }
    if (state.chatBusy) return;
    if (input) input.value = "";
    state.chat.push({ role: "me", text: msg });
    state.chips = [];
    state.chatBusy = true;
    renderChat();
    var context = { message: msg, group: state.groupSize || null, matters: state.matters.slice() };
    if (opts && opts.venueId) context.venue_id = opts.venueId;
    api("/api/chat", context).then(function (res) {
      state.chatBusy = false;
      state.chat.push({ role: "bot", text: (res && res.reply) || "I did not get an answer back." });
      if (res && res.chips) state.chips = res.chips;
      renderChat();
    }).catch(function (err) {
      state.chatBusy = false;
      state.chat.push({ role: "bot", text: "I could not reach the engine (" +
        ((err && err.message) || "no answer") + "). Is the run.py window still open?" });
      renderChat();
    });
  }

  /* ------------------------------------------------------------------ */
  /* DEMO SETTINGS SHEET                                                 */
  /* ------------------------------------------------------------------ */
  function renderSheet() {
    var s = state.snap || {};
    var cams = s.cameras || [];
    var camRows = cams.length ? cams.map(function (c) {
      var tag = c.error ? '<span class="chip bad">error</span>'
        : c.ready ? '<span class="chip good">live</span>' : '<span class="chip warn">starting</span>';
      return '<div class="cv-row"><div><b style="font-size:.92rem">' + esc(c.id) + "</b>" +
        '<div class="note">' + esc(c.source) + " - " + c.frames + " frames - " + c.fps + " fps" +
        (c.error ? " - " + esc(c.error) : "") + "</div></div><div class=\"spacer\"></div>" + tag + "</div>";
    }).join("") : '<p class="note">No camera attached - every venue is running on the demo dataset, ' +
      "which is the intended fallback for the demo.</p>";

    el("sheet-body").innerHTML = "<h3>Demo controls</h3>" +
      '<div class="panel"><div class="panel-head"><span class="panel-title">Computer vision</span>' +
      '<div class="spacer"></div><span class="chip ' + (s.mode === "live-cv" ? "live" : "off") + '">' +
      (s.mode === "live-cv" ? "Live camera" : "Demo data") + "</span></div>" + camRows +
      '<p class="note">With no camera attached, every venue is generated from the demo dataset, so all the ' +
      "screens work. To feed it a real camera: <code>python run.py --camera 0 --show</code> - the label at the " +
      "top then reads Live camera instead of Demo data.</p></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Campus clock</span>' +
      '<div class="spacer"></div><span class="note">' + (s.clock ? esc(s.clock.label) : "--:--") + " - " +
      (s.clock ? s.clock.speed : 30) + "x speed</span></div>" +
      '<div class="chip-row">' + [1, 10, 30, 60, 120].map(function (n) {
        return '<button class="chip' + (s.clock && s.clock.speed === n ? " is-on" : "") +
          '" data-act="speed" data-n="' + n + '">' + n + "x</button>";
      }).join("") + "</div>" +
      '<div class="chip-row" style="margin-top:8px">' + ["Mon", "Wed", "Fri", "Sat"].map(function (d) {
        return '<button class="chip' + (s.clock && s.clock.day === d ? " is-on" : "") + '" data-act="day" data-d="' +
          d + '">' + d + "</button>";
      }).join("") + "</div>" +
      '<p class="note">' + (s.clock ? s.clock.speed : 30) + "x makes one campus minute pass every " +
      (60 / (s.clock ? s.clock.speed : 30)).toFixed(0) + " seconds, so a whole lunch rush fits inside a short " +
      "demo.</p>" +
      '<p class="field-label">Jump to</p><div class="chip-row">' +
      ["08:00", "13:00", "17:30", "19:30"].map(function (t) {
        return '<button class="chip" data-act="time" data-t="' + t + '">' + t + "</button>";
      }).join("") + "</div></div>" +

      '<div class="panel"><div class="panel-head"><span class="panel-title">Scenario</span></div>' +
      '<div class="chip-row">' +
      '<button class="chip" data-act="scenario" data-s="">normal day</button>' +
      '<button class="chip" data-act="scenario" data-s="rush">lunch rush</button>' +
      '<button class="chip" data-act="scenario" data-s="ghost">ghost seats</button>' +
      '<button class="chip" data-act="scenario" data-s="empty">near empty</button></div>' +
      '<p class="note">\'Ghost seats\' makes people abandon bags more often, so the timer story is easy to ' +
      "show on camera.</p></div>" +
      '<button class="btn soft wide" data-act="sheet-close">Close</button>';
  }

  /* ------------------------------------------------------------------ */
  /* RENDER + EVENTS                                                     */
  /* ------------------------------------------------------------------ */
  function render() {
    if (!state.snap) return;
    var s = state.snap;

    document.body.dataset.mode = state.mode;
    document.body.dataset.screen = state.screen;

    var alerts = (s.alerts || []).length;
    el("tab-alerts-count").textContent = alerts ? String(alerts) : "";
    el("menu-status").textContent = (s.mode === "live-cv" ? "Live camera detection" : "Demo data") +
      " - " + s.clock.label + " - " + s.clock.speed + "x";

    el("mode-switch").querySelectorAll("button").forEach(function (b) {
      b.classList.toggle("is-on", b.dataset.mode === state.mode);
    });
    if (state.dragging) return;

    if (state.screen === "home") {
      if (!el("home-body").childElementCount) renderHome();
    }
    if (state.screen === "recommend") renderRecommend();
    if (state.screen === "map" && due("map", 2000)) renderMap();
    if (state.screen === "venue" && due("venue", 2500)) renderVenue();
    if (state.screen === "alerts" && due("alerts", 2000)) renderAlerts();
    if (state.screen === "insights" && due("insights", 3000)) renderInsights();
    if (state.sheetOpen) renderSheet();
  }

  function due(key, ms) {
    var now = Date.now();
    if (!state.lastAt[key] || now - state.lastAt[key] >= ms) {
      state.lastAt[key] = now;
      return true;
    }
    return false;
  }

  function showScreen() {
    document.body.dataset.screen = state.screen;
    $(".screen").forEach(function (node) {
      var on = node.id === "screen-" + state.screen;
      if (on && !node.classList.contains("is-active")) {
        node.classList.add("is-active", "enter");
        window.setTimeout(function () { node.classList.remove("enter"); }, 520);
      } else if (!on) {
        node.classList.remove("is-active", "enter");
      }
    });
  }

  function go(screen) {
    state.screen = screen;
    state.lastAt = {};
    closeMenu();
    showScreen();
    window.scrollTo({ top: 0, behavior: "smooth" });
    render();
  }

  function openMenu() {
    state.menuOpen = true;
    el("menu").classList.add("is-open");
    el("menu").setAttribute("aria-hidden", "false");
    el("menu-btn").setAttribute("aria-expanded", "true");
  }

  function closeMenu() {
    state.menuOpen = false;
    el("menu").classList.remove("is-open");
    el("menu").setAttribute("aria-hidden", "true");
    el("menu-btn").setAttribute("aria-expanded", "false");
  }

  function toast(title, body, icon) {
    var node = document.createElement("div");
    node.className = "toast";
    node.innerHTML = '<div class="ic">' + (icon || "&#10022;") + "</div><div><b>" + esc(title) + "</b><p>" +
      esc(body) + "</p></div>";
    el("toasts").appendChild(node);
    setTimeout(function () { node.remove(); }, 5200);
  }

  function applySnapshot(snap) {
    state.snap = snap;
    render();
  }

  function connect() {
    try {
      var es = new EventSource("/api/events");
      es.addEventListener("snapshot", function (ev) {
        try { applySnapshot(JSON.parse(ev.data)); } catch (err) { /* ignore a bad frame */ }
      });
      es.onerror = function () {
        el("menu-status").textContent = "Reconnecting to the engine...";
      };
    } catch (err) {
      showError();
    }
    api("/api/snapshot").then(applySnapshot).catch(showError);
  }

  function showError() {
    el("home-body").innerHTML = '<div class="err">The app server is not reachable.' +
      "<code>python run.py</code></div>";
  }

  document.addEventListener("click", function (ev) {
    var node = ev.target.closest("[data-act]");
    if (!node) return;
    var act = node.dataset.act;

    if (act === "nav") {
      if (node.dataset.screen === "home") {
        state.started = false;
        state.showAll = false;
        el("home-body").innerHTML = "";
      }
      go(node.dataset.screen);
    } else if (act === "brand") {
      state.started = false;
      state.showAll = false;
      el("home-body").innerHTML = "";
      go("home");
    } else if (act === "menu") {
      openMenu();
    } else if (act === "menu-close") {
      closeMenu();
    } else if (act === "expand") {
      state.showAll = !state.showAll;
      renderRecommend();
    } else if (act === "venue") {
      state.venueId = node.dataset.id;
      go("venue");
    } else if (act === "group") {
      state.groupSize = Number(node.dataset.n) || null;
      if (state.screen === "home") {
        renderHome();
      } else {
        state.lastAt = {};
        render();
      }
    } else if (act === "matters") {
      var key = node.dataset.key;
      var at = state.matters.indexOf(key);
      if (at >= 0) state.matters.splice(at, 1);
      else state.matters.push(key);
      renderHome();
    } else if (act === "prefs-done") {
      if (!state.groupSize) return;
      state.started = true;
      state.showAll = false;
      go("recommend");
    } else if (act === "prefs-skip") {
      state.started = true;
      state.showAll = true;
      go("recommend");
    } else if (act === "mode") {
      state.mode = node.dataset.mode;
      closeMenu();
      go(state.mode === "staff" ? "insights" : (state.started ? "recommend" : "home"));
    } else if (act === "insight") {
      state.insightVenue = node.dataset.id;
      renderInsights();
    } else if (act === "pref") {
      state.notify[node.dataset.key] = !state.notify[node.dataset.key];
      render();
    } else if (act === "squad") {
      var venue = state.snap.venues.filter(function (v) { return v.id === node.dataset.id; })[0];
      var grp = findGroup(venue, state.groupSize || 4);
      if (!grp) return;
      api("/api/squad", { venue_id: venue.id, seats: grp.seats.map(function (s) { return s.id; }) }).then(function (res) {
        state.squad = res;
        toast("Seats held - code " + res.code,
          "Seats " + res.seats.join(", ") + " for " + res.valid_minutes + " minutes.", "&#127903;");
        go("alerts");
      }).catch(function (err) {
        toast("Could not hold those seats", (err && err.message) || "Try again.", "&#9888;");
      });
    } else if (act === "chat" || act === "chat-open") {
      openChat();
    } else if (act === "ask-why") {
      var why = state.snap.venues.filter(function (v) { return v.id === node.dataset.id; })[0];
      if (why) openChat("Why is " + why.name + " the best place right now?", why.id);
      else openChat();
    } else if (act === "chat-close") {
      closeChat();
    } else if (act === "chat-chip") {
      sendChat(node.textContent);
      setTimeout(function () { el("chat-input").focus(); }, 50);
    } else if (act === "seat") {
      var v = state.snap.venues.filter(function (x) { return x.id === state.venueId; })[0];
      var seat = v && v.seats.filter(function (s) { return s.id === node.dataset.id; })[0];
      if (seat) {
        toast("Seat " + seat.id, seat.state.replace("_", " ") +
          (seat.camera ? " - watched by a camera" : " - from the simulator"), "&#129681;");
      }
    } else if (act === "sheet") {
      closeMenu();
      state.sheetOpen = true;
      el("sheet").classList.add("is-open");
      renderSheet();
    } else if (act === "sheet-close") {
      state.sheetOpen = false;
      el("sheet").classList.remove("is-open");
    } else if (act === "speed") {
      api("/api/config", { speed: Number(node.dataset.n) });
    } else if (act === "day") {
      api("/api/config", { day: node.dataset.d });
    } else if (act === "scenario") {
      api("/api/config", { scenario: node.dataset.s || null });
    } else if (act === "time") {
      api("/api/config", { time: node.dataset.t }).then(function () {
        toast("Campus clock", "Jumped to " + node.dataset.t + ".", "&#9201;");
      });
    }
  });

  document.addEventListener("input", function (ev) {
    var node = ev.target.closest('[data-act="tm"]');
    if (!node) return;
    state.dragging = true;
    state.tm[node.dataset.id] = Number(node.value);
    renderVenue();
    state.dragging = false;
  });

  document.addEventListener("pointerdown", function (ev) {
    if (ev.target.closest('[data-act="tm"]')) state.dragging = true;
  });
  document.addEventListener("pointerup", function () { state.dragging = false; });

  el("chat-form").addEventListener("submit", function (ev) {
    ev.preventDefault();
    sendChat();
  });
  el("sheet").addEventListener("click", function (ev) {
    if (ev.target === el("sheet")) {
      state.sheetOpen = false;
      el("sheet").classList.remove("is-open");
    }
  });
  el("menu").addEventListener("click", function (ev) {
    if (ev.target === el("menu")) closeMenu();
  });
  document.addEventListener("keydown", function (ev) {
    if (ev.key !== "Escape") return;
    if (state.menuOpen) closeMenu();
    if (state.sheetOpen) {
      state.sheetOpen = false;
      el("sheet").classList.remove("is-open");
    }
    closeChat();
  });

  /* The giant wordmark assembles letter by letter. Same font, same colours,
     just a soft rise so it does not appear all at once. */
  function splitMega() {
    var node = el("mega");
    if (!node) return;
    var index = 0;
    $(".mega-line", node).forEach(function (line) {
      var text = line.getAttribute("data-text") || line.textContent;
      line.textContent = "";
      text.split("").forEach(function (ch) {
        var span = document.createElement("span");
        span.className = "ch";
        span.style.setProperty("--i", index);
        index += 1;
        span.textContent = ch;
        line.appendChild(span);
      });
    });
  }

  splitMega();
  connect();
})();
