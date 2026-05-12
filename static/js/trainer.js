const form = document.querySelector('#packForm');
const statusEl = document.querySelector('#status');
const runButton = document.querySelector('#runButton');
const saveButton = document.querySelector('#saveButton');
const saveAsButton = document.querySelector('#saveAsButton');
const deleteButton = document.querySelector('#deleteButton');
const printSummaryButton = document.querySelector('#printSummaryButton');
const newShipmentButton = document.querySelector('#newShipmentButton');
const refreshShipmentsButton = document.querySelector('#refreshShipmentsButton');
const resultsEl = document.querySelector('#results');
const resultTitleEl = document.querySelector('#resultTitle');
const printHeaderEl = document.querySelector('#printHeader');
const summaryEl = document.querySelector('#summary');
const palletsEl = document.querySelector('#pallets');
const packingListsEl = document.querySelector('#packingLists');
const diagnosticsEl = document.querySelector('#diagnostics');
const overflowEl = document.querySelector('#overflow');
const sampleLink = document.querySelector('#downloadSample');
const shipmentListEl = document.querySelector('#shipmentList');
const shipmentNameEl = document.querySelector('#shipmentName');

let currentShipmentId = null;
let currentItems = [];
let currentPallet = null;
let currentResult = null;
let draggedBox = null;

const sampleCsv = `sku,quantity,height,length,depth,weight
SKU-00005,30,25.27,24.18,39.84,100
SKU-00006,26,38.38,30.17,36.14,100
SKU-00009,16,28.91,29.03,27.78,100
SKU-00001,35,14.5,17.5,19.58,67.32
SKU-00010,44,11.39,11.27,4.31,7.84
SKU-00002,12,4.49,7.6,14.09,2.57
`;

function setStatus(message, kind = '') {
  statusEl.textContent = message;
  statusEl.dataset.kind = kind;
}

function metric(label, value) {
  return `<div class="metric"><span>${label}</span><strong>${value}</strong></div>`;
}

function colorForSku(sku) {
  let hash = 0;
  for (const char of sku) hash = ((hash << 5) - hash + char.charCodeAt(0)) | 0;
  return `hsl(${Math.abs(hash) % 360} 78% 78%)`;
}

function formPallet() {
  const formData = new FormData(form);
  return {
    length: Number(formData.get('pallet_length') || 48),
    depth: Number(formData.get('pallet_depth') || 40),
    max_height: Number(formData.get('max_height') || 72),
    max_overhang: Number(formData.get('max_overhang') || 0),
    allow_extra_pallets: true,
  };
}

function applyPalletToForm(pallet = {}) {
  form.elements.pallet_length.value = pallet.length ?? pallet.pallet_length ?? 48;
  form.elements.pallet_depth.value = pallet.depth ?? pallet.pallet_depth ?? 40;
  form.elements.max_height.value = pallet.max_height ?? pallet.height ?? 72;
  form.elements.max_overhang.value = pallet.max_overhang ?? pallet.overhang ?? 0;
}

function setButtons() {
  const hasResult = Boolean(currentResult);
  saveButton.disabled = !hasResult;
  saveAsButton.disabled = !hasResult;
  deleteButton.disabled = !currentShipmentId;
  printSummaryButton.disabled = !hasResult;
}

function summarizePallet(pallet) {
  const bySku = new Map();
  for (const box of pallet.boxes || []) {
    const row = bySku.get(box.sku) || { sku: box.sku, quantity: 0, weight: 0 };
    row.quantity += 1;
    row.weight += Number(box.weight || 0);
    bySku.set(box.sku, row);
  }
  return [...bySku.values()].sort((a, b) => a.sku.localeCompare(b.sku));
}

