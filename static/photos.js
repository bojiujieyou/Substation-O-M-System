function photoFileUrl(photoId) {
    return withProject(`/photos/file/${photoId}`);
}

function photoThumbUrl(photoId) {
    return withProject(`/photos/thumb/${photoId}`);
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
        ? `<img src="${photoThumbUrl(photo.id)}" alt="${escapeHtml(photo.filename)}" loading="lazy">`
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
const DEFAULT_FAULT_ONLY = false;
let faultOnlyMode = DEFAULT_FAULT_ONLY;
let previewZoom = 1;
let previewDragState = null;
let currentPreviewPhotoId = null;
const PREVIEW_ZOOM_MIN = 1;
const PREVIEW_ZOOM_MAX = 10;
const PREVIEW_ZOOM_STEP = 0.2;
const PREVIEW_WHEEL_STEP = 0.12;

function previewElements() {
    return {
        wrap: document.querySelector('#photo-preview-modal .photo-preview-wrap'),
        canvas: document.getElementById('photo-preview-canvas'),
        image: document.getElementById('photo-preview-image'),
        indicator: document.getElementById('photo-zoom-indicator'),
        sequence: document.getElementById('photo-preview-sequence'),
        prev: document.getElementById('photo-preview-prev'),
        next: document.getElementById('photo-preview-next'),
        modal: document.getElementById('photo-preview-modal'),
    };
}

function previewablePhotos() {
    return [...groupsCache.flatMap((group) => group.photos || []), ...unmatchedCache]
        .filter((photo) => photo && photo.is_image);
}

function previewIndex(photoId) {
    return previewablePhotos().findIndex((photo) => photo.id === Number(photoId));
}

function clamp(value, min, max) {
    return Math.min(max, Math.max(min, value));
}

function syncPreviewNavigation() {
    const { prev, next, sequence } = previewElements();
    const photos = previewablePhotos();
    const index = previewIndex(currentPreviewPhotoId);
    const hasCurrent = index >= 0;

    if (sequence) {
        sequence.textContent = hasCurrent ? `${index + 1} / ${photos.length}` : '- / -';
    }
    if (prev) {
        prev.disabled = !hasCurrent || index <= 0;
    }
    if (next) {
        next.disabled = !hasCurrent || index >= photos.length - 1;
    }
}

function resolvePreviewImageSize() {
    const { wrap, image } = previewElements();
    if (!wrap || !image || !image.naturalWidth || !image.naturalHeight) return null;

    const wrapStyles = window.getComputedStyle(wrap);
    const paddingX = (parseFloat(wrapStyles.paddingLeft) || 0) + (parseFloat(wrapStyles.paddingRight) || 0);
    const paddingY = (parseFloat(wrapStyles.paddingTop) || 0) + (parseFloat(wrapStyles.paddingBottom) || 0);
    const availableWidth = Math.max(120, wrap.clientWidth - paddingX);
    const availableHeight = Math.max(120, wrap.clientHeight - paddingY);
    const fitScale = Math.min(availableWidth / image.naturalWidth, availableHeight / image.naturalHeight);

    return {
        width: Math.max(1, image.naturalWidth * fitScale),
        height: Math.max(1, image.naturalHeight * fitScale),
        availableWidth,
        availableHeight,
    };
}

function syncPreviewZoom(options = {}) {
    const { wrap, canvas, image, indicator } = previewElements();
    if (!wrap || !canvas || !image || !indicator) return;
    const imageSize = resolvePreviewImageSize();
    if (!imageSize) return;

    const focusOffsetX = Number.isFinite(options.offsetX) ? options.offsetX : wrap.clientWidth / 2;
    const focusOffsetY = Number.isFinite(options.offsetY) ? options.offsetY : wrap.clientHeight / 2;
    const currentImageWidth = image.offsetWidth || (imageSize.width * Math.max(previewZoom, PREVIEW_ZOOM_MIN));
    const currentImageHeight = image.offsetHeight || (imageSize.height * Math.max(previewZoom, PREVIEW_ZOOM_MIN));
    const currentImageLeft = image.offsetLeft || 0;
    const currentImageTop = image.offsetTop || 0;
    const cursorContentX = wrap.scrollLeft + focusOffsetX;
    const cursorContentY = wrap.scrollTop + focusOffsetY;
    const relativeX = currentImageWidth > 0
        ? clamp((cursorContentX - currentImageLeft) / currentImageWidth, 0, 1)
        : 0.5;
    const relativeY = currentImageHeight > 0
        ? clamp((cursorContentY - currentImageTop) / currentImageHeight, 0, 1)
        : 0.5;

    const scaledWidth = imageSize.width * previewZoom;
    const scaledHeight = imageSize.height * previewZoom;
    const canvasWidth = Math.max(imageSize.availableWidth, scaledWidth);
    const canvasHeight = Math.max(imageSize.availableHeight, scaledHeight);
    const maxImageLeft = Math.max(0, canvasWidth - scaledWidth);
    const maxImageTop = Math.max(0, canvasHeight - scaledHeight);
    const nextImageLeft = clamp(focusOffsetX - (relativeX * scaledWidth), 0, maxImageLeft);
    const nextImageTop = clamp(focusOffsetY - (relativeY * scaledHeight), 0, maxImageTop);

    image.style.width = `${scaledWidth}px`;
    image.style.height = `${scaledHeight}px`;
    image.style.left = `${nextImageLeft}px`;
    image.style.top = `${nextImageTop}px`;
    canvas.style.width = `${canvasWidth}px`;
    canvas.style.height = `${canvasHeight}px`;
    image.style.opacity = '1';
    indicator.textContent = `${Math.round(previewZoom * 100)}%`;
    const overflowing = scaledWidth > imageSize.availableWidth + 1 || scaledHeight > imageSize.availableHeight + 1;
    wrap.classList.toggle('is-zoomed', overflowing);
    if (previewZoom <= 1.01) {
        wrap.scrollLeft = 0;
        wrap.scrollTop = 0;
        return;
    }

    const nextImageWidth = image.offsetWidth || (imageSize.width * previewZoom);
    const nextImageHeight = image.offsetHeight || (imageSize.height * previewZoom);
    const maxScrollLeft = Math.max(0, wrap.scrollWidth - wrap.clientWidth);
    const maxScrollTop = Math.max(0, wrap.scrollHeight - wrap.clientHeight);
    wrap.scrollLeft = clamp(nextImageLeft + (relativeX * nextImageWidth) - focusOffsetX, 0, maxScrollLeft);
    wrap.scrollTop = clamp(nextImageTop + (relativeY * nextImageHeight) - focusOffsetY, 0, maxScrollTop);
}

function setPreviewZoom(nextZoom, options = {}) {
    previewZoom = Math.min(PREVIEW_ZOOM_MAX, Math.max(PREVIEW_ZOOM_MIN, Number(nextZoom) || 1));
    syncPreviewZoom(options);
}

function adjustPreviewZoom(delta) {
    setPreviewZoom(Math.round((previewZoom + delta) * 100) / 100);
}

function resetPreviewZoom() {
    setPreviewZoom(1);
}

function startPreviewDrag(event) {
    const { wrap } = previewElements();
    if (!wrap || previewZoom <= 1.01 || event.button !== 0) return;
    event.preventDefault();
    previewDragState = {
        startX: event.clientX,
        startY: event.clientY,
        scrollLeft: wrap.scrollLeft,
        scrollTop: wrap.scrollTop,
    };
    wrap.classList.add('is-dragging');
    document.body.style.cursor = 'grabbing';
}

function movePreviewDrag(event) {
    const { wrap } = previewElements();
    if (!wrap || !previewDragState) return;
    event.preventDefault();
    const deltaX = event.clientX - previewDragState.startX;
    const deltaY = event.clientY - previewDragState.startY;
    wrap.scrollLeft = previewDragState.scrollLeft - deltaX;
    wrap.scrollTop = previewDragState.scrollTop - deltaY;
}

function stopPreviewDrag() {
    const { wrap } = previewElements();
    previewDragState = null;
    if (wrap) wrap.classList.remove('is-dragging');
    document.body.style.cursor = '';
}

function syncFaultOnlyToggle() {
    const button = document.getElementById('filter-fault-only');
    if (!button) return;
    button.setAttribute('aria-pressed', faultOnlyMode ? 'true' : 'false');
    button.textContent = faultOnlyMode ? '只看故障站点中' : '只看故障站点';
    button.title = faultOnlyMode ? '当前仅显示存在未关闭故障的站点照片，点击恢复全部照片' : '点击后仅显示存在未关闭故障的站点照片';
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
        if (faultOnlyMode) params.set('has_fault', '1');
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
            syncPreviewNavigation();
            empty.style.display = 'block';
            loading.style.display = 'none';
            return;
        }

        groupsEl.innerHTML = groupsCache.map(buildGroupSection).join('');
        groupsEl.style.display = groupsCache.length ? 'block' : 'none';

        const unmatchedGrid = document.getElementById('photo-unmatched-grid');
        unmatchedGrid.innerHTML = unmatchedCache.map(photoCard).join('');
        unmatchedEl.style.display = unmatchedCache.length ? 'block' : 'none';
        syncPreviewNavigation();

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
    currentPreviewPhotoId = photo.id;

    document.getElementById('photo-preview-title').textContent = photo.filename || '照片预览';
    const img = document.getElementById('photo-preview-image');
    const canvas = document.getElementById('photo-preview-canvas');
    img.style.opacity = '0';
    img.style.width = '';
    img.style.height = '';
    if (canvas) {
        canvas.style.width = '';
        canvas.style.height = '';
    }
    img.onload = () => {
        previewZoom = 1;
        syncPreviewZoom();
    };
    img.src = photoFileUrl(photo.id);
    if (img.complete) {
        previewZoom = 1;
        syncPreviewZoom();
    }
    syncPreviewNavigation();

    document.getElementById('photo-preview-meta').innerHTML = `
        <div class="detail-row"><span class="detail-label">文件名</span><span class="detail-value">${escapeHtml(photo.filename || '-')}</span></div>
        <div class="detail-row"><span class="detail-label">目录</span><span class="detail-value">${escapeHtml(photo.rel_path || '-')}</span></div>
        <div class="detail-row"><span class="detail-label">匹配状态</span><span class="detail-value">${statusText(photo.match_status)}</span></div>
        <div class="detail-row"><span class="detail-label">时间</span><span class="detail-value">${formatDate(photo.file_mtime)}</span></div>
    `;

    const modalEl = document.getElementById('photo-preview-modal');
    const modal = bootstrap.Modal.getOrCreateInstance(modalEl);
    if (!modalEl.classList.contains('show')) {
        modal.show();
    }
}

