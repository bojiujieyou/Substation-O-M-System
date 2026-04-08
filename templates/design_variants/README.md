# 设计风格变体

本文件夹包含三种不同的设计风格变体，用于预览和选择最适合的界面风格。

## 📁 文件结构

```
design_variants/
├── README.md                    # 本文件
├── templates/
│   ├── index.html              # 风格选择页
│   ├── base_style1.html        # 风格1基础模板
│   ├── base_style2.html        # 风格2基础模板
│   ├── base_style3.html        # 风格3基础模板
│   ├── index_style1.html       # 风格1首页
│   ├── index_style2.html       # 风格2首页
│   └── index_style3.html       # 风格3首页
└── static/
    ├── style1.css              # 风格1样式
    ├── style2.css              # 风格2样式
    └── style3.css              # 风格3样式
```

## 🎨 三种风格说明

### 风格1：健身应用风格（温暖米色系）
- **配色**：米色/灰褐色背景，橙色渐变强调
- **特点**：圆润的卡片设计，温暖舒适，适合长时间使用
- **适用场景**：需要减少视觉疲劳的日常运维场景
- **访问地址**：http://localhost:5000/design/style1

### 风格2：金融应用风格（清爽白色系）
- **配色**：白色/浅灰背景，橙色强调
- **特点**：专业简洁，数据密集型展示
- **适用场景**：商务汇报、数据分析场景
- **访问地址**：http://localhost:5000/design/style2

### 风格3：分析仪表盘风格（数据密集型）
- **配色**：深色侧边栏，白色主内容区，红色强调
- **特点**：专业分析工具风格，信息密度高
- **适用场景**：专业监控、数据分析场景
- **访问地址**：http://localhost:5000/design/style3

## 🚀 如何使用

### 1. 启动应用
```bash
python app.py
```

### 2. 访问风格选择页
打开浏览器访问：http://localhost:5000/design

### 3. 选择风格预览
点击任意风格卡片，查看完整的首页效果

### 4. 对比效果
在不同风格之间切换，对比查看效果

## 🔄 如何应用选定的风格

如果确定使用某个风格，需要进行以下操作：

### 方案A：替换现有文件（推荐）
```bash
# 1. 备份原文件
cp templates/base.html templates/base.html.backup
cp templates/index.html templates/index.html.backup
cp static/style.css static/style.css.backup

# 2. 复制选定风格的文件（以风格1为例）
cp templates/design_variants/base_style1.html templates/base.html
cp templates/design_variants/index_style1.html templates/index.html
cp static/design_variants/style1.css static/style.css

# 3. 更新其他页面模板以使用新的base.html
# 需要手动调整其他页面（stations.html, faults.html等）以适配新的布局
```

### 方案B：创建新的主题系统
在 `config.py` 中添加主题配置：
```python
THEME = 'style1'  # 可选: style1, style2, style3, default
```

然后修改 `base.html` 根据配置加载不同的CSS文件。

## 📝 设计特点对比

| 特性 | 风格1 | 风格2 | 风格3 |
|------|-------|-------|-------|
| 导航位置 | 左侧固定 | 左侧固定 | 左侧固定 |
| 主色调 | 橙色渐变 | 橙色 | 红色 |
| 背景色 | 米色 | 白色 | 浅灰 |
| 侧边栏 | 白色 | 白色 | 深色 |
| 圆角大小 | 大（12-16px） | 中（10-14px） | 小（8-12px） |
| 卡片阴影 | 柔和 | 轻微 | 轻微 |
| 字体风格 | 友好 | 专业 | 严谨 |
| 信息密度 | 中等 | 中等 | 高 |

## 🎯 下一步计划

1. **确定风格**：选择一种风格作为主要设计方向
2. **完善页面**：将选定风格应用到所有页面（变电站列表、故障记录、统计等）
3. **响应式优化**：确保在移动端也有良好的显示效果
4. **用户测试**：收集实际用户的反馈
5. **迭代优化**：根据反馈进行调整

## 💡 技术说明

- **框架**：Flask + Jinja2模板
- **CSS**：纯CSS，使用CSS变量实现主题化
- **图表**：Chart.js
- **图标**：内联SVG
- **字体**：Inter + JetBrains Mono（Google Fonts）

## 🔧 自定义调整

如果需要调整某个风格的配色或样式：

1. 编辑对应的CSS文件（如 `static/design_variants/style1.css`）
2. 修改CSS变量部分（`:root` 选择器中的变量）
3. 刷新浏览器查看效果

例如，修改风格1的主色调：
```css
:root {
    --accent-primary: #FF6B35;  /* 改为你想要的颜色 */
    --accent-secondary: #F7931E;
}
```

## 📞 反馈

如有任何问题或建议，请联系开发团队。
