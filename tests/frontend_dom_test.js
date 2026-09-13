const assert = require('node:assert');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<table><tbody id="results-tbody"></tbody></table>', { runScripts: 'outside-only' });
const { window } = dom;
window.fetch = async () => ({ ok: true, json: async () => ({}) });
window.Chart = function () {};
let source = fs.readFileSync('static/js/app.js', 'utf8');
source = source.replace('document.addEventListener(\'DOMContentLoaded\'', '/* test suppresses bootstrap */ document.addEventListener(\'testDOMContentLoaded\'');
source += '\nwindow.__TableManager = TableManager; window.__PixelProbeApp = PixelProbeApp; window.__StatsDashboard = StatsDashboard;';
window.eval(source);

const table = new window.__TableManager({ getScanResults: async () => ({}) });
window.app = { viewFile() {}, rescanFile() {}, orphanCheckFile() {}, changeCheckFile() {}, acceptBitrot() {}, viewScanOutput() {}, downloadFile() {}, markFileAsGood() {} };
const payload = 'Movie" onmouseover="alert(1) <img src=x onerror=alert(2)>';
const row = table.renderRow({ id: 7, file_path: payload, file_type: payload, scan_tool: payload, scan_status: 'pending' });
window.document.querySelector('#results-tbody').appendChild(row);
assert.equal(row.querySelector('.file-path-cell').textContent, payload);
assert.equal(row.querySelector('.file-path-cell').title, payload);
assert.equal(row.querySelector('[onmouseover]'), null);
assert.equal(row.querySelector('[onerror]'), null);
assert.equal(row.children[1].textContent, 'Pending');
let invoked = false;
window.app.viewFile = () => { invoked = true; };
row.querySelector('.action-buttons button').click();
assert.equal(invoked, true);
assert.equal(row.querySelector('.action-buttons button').classList.contains('btn-primary'), true);
assert.equal(row.querySelector('.file-checkbox-control input').getAttribute('aria-label'), `Select ${payload}`);

const statusCases = [
  [{ scan_status: 'pending' }, 'neutral', 'Pending'],
  [{ scan_status: 'scanning' }, 'info', 'Scanning'],
  [{ scan_status: 'unreadable' }, 'danger', 'Unreadable'],
  [{ scan_status: 'error' }, 'danger', 'Scan Error'],
  [{ scan_status: 'completed' }, 'success', 'Healthy'],
  [{ scan_status: 'completed', has_warnings: true }, 'warning', 'Warning'],
  [{ scan_status: 'completed', is_corrupted: true }, 'danger', 'Corrupted']
];
for (const [statusFile, statusClass, statusText] of statusCases) {
  const file = { id: 100, file_path: payload, file_size: 0, ...statusFile };
  const desktopStatus = table.renderRow(file).children[1].querySelector('.badge');
  const mobileStatus = table.renderMobileCard(file).querySelector('.badge');
  assert.equal(desktopStatus.classList.contains(`badge-${statusClass}`), true);
  assert.equal(mobileStatus.classList.contains(`badge-${statusClass}`), true);
  assert.equal(desktopStatus.textContent, statusText);
  assert.equal(mobileStatus.textContent, statusText);
  assert.equal(mobileStatus.closest('.result-card').querySelector('img'), null);
}

