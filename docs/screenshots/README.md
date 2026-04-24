# 页面截图说明

这个目录用于存放仓库 README 或文档中引用的页面截图。

当前仓库已经在主 README 中补充了页面预览说明和 Mermaid 流程图，后续如果需要进一步增强展示效果，建议把真实页面截图统一放到本目录，并按下面的命名方式维护：

- `dashboard-home.png`
- `stations-list.png`
- `station-detail.png`
- `fault-new.png`
- `fault-list.png`
- `statistics.png`
- `photos.png`
- `admin-import.png`
- `control-panel.png`

建议截图规范：

- 分辨率优先使用 1600x900 或 1920x1080
- 尽量使用当前主设计基线页面：`/design/style2/*`
- 截图前准备一份脱敏数据，避免真实 IP、手机号、账号信息直接出现在公开文档中
- 若用于对外展示，建议统一浏览器缩放比例和窗口尺寸

如需在 README 中插图，可使用相对路径：

```md
![站点列表](./docs/screenshots/stations-list.png)
```
