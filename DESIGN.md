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

- **Display/Hero:** `Noto Sans SC` Bold — 无需额外Display字体，Body字体加粗即可
- **Body/UI:** `Noto Sans SC` (400/500/600) — 思源黑体，中文完整，商业免费，Professional
- **Data/Tables:** `JetBrains Mono` (400/500) — 数字/IP/时间戳等宽对齐，必须支持 tabular-nums
- **Code:** `JetBrains Mono`
- **加载：** Google Fonts CDN（国内可用）
- **Scale:**

| Level | Size | Weight | Usage |
|-------|------|--------|-------|
| Page Title | 20px | 700 | 页面大标题 |
| Card Title | 16px | 600 | 卡片/弹窗标题 |
| Body | 14px | 400 | 正文、表格内容 |
| Secondary | 13px | 400 | 辅助说明 |
| Label | 12px | 500 | 表单标签、次要信息 |
| Micro | 11px | 400 | 极小标签 |

---

## Color

- **Approach:** 克制 — 主色 + 语义色，色彩是功能工具而非装饰

### Primary

| Token | HEX | Usage |
|-------|-----|-------|
| `--color-primary` | `#1a73e8` | 主按钮、链接、激活状态 |
| `--color-primary-hover` | `#1557b0` | 主色Hover |
| `--color-secondary` | `#1fb6b9` | 次要强调色 |
| `--color-accent` | `#7b61ff` | 特殊强调色（紫色） |

### Background & Surface

| Token | HEX | Usage |
|-------|-----|-------|
| `--color-bg` | `#f3f6fc` | 页面背景 |
| `--color-bg-elevated` | `#e9eefc` | 提升区块背景 |
| `--color-surface` | `#ffffff` | 卡片、表格、弹窗背景 |
| `--color-surface-strong` | `#f8faff` | 卡片内区块背景 |

### Text

| Token | HEX | Usage |
|-------|-----|-------|
| `--color-text` | `#1a2340` | 主文字（高对比度） |
| `--color-text-secondary` | `#4f5b7a` | 次要文字 |
| `--color-text-muted` | `#7b86a8` | 占位符、禁用文字 |

### Semantic — Status Colors

| Token | HEX | BG | Usage |
|-------|-----|-----|-------|
| Success | `#157347` | `#dcf7e6` | 正常状态、已归档 |
| Danger | `#c42a32` | `#fee9eb` | 故障、紧急、错误 |
| Warning | `#b7791f` | `#fff4dc` | 处理中、告警 |
| Info | `#1a73e8` | `#eaf0ff` | 提示、未关联 |

### Border

| Token | HEX | Usage |
|-------|-----|-------|
| `--color-border` | `#d8e0f3` | 默认边框、分割线 |
| `--color-border-strong` | `#7f8bb2` | 强调边框 |

### Header/Nav

| Token | Value | Usage |
|-------|-------|-------|
| `--color-header-bg` | `linear-gradient(105deg, #112a62 0%, #223f86 45%, #1a73e8 100%)` | 导航栏背景 |
| `--color-header-text` | `#f4f7ff` | 导航栏主文字 |
| `--color-header-text-muted` | `rgba(244, 247, 255, 0.84)` | 导航栏次要文字 |

---

## Spacing

- **Base unit:** 4px（电网SCADA传统是紧凑间距，符合数据密集型工具）
- **Density:** 紧凑 (Compact)

| Token | Value | Usage |
|-------|-------|-------|
| `--space-2xs` | 2px | 元素内部填充 |
| `--space-xs` | 4px | 紧密相关元素 |
| `--space-sm` | 8px | 相关组件之间 |
| `--space-md` | 16px | 组件之间（主要间距） |
| `--space-lg` | 24px | 区块之间 |
| `--space-xl` | 32px | 大区块之间 |
| `--space-2xl` | 48px | 页面边距 |

**表格行高：** 44px（桌面+触控友好）
**页面边距：** 桌面24px，移动16px

---

## Shadow

