# Design System — 变电站图像监控运维平台

> 生成时间：2026-03-25
> 生成方式：/design-consultation

---

## Product Context

- **What this is:** 变电站图像监控运维平台 — 浙江丽水地区电网的内部运维工具
- **Who it's for:** 运维人员（现场检修）和管理人员（监控全局）
- **Space/industry:** 工业电网 / 基础设施监控
- **Project type:** 内部工具 / 数据仪表盘（不是公众产品或营销页）

---

## Aesthetic Direction

- **Direction:** 工业功能主义 (Industrial Utilitarian)
- **Decoration level:** 最小化 — 颜色只用于状态区分，装饰为零
- **Mood:** 专业得像一块好的电工仪表盘 — 数据清晰、状态分明、没有干扰。运维人员在高压环境下5秒完成报故障，需要的是信任感而不是玩具感。
- **Reference:** SCADA系统、航空管制台、工业控制面板

---

## Typography

- **Display/Hero:** `Inter` Bold — 首页Hero、页面大标题使用更紧凑的英文化无衬线，强调现代仪表盘感
- **Body/UI:** `Inter` (400/500/600/700) — 当前实现已统一采用 Inter，中文回退系统 sans-serif
- **Data/Tables:** `JetBrains Mono` (400/500) — 数字/IP/时间戳等宽对齐，必须支持 tabular-nums
- **Code:** `JetBrains Mono`
- **加载：** Google Fonts CDN（Inter + JetBrains Mono）
- **Scale:**

| Level | Size | Weight | Usage |
|-------|------|--------|-------|
| Page Title | 28px | 700 | 顶栏页面标题 |
| Card Title | 18px | 700 | 卡片标题 |
| Body | 14px | 400 | 正文、表格内容 |
| Secondary | 13px | 400 | 辅助说明 |
| Label | 12px | 600 | 表头、小标签、辅助控件 |
| Micro | 11px | 600 | 侧栏分组标题、极小标识 |

---

## Color

- **Approach:** 白底专业工具风格，颜色用于操作优先级、状态区分和少量强调

### Background & Surface

| Token | HEX | Usage |
|-------|-----|-------|
| `--bg-primary` | `#F7F9FC` | 页面背景、hover浅底 |
| `--bg-secondary` | `#FFFFFF` | 基础白底、按钮hover背景 |
| `--bg-elevated` | `#FFFFFF` | 提升层背景 |
| `--bg-card` | `#FFFFFF` | 卡片、表格、弹窗背景 |
| `--bg-sidebar` | `#FFFFFF` | 侧栏背景 |

### Accent

| Token | HEX | Usage |
|-------|-----|-------|
| `--accent-primary` | `#FF6B35` | 主按钮、主操作、激活强调 |
| `--accent-secondary` | `#F7931E` | 主按钮 hover、次级强调 |
| `--accent-success` | `#34C759` | 成功状态 |
| `--accent-warning` | `#FF9500` | 告警、处理中 |
| `--accent-danger` | `#FF3B30` | 错误、删除、紧急 |
| `--accent-info` | `#007AFF` | 信息提示 |

### Text

| Token | HEX | Usage |
|-------|-----|-------|
| `--text-primary` | `#1C1C1E` | 主文字 |
| `--text-secondary` | `#8E8E93` | 次要文字 |
| `--text-muted` | `#C7C7CC` | 占位、弱化说明 |
| `--text-inverse` | `#FFFFFF` | 深色/强调底上的文字 |

### Border

| Token | HEX | Usage |
|-------|-----|-------|
| `--border-color` | `#E5E5EA` | 默认边框、分割线 |
| `--border-strong` | `#D1D1D6` | hover/选中后的强化边框 |

---

## Spacing

- **Base unit:** 4px（仍保持紧凑工具密度）
- **Density:** 紧凑 (Compact)

| Token | Value | Usage |
|-------|-------|-------|
| `--space-xs` | 4px | 紧密元素 |
| `--space-sm` | 8px | 按钮内图标间距、短距离组合 |
| `--space-md` | 16px | 表单项、普通组件间距 |
| `--space-lg` | 24px | 卡片内外主间距 |
| `--space-xl` | 32px | 页面区块 |
| `--space-2xl` | 48px | 大区块、空状态留白 |

**表格行高：** 48px 左右（含 16px 单元格 padding）
**页面边距：** 桌面 32px 主内容 padding，移动端 16px

---

## Shadow

- **Approach:** 极轻阴影 — 白底卡片和弹窗只做层级区分，不做厚重悬浮感

