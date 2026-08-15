// Scenario Recorder - the part that runs inside the page.
//
// Injected over the launcher's existing CDP connection (engine/recorder.py), the
// same way the execution HUD is, and isolated the same way: one host element on
// <html>, everything inside a Shadow DOM, so the app's CSS and JS never see it.
//
// The recorder captures EXPLICITLY. Moving the mouse, typing, focusing and every
// intermediate input event are ignored - none of them become a step. A step
// exists only when it is asked for: press Capture Step, hover until the element
// you want is outlined, click it, and choose from the actions that element can
// take. That is one step, and nothing else is.
//
// Python is the one that acts and the one that remembers. This side collects
// what the user picked into a queue and hands it over when drained; the steps
// shown in the panel are pushed back from Python, so the panel survives a
// navigation as soon as the next state arrives rather than keeping its own copy.
//
// Entry points, all on window.__Recorder: start(), stop(), render(state),
// drain(), pending() and armed().
(function () {
  "use strict";
  var HOST_ID = "__cms_recorder_host__";
  var FLAG = "data-cms-record";     // set by the extension's content script

  var CSS = "" +
    ":host{all:initial}" +
    // The outline drawn over whatever the pointer is on. Never interactive: it
    // must not swallow the click that picks the element underneath it.
    ".hi{position:fixed;pointer-events:none;z-index:2147483646;box-sizing:border-box;" +
      "border:2px solid #4da3ff;border-radius:3px;background:rgba(77,163,255,0.16);" +
      "box-shadow:0 0 0 2px rgba(77,163,255,0.35)}" +
    ".hi-tag{position:absolute;top:-19px;left:-2px;padding:1px 6px;border-radius:3px;" +
      "background:#4da3ff;color:#0b1220;white-space:nowrap;max-width:60vw;" +
      "overflow:hidden;text-overflow:ellipsis;" +
      "font:600 11px/1.5 'Segoe UI',system-ui,-apple-system,sans-serif}" +
    ".wrap{position:fixed;top:14px;right:14px;width:380px;max-height:92vh;" +
      "display:flex;flex-direction:column;pointer-events:none;" +
      "font:12px/1.45 'Segoe UI',system-ui,-apple-system,sans-serif;color:#e8eaed;" +
      "z-index:2147483647}" +
    ".card{display:flex;flex-direction:column;min-height:0;max-height:92vh;" +
      "background:rgba(18,19,22,0.92);border:1px solid rgba(255,255,255,0.10);" +
      "border-radius:12px;box-shadow:0 10px 34px rgba(0,0,0,0.5);" +
      "backdrop-filter:blur(7px);-webkit-backdrop-filter:blur(7px);" +
      "pointer-events:auto;overflow:hidden}" +
    ".header{display:flex;align-items:center;gap:8px;padding:8px 10px;cursor:move;" +
      "user-select:none;border-bottom:1px solid rgba(255,255,255,0.08);" +
      "background:rgba(255,255,255,0.04);flex:0 0 auto}" +
    ".h-title{flex:1;font-weight:600;color:#fff;white-space:nowrap;overflow:hidden;" +
      "text-overflow:ellipsis}" +
    ".btn{width:22px;height:22px;display:flex;align-items:center;justify-content:center;" +
      "border-radius:5px;cursor:pointer;color:#cfd3d7;flex:0 0 auto;" +
      "background:rgba(255,255,255,0.06)}" +
    ".btn:hover{background:rgba(255,255,255,0.14);color:#fff}" +
    ".body{padding:8px 10px;overflow:auto;min-height:0;flex:1 1 auto}" +
    ".bar{display:flex;gap:6px;padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.08);" +
      "flex:0 0 auto}" +
    ".action{flex:1;text-align:center;padding:6px 8px;border-radius:6px;cursor:pointer;" +
      "background:#2f6fd0;color:#fff;font-weight:600}" +
    ".action:hover{background:#3f80e4}" +
    ".action.armed{background:#c9770f}" +
    ".ghost-btn{padding:6px 10px;border-radius:6px;cursor:pointer;" +
      "background:rgba(255,255,255,0.08);color:#e8eaed}" +
    ".ghost-btn:hover{background:rgba(255,255,255,0.16)}" +
    ".hint{padding:6px 10px;color:#9aa0a6;border-bottom:1px solid rgba(255,255,255,0.08)}" +
    ".step{display:flex;gap:8px;padding:4px 2px;border-bottom:1px solid rgba(255,255,255,0.05)}" +
    ".step-n{color:#6b7076;min-width:18px;text-align:right}" +
    ".step-a{color:#8ab4f8;font-weight:600}" +
    ".step-t{color:#cfd3d7;word-break:break-all}" +
    ".empty{color:#9aa0a6;padding:6px 2px}" +
    // The action menu that opens where the element was clicked.
    ".menu{position:fixed;z-index:2147483647;min-width:230px;max-height:60vh;overflow:auto;" +
      "background:rgba(18,19,22,0.97);border:1px solid rgba(255,255,255,0.14);" +
      "border-radius:10px;box-shadow:0 10px 34px rgba(0,0,0,0.6);pointer-events:auto;" +
      "font:12px/1.45 'Segoe UI',system-ui,-apple-system,sans-serif;color:#e8eaed}" +
    ".menu-head{padding:8px 10px;border-bottom:1px solid rgba(255,255,255,0.10);" +
      "color:#9aa0a6;word-break:break-all}" +
    ".menu-sel{color:#8ab4f8;display:block;margin-top:3px}" +
    ".menu-item{padding:7px 10px;cursor:pointer;display:flex;gap:8px;align-items:baseline}" +
    ".menu-item:hover{background:rgba(255,255,255,0.10)}" +
    ".menu-item.on{background:rgba(77,163,255,0.22)}" +
    ".menu-item .key{color:#6b7076;min-width:12px}" +
    ".menu-item .why{color:#9aa0a6;font-size:11px;margin-left:auto}" +
    ".menu-foot{padding:6px 10px;color:#6b7076;font-size:11px;" +
      "border-top:1px solid rgba(255,255,255,0.10)}" +
    ".menu-cancel{color:#ffab40}";

  function elem(tag, cls) {
    var n = document.createElement(tag);
    if (cls) n.className = cls;
    return n;
  }

  // ---------------------------------------------------------------- selectors
  //
  // Turning an element into something a flow can name. In order of preference:
  //
  //   1. a name already in selectors.yaml whose selector matches this element -
  //      by far the best outcome, because the recording then reads like the
  //      flows next to it and follows the tree when that name is re-pointed;
  //   2. a structural attribute the app chose deliberately: data-menu-xmlid,
  //      name, data-testid, a stable id;
  //   3. a tag + class path, scoped to the nearest identifiable ancestor.
  //
  // Never text. A selector keyed on a visible label is the one thing guaranteed
  // to break here: the same app renders Ukrainian on one environment and English
  // on another, so :has-text("Save") records green and fails everywhere else.
  var UNSTABLE = /(^|[-_])(ng|css|sc|jsx|emotion)[-_]?[a-z0-9]{4,}|\d{4,}|^o_[a-z]+_\d+$/i;

  function stableClasses(el) {
    var out = [];
    var list = (el.className && el.className.baseVal !== undefined)
      ? el.className.baseVal.split(/\s+/)          // SVG
      : String(el.className || "").split(/\s+/);
    for (var i = 0; i < list.length; i++) {
      var c = list[i].trim();
      // A generated class is worse than no class: it changes on the next build
      // and takes the recording with it.
      if (c && !UNSTABLE.test(c)) out.push(c);
    }
    return out;
  }

  function cssEscape(value) {
    return String(value).replace(/["\\]/g, "\\$&");
  }

  function attributeSelector(el) {
    // Attributes an application sets on purpose, most specific first. These are
    // the ones that survive a restyle, a re-render and a translation.
    var named = ["data-menu-xmlid", "data-testid", "data-test", "data-qa", "data-cy",
                 "name", "data-field", "data-name", "aria-controls"];
    for (var i = 0; i < named.length; i++) {
      var value = el.getAttribute && el.getAttribute(named[i]);
      if (value && !UNSTABLE.test(value)) {
        return "[" + named[i] + '="' + cssEscape(value) + '"]';
      }
    }
    if (el.id && !UNSTABLE.test(el.id)) return "#" + CSS_ident(el.id);
    return "";
  }

  function CSS_ident(value) {
    // An id can legally hold characters a selector cannot, so fall back to the
    // attribute form rather than emitting something that will not parse.
    return /^[A-Za-z_][\w-]*$/.test(value) ? value : '[id="' + cssEscape(value) + '"]';
  }

  function matchesUniquely(selector, el) {
    try {
      var found = document.querySelectorAll(selector);
      return found.length === 1 && found[0] === el;
    } catch (e) { return false; }
  }

  function matchesAtAll(selector, el) {
    try {
      return el.matches(selector);
    } catch (e) { return false; }
  }

  function namedSelector(el, selectors) {
    // Prefer a name the tree already has. Playwright syntax (:has-text,
    // :nth-match) is not parseable by the browser, so each one is tried and the
    // ones that throw are simply not candidates - Python still resolves them.
    var best = null;
    for (var name in selectors) {
      if (!Object.prototype.hasOwnProperty.call(selectors, name)) continue;
      if (!matchesAtAll(selectors[name], el)) continue;
      // A name that picks out exactly this element beats one that also matches
      // its siblings.
      if (matchesUniquely(selectors[name], el)) return name;
      if (!best) best = name;
    }
    return best;
  }

  function structuralSelector(el) {
    var own = attributeSelector(el);
    if (own && matchesUniquely(own, el)) return own;

    var tag = (el.tagName || "").toLowerCase();
    var classes = stableClasses(el);
    var base = own || (tag + (classes.length ? "." + classes.slice(0, 2).join(".") : ""));
    if (matchesUniquely(base, el)) return base;

    // Scope it to the nearest ancestor that can be named, which is what makes a
    // repeated row or cell addressable without counting from the document root.
    var parent = el.parentElement;
    for (var depth = 0; parent && depth < 4; depth++) {
      var scope = attributeSelector(parent);
      if (!scope) {
        var pclasses = stableClasses(parent);
        if (pclasses.length) {
          scope = (parent.tagName || "").toLowerCase() + "." + pclasses[0];
        }
      }
      if (scope) {
        var combined = scope + " " + base;
        if (matchesUniquely(combined, el)) return combined;
      }
      parent = parent.parentElement;
    }
    // Nothing unique: return the best description anyway and say so in the menu.
    return base;
  }

  function describeTarget(el, selectors) {
    var name = namedSelector(el, selectors || {});
    var selector = structuralSelector(el);
    return {
      name: name || "",
      selector: selector,
      unique: name ? matchesUniquely((selectors || {})[name], el)
                   : matchesUniquely(selector, el),
      tag: (el.tagName || "").toLowerCase(),
      type: (el.getAttribute && el.getAttribute("type")) || "",
      role: (el.getAttribute && el.getAttribute("role")) || "",
      editable: isEditable(el),
      value: readValue(el),
      // Only ever shown in the menu so a person can tell which element they
      // picked; never put into a selector.
      text: (el.textContent || "").trim().slice(0, 60)
    };
  }

  function isEditable(el) {
    var tag = (el.tagName || "").toLowerCase();
    if (tag === "textarea") return true;
    if (tag === "input") {
      var t = (el.getAttribute("type") || "text").toLowerCase();
      return ["button", "submit", "reset", "checkbox", "radio", "file",
              "image", "hidden"].indexOf(t) < 0;
    }
    return !!el.isContentEditable;
  }

  function readValue(el) {
    try { return typeof el.value === "string" ? el.value : ""; } catch (e) { return ""; }
  }

  // ------------------------------------------------------------ action menu
  //
  // What a given element can sensibly be asked to do. A filter over the
  // compiler's own vocabulary rather than a list of its own: Python passes the
  // grammar in, so this cannot drift from what a scenario may contain.
  function actionsFor(target, grammar) {
    var known = {};
    ["selector_only", "selector_and_value", "value_only", "url_target"].forEach(
      function (group) {
        (grammar[group] || []).forEach(function (a) { known[a] = group; });
      });
    var out = [];
    function offer(action, why) {
      if (known[action]) out.push({ action: action, why: why || "" });
    }
    if (target.tag === "select") {
      offer("select", "choose an option by value");
    } else if (target.editable) {
      offer("fill", "type into it");
      offer("click", "focus it");
    } else {
      offer("click", "click it");
    }
    offer("assert_visible", "check it is on screen");
    offer("assert_not_visible", "check it is gone");
    offer("assert_exists", "check it is in the DOM");
    offer("assert_text_contains", "check what it says");
    offer("wait_for", "wait until it appears");
    return out;
  }

  var Recorder = {
    _shadow: null, _card: null, _body: null, _titleEl: null, _hi: null,
    _armBtn: null, _hintEl: null, _menu: null,
    _armed: false, _running: false,
    _queue: [],            // events waiting for Python to drain
    _state: { steps: [], scenario: "", status: "idle" },
    _selectors: {}, _grammar: {},
    _drag: null,
    _hover: null,

    // ------------------------------------------------------------- lifecycle
    configure: function (selectors, grammar) {
      this._selectors = selectors || {};
      this._grammar = grammar || {};
    },

    start: function () {
      this._running = true;
      this._bindKeys();
      this.ensure();
      this._push({ kind: "started", url: location.href });
      this.paint();
    },

    stop: function () {
      this._running = false;
      this._unbindKeys();
      this.disarm();
      var host = document.getElementById(HOST_ID);
      if (host && host.parentNode) host.parentNode.removeChild(host);
      this._shadow = null;
    },

    running: function () { return !!this._running; },
    armed: function () { return !!this._armed; },

    // The extension cannot reach into this world, so it leaves a mark on <html>
    // instead. Taking it clears it, which makes a double click harmless.
    takeRequest: function () {
      try {
        var root = document.documentElement;
        if (root && root.hasAttribute(FLAG)) {
          root.removeAttribute(FLAG);
          return true;
        }
      } catch (e) {}
      return false;
    },

    // --------------------------------------------------------------- python
    drain: function () {
      var out = this._queue;
      this._queue = [];
      return out;
    },

    pending: function () { return this._queue.length; },

    _push: function (event) {
      this._queue.push(event);
      // A page can be navigated away from at any moment and the queue goes with
      // it, so keep it small and let Python be the memory.
      if (this._queue.length > 200) this._queue.shift();
    },

    render: function (state) {
      try {
        this._state = state || this._state;
        if (this._running) { this.ensure(); this.paint(); }
      } catch (e) { /* never throw into the page */ }
    },

    // ------------------------------------------------------------------ DOM
    ensure: function () {
      var host = document.getElementById(HOST_ID);
      if (host && this._shadow) return;
      if (!host) {
        host = elem("div");
        host.id = HOST_ID;
        host.setAttribute("aria-hidden", "true");
        (document.documentElement || document.body).appendChild(host);
      }
      var shadow = host.shadowRoot || host.attachShadow({ mode: "open" });
      shadow.innerHTML = "";
      var style = document.createElement("style");
      style.textContent = CSS;
      shadow.appendChild(style);

      var wrap = elem("div", "wrap");
      var card = elem("div", "card");

      var header = elem("div", "header");
      var title = elem("div", "h-title");
      title.textContent = "Recording";
      header.appendChild(title);
      header.appendChild(this._makeBtn("–", "Minimize", this._toggleMinimize));
      card.appendChild(header);

      var bar = elem("div", "bar");
      var arm = elem("div", "action");
      arm.textContent = "Capture Step";
      var self = this;
      arm.addEventListener("click", function (e) {
        e.stopPropagation();
        self.toggleArm();
      });
      bar.appendChild(arm);
      var finish = elem("div", "ghost-btn");
      finish.textContent = "Finish";
      finish.title = "Stop recording and save what has been captured";
      finish.addEventListener("click", function (e) {
        e.stopPropagation();
        self._push({ kind: "finish" });
      });
      bar.appendChild(finish);
      card.appendChild(bar);

      var hint = elem("div", "hint");
      card.appendChild(hint);

      var body = elem("div", "body");
      card.appendChild(body);
      wrap.appendChild(card);
      shadow.appendChild(wrap);

      this._shadow = shadow;
      this._card = card;
      this._body = body;
      this._titleEl = title;
      this._armBtn = arm;
      this._hintEl = hint;
      this._wireDrag(header);
    },

    _makeBtn: function (glyph, tip, handler) {
      var b = elem("div", "btn");
      b.textContent = glyph;
      b.title = tip;
      var self = this;
      b.addEventListener("click", function (e) {
        e.stopPropagation();
        handler.call(self, b);
      });
      return b;
    },

    _toggleMinimize: function (btn) {
      var hidden = this._body.style.display === "none";
      this._body.style.display = hidden ? "" : "none";
      btn.textContent = hidden ? "–" : "+";
    },

    _wireDrag: function (handle) {
      var self = this;
      handle.addEventListener("mousedown", function (e) {
        if (e.button !== 0) return;
        var wrap = self._card.parentNode;
        var rect = wrap.getBoundingClientRect();
        self._drag = { dx: e.clientX - rect.left, dy: e.clientY - rect.top };
        e.preventDefault();
      });
      document.addEventListener("mousemove", function (e) {
        if (!self._drag) return;
        var wrap = self._card.parentNode;
        wrap.style.left = (e.clientX - self._drag.dx) + "px";
        wrap.style.top = (e.clientY - self._drag.dy) + "px";
        wrap.style.right = "auto";
      });
      document.addEventListener("mouseup", function () { self._drag = null; });
    },

    paint: function () {
      if (!this._shadow) return;
      var state = this._state || {};
      this._titleEl.textContent = "Recording" +
        (state.scenario ? " · " + state.scenario : "");
      this._armBtn.textContent = this._armed ? "Pick an element… (Esc)"
                                             : "Capture Step  (F2)";
      this._armBtn.className = this._armed ? "action armed" : "action";
      this._hintEl.textContent = this._armed
        ? "Hover to outline, click to pick. Menus stay open while capturing."
        : (state.status === "busy" ? "Working…"
           : "Nothing is recorded until you press Capture Step. Press F2 instead "
             + "to keep a dropdown open.");

      var steps = state.steps || [];
      this._body.innerHTML = "";
      if (!steps.length) {
        var empty = elem("div", "empty");
        empty.textContent = "No steps yet.";
        this._body.appendChild(empty);
        return;
      }
      for (var i = 0; i < steps.length; i++) {
        var row = elem("div", "step");
        var n = elem("div", "step-n"); n.textContent = String(i + 1);
        var a = elem("div", "step-a"); a.textContent = steps[i].action || "";
        var t = elem("div", "step-t");
        t.textContent = steps[i].target || steps[i].value || "";
        row.appendChild(n); row.appendChild(a); row.appendChild(t);
        this._body.appendChild(row);
      }
      this._body.scrollTop = this._body.scrollHeight;
    },

    // ------------------------------------------------------------- capturing
    toggleArm: function () {
      if (this._armed) this.disarm(); else this.arm();
    },

    arm: function () {
      if (this._armed) return;
      this._armed = true;
      this._bind();
      this.paint();
    },

    disarm: function () {
      this._armed = false;
      this._unbind();
      this._clearHighlight();
      this._closeMenu();
      this.paint();
    },

    // Keys work while the recorder is running, not only while it is armed - the
    // whole point being that arming must not require a click. Clicking anywhere,
    // including on this panel, dismisses whatever transient thing the app has
    // open: a dropdown, a popover, an autocomplete list. Those are exactly the
    // things worth recording, so F2 arms instead.
    _bindKeys: function () {
      if (this._onKey) return;
      var self = this;
      this._onKey = function (e) { self._key(e); };
      document.addEventListener("keydown", this._onKey, true);
    },

    _unbindKeys: function () {
      if (this._onKey) document.removeEventListener("keydown", this._onKey, true);
      this._onKey = null;
    },

    _key: function (e) {
      if (e.key === "F2") {
        this.toggleArm();
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      if (e.key === "Escape" && (this._armed || this._menu)) {
        this.disarm();
        e.preventDefault();
        e.stopPropagation();
        return;
      }
      // With the action menu open, choosing by keyboard is the only way that
      // leaves the app's own popup standing - a click on this panel is an
      // outside-click as far as the app is concerned.
      if (!this._menu) return;
      var items = this._menuItems || [];
      if (/^[1-9]$/.test(e.key)) {
        var index = parseInt(e.key, 10) - 1;
        if (items[index]) {
          e.preventDefault();
          e.stopPropagation();
          this._choose(items[index].action, this._pickedTarget);
        }
        return;
      }
      if (e.key === "ArrowDown" || e.key === "ArrowUp") {
        this._moveMenu(e.key === "ArrowDown" ? 1 : -1);
        e.preventDefault();
        e.stopPropagation();
      } else if (e.key === "Enter" && items[this._menuIndex]) {
        e.preventDefault();
        e.stopPropagation();
        this._choose(items[this._menuIndex].action, this._pickedTarget);
      }
    },

    _bind: function () {
      var self = this;
      this._onMove = function (e) { self._track(e); };
      // Capture phase, so the pick is taken before the app sees it. All three
      // matter: an app closes its dropdown on mousedown, so intercepting only
      // the click would let the menu shut before the element could be picked
      // out of it.
      this._onClick = function (e) { self._pick(e); };
      this._onDown = function (e) { self._swallow(e); };
      document.addEventListener("mousemove", this._onMove, true);
      document.addEventListener("click", this._onClick, true);
      document.addEventListener("mousedown", this._onDown, true);
      document.addEventListener("pointerdown", this._onDown, true);
      document.addEventListener("mouseup", this._onDown, true);
    },

    _unbind: function () {
      if (this._onMove) document.removeEventListener("mousemove", this._onMove, true);
      if (this._onClick) document.removeEventListener("click", this._onClick, true);
      if (this._onDown) {
        document.removeEventListener("mousedown", this._onDown, true);
        document.removeEventListener("pointerdown", this._onDown, true);
        document.removeEventListener("mouseup", this._onDown, true);
      }
      this._onMove = this._onClick = this._onDown = null;
    },

    _swallow: function (e) {
      // Everything except the click itself, which _pick handles. Taking these
      // away from the app is what keeps a dropdown open long enough to point at
      // something inside it.
      if (this._ours(e)) return;
      e.preventDefault();
      e.stopPropagation();
    },

    _inPanel: function (el) {
      var host = document.getElementById(HOST_ID);
      return !!(host && (el === host || host.contains(el)));
    },

    // Anything the panel receives belongs to the panel. Events from inside a
    // shadow tree are retargeted to the host on the way out, so a click on a
    // menu item arrives here as the host - which is exactly what has to be let
    // through, or arming would swallow the very menu it just opened.
    //
    // Nothing needs to see *through* the panel: .wrap is pointer-events:none and
    // the highlight box is too, so only the card itself is ever a target.
    _ours: function (e) {
      var el = e.target;
      return !el || this._inPanel(el) || el.id === HOST_ID;
    },

    _track: function (e) {
      if (this._ours(e)) { this._clearHighlight(); return; }
      var el = e.target;
      if (el === this._hover) return;
      this._hover = el;
      this._highlight(el);
    },

    _highlight: function (el) {
      if (!this._shadow) return;
      this._clearHighlight();
      var rect = el.getBoundingClientRect();
      if (!rect || (!rect.width && !rect.height)) return;
      var box = elem("div", "hi");
      box.style.left = rect.left + "px";
      box.style.top = rect.top + "px";
      box.style.width = rect.width + "px";
      box.style.height = rect.height + "px";
      var tag = elem("div", "hi-tag");
      var described = describeTarget(el, this._selectors);
      tag.textContent = described.name || described.selector;
      if (rect.top < 22) tag.style.top = "0";
      box.appendChild(tag);
      this._shadow.appendChild(box);
      this._hi = box;
    },

    _clearHighlight: function () {
      if (this._hi && this._hi.parentNode) this._hi.parentNode.removeChild(this._hi);
      this._hi = null;
    },

    _pick: function (e) {
      if (this._ours(e)) return;              // the panel's own clicks are its own
      // Take the click away from the app: while armed, clicking chooses an
      // element rather than pressing what is under the pointer.
      e.preventDefault();
      e.stopPropagation();
      var target = describeTarget(e.target, this._selectors);
      this._openMenu(e.clientX, e.clientY, target);
    },

    // ------------------------------------------------------------- the menu
    _openMenu: function (x, y, target) {
      this._closeMenu();
      this._clearHighlight();
      var menu = elem("div", "menu");
      menu.style.left = Math.min(x, window.innerWidth - 250) + "px";
      menu.style.top = Math.min(y, window.innerHeight - 260) + "px";

      var head = elem("div", "menu-head");
      head.textContent = target.tag + (target.text ? ' "' + target.text + '"' : "");
      var sel = elem("span", "menu-sel");
      sel.textContent = (target.name ? target.name + "  (selectors.yaml)" : target.selector)
        + (target.unique ? "" : "  · matches more than one");
      head.appendChild(sel);
      menu.appendChild(head);

      var self = this;
      var choices = actionsFor(target, this._grammar);
      this._menuItems = choices;
      this._menuIndex = 0;
      this._pickedTarget = target;
      this._menuEls = [];
      choices.forEach(function (choice, index) {
        var item = elem("div", "menu-item");
        var key = elem("span", "key");
        key.textContent = index < 9 ? String(index + 1) : " ";
        var label = elem("span");
        label.textContent = choice.action;
        var why = elem("span", "why");
        why.textContent = choice.why;
        item.appendChild(key);
        item.appendChild(label);
        item.appendChild(why);
        item.addEventListener("click", function (ev) {
          ev.stopPropagation();
          self._choose(choice.action, target);
        });
        menu.appendChild(item);
        self._menuEls.push(item);
      });

      var cancel = elem("div", "menu-item menu-cancel");
      cancel.textContent = "Cancel";
      cancel.addEventListener("click", function (ev) {
        ev.stopPropagation();
        self._closeMenu();
      });
      menu.appendChild(cancel);

      var foot = elem("div", "menu-foot");
      foot.textContent = "1-9 or ↑↓ + Enter · Esc cancels";
      menu.appendChild(foot);

      this._shadow.appendChild(menu);
      this._menu = menu;
      this._moveMenu(0);
    },

    _moveMenu: function (delta) {
      var items = this._menuEls || [];
      if (!items.length) return;
      this._menuIndex = (this._menuIndex + delta + items.length) % items.length;
      for (var i = 0; i < items.length; i++) {
        items[i].className = "menu-item" + (i === this._menuIndex ? " on" : "");
      }
      var chosen = items[this._menuIndex];
      if (chosen && chosen.scrollIntoView) chosen.scrollIntoView({ block: "nearest" });
    },

    _closeMenu: function () {
      if (this._menu && this._menu.parentNode) this._menu.parentNode.removeChild(this._menu);
      this._menu = null;
      this._menuItems = null;
      this._menuEls = null;
      this._pickedTarget = null;
      this._menuIndex = 0;
    },

    _choose: function (action, target) {
      this._closeMenu();
      var step = {
        action: action,
        // The name if the tree already has one, so the recording reads like the
        // flows beside it; the synthesized selector otherwise.
        target: target.name || target.selector,
        selector: target.selector,
        named: !!target.name
      };
      if (action === "fill" || action === "select") {
        // The value is the one thing the element cannot answer for itself.
        var current = target.value || "";
        var typed = window.prompt(
          action === "select" ? "Option value to select:" : "Text to type:", current);
        if (typed === null) { this.disarm(); return; }
        step.value = typed;
      } else if (action === "assert_text_contains") {
        var expected = window.prompt("Text it should contain:", target.text || "");
        if (expected === null) { this.disarm(); return; }
        step.value = expected;
      }
      // Disarm first: performing the step may navigate, and an armed recorder
      // would then eat the first click on the next page.
      this.disarm();
      this._push({ kind: "step", step: step });
    }
  };

  window.__Recorder = Recorder;
})();