const reportsDom = new JSDOM('<table id="scan-reports-table"><tbody></tbody></table><div id="scan-reports-cards"></div><div id="scan-reports-pagination"></div>', { runScripts: 'outside-only' });
const reportsWindow = reportsDom.window;
reportsWindow.fetch = async () => ({ ok: true, json: async () => ({ reports: [{
  report_id: 'report\'"<img src=x onerror=alert(1)>', scan_type: 'full_scan', status: 'completed',
  start_time: '2025-01-01T00:00:00', duration_formatted: '<b>bad</b>', files_scanned: 1,
  files_corrupted: 0, files_with_warnings: 0, filename: 'x" onmouseover="alert(2)'
}], page: 1, pages: 1, total: 1 }) });
let reportSource = fs.readFileSync('static/js/app.js', 'utf8');
reportSource = reportSource.replace('document.addEventListener(\'DOMContentLoaded\'', 'document.addEventListener(\'testDOMContentLoaded\'');
reportSource += '\nwindow.__PixelProbeApp = PixelProbeApp;';
reportsWindow.eval(reportSource);
const reportApp = Object.create(reportsWindow.__PixelProbeApp.prototype);
reportApp.table = { formatDate: () => 'safe date' };
reportApp.selectedReports = new Set();
reportApp.updateReportSelectionUI = () => {};
reportApp.showNotification = () => {};
reportApp.loadScanReports(1);
setImmediate(() => {
  const reportText = reportsWindow.document.querySelector('#scan-reports-table tbody').textContent;
  assert.match(reportText, /<b>bad<\/b>/);
  assert.equal(reportsWindow.document.querySelector('#scan-reports-table [onclick]'), null);
  assert.equal(reportsWindow.document.querySelector('#scan-reports-table img'), null);
  assert.equal(reportsWindow.document.querySelector('#scan-reports-cards [onclick]'), null);
});

const viewerDom = new JSDOM(`
  <div id="media-viewer-modal"><div class="modal-content">
    <div class="modal-header"><h3 class="modal-title"></h3><button class="modal-close"></button></div>
    <div class="modal-body"></div>
  </div></div>`, { runScripts: 'outside-only' });
const viewerWindow = viewerDom.window;
viewerWindow.Chart = function () {};
let viewerSource = fs.readFileSync('static/js/app.js', 'utf8');
viewerSource = viewerSource.replace('document.addEventListener(\'DOMContentLoaded\'', 'document.addEventListener(\'testDOMContentLoaded\'');
viewerSource += '\nwindow.__PixelProbeApp = PixelProbeApp;';
viewerWindow.eval(viewerSource);
const viewerApp = Object.create(viewerWindow.__PixelProbeApp.prototype);
viewerApp.closeModal = () => {};
const unsafeFilename = '<img src=x onerror=alert(1)> movie.bin';
viewerApp.showMediaViewerModal({ id: 42, file_path: `/media/${unsafeFilename}`, file_type: 'application/octet-stream' });
const viewerBody = viewerWindow.document.querySelector('#media-viewer-modal .modal-body');
assert.equal(viewerBody.querySelector('.media-preview-unavailable').textContent, 'Preview not available for this file type.');
assert.equal(viewerBody.textContent.includes('<p style='), false);
assert.equal(viewerWindow.document.querySelector('#media-viewer-modal img'), null);
assert.equal(viewerWindow.document.querySelector('#media-viewer-modal .modal-title').textContent, unsafeFilename);
viewerApp.showMediaViewerModal({ id: 43, file_path: '/media/portrait.mp4', file_type: 'video/mp4' });
assert.equal(viewerBody.querySelector('.media-preview video').controls, true);
assert.equal(viewerWindow.document.querySelectorAll('#media-viewer-modal .media-viewer-footer').length, 1);
assert.equal(viewerWindow.document.querySelector('#media-viewer-modal .media-viewer-footer a').textContent, 'Download');