function stepPreview(offset) {
    const photos = previewablePhotos();
    const index = previewIndex(currentPreviewPhotoId);
    if (index < 0) return;
    const target = photos[index + offset];
    if (!target) return;
    openPreview(target.id);
}

function resetFilters() {
    document.getElementById('filter-county').value = '';
    document.getElementById('filter-status').value = '';
    document.getElementById('filter-keyword').value = '';
    faultOnlyMode = DEFAULT_FAULT_ONLY;
    syncFaultOnlyToggle();
    loadPhotoGroups();
}

function toggleFaultOnlyMode() {
    faultOnlyMode = !faultOnlyMode;
    syncFaultOnlyToggle();
    loadPhotoGroups();
}

document.getElementById('filter-search').addEventListener('click', loadPhotoGroups);
document.getElementById('filter-reset').addEventListener('click', resetFilters);
document.getElementById('filter-fault-only')?.addEventListener('click', toggleFaultOnlyMode);
document.getElementById('filter-keyword').addEventListener('keydown', (event) => {
    if (event.key === 'Enter') {
        event.preventDefault();
        loadPhotoGroups();
    }
});

document.getElementById('photo-zoom-in-btn')?.addEventListener('click', () => adjustPreviewZoom(PREVIEW_ZOOM_STEP));
document.getElementById('photo-zoom-out-btn')?.addEventListener('click', () => adjustPreviewZoom(-PREVIEW_ZOOM_STEP));
document.getElementById('photo-zoom-reset-btn')?.addEventListener('click', resetPreviewZoom);
document.getElementById('photo-preview-prev')?.addEventListener('click', () => stepPreview(-1));
document.getElementById('photo-preview-next')?.addEventListener('click', () => stepPreview(1));

