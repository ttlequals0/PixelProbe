const assert = require('node:assert');
const fs = require('node:fs');
const { JSDOM } = require('jsdom');

const dom = new JSDOM('<table><tbody id="results-tbody"></tbody></table>', { runScripts: 'outside-only' });
const { window } = dom;
window.fetch = async () => ({ ok: true, json: async () => ({}) });
window.Chart = function () {};
let source = fs.readFileSync('static/js/app.js', 'utf8');
source = source.replace('document.addEventListener(\'DOMContentLoaded\'', '/* test suppresses bootstrap */ document.addEventListener(\'testDOMContentLoaded\'');
source += '\nwindow.__TableManager = TableManager; window.__PixelProbeApp = PixelProbeApp; window.__StatsDashboard = StatsDashboard; window.__ProgressManager = ProgressManager; window.__APIClient = APIClient; window.__initializeMobileScrollLock = initializeMobileScrollLock;';
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
for (const mode of ['hash', 'name']) {
  const file = { id: 101, file_path: payload, scan_status: 'completed', duplicate_group_size: 3, duplicate_mode: mode };
  const expected = `3 files: ${mode === 'name' ? 'same filename candidates' : 'same recorded content'}`;
  assert.equal(table.renderRow(file).querySelector('.duplicate-label').textContent, expected);
  assert.equal(table.renderMobileCard(file).querySelector('.duplicate-label').textContent, expected);
  assert.equal(table.renderMobileCard(file).querySelector('img'), null);
}
assert.equal(row.querySelector('.duplicate-label'), null);

const duplicateControls = window.document.createElement('div');
duplicateControls.innerHTML = '<button data-filter="duplicates">Duplicates</button><button data-filter="all">All Files</button><div id="duplicate-controls" hidden><select id="duplicate-mode"><option value="hash">Content</option><option value="name">Filename</option></select><p id="duplicate-help"></p></div>';
window.document.body.appendChild(duplicateControls);
const requestedDuplicateModes = [];
const duplicateTable = new window.__TableManager({ getScanResults: async params => {
  requestedDuplicateModes.push(params.duplicate_mode);
  return { results: [], total: 0 };
} });
duplicateTable.bindEvents();
window.document.querySelector('[data-filter="duplicates"]').click();
assert.equal(window.document.querySelector('#duplicate-controls').hidden, false);
assert.equal(requestedDuplicateModes.at(-1), 'hash');
const duplicateSelect = window.document.querySelector('#duplicate-mode');
duplicateSelect.value = 'name';
duplicateSelect.dispatchEvent(new window.Event('change'));
assert.equal(requestedDuplicateModes.at(-1), 'name');
assert.match(window.document.querySelector('#duplicate-help').textContent, /contents may differ/);
window.document.querySelector('[data-filter="all"]').click();
assert.equal(window.document.querySelector('#duplicate-controls').hidden, true);
assert.equal(requestedDuplicateModes.at(-1), undefined);

function makeTableWindow(width = 900) {
  const tableDom = new JSDOM(`
    <button data-filter="all">All Files</button><button data-filter="duplicates">Duplicates</button>
    <div id="duplicate-controls"><select id="duplicate-mode"><option value="hash">Hash</option><option value="name">Name</option></select><p id="duplicate-help"></p></div>
    <div id="duplicate-index-status" hidden></div>
    <select id="items-per-page"><option value="50" selected>50</option><option value="all">All</option></select>
    <select id="path-filter"><option value="">All Paths</option></select><input id="search-input">
    <input type="checkbox" id="select-all"><span class="selection-info"></span>
    <div><div class="table-container"><table><tbody id="results-tbody"></tbody></table></div></div>
    <div class="pagination"></div>`, { runScripts: 'outside-only', url: 'http://localhost' });
  const tableWindow = tableDom.window;
  Object.defineProperty(tableWindow, 'innerWidth', { value: width, writable: true, configurable: true });
  tableWindow.Chart = function () {};
  tableWindow.app = { viewFile() {}, rescanFile() {}, orphanCheckFile() {}, changeCheckFile() {}, acceptBitrot() {}, viewScanOutput() {}, downloadFile() {}, markFileAsGood() {} };
  let tableSource = fs.readFileSync('static/js/app.js', 'utf8');
  tableSource = tableSource.replace('document.addEventListener(\'DOMContentLoaded\'', 'document.addEventListener(\'testDOMContentLoaded\'');
  tableSource += '\nwindow.__TableManager = TableManager; window.__PixelProbeApp = PixelProbeApp; window.__APIClient = APIClient;';
  tableWindow.eval(tableSource);
  return tableWindow;
}