function renderSummary(result) {
  const diagnostics = result.diagnostics || {};
  const stats = result.stats || {};
  const totalWeight = (result.pallets || []).flatMap(p => p.boxes || []).reduce((sum, b) => sum + Number(b.weight || 0), 0);
  summaryEl.innerHTML = [
    metric('Shipment', shipmentNameEl.value || 'Unsaved shipment'),
    metric('Route', diagnostics.route || diagnostics.selected_route || 'Manual edit'),
    metric('Pallets used', stats.pallets_used ?? result.pallets.length),
    metric('Boxes placed', diagnostics.boxes_placed ?? stats.boxes_placed ?? 0),
    metric('Overflow', diagnostics.overflow_boxes ?? result.overflow.length),
    metric('Total weight', totalWeight.toFixed(1)),
    metric('Runtime seconds', Number(diagnostics.runtime_seconds || 0).toFixed(3)),
    metric('Violations', diagnostics.violations?.total ?? 0),
  ].join('');
  printHeaderEl.innerHTML = `<h1>${shipmentNameEl.value || 'Shipment summary'}</h1><p>Generated ${new Date().toLocaleString()}</p>`;
}

function findBox(boxId) {
  for (const pallet of currentResult?.pallets || []) {
    const index = (pallet.boxes || []).findIndex(box => box.id === boxId);
    if (index >= 0) return { pallet, index, box: pallet.boxes[index] };
  }
  return null;
}

function nextManualPosition(targetPallet, box) {
  const pallet = currentPallet || formPallet();
  const maxOverhang = Number(pallet.max_overhang || 0);
  const minX = -maxOverhang;
  const minY = -maxOverhang;
  const usableLength = Number(pallet.length || 48) + 2 * maxOverhang;
  const usableDepth = Number(pallet.depth || 40) + 2 * maxOverhang;
  const cols = Math.max(1, Math.floor(usableLength / Math.max(1, box.length)));
  const row = Math.floor((targetPallet.boxes || []).length / cols);
  const col = (targetPallet.boxes || []).length % cols;
  return {
    x: minX + Math.min(col * box.length, Math.max(0, usableLength - box.length)),
    y: minY + Math.min(row * box.depth, Math.max(0, usableDepth - box.depth)),
    z: 0,
  };
}

function moveBoxToPallet(boxId, targetPalletIndex) {
  const found = findBox(boxId);
  if (!found) return;
  const targetPallet = currentResult.pallets.find(pallet => Number(pallet.index || pallet.id) === Number(targetPalletIndex));
  if (!targetPallet || targetPallet === found.pallet) return;
  const [box] = found.pallet.boxes.splice(found.index, 1);
  Object.assign(box, nextManualPosition(targetPallet, box));
  targetPallet.boxes.push(box);
  currentResult.diagnostics = currentResult.diagnostics || {};
  currentResult.diagnostics.manual_edit = 'Boxes were moved in the browser; rerun packer to revalidate geometry.';
  currentResult.stats = currentResult.stats || {};
  currentResult.stats.pallets_used = currentResult.pallets.filter(p => p.boxes.length).length;
  renderResult(currentResult);
  setStatus('Box moved. Save shipment to keep this manual pallet assignment.', 'warn');
}

