/**
 * 共享工具函数 - 所有页面通过 base.html 加载
 */

function getProjectCodeFromUrl() {
    return new URLSearchParams(window.location.search).get('project') || '';
}

function escapeHtml(text) {
    if (text == null) return '';
    const div = document.createElement('div');
    div.textContent = String(text);
    return div.innerHTML;
}

function withProject(path, extraParams) {
    if (window.AppProjectState && typeof window.AppProjectState.withProject === 'function') {
        return window.AppProjectState.withProject(path, extraParams);
    }
    const url = new URL(path, window.location.origin);
    if (extraParams) {
        Object.entries(extraParams).forEach(([key, value]) => {
            if (value !== undefined && value !== null && value !== '') {
                url.searchParams.set(key, value);
            }
        });
    }
    return `${url.pathname}${url.search}${url.hash}`;
}

async function fetchJson(url, options) {
    const response = await fetch(url, options);
    const data = await response.json().catch(() => ({}));
    if (!response.ok) {
        throw new Error(data.error || '请求失败');
    }
    return data;
}

function setInlineMessage(targetOrId, message, isError = false, normalClass = 'form-hint') {
    const el = typeof targetOrId === 'string'
        ? document.getElementById(targetOrId)
        : targetOrId;
    if (!el) return;
    el.className = isError ? 'text-danger' : normalClass;
    el.textContent = message || '';
}

function renderTableMessage(targetOrId, colspan, message, isError = false) {
    const el = typeof targetOrId === 'string'
        ? document.getElementById(targetOrId)
        : targetOrId;
    if (!el) return;
    const toneClass = isError ? 'text-danger' : 'text-muted';
    el.innerHTML = `<tr><td colspan="${Number(colspan) || 1}" class="${toneClass}">${escapeHtml(message || '')}</td></tr>`;
}

function renderBlockMessage(targetOrId, message, isError = false) {
    const el = typeof targetOrId === 'string'
        ? document.getElementById(targetOrId)
        : targetOrId;
    if (!el) return;
    const toneClass = isError ? 'text-danger' : 'text-muted';
    el.innerHTML = `<div class="${toneClass}">${escapeHtml(message || '')}</div>`;
}

window.AppProjectState = {
    projectCode: '',
    projects: [],
    defaultProjectCode: '',
    _readyPromise: null,

    ready() {
        if (!this._readyPromise) {
            this._readyPromise = this._init();
        }
        return this._readyPromise;
    },

    async _init() {
        try {
            const data = await fetchJson('/api/projects');
            this.projects = data.projects || [];
            this.defaultProjectCode = data.default_project_code || '';

            const visibleCodes = this.projects.map(project => project.code);
            const requestedCode = getProjectCodeFromUrl();
            let resolvedCode = requestedCode;

            if (!resolvedCode || !visibleCodes.includes(resolvedCode)) {
                resolvedCode = this.defaultProjectCode || visibleCodes[0] || '';
            }

            this.projectCode = resolvedCode || '';
            this._syncUrl();
            this._renderGlobalSelect();
            this._syncProjectLinks();
        } catch (error) {
            console.error('项目状态初始化失败:', error);
            this._renderGlobalSelect(true);
        }
        return this;
    },

    getProjectCode() {
        return this.projectCode || getProjectCodeFromUrl() || this.defaultProjectCode || '';
    },

    getCurrentProject() {
        const code = this.getProjectCode();
        return this.projects.find(project => project.code === code) || null;
    },

    withProject(path, extraParams) {
        const url = new URL(path, window.location.origin);
        const params = { ...(extraParams || {}) };
        const explicitProject = Object.prototype.hasOwnProperty.call(params, 'project') ? params.project : undefined;
        const projectCode = explicitProject !== undefined ? explicitProject : this.getProjectCode();

        if (projectCode) {
            url.searchParams.set('project', projectCode);
        } else {
            url.searchParams.delete('project');
        }

        Object.entries(params).forEach(([key, value]) => {
            if (key === 'project') return;
            if (value !== undefined && value !== null && value !== '') {
                url.searchParams.set(key, value);
            }
        });

        return `${url.pathname}${url.search}${url.hash}`;
    },

    navigate(projectCode) {
        this.projectCode = projectCode || '';
        const url = new URL(window.location.href);
        if (this.projectCode) {
            url.searchParams.set('project', this.projectCode);
        } else {
            url.searchParams.delete('project');
        }
        window.location.href = url.toString();
    },

    _syncUrl() {
        const url = new URL(window.location.href);
        const current = url.searchParams.get('project') || '';
        const next = this.projectCode || '';
        if (current === next) return;

        if (next) {
            url.searchParams.set('project', next);
        } else {
            url.searchParams.delete('project');
        }
        window.history.replaceState({}, '', url.toString());
    },

    _renderGlobalSelect(hasError) {
        const select = document.getElementById('global-project-select');
        if (!select) return;

        if (hasError) {
            select.innerHTML = '<option value="">项目加载失败</option>';
            select.disabled = true;
            return;
        }

        if (!this.projects.length) {
            select.innerHTML = '<option value="">无可用项目</option>';
            select.disabled = true;
            return;
        }

        select.innerHTML = this.projects.map(project => `
            <option value="${escapeHtml(project.code)}">${escapeHtml(project.name)}</option>
        `).join('');
        select.value = this.getProjectCode();
        select.disabled = false;

        if (!select.dataset.bound) {
            select.addEventListener('change', (event) => {
                this.navigate(event.target.value);
            });
            select.dataset.bound = 'true';
        }
    },

    _syncProjectLinks() {
        const links = document.querySelectorAll('[data-project-link]');
        links.forEach(link => {
            const href = link.getAttribute('href');
            if (!href || href.startsWith('javascript:') || href.startsWith('#')) {
                return;
            }
            link.setAttribute('href', this.withProject(href));
        });
    }
};

document.addEventListener('DOMContentLoaded', () => {
    if (window.AppProjectState && typeof window.AppProjectState.ready === 'function') {
        window.AppProjectState.ready();
    }
});