document.querySelector('#photo-preview-modal .photo-preview-wrap')?.addEventListener('wheel', (event) => {
    event.preventDefault();
    const { wrap } = previewElements();
    if (!wrap) return;
    const rect = wrap.getBoundingClientRect();
    setPreviewZoom(
        Math.round((previewZoom + (event.deltaY < 0 ? PREVIEW_WHEEL_STEP : -PREVIEW_WHEEL_STEP)) * 100) / 100,
        {
            offsetX: event.clientX - rect.left,
            offsetY: event.clientY - rect.top,
        }
    );
}, { passive: false });

document.querySelector('#photo-preview-modal .photo-preview-wrap')?.addEventListener('mousedown', startPreviewDrag);
document.addEventListener('mousemove', movePreviewDrag);
document.addEventListener('mouseup', stopPreviewDrag);
document.addEventListener('mouseleave', stopPreviewDrag);
document.addEventListener('keydown', (event) => {
    const { modal } = previewElements();
    if (!modal || !modal.classList.contains('show')) return;
    if (event.key === 'ArrowLeft') {
        event.preventDefault();
        stepPreview(-1);
    } else if (event.key === 'ArrowRight') {
        event.preventDefault();
        stepPreview(1);
    }
});

document.getElementById('photo-preview-modal')?.addEventListener('hidden.bs.modal', () => {
    currentPreviewPhotoId = null;
    stopPreviewDrag();
    resetPreviewZoom();
    syncPreviewNavigation();
});

document.getElementById('photo-preview-modal')?.addEventListener('shown.bs.modal', () => {
    syncPreviewZoom();
});

window.addEventListener('resize', () => {
    const { modal } = previewElements();
    if (modal && modal.classList.contains('show')) {
        syncPreviewZoom();
    }
});

document.addEventListener('DOMContentLoaded', async () => {
    if (window.AppProjectState && typeof window.AppProjectState.ready === 'function') {
        await window.AppProjectState.ready();
    }
    syncFaultOnlyToggle();
    loadPhotoGroups();
});