(async () => {
  const statsDom = new JSDOM(`
    <div id="total-files"></div><div id="healthy-files"></div><div id="corrupted-files"></div>
    <div id="warning-files"></div><div id="bitrot-files"></div><div id="pending-files"></div>
    <div id="scanning-files"></div><div id="integrity-checked"></div>
    <button id="integrity-details-toggle" aria-expanded="false"></button>
    <div id="integrity-detail-panel" hidden><div id="integrity-details"></div><div id="integrity-refresh-status"></div></div>`,
  { runScripts: 'outside-only' });
  const statsWindow = statsDom.window;
  statsWindow.Chart = function () {};
  Object.defineProperty(statsWindow.document, 'hidden', { value: false, configurable: true });
  const timers = [];
  const originalSetTimeout = global.setTimeout;
  const originalClearTimeout = global.clearTimeout;
  global.setTimeout = (callback, delay) => {
    timers.push({ callback, delay });
    return timers.length;
  };
  global.clearTimeout = () => {};
  let statsSource = fs.readFileSync('static/js/app.js', 'utf8');
  statsSource = statsSource.replace('document.addEventListener(\'DOMContentLoaded\'', 'document.addEventListener(\'testDOMContentLoaded\'');
  statsSource += '\nwindow.__StatsDashboard = StatsDashboard;';
  statsWindow.eval(statsSource);
  let shouldFail = false;
  const dashboard = new statsWindow.__StatsDashboard({
    getStats: async () => {
      if (shouldFail) throw new Error('network');
      return { total_files: 8, completed_files: 5, healthy_files: 3, corrupted_files: 1, warning_files: 0,
        pending_files: 0, scanning_files: 0, integrity: { total_files: 5, checked_percent: 40,
          attempted_files: 3, checked_files: 2, integrity_error_files: 1,
          integrity_unavailable_files: 1, never_attempted: 2, bitrot_suspected: 0 } };
    }
  });
  await dashboard.updateStats();
  assert.equal(statsWindow.document.querySelector('#integrity-detail-panel').hidden, true);
  statsWindow.document.querySelector('#integrity-details-toggle').click();
  assert.equal(statsWindow.document.querySelector('#integrity-detail-panel').hidden, false);
  assert.equal(statsWindow.document.querySelector('#integrity-details-toggle').getAttribute('aria-expanded'), 'true');
  assert.equal(statsWindow.document.querySelector('#total-files').textContent, '8');
  assert.match(statsWindow.document.querySelector('#integrity-details').textContent, /Successful integrity rechecks 2/);
  assert.match(statsWindow.document.querySelector('#integrity-details').textContent, /with no recorded recheck attempt 2/);
  assert.match(statsWindow.document.querySelector('#integrity-details').textContent, /Legacy integrity outcomes were not recorded/);
  assert.match(statsWindow.document.querySelector('#integrity-refresh-status').textContent, /Last refreshed/);
  shouldFail = true;
  dashboard.startAutoRefresh();
  await dashboard._statsPoll();
  assert.match(statsWindow.document.querySelector('#integrity-refresh-status').textContent, /Refresh failed.*Showing data from/);
  assert.equal(statsWindow.document.querySelector('#integrity-refresh-status').classList.contains('is-stale'), true);
  assert.equal(statsWindow.document.querySelector('#integrity-details-toggle').classList.contains('is-stale'), true);
  assert.equal(dashboard.statsPollDelay, 60000);
  dashboard.stopAutoRefresh();
  global.setTimeout = originalSetTimeout;
  global.clearTimeout = originalClearTimeout;
})().catch((error) => {
  process.exitCode = 1;
  throw error;
});

