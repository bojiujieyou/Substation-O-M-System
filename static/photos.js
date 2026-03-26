function escapeHtml(text) {
    if (!text) return '';
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
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
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit'
    });
}

function photoCard(photo) {
    const imagePart = photo.is_image
        ? `<img src="/photos/file/${photo.id}" alt="${escapeHtml(photo.filename)}" loading="lazy">`
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

        const response = await fetch(`/api/photos/groups?${params.toString()}`);
        const data = await response.json();

        if (!response.ok) {
            throw new Error(data.error || '加载失败');
        }

        groupsCache = data.groups || [];
        unmatchedCache = data.unmatched || [];

        const flatResponse = await fetch(`/api/photos?${params.toString()}&page=1&page_size=500`);
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
    img.src = `/photos/file/${photo.id}`;

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

document.addEventListener('DOMContentLoaded', loadPhotoGroups);
