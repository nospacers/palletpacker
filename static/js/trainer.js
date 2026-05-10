const form = document.querySelector('#packForm');
const statusEl = document.querySelector('#status');
const runButton = document.querySelector('#runButton');
const resultsEl = document.querySelector('#results');
const summaryEl = document.querySelector('#summary');
const palletsEl = document.querySelector('#pallets');
const diagnosticsEl = document.querySelector('#diagnostics');
const overflowEl = document.querySelector('#overflow');
const sampleLink = document.querySelector('#downloadSample');

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
  const hue = Math.abs(hash) % 360;
  return `hsl(${hue} 78% 78%)`;
}

function renderSummary(result) {
  const diagnostics = result.diagnostics || {};
  const stats = result.stats || {};
  summaryEl.innerHTML = [
    metric('Route', diagnostics.route || diagnostics.selected_route || '—'),
    metric('Pallets used', stats.pallets_used ?? result.pallets.length),
    metric('Boxes placed', diagnostics.boxes_placed ?? stats.boxes_placed ?? 0),
    metric('Overflow', diagnostics.overflow_boxes ?? result.overflow.length),
    metric('Runtime seconds', Number(diagnostics.runtime_seconds || 0).toFixed(3)),
    metric('Violations', diagnostics.violations?.total ?? 0),
  ].join('');
}

function renderPallets(result) {
  const formData = new FormData(form);
  const palletLength = Number(formData.get('pallet_length') || 48);
  const palletDepth = Number(formData.get('pallet_depth') || 40);
  const maxOverhang = Number(formData.get('max_overhang') || 0);
  const usableLength = palletLength + 2 * maxOverhang;
  const usableDepth = palletDepth + 2 * maxOverhang;
  palletsEl.innerHTML = '';
  for (const pallet of result.pallets || []) {
    const card = document.createElement('article');
    card.className = 'card pallet-card';
    card.innerHTML = `<h3>Pallet ${pallet.index || pallet.id}</h3><p class="hint">${pallet.boxes.length} boxes · height ${Number(pallet.height || 0).toFixed(2)}</p><div class="footprint"></div>`;
    const footprint = card.querySelector('.footprint');
    for (const box of pallet.boxes) {
      const el = document.createElement('div');
      el.className = 'box';
      el.style.left = `${((box.x + maxOverhang) / usableLength) * 100}%`;
      el.style.top = `${((box.y + maxOverhang) / usableDepth) * 100}%`;
      el.style.width = `${(box.length / usableLength) * 100}%`;
      el.style.height = `${(box.depth / usableDepth) * 100}%`;
      el.style.background = colorForSku(box.sku);
      el.title = `${box.sku} (${box.length} × ${box.depth} × ${box.height}) @ z ${box.z}`;
      el.textContent = box.sku.replace(/^SKU-/, '');
      footprint.appendChild(el);
    }
    palletsEl.appendChild(card);
  }
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
  resultsEl.hidden = false;
  renderSummary(result);
  renderPallets(result);
  diagnosticsEl.textContent = JSON.stringify(result.diagnostics || {}, null, 2);
  renderOverflow(result);
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
    const response = await fetch('/api/pack-csv', { method: 'POST', body: new FormData(form) });
    const payload = await response.json();
    if (!response.ok) {
      setStatus((payload.errors || ['CSV upload failed']).join('; '), 'error');
      return;
    }
    renderResult(payload);
    setStatus(`Packed ${payload.diagnostics.boxes_placed} boxes in ${payload.diagnostics.runtime_seconds.toFixed(3)} seconds.`, 'ok');
  } catch (error) {
    setStatus(`Unable to run packer: ${error.message}`, 'error');
  } finally {
    runButton.disabled = false;
  }
});

sampleLink.addEventListener('click', () => {
  const blob = new Blob([sampleCsv], { type: 'text/csv' });
  const url = URL.createObjectURL(blob);
  const link = document.createElement('a');
  link.href = url;
  link.download = 'pallet-packer-sample.csv';
  link.click();
  URL.revokeObjectURL(url);
});