(async () => {
  const payload = 'Stored "value" <img src=x onerror=alert(1)>';
  const componentDom = new JSDOM(`
    <div id="schedules-list"></div><div id="excluded-paths-list"></div>
    <div id="confirm-modal"><button class="modal-close"></button><div id="confirm-title"></div><div id="confirm-message"></div></div>`,
  { runScripts: 'outside-only' });
  const componentWindow = componentDom.window;
  componentWindow.Chart = function () {};
  let componentSource = fs.readFileSync('static/js/app.js', 'utf8');
  componentSource = componentSource.replaceAll("document.addEventListener('DOMContentLoaded'", "document.addEventListener('testDOMContentLoaded'");
  componentSource += '\nwindow.__PixelProbeApp = PixelProbeApp;';
  componentWindow.eval(componentSource);
  const componentApp = Object.create(componentWindow.__PixelProbeApp.prototype);
  componentApp.formatScanType = value => value;
  let healthcheckName;
  let exclusionValue;
  componentApp.showEditSchedule = () => {};
  componentApp.showHealthcheckConfig = (_id, name) => { healthcheckName = name; };
  componentApp.toggleSchedule = () => {};
  componentApp.deleteSchedule = () => {};
  componentApp.removeExclusion = (_type, value) => { exclusionValue = value; };
  const schedule = componentApp.renderSchedule({ id: 12, name: payload, cron_expression: payload,
    scan_type: payload, scan_paths: [payload], is_active: true, has_healthcheck: false });
  componentWindow.document.querySelector('#schedules-list').appendChild(schedule);
  assert.match(schedule.textContent, /Stored "value" <img src=x onerror=alert\(1\)>/);
  assert.equal(schedule.querySelector('img'), null);
  assert.equal(schedule.querySelector('[onerror]'), null);
  schedule.querySelector('[title="Configure Healthcheck"]').click();
  assert.equal(healthcheckName, payload);
  componentApp.renderExclusionList('#excluded-paths-list', [payload], 'path', 'None');
  const exclusion = componentWindow.document.querySelector('#excluded-paths-list');
  assert.equal(exclusion.querySelector('img'), null);
  exclusion.querySelector('button').click();
  assert.equal(exclusionValue, payload);
  componentApp.showNotification(payload, 'warning');
  assert.equal(componentWindow.document.querySelector('.notification').textContent, payload);
  const confirmation = componentApp.showConfirmModal(payload, payload);
  assert.equal(componentWindow.document.querySelector('#confirm-title').textContent, payload);
  assert.equal(componentWindow.document.querySelector('#confirm-message').textContent, payload);
  assert.equal(componentWindow.document.querySelector('#confirm-modal img'), null);
  componentApp.hideConfirmModal(true);
  assert.equal(await confirmation, true);

  const authDom = new JSDOM('<div id="usersList"></div><div id="tokensList"></div>', { runScripts: 'outside-only' });
  const authWindow = authDom.window;
  authWindow.fetch = async (url) => ({ json: async () => url === '/api/users' ? {
    users: [{ id: 2, username: payload, email: payload, is_admin: false }]
  } : { tokens: [{ id: 3, description: payload, created_at: '2025-01-01T00:00:00' }] } });
  let authSource = fs.readFileSync('static/js/auth.js', 'utf8');
  authSource = authSource.replace("document.addEventListener('DOMContentLoaded'", "document.addEventListener('testDOMContentLoaded'");
  authWindow.eval(authSource);
  const auth = authWindow.AuthManager;
  auth.currentUser = { id: 1, is_admin: true };
  let deletedUser;
  let deletedToken;
  auth.deleteUser = id => { deletedUser = id; };
  auth.deleteToken = id => { deletedToken = id; };
  await auth.loadUsers();
  await auth.loadApiTokens();
  assert.equal(authWindow.document.querySelector('#usersList img'), null);
  assert.equal(authWindow.document.querySelector('#tokensList img'), null);
  assert.equal(authWindow.document.querySelector('#usersList').textContent.includes(payload), true);
  assert.equal(authWindow.document.querySelector('#tokensList').textContent.includes(payload), true);
  authWindow.document.querySelector('#usersList button').click();
  authWindow.document.querySelector('#tokensList button').click();
  assert.equal(deletedUser, 2);
  assert.equal(deletedToken, 3);
  let redirected = false;
  let logoutMessage;
  auth.redirectToLogin = () => { redirected = true; };
  auth.showNotification = message => { logoutMessage = message; };
  authWindow.fetch = async () => ({ ok: false, json: async () => ({}) });
  await auth.logout();
  assert.equal(redirected, false);
  assert.equal(logoutMessage, 'Logout failed. Your session is still active.');
  authWindow.fetch = async () => ({ ok: true, json: async () => ({}) });
  await auth.logout();
  assert.equal(redirected, true);
})().catch((error) => {
  process.exitCode = 1;
  throw error;
});
