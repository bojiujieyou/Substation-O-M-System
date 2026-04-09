function photoFileUrl(photoId) {
    return withProject(`/photos/file/${photoId}`);
}

function statusText(status) {
    if (status === 'matched') return '已匹配';
    if (status === 'unmatched') return '未匹配';
    if (status === 'ignored') return '非图片';
    return status || '-';
}

function formatDate(value) {
    if (!value) return '-';
    const d = new Date(value);
    if (Number.isNaN(d.getTime())) return value;
    return d.toLocaleString('zh-CN', {
        timeZone: 'Asia/Shanghai',
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
    });
}

function photoCard(photo) {
    const imagePart = photo.is_image
        ? `<img src="${photoFileUrl(photo.id)}" alt="${escapeHtml(photo.filename)}" loading="lazy">`
        : `<div class="photo-placeholder">非图片文件</div>`;

    return `
        <article class="photo-card" data-photo-id="${photo.id}">
            <button type="button" class="photo-thumb" onclick="openPreview(${photo.id})" ${photo.is_image ? '' : 'disabled'}>
                ${imagePart}
            </button>
            <div class="photo-meta">
                <div class="photo-name" title="${escapeHtml(photo.filename)}">${escapeHtml(photo.filename)}</div>
                <div class="photo-submeta">${formatDate(photo.file_mtime)}</div>
                <div class="photo-submeta">${escapeHtml(photo.rel_path || '')}</div>
                <div class="photo-submeta"><span class="badge badge-${photo.match_status}">${statusText(photo.match_status)}</span></div>
            </div>
        </article>
    `;
}

function buildGroupSection(group) {
    return `
        <section class="card photo-group-card">
            <div class="card-body">
                <div class="photo-group-header">
                    <h3 class="card-title">${escapeHtml(group.station_name)} <span class="text-muted">(${escapeHtml(group.county || '-')})</span></h3>
                    <span class="badge badge-handling">${group.photos.length} 张</span>
                </div>
                <div class="photo-grid">
                    ${group.photos.map(photoCard).join('')}
                </div>
            </div>
        </section>
    `;
}

let groupsCache = [];
let unmatchedCache = [];
let photosFlatCache = [];
let previewZoom = 1;
let previewDragState = null;
const PREVIEW_ZOOM_MIN = 1;
const PREVIEW_ZOOM_MAX = 2.2;
const PREVIEW_ZOOM_STEP = 0.1;
const PREVIEW_WHEEL_STEP = 0.05;
const PREVIEW_TAP_ZOOM = 1.35;

function previewElements() {
    return {
        wrap: document.querySelector('#photo-preview-modal .photo-preview-wrap'),
        image: document.getElementById('photo-preview-image'),
        indicator: document.getElementById('photo-zoom-indicator'),
    };
}

function syncPreviewZoom() {
    const { wrap, image, indicator } = previewElements();
    if (!wrap || !image || !indicator) return;
    image.style.transform = `scale(${previewZoom})`;
    indicator.textContent = `${Math.round(previewZoom * 100)}%`;
    wrap.classList.toggle('is-zoomed', previewZoom > 1.01);
    if (previewZoom <= 1.01) {
        wrap.scrollLeft = 0;
        wrap.scrollTop = 0;
    }
}

function setPreviewZoom(nextZoom, options = {}) {
    const { image } = previewElements();
    if (image && Number.isFinite(options.originX) && Number.isFinite(options.originY)) {
        image.style.transformOrigin = `${options.originX}% ${options.originY}%`;
    } else if (image && Number(nextZoom) <= PREVIEW_ZOOM_MIN + 0.001) {
        image.style.transformOrigin = 'center center';
    }
    previewZoom = Math.min(PREVIEW_ZOOM_MAX, Math.max(PREVIEW_ZOOM_MIN, Number(nextZoom) || 1));
    syncPreviewZoom();
}

function adjustPreviewZoom(delta) {
    setPreviewZoom(Math.round((previewZoom + delta) * 10) / 10);
}

function resetPreviewZoom() {
    setPreviewZoom(1);
}

function startPreviewDrag(event) {
    const { wrap } = previewElements();
    if (!wrap || previewZoom <= 1.01) return;
    previewDragState = {
        startX: event.clientX,
        startY: event.clientY,
        scrollLeft: wrap.scrollLeft,
        scrollTop: wrap.scrollTop,
    };
    wrap.classList.add('is-dragging');
}

function movePreviewDrag(event) {
    const { wrap } = previewElements();
    if (!wrap || !previewDragState) return;
    wrap.scrollLeft = previewDragState.scrollLeft - (event.clientX - previewDragState.startX);
    wrap.scrollTop = previewDragState.scrollTop - (event.clientY - previewDragState.startY);
}

function stopPreviewDrag() {
    const { wrap } = previewElements();
    previewDragState = null;
    if (wrap) wrap.classList.remove('is-dragging');
}

