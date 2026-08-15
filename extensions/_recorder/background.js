// "Start Scenarios" in the right-click menu of any window the launcher opened.
//
// The whole job of this extension is that one menu item. Recording itself happens
// in the page, driven from Python over the connection the launcher already holds
// open (engine/recorder.py) - so nothing here talks to the network, asks for host
// permissions, or knows what a scenario is.
//
// It only has to get a message to the tab. The content script turns that into an
// attribute on <html>, which the recorder - injected into the page's main world,
// where an extension cannot reach - is watching for. The DOM is the one thing the
// extension's isolated world and the page's main world share.
"use strict";

const MENU_ID = "cms-start-scenarios";

function createMenu() {
  // remove-then-create: the service worker is restarted freely by Chrome, and
  // creating an id that already exists is an error.
  chrome.contextMenus.removeAll(() => {
    chrome.contextMenus.create({
      id: MENU_ID,
      title: "Start Scenarios",
      contexts: ["all"],
    });
  });
}

chrome.runtime.onInstalled.addListener(createMenu);
chrome.runtime.onStartup.addListener(createMenu);
createMenu();

chrome.contextMenus.onClicked.addListener((info, tab) => {
  if (info.menuItemId !== MENU_ID || !tab || tab.id === undefined) return;
  chrome.tabs.sendMessage(tab.id, { cms: "start-recording" }, () => {
    // A tab with no content script (chrome://, the web store) answers with a
    // lastError. Read it so Chrome does not log it as unchecked, and stop -
    // there is nothing to record on a page the recorder cannot reach.
    void chrome.runtime.lastError;
  });
});
