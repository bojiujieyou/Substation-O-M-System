// app.js — 前端JavaScript

// 状态显示管理
const StateManager = {
    showLoading: (element) => {
        element.innerHTML = '<div class="loading-state"><div class="spinner"></div><span>加载中...</span></div>';
        element.style.display = 'block';
    },

    showError: (element, message, onRetry) => {
        element.innerHTML = `
            <div class="error-state">
                <span class="error-icon">!</span>
                <span>${message}</span>
                ${onRetry ? '<button class="btn btn-sm btn-secondary" onclick="' + onRetry + '()">重试</button>' : ''}
            </div>
        `;
        element.style.display = 'block';
    },

    showEmpty: (element, message) => {
        element.innerHTML = `<div class="empty-state"><p>${message}</p></div>`;
        element.style.display = 'block';
    },

    hide: (element) => {
        element.style.display = 'none';
    }
};

// API请求封装
const API = {
    async get(url) {
        const response = await fetch(url);
        if (!response.ok) {
            const error = await response.json().catch(() => ({ error: '请求失败' }));
            throw new Error(error.error || `HTTP ${response.status}`);
        }
        return response.json();
    },

    async post(url, data) {
        const response = await fetch(url, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || `HTTP ${response.status}`);
        }
        return result;
    },

    async put(url, data) {
        const response = await fetch(url, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(data)
        });
        const result = await response.json();
        if (!response.ok) {
            throw new Error(result.error || `HTTP ${response.status}`);
        }
        return result;
    }
};

// 表单验证
const FormValidator = {
    required: (value) => {
        if (!value || (typeof value === 'string' && !value.trim())) {
            return '此字段为必填项';
        }
        return null;
    },

    phone: (value) => {
        if (!value) return null; // 可选
        const phonePattern = /^1[3-9]\d{9}$/;
        if (!phonePattern.test(value.replace(/\s/g, ''))) {
            return '请输入有效的手机号码';
        }
        return null;
    },

    ip: (value) => {
        if (!value) return null; // 可选
        const ipPattern = /^(\d{1,3}\.){3}\d{1,3}$/;
        if (!ipPattern.test(value)) {
            return '请输入有效的IP地址';
        }
        return null;
    }
};

// 格式化工具
const FormatUtils = {
    date: (dateString) => {
        if (!dateString) return '-';
        const date = new Date(dateString);
        return date.toLocaleString('zh-CN', {
            timeZone: 'Asia/Shanghai',
            year: 'numeric',
            month: '2-digit',
            day: '2-digit',
            hour: '2-digit',
            minute: '2-digit'
        });
    },

    number: (num) => {
        if (typeof num !== 'number') return num;
        return num.toLocaleString('zh-CN');
    }
};

// 导出供模板使用
window.StateManager = StateManager;
window.API = API;
window.FormValidator = FormValidator;
window.FormatUtils = FormatUtils;