async function loadPhotoGroups() {
    const loading = document.getElementById('photo-loading');
    const error = document.getElementById('photo-error');
    const empty = document.getElementById('photo-empty');
    const groupsEl = document.getElementById('photo-groups');
    const unmatchedEl = document.getElementById('photo-unmatched');

    loading.style.display = 'flex';
    error.style.display = 'none';
    empty.style.display = 'none';
    groupsEl.style.display = 'none';
    unmatchedEl.style.display = 'none';

    try {
        const county = document.getElementById('filter-county').value;
        const status = document.getElementById('filter-status').value;
        const keyword = document.getElementById('filter-keyword').value.trim();

        const params = new URLSearchParams();
        if (county) params.set('county', county);
        if (status) params.set('status', status);
        if (keyword) params.set('keyword', keyword);
        params.set('limit_per_group', '120');

        const response = await fetch(withProject('/api/photos/groups', Object.fromEntries(params.entries())));
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || '加载失败');
        }

        groupsCache = data.groups || [];
        unmatchedCache = data.unmatched || [];

        const flatResponse = await fetch(withProject('/api/photos', {
            ...Object.fromEntries(params.entries()),
            page: 1,
            page_size: 500
        }));
        const flatData = await flatResponse.json();
        photosFlatCache = flatResponse.ok ? (flatData.photos || []) : [];

        const hasData = groupsCache.length > 0 || unmatchedCache.length > 0;
        if (!hasData) {
            empty.style.display = 'block';
            loading.style.display = 'none';
            return;
        }

        groupsEl.innerHTML = groupsCache.map(buildGroupSection).join('');
        groupsEl.style.display = groupsCache.length ? 'block' : 'none';

        const unmatchedGrid = document.getElementById('photo-unmatched-grid');
        unmatchedGrid.innerHTML = unmatchedCache.map(photoCard).join('');
        unmatchedEl.style.display = unmatchedCache.length ? 'block' : 'none';

        loading.style.display = 'none';

    } catch (e) {
        loading.style.display = 'none';
        error.style.display = 'flex';
        document.getElementById('photo-error-text').textContent = e.message || '加载失败';
    }
}

function findPhotoById(photoId) {
    const id = Number(photoId);
    for (const group of groupsCache) {
        const found = (group.photos || []).find(p => p.id === id);
        if (found) return found;
    }
    const unmatchedFound = unmatchedCache.find(p => p.id === id);
    if (unmatchedFound) return unmatchedFound;
    return photosFlatCache.find(p => p.id === id) || null;
}

function openPreview(photoId) {
    const photo = findPhotoById(photoId);
    if (!photo || !photo.is_image) return;

    document.getElementById('photo-preview-title').textContent = photo.filename || '照片预览';
    const img = document.getElementById('photo-preview-image');
    img.src = photoFileUrl(photo.id);
    resetPreviewZoom();

    document.getElementById('photo-preview-meta').innerHTML = `
        <div class="detail-row"><span class="detail-label">文件名</span><span class="detail-value">${escapeHtml(photo.filename || '-')}</span></div>
        <div class="detail-row"><span class="detail-label">目录</span><span class="detail-value">${escapeHtml(photo.rel_path || '-')}</span></div>
        <div class="detail-row"><span class="detail-label">匹配状态</span><span class="detail-value">${statusText(photo.match_status)}</span></div>
        <div class="detail-row"><span class="detail-label">时间</span><span class="detail-value">${formatDate(photo.file_mtime)}</span></div>
    `;

    const modal = new bootstrap.Modal(document.getElementById('photo-preview-modal'));
    modal.show();
}

function resetFilters() {
    document.getElementById('filter-county').value = '';
    document.getElementById('filter-status').value = '';
    document.getElementById('filter-keyword').value = '';
    loadPhotoGroups();
}

document.getElementById('filter-search').addEventListener('click', loadPhotoGroups);
document.getElementById('filter-reset').addEventListener('click', resetFilters);
document.getElementById('filter-keyword').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        event.preventDefault();
        loadPhotoGroups();
    }
});

document.getElementById('photo-zoom-in-btn')?.addEventListener('click', () => adjustPreviewZoom(PREVIEW_ZOOM_STEP));
document.getElementById('photo-zoom-out-btn')?.addEventListener('click', () => adjustPreviewZoom(-PREVIEW_ZOOM_STEP));
document.getElementById('photo-zoom-reset-btn')?.addEventListener('click', resetPreviewZoom);

document.getElementById('photo-preview-image')?.addEventListener('click', () => {
    if (previewZoom > 1.01) {
        resetPreviewZoom();
    } else {
        setPreviewZoom(PREVIEW_TAP_ZOOM);
    }
});

document.querySelector('#photo-preview-modal .photo-preview-wrap')?.addEventListener('wheel', (event) => {
    event.preventDefault();
    const { image } = previewElements();
    if (image) {
        const rect = image.getBoundingClientRect();
        const originX = Math.min(100, Math.max(0, ((event.clientX - rect.left) / rect.width) * 100));
        const originY = Math.min(100, Math.max(0, ((event.clientY - rect.top) / rect.height) * 100));
        setPreviewZoom(
            Math.round((previewZoom + (event.deltaY < 0 ? PREVIEW_WHEEL_STEP : -PREVIEW_WHEEL_STEP)) * 10) / 10,
            { originX, originY }
        );
        return;
    }
    adjustPreviewZoom(event.deltaY < 0 ? PREVIEW_WHEEL_STEP : -PREVIEW_WHEEL_STEP);
}, { passive: false });

document.querySelector('#photo-preview-modal .photo-preview-wrap')?.addEventListener('mousedown', startPreviewDrag);
document.addEventListener('mousemove', movePreviewDrag);
document.addEventListener('mouseup', stopPreviewDrag);
document.addEventListener('mouseleave', stopPreviewDrag);

document.getElementById('photo-preview-modal')?.addEventListener('hidden.bs.modal', () => {
    stopPreviewDrag();
    resetPreviewZoom();
});

document.addEventListener('DOMContentLoaded', async () => {
    if (window.AppProjectState && typeof window.AppProjectState.ready === 'function') {
        await window.AppProjectState.ready();
    }
    loadPhotoGroups();
});