(async () => {
  const tableWindow = makeTableWindow();
  let calls = 0;
  const table = new tableWindow.__TableManager({
    getScanResults: async () => {
      calls++;
      return { results: [{ id: 1, file_path: '/media/one.mp4', scan_status: 'completed' }], total: 1 };
    }
  });
  await table.init();
  const row = tableWindow.document.querySelector('#results-tbody tr');
  row.querySelector('.file-checkbox').click();
  tableWindow.dispatchEvent(new tableWindow.Event('resize'));
  await Promise.resolve();
  assert.equal(calls, 1);
  assert.equal(tableWindow.document.querySelector('#results-tbody tr'), row);

  tableWindow.innerWidth = 700;
  tableWindow.dispatchEvent(new tableWindow.Event('resize'));
  const mobileSelection = tableWindow.document.querySelector('.mobile-results .file-checkbox');
  assert.ok(mobileSelection);
  assert.equal(mobileSelection.checked, true);
  tableWindow.innerWidth = 900;
  tableWindow.dispatchEvent(new tableWindow.Event('resize'));
  assert.equal(tableWindow.document.querySelector('#results-tbody .file-checkbox').checked, true);
  assert.equal(calls, 1);
  table.dispose();
})();

(async () => {
  const tableWindow = makeTableWindow();
  const pending = [];
  const calls = [];
  const table = new tableWindow.__TableManager({
    getScanResults: params => {
      calls.push(params.search || '');
      return new Promise(resolve => pending.push({ search: params.search || '', resolve }));
    }
  });
  table.searchQuery = 'A';
  const firstA = table.loadData();
  table.searchQuery = 'B';
  const onlyB = table.loadData();
  table.searchQuery = 'A';
  const latestA = table.loadData();
  assert.deepEqual(calls, ['A', 'B']);
  pending.find(item => item.search === 'A').resolve({ results: [{ id: 3, file_path: 'A.mp4' }], total: 100 });
  await Promise.all([firstA, latestA]);
  assert.equal(tableWindow.document.querySelector('#results-tbody .file-path-cell').textContent, 'A.mp4');
  const latestPagination = tableWindow.document.querySelector('.pagination').innerHTML;
  pending.find(item => item.search === 'B').resolve({ results: [{ id: 2, file_path: 'B.mp4' }], total: 20 });
  await onlyB;
  assert.equal(tableWindow.document.querySelector('#results-tbody .file-path-cell').textContent, 'A.mp4');
  assert.equal(tableWindow.document.querySelector('.pagination').innerHTML, latestPagination);

  table.searchQuery = 'A';
  const refreshedA = table.loadData();
  assert.deepEqual(calls, ['A', 'B', 'A']);
  pending[2].resolve({ results: [{ id: 4, file_path: 'A refreshed.mp4' }], total: 150 });
  await refreshedA;
  assert.equal(tableWindow.document.querySelector('#results-tbody .file-path-cell').textContent, 'A refreshed.mp4');
  assert.match(tableWindow.document.querySelector('.pagination').textContent, /3/);
  table.dispose();
})();

