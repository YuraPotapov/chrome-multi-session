// Hand the "Start Scenarios" click to the recorder running in the page.
//
// A content script lives in an isolated world: it can see the DOM but not the
// page's own globals, so it cannot call the recorder directly. The DOM is the
// shared ground, and an attribute on <html> is the smallest thing that crosses
// it - the recorder polls for it and clears it once it has taken it, which also
// makes the handover idempotent if two clicks arrive close together.
(function () {
  "use strict";
  var FLAG = "data-cms-record";

  chrome.runtime.onMessage.addListener(function (message, _sender, respond) {
    if (message && message.cms === "start-recording") {
      document.documentElement.setAttribute(FLAG, String(Date.now()));
      respond({ ok: true });
    }
    return false;   // answered synchronously
  });
})();