- **Approach:** 极轻阴影 — 信息密度优先，卡片不需要厚重阴影

| Token | Value | Usage |
|-------|-------|-------|
| `--shadow-sm` | `0 2px 8px rgba(26, 35, 64, 0.08)` | 默认卡片 |
| `--shadow-md` | `0 8px 20px rgba(26, 35, 64, 0.12)` | Hover/交互反馈 |
| `--shadow-lg` | `0 16px 34px rgba(17, 42, 98, 0.18)` | 弹窗 |

---

## Border Radius

- **Approach:** 克制 — 工业感不喜欢过度圆润

| Token | Value | Usage |
|-------|-------|-------|
| `--radius-sm` | 4px | 按钮、输入框、徽章 |
| `--radius-md` | 6px | 卡片、下拉 |
| `--radius-lg` | 8px | 弹窗 |
| `--radius-full` | 9999px | 标签/胶囊 |

---

## Motion

- **Approach:** 最小功能动效 — 状态清晰 > 动画美观

| Token | Duration | Usage |
|-------|---------|-------|
| `--transition-micro` | 100ms | Hover状态 |
| `--transition-short` | 150ms | 展开/收起 |
| `--transition-medium` | 250ms | 弹窗 |

**Easing:** `cubic-bezier(0.4, 0, 0.2, 1)` (Material标准缓动)

---

## Layout

- **Approach:** Grid-disciplined — 严格列布局，可预测对齐
- **Grid:** 12列，桌面12/平板8/手机4
- **Max content width:** 1200px
- **Page margin:** 桌面24px，移动16px

---

## Component Specifications

### Button

```css
.btn {
  height: 36px;           /* 桌面 */
  padding: 0 16px;
  font-size: 14px;
  font-weight: 500;
  border-radius: var(--radius-sm);
  font-family: var(--font-sans);
  transition: all 150ms cubic-bezier(0.4,0,0.2,1);
}
.btn-primary { background: var(--color-primary); color: white; }
.btn-primary:hover { background: var(--color-primary-hover); }
.btn-secondary { background: white; color: var(--color-primary); border: 1px solid var(--color-primary); }
.btn-secondary:hover { background: var(--color-info-bg); }
.btn-ghost { background: transparent; color: var(--color-primary); border: none; }
.btn-ghost:hover { background: var(--color-info-bg); }
.btn-danger { background: var(--color-danger); color: white; }
.btn:disabled { opacity: 0.5; cursor: not-allowed; }
```

**Mobile:** 触控目标最小 44px

### Card

```css
.card {
  background: var(--color-surface);
  border-radius: var(--radius-md);
  box-shadow: var(--shadow-sm);
  padding: var(--space-md);
}
```

### Table

```css
table { width: 100%; border-collapse: collapse; }
thead { background: var(--color-bg); }
th {
  text-align: left;
  padding: 12px 16px;
  font-size: 12px;
  font-weight: 500;
  color: var(--color-text-secondary);
  border-bottom: 1px solid var(--color-border);
}
td {
  padding: 12px 16px;
  font-size: 14px;
  border-bottom: 1px solid var(--color-border);
}
/* 无斑马纹 — 数据密集时斑马纹反而干扰 */
tr:hover td { background: var(--color-bg); }
```

### Modal