(async () => {
  const tableWindow = makeTableWindow();
  let timerCallback;
  let nextTimerId = 0;
  tableWindow.setTimeout = (callback, delay) => {
    assert.equal(delay, 30000);
    timerCallback = callback;
    return ++nextTimerId;
  };
  tableWindow.clearTimeout = () => {};
  let requests = 0;
  const table = new tableWindow.__TableManager({
    getScanResults: async () => {
      requests++;
      if (requests === 1) return { results: [{ id: 5, file_path: 'accepted.mp4' }], total: 1 };
      if (requests === 2 || requests === 4) {
        const error = new Error('initializing');
        error.status = 503;
        error.data = { duplicate_status: { ready: false, updated_at: null, stale: true } };
        throw error;
      }
      return { results: [{ id: 6, file_path: 'ready.mp4', duplicate_group_size: 2, duplicate_mode: 'hash' }],
        total: 2, duplicate_status: { ready: true, updated_at: '2026-10-10T12:00:00Z', stale: false } };
    }
  });
  await table.init();
  const oldRow = tableWindow.document.querySelector('#results-tbody tr');
  table.filter = 'duplicates';
  await table.loadData();
  assert.equal(oldRow.isConnected, false);
  assert.equal(tableWindow.document.querySelectorAll('.file-checkbox').length, 0);
  assert.equal(tableWindow.document.querySelector('.table-container').style.display, 'none');
  assert.equal(tableWindow.document.querySelector('.pagination').style.display, 'none');
  assert.match(tableWindow.document.querySelector('#duplicate-index-status').textContent, /Preparing duplicate results/);
  assert.equal(table.duplicateIndexNotReady, true);
  tableWindow.document.querySelector('#select-all').click();
  assert.deepEqual(Array.from(table.selectedFiles), []);
  timerCallback();
  await new Promise(resolve => setImmediate(resolve));
  assert.equal(requests, 3);
  assert.equal(tableWindow.document.querySelector('#results-tbody .file-path-cell').title, 'ready.mp4');
  assert.equal(tableWindow.document.querySelector('#duplicate-index-status').hidden, true);

  const acceptedDuplicateRow = tableWindow.document.querySelector('#results-tbody tr');
  await table.loadData();
  assert.equal(tableWindow.document.querySelector('#results-tbody tr'), acceptedDuplicateRow);
  assert.equal(tableWindow.document.querySelector('.table-container').style.display, '');
  assert.match(tableWindow.document.querySelector('#duplicate-index-status').textContent, /Preparing duplicate results/);

  table.filter = 'duplicates';
  let rejectOldDuplicate;
  table.api.getScanResults = params => {
    if (params.duplicate_mode) return new Promise((resolve, reject) => { rejectOldDuplicate = reject; });
    return Promise.resolve({ results: [{ id: 7, file_path: 'normal.mp4' }], total: 1 });
  };
  const oldDuplicateRequest = table.loadData();
  table.filter = 'all';
  await table.loadData();
  const oldError = new Error('still initializing');
  oldError.data = { duplicate_status: { ready: false, updated_at: null, stale: true } };
  rejectOldDuplicate(oldError);
  await oldDuplicateRequest;
  assert.equal(tableWindow.document.querySelector('#results-tbody .file-path-cell').textContent, 'normal.mp4');
  assert.equal(tableWindow.document.querySelector('#duplicate-index-status').hidden, true);
  assert.equal(table.duplicateIndexNotReady, false);
  assert.equal(table.duplicateRetryTimer, null);
  table.dispose();
})();

(async () => {
  const tableWindow = makeTableWindow(700);
  const table = new tableWindow.__TableManager({
    getScanResults: async params => ({
      results: [{ id: params.search === 'new' ? 22 : 11,
        file_path: `${params.search || 'old'}.mp4` }],
      total: 1
    })
  });
  await table.init();
  assert.equal(tableWindow.document.querySelectorAll('#results-tbody .file-checkbox').length, 0);
  table.searchQuery = 'new';
  await table.loadData();
  tableWindow.innerWidth = 900;
  tableWindow.dispatchEvent(new tableWindow.Event('resize'));
  assert.equal(tableWindow.document.querySelectorAll('.mobile-results .file-checkbox').length, 0);
  assert.deepEqual(
    Array.from(tableWindow.document.querySelectorAll('#results-tbody .file-checkbox'), checkbox => checkbox.value),
    ['22']
  );
  tableWindow.document.querySelector('#select-all').click();
  assert.deepEqual(Array.from(table.selectedFiles), [22]);
  table.dispose();
})();

