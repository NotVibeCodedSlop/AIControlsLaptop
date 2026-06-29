// main.js
const { app, BrowserWindow, session } = require('electron');
const path = require('path');

// CRITICAL: Disable Site Isolation so webSecurity: false can access cross-origin iframes!
app.commandLine.appendSwitch('disable-site-isolation-trials');

function createWindow () {
    const win = new BrowserWindow({
        width: 1400,
        height: 900,
        webPreferences: {
            nodeIntegration: true,
            contextIsolation: false,
            webSecurity: false // Bypasses CORS
        }
    });

    // Intercept headers to strip X-Frame-Options and Content-Security-Policy.
    session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
        const responseHeaders = details.responseHeaders;

        // Delete X-Frame-Options headers
        delete responseHeaders['x-frame-options'];
        delete responseHeaders['X-Frame-Options'];

        // Delete Content-Security-Policy headers
        delete responseHeaders['content-security-policy'];
        delete responseHeaders['Content-Security-Policy'];

        callback({
            cancel: false,
            responseHeaders: responseHeaders
        });
    });

    win.loadFile('index.html');
}

app.whenReady().then(createWindow);

app.on('window-all-closed', () => {
    if (process.platform !== 'darwin') {
        app.quit();
    }
});