function renderPallets(result) {
  const pallet = currentPallet || formPallet();
  const palletLength = Number(pallet.length || 48);
  const palletDepth = Number(pallet.depth || 40);
  const maxOverhang = Number(pallet.max_overhang || 0);
  const usableLength = palletLength + 2 * maxOverhang;
  const usableDepth = palletDepth + 2 * maxOverhang;
  palletsEl.innerHTML = '';
  for (const palletRow of result.pallets || []) {
    const palletIndex = palletRow.index || palletRow.id;
    const card = document.createElement('article');
    card.className = 'card pallet-card';
    card.innerHTML = `<h3>Pallet ${palletIndex}</h3><p class="hint">${palletRow.boxes.length} boxes · height ${Number(palletRow.height || 0).toFixed(2)}</p><div class="footprint" data-pallet-index="${palletIndex}"></div>`;
    const footprint = card.querySelector('.footprint');
    footprint.addEventListener('dragover', event => { event.preventDefault(); footprint.classList.add('drag-over'); });
    footprint.addEventListener('dragleave', () => footprint.classList.remove('drag-over'));
    footprint.addEventListener('drop', event => {
      event.preventDefault();
      footprint.classList.remove('drag-over');
      moveBoxToPallet(event.dataTransfer.getData('text/plain'), palletIndex);
    });
    for (const box of palletRow.boxes) {
      const el = document.createElement('div');
      el.className = 'box';
      el.draggable = true;
      el.dataset.boxId = box.id;
      el.style.left = `${((box.x + maxOverhang) / usableLength) * 100}%`;
      el.style.top = `${((box.y + maxOverhang) / usableDepth) * 100}%`;
      el.style.width = `${(box.length / usableLength) * 100}%`;
      el.style.height = `${(box.depth / usableDepth) * 100}%`;
      el.style.background = colorForSku(box.sku);
      el.title = `${box.id} · ${box.sku} (${box.length} × ${box.depth} × ${box.height}) @ z ${box.z}`;
      el.textContent = box.sku.replace(/^SKU-/, '');
      el.addEventListener('dragstart', event => {
        draggedBox = box.id;
        event.dataTransfer.setData('text/plain', box.id);
      });
      el.addEventListener('dragend', () => { draggedBox = null; });
      footprint.appendChild(el);
    }
    palletsEl.appendChild(card);
  }
}

function renderPackingLists(result) {
  packingListsEl.innerHTML = (result.pallets || []).map(pallet => {
    const rows = summarizePallet(pallet).map(row => `<tr><td>${row.sku}</td><td>${row.quantity}</td><td>${row.weight.toFixed(1)}</td></tr>`).join('');
    return `<section class="card"><h4>Pallet ${pallet.index || pallet.id} packing list</h4><table><thead><tr><th>SKU</th><th>Qty</th><th>Weight</th></tr></thead><tbody>${rows}</tbody></table></section>`;
  }).join('');
}

function renderOverflow(result) {
  const overflow = result.overflow || result.unplaced || [];
  if (!overflow.length) {
    overflowEl.innerHTML = '<p class="status" data-kind="ok">No overflow boxes.</p>';
    return;
  }
  const rows = overflow.map(box => `<tr><td>${box.id}</td><td>${box.sku}</td><td>${box.reason || ''}</td></tr>`).join('');
  overflowEl.innerHTML = `<table><thead><tr><th>ID</th><th>SKU</th><th>Reason</th></tr></thead><tbody>${rows}</tbody></table>`;
}

function renderResult(result) {
  currentResult = result;
  resultsEl.hidden = false;
  resultTitleEl.textContent = `Packing result${currentShipmentId ? ` · Shipment #${currentShipmentId}` : ''}`;
  renderSummary(result);
  renderPallets(result);
  renderPackingLists(result);
  diagnosticsEl.textContent = JSON.stringify(result.diagnostics || {}, null, 2);
  renderOverflow(result);
  setButtons();
}

async function loadShipments() {
  const response = await fetch('/api/shipments');
  const payload = await response.json();
  shipmentListEl.innerHTML = '';
  if (!payload.shipments?.length) {
    shipmentListEl.innerHTML = '<p class="hint">No saved shipments yet.</p>';
    return;
  }
  for (const shipment of payload.shipments) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'shipment-row';
    button.innerHTML = `<strong>${shipment.name}</strong><span>#${shipment.id} · updated ${new Date(shipment.updated_at).toLocaleString()}</span>`;
    button.addEventListener('click', () => loadShipment(shipment.id));
    shipmentListEl.appendChild(button);
  }
}