(async () => {
  const appWindow = makeTableWindow();
  appWindow.fetch = async () => ({
    ok: false, status: 503,
    headers: { get: name => name === 'Retry-After' ? '30' : null },
    json: async () => ({ error: 'Duplicate index is initializing', duplicate_status: { ready: false, updated_at: null, stale: true } })
  });
  const api = new appWindow.__APIClient();
  await assert.rejects(api.getStats(), error => error.status === 503 && error.data.duplicate_status.ready === false);

  let unblockStats;
  const calls = [];
  const instance = {
    stats: { init: () => new Promise(resolve => { unblockStats = resolve; }) },
    table: { init: () => { calls.push('table'); return Promise.resolve(); } },
    loadScanPaths: () => { calls.push('paths'); },
    detectOngoingOperations: () => { calls.push('operations'); }
  };
  await appWindow.__PixelProbeApp.prototype.init.call(instance);
  assert.deepEqual(calls, ['table', 'paths', 'operations']);
  unblockStats();
})();

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

const progressDom = new JSDOM(`
  <div class="progress-container"><div class="progress-bar"><span class="progress-text"></span></div></div>
  <div class="progress-details"></div>`, { runScripts: 'outside-only' });
const progressWindow = progressDom.window;
progressWindow.Chart = function () {};
let progressSource = fs.readFileSync('static/js/app.js', 'utf8');
progressSource = progressSource.replace('document.addEventListener(\'DOMContentLoaded\'', 'document.addEventListener(\'testDOMContentLoaded\'');
progressSource += '\nwindow.__ProgressManager = ProgressManager;';
progressWindow.eval(progressSource);
const progress = new progressWindow.__ProgressManager({});
const unsafeActivePath = '"/><img src=x onerror=alert(1)>';
progress.update(10, 'Scanning files', '', false, {
  eta: '1h 2m',
  activeFileCount: 9,
  activeFiles: [
    { file: unsafeActivePath, directory: unsafeActivePath },
    { file: 'two.mp4', directory: '/media/two' },
    { file: 'three.mp4', directory: '/media/three' },
    { file: 'four.mp4', directory: '/media/four' },
    { file: 'five.mp4', directory: '/media/five' }
  ]
});
const progressDetails = progressWindow.document.querySelector('.progress-details');
assert.equal(progressDetails.querySelector('.scan-eta').textContent, 'Estimated time remaining: 1h 2m');
assert.equal(progressDetails.querySelector('.active-files summary').textContent, `Active file: ${unsafeActivePath} (+8 more)`);
assert.equal(progressDetails.querySelectorAll('.active-files li').length, 4);
assert.equal(progressDetails.querySelector('.active-files li').textContent, unsafeActivePath);
assert.equal(progressDetails.querySelector('.active-files li').title, unsafeActivePath);
assert.equal(progressDetails.querySelector('.active-files img'), null);
const activeDetails = progressDetails.querySelector('.active-files');
activeDetails.open = true;
activeDetails.dispatchEvent(new progressWindow.Event('toggle'));
progress.update(10, 'Scanning files', '', false, {
  eta: '1h 2m', activeFileCount: 9, activeFiles: [{ file: unsafeActivePath, directory: unsafeActivePath }]
});
assert.equal(progressDetails.querySelector('.active-files').open, true);
progress.update(11, 'Scanning files', '', false, { eta: '1h 1m', activeFileCount: 0, activeFiles: [] });
assert.equal(progressDetails.querySelector('.scan-eta').textContent, 'Estimated time remaining: 1h 1m');
assert.equal(progressDetails.querySelector('.active-files'), null);
progress.update(12, 'Scanning files', '', false, null);
assert.equal(progressDetails.querySelector('.scan-activity'), null);

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
const unsafeFilename = '<img src=x onerror=alert(1)> movie.bin';
const viewerBody = viewerWindow.document.querySelector('#media-viewer-modal .modal-body');
let previewResponse;
viewerWindow.fetch = async () => previewResponse;
const previewHeaders = (contentType, disposition = 'inline') => ({
  get: name => ({ 'content-type': contentType, 'content-disposition': disposition }[name.toLowerCase()] || null),
});