```css
.modal {
  background: white;
  border-radius: var(--radius-lg);
  box-shadow: var(--shadow-lg);
  max-width: 800px; /* 详情弹窗 */
}
.modal-sm { max-width: 500px; } /* 确认弹窗 */
.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  padding: 16px 20px;
  border-bottom: 1px solid var(--color-border);
}
.modal-body { padding: 20px; }
.modal-footer {
  display: flex;
  justify-content: flex-end;
  gap: 12px;
  padding: 16px 20px;
  border-top: 1px solid var(--color-border);
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
.form-input {
  width: 100%;
  height: 36px;
  padding: 0 12px;
  font-size: 14px;
  font-family: var(--font-sans);
  border: 1px solid var(--color-border);
  border-radius: var(--radius-sm);
  background: white;
  color: var(--color-text);
  transition: border-color 150ms;
}
.form-input:focus {
  outline: none;
  border-color: var(--color-primary);
  border-width: 2px;
  padding: 0 11px;
}
.form-input::placeholder { color: var(--color-text-muted); }
.form-input.error { border-color: var(--color-danger); }
.form-error {
  font-size: 12px;
  color: var(--color-danger);
  margin-top: 4px;
}
.form-label {
  display: block;
  font-size: 14px;
  font-weight: 500;
  color: var(--color-text);
  margin-bottom: 6px;
}
.form-select {
  appearance: none;
  background-image: url("data:image/svg+xml,...");
  background-repeat: no-repeat;
  background-position: right 12px center;
  padding-right: 36px;
  cursor: pointer;
}
textarea.form-input { height: auto; min-height: 80px; padding: 10px 12px; resize: vertical; }
```

### Badge/Status

```css
.badge {
  display: inline-flex;
  align-items: center;
  padding: 2px 10px;
  border-radius: var(--radius-full);
  font-size: 12px;
  font-weight: 500;
}
.badge-processing { background: var(--color-warning-bg); color: #7d4e00; }
.badge-closed { background: var(--color-success); color: white; }
.badge-urgent { background: var(--color-danger); color: white; }
.badge-info { background: var(--color-info-bg); color: var(--color-primary); }
```

---

## Responsive

- **断点:** 576px（手机）/ 768px（平板）/ 992px（桌面）
- **手机布局:** 统计卡片改为2x2网格，表格改为卡片列表
- **表单:** 全宽输入框，下拉选择器适配触控
- **触控目标:** 所有可点击元素最小 44px x 44px

---

## Accessibility

- **ARIA地标:** `<main>`、`<nav>`、`<header>`、`<footer>`
- **表单标签:** 所有 input/select 必须有 `<label>` 关联
- **颜色对比度:** 文本≥4.5:1，大文本≥3:1
- **键盘导航:** Tab焦点可见（`outline: 2px solid var(--color-primary)`）
- **错误提示:** 表单验证错误必须用文字说明，不只是颜色

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
  --color-primary: #1a73e8;
  --color-primary-hover: #1557b0;
  --color-bg: #f8f9fa;
  --color-surface: #ffffff;
  --color-text: #202124;
  --color-text-secondary: #5f6368;
  --color-text-muted: #9aa0a6;
  --color-success: #1e8e3e;
  --color-success-bg: #e6f4ea;
  --color-danger: #d93025;
  --color-danger-bg: #fce8e6;
  --color-warning: #f9ab00;
  --color-warning-bg: #fef7e0;
  --color-info: #1a73e8;
  --color-info-bg: #e8f0fe;
  --color-border: #dadce0;
  --color-border-strong: #5f6368;
  --shadow-sm: 0 1px 2px rgba(0,0,0,0.05);
  --shadow-md: 0 4px 6px rgba(0,0,0,0.07);
  --shadow-lg: 0 10px 15px rgba(0,0,0,0.10);
  --radius-sm: 4px;
  --radius-md: 6px;
  --radius-lg: 8px;
  --radius-full: 9999px;
  --font-sans: 'Noto Sans SC', -apple-system, BlinkMacSystemFont, sans-serif;
  --font-mono: 'JetBrains Mono', 'Fira Code', monospace;
  --space-2xs: 2px;
  --space-xs: 4px;
  --space-sm: 8px;
  --space-md: 16px;
  --space-lg: 24px;
  --space-xl: 32px;
  --space-2xl: 48px;
  --transition-micro: 100ms cubic-bezier(0.4, 0, 0.2, 1);
  --transition-short: 150ms cubic-bezier(0.4, 0, 0.2, 1);
  --transition-medium: 250ms cubic-bezier(0.4, 0, 0.2, 1);
}
```

---

## Decisions Log

| Date | Decision | Rationale |
|------|----------|-----------|
| 2026-03-25 | Initial design system created | Created by /design-consultation based on industrial operations tool context |