async function loadShipment(id) {
  const response = await fetch(`/api/shipments/${id}`);
  const payload = await response.json();
  if (!response.ok) {
    setStatus((payload.errors || ['Unable to load shipment']).join('; '), 'error');
    return;
  }
  const shipment = payload.shipment;
  currentShipmentId = shipment.id;
  currentItems = shipment.items || [];
  currentPallet = shipment.pallet || formPallet();
  shipmentNameEl.value = shipment.name;
  applyPalletToForm(currentPallet);
  renderResult(shipment.result);
  setStatus(`Loaded shipment #${shipment.id}.`, 'ok');
}

async function saveShipment(asNew = false) {
  if (!currentResult) return;
  const body = {
    name: shipmentNameEl.value || 'Untitled shipment',
    items: currentItems,
    pallet: currentPallet || formPallet(),
    result: currentResult,
  };
  const url = asNew || !currentShipmentId ? '/api/shipments' : `/api/shipments/${currentShipmentId}`;
  const response = await fetch(url, {
    method: asNew || !currentShipmentId ? 'POST' : 'PUT',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  });
  const payload = await response.json();
  if (!response.ok) {
    setStatus((payload.errors || ['Unable to save shipment']).join('; '), 'error');
    return;
  }
  currentShipmentId = payload.shipment.id;
  currentItems = payload.shipment.items;
  currentPallet = payload.shipment.pallet;
  currentResult = payload.shipment.result;
  renderResult(currentResult);
  await loadShipments();
  setStatus(`Saved shipment #${currentShipmentId}.`, 'ok');
}

function resetShipment() {
  currentShipmentId = null;
  currentItems = [];
  currentPallet = formPallet();
  currentResult = null;
  shipmentNameEl.value = '';
  resultsEl.hidden = true;
  document.querySelector('#csvFile').value = '';
  setButtons();
  setStatus('Started a new unsaved shipment. Upload a CSV to pack it.');
}

form.addEventListener('submit', async event => {
  event.preventDefault();
  if (!document.querySelector('#csvFile').files.length) {
    setStatus('Please choose a CSV file first.', 'error');
    return;
  }
  runButton.disabled = true;
  setStatus('Uploading CSV and running Python packer…');
  try {
    const formData = new FormData(form);
    const response = await fetch('/api/pack-csv', { method: 'POST', body: formData });
    const payload = await response.json();
    if (!response.ok) {
      setStatus((payload.errors || ['CSV upload failed']).join('; '), 'error');
      return;
    }
    currentShipmentId = null;
    currentItems = payload.uploaded_items || [];
    currentPallet = formPallet();
    renderResult(payload);
    setStatus(`Packed ${payload.diagnostics.boxes_placed} boxes in ${payload.diagnostics.runtime_seconds.toFixed(3)} seconds. Save it to keep this shipment.`, 'ok');
  } catch (error) {
    setStatus(`Unable to run packer: ${error.message}`, 'error');
  } finally {
    runButton.disabled = false;
  }
});

saveButton.addEventListener('click', () => saveShipment(false));
saveAsButton.addEventListener('click', () => saveShipment(true));
refreshShipmentsButton.addEventListener('click', loadShipments);
newShipmentButton.addEventListener('click', resetShipment);
deleteButton.addEventListener('click', async () => {
  if (!currentShipmentId || !confirm('Delete this saved shipment?')) return;
  const response = await fetch(`/api/shipments/${currentShipmentId}`, { method: 'DELETE' });
  if (response.ok) {
    resetShipment();
    await loadShipments();
    setStatus('Shipment deleted.', 'ok');
  } else {
    setStatus('Unable to delete shipment.', 'error');
  }
});
printSummaryButton.addEventListener('click', () => window.print());

sampleLink.addEventListener('click', () => {
  const blob = new Blob([sampleCsv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'pallet-packer-sample.csv';
  link.click();
  URL.revokeObjectURL(url);
});

loadShipments().catch(error => setStatus(`Unable to load saved shipments: ${error.message}`, 'error'));
setButtons();