async function verifyViewer() {
  previewResponse = { ok: true, status: 200, headers: previewHeaders('application/octet-stream', 'attachment') };
  await viewerApp.showMediaViewerModal({ id: 42, file_path: `/media/${unsafeFilename}`, file_type: 'application/octet-stream' });
  assert.equal(viewerBody.querySelector('.media-preview-unavailable').textContent, 'Preview unavailable.');
  assert.equal(viewerBody.textContent.includes('<p style='), false);
  assert.equal(viewerWindow.document.querySelector('#media-viewer-modal img'), null);
  assert.equal(viewerWindow.document.querySelector('#media-viewer-modal .modal-title').textContent, unsafeFilename);

  previewResponse = { ok: true, status: 200, headers: previewHeaders('image/jpeg', 'inline; filename=attachment.jpg') };
  await viewerApp.showMediaViewerModal({ id: 42, file_path: '/media/photo.jpg', file_type: 'Unknown' });
  assert.notEqual(viewerBody.querySelector('.media-preview-image'), null);

  previewResponse = { ok: true, status: 200, headers: previewHeaders('video/mp4') };
  await viewerApp.showMediaViewerModal({ id: 43, file_path: '/media/portrait.mp4', file_type: 'Unknown' });
  assert.equal(viewerBody.querySelector('.media-preview video').controls, true);
  assert.equal(viewerBody.querySelector('.media-preview source').type, 'video/mp4');
  assert.equal(viewerWindow.document.querySelectorAll('#media-viewer-modal .media-viewer-footer').length, 1);
  assert.equal(viewerWindow.document.querySelector('#media-viewer-modal .media-viewer-footer a').textContent, 'Download');
  const oldVideo = viewerBody.querySelector('.media-preview video');
  await viewerApp.showMediaViewerModal({ id: 43, file_path: '/media/portrait.mp4', file_type: 'Unknown' });
  const currentVideo = viewerBody.querySelector('.media-preview video');
  oldVideo.dispatchEvent(new viewerWindow.Event('error'));
  assert.notEqual(currentVideo.style.display, 'none');
  assert.equal(viewerBody.querySelector('#video-error-43').style.display, 'none');

  previewResponse = { ok: true, status: 200, headers: previewHeaders('audio/mpeg') };
  await viewerApp.showMediaViewerModal({ id: 44, file_path: '/media/song.mp3', file_type: 'Unknown' });
  assert.equal(viewerBody.querySelector('.media-preview-audio').controls, true);
  assert.equal(viewerBody.querySelector('.media-preview-audio source').type, 'audio/mpeg');

  previewResponse = { ok: true, status: 200, headers: previewHeaders('image/jpeg') };
  await viewerApp.showMediaViewerModal({ id: 45, file_path: '/media/photo.jpg', file_type: 'Unknown' });
  viewerBody.querySelector('img').dispatchEvent(new viewerWindow.Event('error'));
  assert.equal(viewerBody.querySelector('.media-preview-unavailable').textContent, 'Preview could not be loaded.');

  previewResponse = { ok: false, status: 401, headers: previewHeaders(null) };
  await viewerApp.showMediaViewerModal({ id: 46, file_path: '/media/private.mp3', file_type: 'Unknown' });
  assert.equal(viewerBody.querySelector('.media-preview-unavailable').textContent, 'Sign in again to preview this file.');

  let resolveLateHead;
  viewerWindow.fetch = () => new Promise(resolve => { resolveLateHead = resolve; });
  const pending = viewerApp.showMediaViewerModal({ id: 47, file_path: '/media/late.jpg', file_type: 'Unknown' });
  assert.equal(viewerWindow.document.querySelector('#media-viewer-modal').style.display, 'block');
  assert.equal(viewerBody.querySelector('.media-preview-unavailable').textContent, 'Loading preview...');
  assert.equal(viewerWindow.document.querySelectorAll('#media-viewer-modal .media-viewer-footer').length, 1);
  viewerApp.closeModal('media-viewer-modal');
  resolveLateHead({ ok: true, status: 200, headers: previewHeaders('image/jpeg') });
  await pending;
  assert.equal(viewerWindow.document.querySelector('#media-viewer-modal').style.display, 'none');
  assert.equal(viewerBody.querySelector('img'), null);
  viewerWindow.fetch = async () => previewResponse;
}

