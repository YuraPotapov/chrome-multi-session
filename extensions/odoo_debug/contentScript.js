const api = typeof browser !== 'undefined' ? browser : chrome;

// Inject page script
const scriptEl = document.createElement('script');
scriptEl.src = api.runtime.getURL('pageScript.js');
(document.head || document.documentElement).appendChild(scriptEl);

// Messaging from background/service worker
api.runtime.onMessage.addListener((request, sender, sendResponse) => {
    if (request.message === 'getOdooDebugInfo') {
        const body = document.body;

        if (body && body.hasAttribute('data-odoo')) {
            sendResponse({
                odooVersion: body.getAttribute('data-odoo'),
                debugMode: body.getAttribute('data-odoo-debug-mode'),
            });
        } else {
            sendResponse({ odooVersion: false });
        }
    }
    return true; // Required for async safety (Firefox MV3)
});