| Token | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 1px 3px rgba(0, 0, 0, 0.06)` | 默认卡片、普通容器 |
| `--shadow-md` | `0 4px 12px rgba(0, 0, 0, 0.08)` | Hover、按钮抬升、图片卡片 |
| `--shadow-lg` | `0 10px 30px rgba(0, 0, 0, 0.1)` | 弹窗 |

---

## Border Radius

- **Approach:** 比旧稿更圆一些，但仍保持工具感，不做夸张胶囊化

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | 6px | 小按钮、紧凑输入框 |
| `--radius-md` | 10px | 常规按钮、筛选器、列表项 |
| `--radius-lg` | 14px | 卡片、弹窗 |
| `--radius-xl` | 20px | 大块容器、统计卡片 |
| `--radius-full` | 9999px | 标签/胶囊 |

---

## Motion

- **Approach:** 最小功能动效 — 统一用 `0.2s ease` 做 hover / 焦点 / 卡片反馈

- **Current usage:**
  - 按钮 hover：背景切换 + 轻微上浮
  - 卡片 hover：阴影增强
  - 侧栏/布局切换：移动端抽屉过渡

---

## Layout

- **Approach:** Grid-disciplined — 以卡片和固定间距组织信息，桌面两列、移动单列
- **Stats grid:** 4列（≤1200px 时 2列，≤768px 时 1列）
- **Charts grid:** 2列（≤768px 时 1列）
- **Sidebar width:** 256px，移动端抽屉式收起
- **Main content padding:** 桌面 32px，移动端 16px

---

## Component Specifications

### Button

```css
.btn {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: var(--space-sm);
  padding: 10px 20px;
  border-radius: var(--radius-md);
  font-size: 14px;
  font-weight: 600;
  border: none;
  cursor: pointer;
  transition: all 0.2s ease;
  text-decoration: none;
}
.btn-primary {
  background: var(--accent-primary);
  color: #fff;
}
.btn-primary:hover {
  background: var(--accent-secondary);
  transform: translateY(-1px);
  box-shadow: var(--shadow-md);
}
.btn-secondary {
  background: var(--bg-primary);
  color: var(--text-primary);
  border: 1px solid var(--border-color);
  font-weight: 500;
}
.btn-secondary:hover {
  background: var(--bg-secondary);
  border-color: var(--border-strong);
}
.btn-outline-secondary {
  background: transparent;
  color: var(--text-secondary);
  border: 1px solid var(--border-color);
  font-weight: 500;
}
.btn-outline-secondary:hover {
  background: var(--bg-primary);
  border-color: var(--border-strong);
  color: var(--text-primary);
}
.btn-ghost {
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
  background: transparent;
  color: var(--text-primary);
  border: 1px solid var(--border-color);
}
.btn-ghost:hover {
  background: var(--bg-primary);
  border-color: var(--accent-primary);
  color: var(--accent-primary);
}
.btn-danger {
  padding: 6px 12px;
  font-size: 13px;
  font-weight: 500;
  background: var(--accent-danger);
  color: #fff;
}
.btn-danger:hover {
  background: #DC2626;
  transform: translateY(-1px);
}
.btn-secondary.btn-sm {
  padding: 6px 12px;
  font-size: 12px;
}
.btn-secondary.btn-md {
  padding: 8px 16px;
  font-size: 13px;
}
```

**Mobile:** 触控目标最小 44px；导航按钮、汉堡按钮和关键操作按钮优先满足

### Card

```css
.card {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-sm);
  padding: var(--space-lg);
  border: 1px solid var(--border-color);
}
```

### Table

```css
table { width: 100%; border-collapse: collapse; }
thead { background: transparent; }
th {
  text-align: left;
  padding: 12px 16px;
  font-size: 12px;
  font-weight: 600;
  color: var(--text-secondary);
  text-transform: uppercase;
  letter-spacing: 0.05em;
  border-bottom: 1px solid var(--border-color);
}
td {
  padding: 16px;
  font-size: 14px;
  border-bottom: 1px solid var(--border-color);
}
/* 无斑马纹，hover 仅用浅底色提示 */
tr:hover td { background: var(--bg-primary); }
```

### Modal

```css
.modal-content {
  background: var(--bg-card);
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  border: 1px solid var(--border-color);
}
.modal-dialog { max-width: 800px; }
.modal-lg { max-width: 960px; }
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--border-color);
}
.modal-body { padding: 20px; }
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  padding: 16px 20px;
  border-top: 1px solid var(--border-color);
}
```

**弹窗宽度规范：**
- 详情弹窗：800px
- 故障处理弹窗：700px
- 确认弹窗：500px
- 统计图表弹窗：900px
- 移动端：弹窗宽度=屏幕宽度-32px

### Form

```css
.form-input,
.form-input-compact {
  width: 100%;
  padding: 10px 16px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  font-size: 14px;
  background: var(--bg-card);
  color: var(--text-primary);
  font-family: var(--font-sans);
  box-sizing: border-box;
}
.form-input:focus,
.form-input-compact:focus {
  outline: none;
  border-color: var(--accent-primary);
}
.form-select {
  width: 100%;
  padding: 10px 14px;
  border: 1px solid var(--border-color);
  border-radius: var(--radius-md);
  background: var(--bg-card);
  color: var(--text-primary);
  font-size: 14px;
  line-height: 1.4;
  transition: border-color 0.2s ease, box-shadow 0.2s ease;
  outline: none;
}
.form-select:focus {
  border-color: var(--accent-primary);
  box-shadow: 0 0 0 3px rgba(255, 107, 53, 0.12);
}
textarea.form-input-modal {
  resize: vertical;
  min-height: 88px;
}
```

### Badge/Status

```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 4px 12px;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 600;
}
.badge-success {
  background: rgba(52, 199, 89, 0.1);
  color: var(--accent-success);
}
.badge-warning {
  background: rgba(255, 149, 0, 0.1);
  color: var(--accent-warning);
}
.badge-danger {
  background: rgba(255, 59, 48, 0.1);
  color: var(--accent-danger);
}
.badge-info {
  background: rgba(0, 122, 255, 0.1);
  color: var(--accent-info);
}
```

---

## Responsive

- **断点:** 576px（手机）/ 768px（平板）/ 992px（桌面）
- **当前进展:** 首页、登录页、统计页、照片页、管理后台已完成主要响应式适配，其余页面继续补齐
- **手机布局:** 统计卡片可改为单列或 2x2，表格在必要时转为卡片式或横向滚动
- **表单:** 全宽输入框，下拉选择器适配触控
- **弹窗:** 移动端宽度 = 屏幕宽度 - 32px
- **触控目标:** 所有可点击元素最小 44px x 44px，导航按钮与汉堡按钮需优先满足

---

## Accessibility

- **ARIA地标:** `<main>`、`<nav>`、`<header>`、`<footer>`，基础模板已落地
- **表单标签:** 关键表单默认保留 `<label>`；登录页等极简场景允许改为 placeholder，但必须保持可理解性
- **颜色对比度:** 深色正文在白底上保持高对比，状态色主要用于强调而不是大面积铺底
- **键盘导航:** Tab焦点必须可见；当前主要输入/选择控件依赖边框高亮与 `box-shadow` 焦点环，后续可继续补强 `:focus-visible` 统一样式
- **错误提示:** 表单验证错误必须用文字说明，不只是颜色
- **装饰图标:** 纯装饰性 SVG 应加 `aria-hidden="true"`

---

## AI Design禁区（禁止的10个模式）

1. ❌ 紫色/靛蓝/渐变背景
2. ❌ 3列图标+标题+描述的卡片网格
3. ❌ 图标放在彩色圆形里作为装饰
4. ❌ 所有标题居中（`text-align: center`）
5. ❌ 所有元素使用相同的圆角半径
6. ❌ 装饰性浮动圆形/波浪SVG分隔线
7. ❌ Emoji作为设计元素（火箭/星星等）
8. ❌ 卡片左侧彩色边线（`border-left`装饰）
9. ❌ 通用欢迎语（"欢迎使用XXX"、"解锁XXX力量"）
10. ❌ 固定节节奏伐（Hero → 3特性 → 证言 → 定价 → CTA）

---

## CSS Variables Summary

```css
:root {
  --bg-primary: #F7F9FC;
  --bg-secondary: #FFFFFF;
  --bg-elevated: #FFFFFF;
  --bg-card: #FFFFFF;
  --bg-sidebar: #FFFFFF;

  --accent-primary: #FF6B35;
  --accent-secondary: #F7931E;
  --accent-success: #34C759;
  --accent-warning: #FF9500;
  --accent-danger: #FF3B30;
  --accent-info: #007AFF;

  --text-primary: #1C1C1E;
  --text-secondary: #8E8E93;
  --text-muted: #C7C7CC;
  --text-inverse: #FFFFFF;

  --border-color: #E5E5EA;
  --border-strong: #D1D1D6;

  --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06);
  --shadow-md: 0 4px 12px rgba(0, 0, 0, 0.08);
  --shadow-lg: 0 10px 30px rgba(0, 0, 0, 0.1);

  --radius-sm: 6px;
  --radius-md: 10px;
  --radius-lg: 14px;
  --radius-xl: 20px;
  --radius-full: 9999px;

  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --space-2xl: 48px;

  --font-sans: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', monospace;
}
```

---

## 当前实现基线（2026-04-06）

- **唯一生效样式文件：** `static/design_variants/style2.css`，`static/style.css` 不再作为当前设计稿基线
- **基础模板：** `templates/base.html`
- **全局工具：** `static/utils.js`（`escapeHtml`、`withProject`、`fetchJson`、`AppProjectState`）
- **全局反馈：** 使用 `showToast(message, type)` 代替原生 `alert()`
- **项目切换：** 顶栏 `#global-project-select` + 链接 `data-project-link` 联动
- **页面范围：** 首页、登录页、统计页、照片页、管理后台、故障记录页等均已按 style2 风格持续演进
- **关键布局基线：** 侧栏 256px，顶栏 sticky，主内容区 `padding: var(--space-xl)`
- **当前反馈组件：** toast 容器 + `showToast(message, type)` 已作为全局提示基线

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-25 | Initial design system created | Created by /design-consultation based on industrial operations tool context |
| 2026-04-06 | Design doc fully re-synced to style2 tokens and component examples | 颜色、间距、圆角、按钮、表单、表格、布局说明已按当前实现修正 |