(async () => {
  await verifyViewer();
  const templateDom = new JSDOM(fs.readFileSync('templates/index.html', 'utf8'));
  const duplicateCard = templateDom.window.document.querySelector('[aria-label="Duplicate files by recorded content hash"]');
  const integrityCard = templateDom.window.document.querySelector('[aria-label="Integrity coverage"]');
  const statsDom = new JSDOM(`
    <div id="total-files"></div><div id="healthy-files"></div><div id="corrupted-files"></div>
    <div id="warning-files"></div><div id="bitrot-files"></div><div id="pending-files"></div>
    <div id="scanning-files"></div>
    ${duplicateCard.outerHTML}${integrityCard.outerHTML}`,
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
        pending_files: 0, scanning_files: 0, duplicate_files: 2, duplicate_groups: 1, duplicate_extra_files: 1,
        integrity: { total_files: 5, checked_percent: 40,
          attempted_files: 3, checked_files: 2, integrity_error_files: 1,
          integrity_unavailable_files: 1, never_attempted: 2, bitrot_suspected: 0 } };
    }
  });
  await dashboard.updateStats();
  assert.equal(statsWindow.document.querySelector('#integrity-detail-panel').hidden, true);
  assert.equal(statsWindow.document.querySelector('#duplicate-detail-panel').hidden, true);
  assert.equal(statsWindow.document.querySelector('#duplicate-files').textContent, '2');
  assert.equal(statsWindow.document.querySelector('#duplicate-detail-panel').contains(
    statsWindow.document.querySelector('#duplicate-files')), false);
  assert.equal(statsWindow.document.querySelector('#duplicate-details-toggle').getAttribute('aria-expanded'), 'false');
  assert.equal(statsWindow.document.querySelector('#duplicate-details-toggle').getAttribute('aria-controls'), 'duplicate-detail-panel');
  statsWindow.document.querySelector('#duplicate-details-toggle').click();
  assert.equal(statsWindow.document.querySelector('#duplicate-detail-panel').hidden, false);
  assert.equal(statsWindow.document.querySelector('#duplicate-details-toggle').getAttribute('aria-expanded'), 'true');
  statsWindow.document.querySelector('#integrity-details-toggle').click();
  assert.equal(statsWindow.document.querySelector('#integrity-detail-panel').hidden, false);
  assert.equal(statsWindow.document.querySelector('#integrity-details-toggle').getAttribute('aria-expanded'), 'true');
  assert.equal(statsWindow.document.querySelector('#total-files').textContent, '8');
  assert.equal(statsWindow.document.querySelector('#duplicate-files').textContent, '2');
  assert.equal(statsWindow.document.querySelector('#duplicate-details').textContent, '1 group; 1 extra file');
  assert.match(statsWindow.document.querySelector('#integrity-details').textContent, /Successful integrity rechecks 2/);
  assert.match(statsWindow.document.querySelector('#integrity-details').textContent, /with no recorded recheck attempt 2/);
  assert.match(statsWindow.document.querySelector('#integrity-details').textContent, /Legacy integrity outcomes were not recorded/);
  assert.match(statsWindow.document.querySelector('#integrity-refresh-status').textContent, /Last refreshed/);
  dashboard.renderStats({ total_files: 8, healthy_files: 3, corrupted_files: 1, warning_files: 0,
    pending_files: 0, scanning_files: 0, duplicate_files: null, duplicate_groups: null,
    duplicate_extra_files: null, duplicate_status: { ready: false, updated_at: null, stale: true } });
  assert.equal(statsWindow.document.querySelector('#duplicate-files').textContent, '2');
  assert.match(statsWindow.document.querySelector('#duplicate-details').textContent, /Refresh delayed/);
  assert.equal(statsWindow.document.querySelector('.duplicate-refresh-warning').hidden, false);
  dashboard.renderStats({ total_files: 8, healthy_files: 3, corrupted_files: 1, warning_files: 0,
    pending_files: 0, scanning_files: 0, duplicate_files: 4, duplicate_groups: 2,
    duplicate_extra_files: 2, duplicate_status: { ready: true, updated_at: '2026-10-10T12:00:00Z', stale: true } });
  assert.equal(statsWindow.document.querySelector('#duplicate-files').textContent, '4');
  assert.match(statsWindow.document.querySelector('#duplicate-details').textContent, /Refresh delayed/);
  assert.equal(statsWindow.document.querySelector('#duplicate-detail-panel').hidden, false);
  assert.equal(statsWindow.document.querySelector('.duplicate-refresh-warning').hidden, false);
  assert.match(statsWindow.document.querySelector('#duplicate-details-toggle').title, /delayed/);
  dashboard.renderStats({ total_files: 8, healthy_files: 3, corrupted_files: 1, warning_files: 0,
    pending_files: 0, scanning_files: 0, duplicate_files: 4, duplicate_groups: 2,
    duplicate_extra_files: 2, duplicate_status: { ready: true, updated_at: null, stale: false } });
  assert.equal(statsWindow.document.querySelector('#duplicate-detail-panel').hidden, false);
  assert.equal(statsWindow.document.querySelector('.duplicate-refresh-warning').hidden, true);
  statsWindow.document.querySelector('#duplicate-details-toggle').click();
  assert.equal(statsWindow.document.querySelector('#duplicate-detail-panel').hidden, true);
  assert.equal(statsWindow.document.querySelector('#duplicate-details-toggle').getAttribute('aria-expanded'), 'false');
  await dashboard.updateStats();
  assert.equal(statsWindow.document.querySelector('#duplicate-detail-panel').hidden, true);
  assert.equal(statsWindow.document.querySelector('#duplicate-files').textContent, '2');

  let finishStats;
  let statsCalls = 0;
  const coalescedDashboard = new statsWindow.__StatsDashboard({
    getStats: () => {
      statsCalls++;
      return new Promise(resolve => { finishStats = resolve; });
    }
  });
  const statsRequestOne = coalescedDashboard.updateStats();
  const statsRequestTwo = coalescedDashboard.updateStats();
  assert.equal(statsCalls, 1);
  finishStats({ total_files: 1, healthy_files: 1, corrupted_files: 0, warning_files: 0,
    pending_files: 0, scanning_files: 0, duplicate_files: null, duplicate_groups: null,
    duplicate_extra_files: null, duplicate_status: { ready: false, updated_at: null, stale: true } });
  await Promise.all([statsRequestOne, statsRequestTwo]);
  assert.equal(statsWindow.document.querySelector('#duplicate-files').textContent, 'Preparing...');

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
  const lockDom = new JSDOM('<div class="sidebar"></div><div id="schedule-modal" class="modal" style="display:none"></div>', { runScripts: 'outside-only' });
  const lockWindow = lockDom.window;
  const lockBody = lockWindow.document.body;
  Object.defineProperty(lockWindow, 'innerWidth', { configurable: true, value: 390, writable: true });
  Object.defineProperty(lockWindow, 'scrollY', { configurable: true, value: 125 });
  let restoredScroll;
  lockWindow.scrollTo = (_x, y) => { restoredScroll = y; };
  lockBody.style.setProperty('overflow', 'scroll', 'important');
  lockBody.style.setProperty('position', 'relative');
  lockWindow.eval(source);
  lockWindow.__initializeMobileScrollLock();
  const sidebar = lockWindow.document.querySelector('.sidebar');
  sidebar.classList.add('active');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(lockBody.style.position, 'fixed');

  const firstModal = lockWindow.document.querySelector('#schedule-modal');
  firstModal.style.display = 'block';
  await new Promise(resolve => setTimeout(resolve, 0));
  sidebar.classList.remove('active');
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(lockBody.style.position, 'fixed');

  const secondModal = lockWindow.document.createElement('div');
  secondModal.className = 'modal';
  secondModal.style.display = 'block';
  lockBody.appendChild(secondModal);
  await new Promise(resolve => setTimeout(resolve, 0));
  firstModal.style.display = 'none';
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(lockBody.style.position, 'fixed');
  secondModal.remove();
  await new Promise(resolve => setTimeout(resolve, 0));
  assert.equal(lockBody.style.getPropertyValue('overflow'), 'scroll');
  assert.equal(lockBody.style.getPropertyPriority('overflow'), 'important');
  assert.equal(lockBody.style.position, 'relative');
  assert.equal(restoredScroll, 125);

  firstModal.style.display = 'block';
  await new Promise(resolve => setTimeout(resolve, 0));
  lockWindow.innerWidth = 900;
  lockWindow.dispatchEvent(new lockWindow.Event('resize'));
  assert.equal(lockBody.style.position, 'relative');
  assert.equal(restoredScroll, 125);

  const postcss = require('postcss');
  const css = postcss.parse(fs.readFileSync('static/css/mobile.css', 'utf8'));
  const declarations = selector => {
    const found = [];
    css.walkRules(selector, rule => rule.walkDecls(decl => found.push([decl.prop, decl.value])));
    return found;
  };
  assert(declarations('.sidebar').some(([prop, value]) => prop === 'height' && value === '100dvh'));
  assert(declarations('.modal').some(([prop, value]) => prop === 'height' && value === '100dvh'));
  assert(declarations('.modal-content').some(([prop, value]) => prop === 'max-height' && value.includes('100dvh')));
  assert(declarations('.modal-body').some(([prop, value]) => prop === 'min-height' && value === '0'));
  assert(declarations('.modal-body').some(([prop, value]) => prop === 'overflow-y' && value === 'auto'));
})().catch((error) => {
  process.exitCode = 1;
  throw error;
});

