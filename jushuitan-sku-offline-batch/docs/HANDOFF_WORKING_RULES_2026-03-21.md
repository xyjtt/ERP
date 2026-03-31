# 交接工作约定

更新日期：2026-03-21

## 单一真源

- 本项目正式路径：`D:\script_files\ERP\jushuitan-sku-offline-batch`
- 后续代码修改、运行、回归、交接，统一在这个目录进行
- `C:\Users\Administrator\Documents\Playground\jushuitan-sku-offline-batch` 仅保留为历史工作副本，不再作为正式开发目录

## 运行态文件

- 本地运行配置：`.env`
- 登录会话：`storage/`
- 调试产物：`artifacts/`
- 执行日志：`logs/`
- 结果输出：`results/`

这些目录可以保留在正式项目目录中使用，但继续保持不提交到 Git。

## 开发约定

- 修改代码前，先确认当前工作目录位于正式路径
- 同一时间只让一个对话或一个人直接编辑正式代码
- 其他并行尝试放在临时目录，确认方案后再合并回正式项目
- 交接时优先更新 `交接文档_当前进度.md` 与本文件

## 恢复工作建议

```powershell
cd D:\script_files\ERP\jushuitan-sku-offline-batch
npm install
npm run start
```

若只做最短调试链路，建议先使用：

```env
TARGET_PLATFORMS=taobao
MAX_PRODUCT_CODES=50
BATCH_SIZE=50
HEADLESS=false
```
