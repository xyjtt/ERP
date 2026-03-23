# 1688 直连后台自动上架 MVP

更新时间：2026-03-20

## 1. 目标

先落地一个最小可执行项目：

- 数据源：Excel / CSV
- 渠道：1688
- 执行方式：Selenium
- 登录方式：人工登录后继续
- 发布方式：先自动填充，人工复核后提交

这个 MVP 刻意不依赖聚水潭，直接进入 1688 商家后台发布页执行。

## 2. 为什么先做这个

当前 `furniture-uploader` 已经具备：

- Excel 模板读取
- 字段校验
- 图片路径校验
- 浏览器执行骨架
- 失败截图和 HTML 快照
- 1688 平台配置骨架

当前真正缺的是：

- 1688 发布页真实 selector
- 首轮联调样本

因此最短路径不是重开仓库，而是把当前仓库收敛成一个单渠道 MVP。

## 3. MVP 范围

本阶段只覆盖以下字段：

- 商品标题
- 价格
- 库存
- 主图
- 详情图
- 商品描述
- 发货地址模板
- 运费模板
- 发货时效模板

本阶段暂不强求：

- 自动类目树选择
- 复杂 SKU 规格矩阵
- 自动发布后链接回写
- 数据库强依赖
- 多渠道复用

## 4. 执行命令

先做数据校验：

```bash
python rpa/main.py --system 1688_direct --platform 1688 --file templates/furniture_template.csv --validate-only
```

运行直连 MVP：

```bash
python rpa/main.py --system 1688_direct --platform 1688 --file templates/furniture_template.csv
```

如需只联调一条数据：

```bash
python rpa/main.py --system 1688_direct --platform 1688 --file templates/furniture_template.csv --limit 1
```

## 4.1 复用已登录浏览器会话

如果本机已经在 Chrome 中完成 1688 登录，不想每次重新登录，推荐两种方式：

### 方式 A：接入已开启远程调试的 Chrome

在 `config/operator_config.local.json` 的 `browser` 中填写：

```json
{
  "browser": {
    "debugger_address": "127.0.0.1:9222",
    "keep_browser_open_on_close": true
  }
}
```

### 方式 B：使用本地 Chrome 用户目录

在 `config/operator_config.local.json` 的 `browser` 中填写：

```json
{
  "browser": {
    "user_data_dir": "C:/Users/Administrator/AppData/Local/Google/Chrome/User Data",
    "profile_directory": "Default"
  }
}
```

注意：

- 若当前默认 Chrome 已经在使用同一个用户目录，方式 B 可能会被 Chrome 锁定
- 更稳定的做法仍然是方式 A
- `selector_probe.py` 也支持同样的会话复用参数

## 4.2 推荐调试方式

推荐直接使用项目内脚本启动一个可接管的 1688 调试 Chrome：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_debug_chrome.ps1
```

如果需要显式指定已有 Chrome profile 路径，也可以：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\start_debug_chrome.ps1 -ProfileDir "C:\Users\Administrator\AppData\Local\Google\Chrome\User Data\Default"
```

登录完成后，抓 1688 关键页面：

```powershell
powershell -ExecutionPolicy Bypass -File .\scripts\capture_1688_pages.ps1
```

默认会输出到：

- `logs/selector_probe/1688/category_page.*`
- `logs/selector_probe/1688/publish_detail_page.*`

## 5. 实施顺序

### 第一步：确认样本

准备 3 到 5 条真实可发的数据，优先选：

- 同一个店铺
- 同一个类目
- 图片完整
- 无复杂规格

### 第二步：抓 selector

优先补齐以下步骤的 selector：

- `title`
- `price`
- `quantity`
- `main_image`
- `detail_images`
- `description`
- `ship_from_template`
- `freight_template`
- `ship_time_template`
- `submit_selector`

### 第三步：跑人工复核模式

保持：

- 人工登录
- 人工检查页面
- 人工最终点击发布

先确保“填得进去、页面不报错、人工能提交成功”。

### 第四步：再考虑自动提交

只有当以下条件连续稳定后，才开启 `auto_submit`：

- selector 稳定
- 必填项识别稳定
- 发布成功页可识别
- 失败提示能被捕获

## 6. 成功标准

满足以下 4 条即可视为 MVP 落地：

1. 能从 Excel 成功读取 1688 发布任务
2. 能自动填充发布页核心字段
3. 人工复核后能成功提交至少 3 条商品
4. 失败时能保留截图和页面快照

## 7. 当前最推荐的开工方式

不要先追求“全自动发布”。

先做：

- `人工登录`
- `自动填表`
- `人工确认发布`

这是当前最稳、最容易尽快看到结果的路径。
## 2026-03-23 Stability Update

- Current live 1688 smoke is stable for `title / price / quantity / main_image / detail_images / description`
- Main image upload now uses the live primary-picture React bridge, which avoids the unreliable picker opener
- Detail images are uploaded through the same bridge and then written into TinyMCE as remote URLs
- `config/platforms/1688.json` has been promoted from empty placeholders to a usable live baseline for the current publish page