(async () => {
  const payload = 'Stored "value" <img src=x onerror=alert(1)>';
  const componentDom = new JSDOM(`
    <div id="schedules-list"></div><div id="excluded-paths-list"></div>
    <div id="excluded-extensions-list"></div>
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
  componentApp.renderExclusionList('#excluded-extensions-list', ['.tmp'], 'extension', 'None');
  const exclusion = componentWindow.document.querySelector('#excluded-paths-list');
  assert.equal(exclusion.querySelector('img'), null);
  exclusion.querySelector('button').click();
  assert.equal(exclusionValue, payload);
  const pathListBeforeFailure = exclusion.innerHTML;
  const extensionListBeforeFailure = componentWindow.document.querySelector(
    '#excluded-extensions-list').innerHTML;
  let parsedFailureResponse = false;
  componentWindow.fetch = async () => ({
    ok: false,
    status: 500,
    json: async () => {
      parsedFailureResponse = true;
      return { paths: [], extensions: [] };
    },
  });
  await componentApp.loadExclusions();
  assert.equal(parsedFailureResponse, false);
  assert.equal(exclusion.innerHTML, pathListBeforeFailure);
  assert.equal(componentWindow.document.querySelector(
    '#excluded-extensions-list').innerHTML, extensionListBeforeFailure);
  assert.equal(componentWindow.document.querySelector(
    '.notification-error').textContent, 'Failed to load exclusions');
  componentApp.showNotification(payload, 'warning');
  assert.equal(componentWindow.document.querySelector('.notification-warning').textContent, payload);
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
