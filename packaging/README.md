# JacarandaParse

在自己的电脑上识别收据、发票等单据，把需要的内容整理成表格。识别全部在本机完成，单据不会上传到任何地方。

[English](#english)

【发布前填写：主界面截图】

▶ [演示视频（约 3 分钟）](【发布前填写：视频链接】)

## 能做什么

- **导入单据**：照片、扫描件和 PDF，可以一次导入多份。
- **自动识别**：识别文字，并按模板取出日期、金额、商家、明细等字段。
- **逐项核对**：每个字段都标出它在原图上的位置；找不到依据或不符合校验规则的字段会被标记，方便人工确认。
  修改都有记录，原始识别结果不会被覆盖。
- **模板**：内置多个国家和地区的常用单据模板，也可以自己新建或从 Excel 导入。
- **导出**：导出为 Excel，单份或汇总均可。
- **界面语言**：简体中文、English。

## 下载

| 系统 | 安装包 |
|---|---|
| macOS（Apple 芯片） | [最新版 `.dmg`](../../releases/latest) |
| Windows 10 / 11（64 位） | [最新版 `-setup.exe`](../../releases/latest) |

暂不支持 Intel 芯片的 Mac 和 Linux。

## 系统要求

- 内存：【发布前填写：实测后的内存要求】
- 磁盘：程序约【发布前填写】，首次启动下载的识别模型约 6.6 GB，另需存放单据和导出文件的空间。
- 首次启动时需要联网下载识别模型，之后可以完全离线使用。

## 安装

本软件目前没有购买苹果和微软的代码签名证书，第一次打开时系统会提示无法验证开发者，按下面的步骤放行即可。

### macOS

1. 打开下载的 `.dmg`，把 JacarandaParse 拖进"应用程序"文件夹。
2. 双击打开。出现"无法验证开发者"的提示时，点"完成"。
3. 打开"系统设置 → 隐私与安全性"，在页面下方找到 JacarandaParse，点"仍要打开"，输入电脑登录密码。

之后就可以正常打开了。

<details>
<summary>提示"已损坏，无法打开"时</summary>

先按下文"核对下载文件"确认文件来自本页且完整，再打开"终端"执行：

```
xattr -cr /Applications/JacarandaParse.app
```

然后重新打开。这条命令会去掉系统对这个程序的下载标记，请只对确认来自本页的文件这样做。

</details>

### Windows

1. 双击下载的 `-setup.exe`。
2. 出现"Windows 已保护你的电脑"时，点"更多信息"，再点"仍要运行"。
3. 按提示完成安装，不需要管理员权限。

公司电脑可能被管理员禁止运行未签名的程序，这种情况请联系管理员。

<details>
<summary>核对下载文件</summary>

每个安装包旁边都有一个 `.sha256` 文件，里面是安装包的校验值。计算下载文件的校验值，与之对比，一致即说明文件完整、未被改动。

- macOS（终端）：`shasum -a 256 下载的文件名`
- Windows（命令提示符）：`certutil -hashfile 下载的文件名 SHA256`

</details>

## 第一次使用

1. 打开程序后，按向导下载识别模型（约 6.6 GB）。程序会自动选择能连上的下载地址，中途断开可以接着下载。
   无法联网的电脑，可以在其他电脑上下载后，放进向导中显示的文件夹。
2. 下载完成后，在"文档"页导入单据，选择模板，等待识别完成后逐项核对。
3. 核对完成后导出为 Excel。

## 数据和隐私

- 单据、识别结果和设置都只保存在本机：
  - macOS：`~/Library/Application Support/io.github.ryancamus12138-sketch.jacarandaparse/`
  - Windows：`%LOCALAPPDATA%\io.github.ryancamus12138-sketch.jacarandaparse\`
- 程序只在两种情况下联网，并且都由你点击后才进行：下载识别模型、检查和下载新版本。程序不收集、不上传任何使用数据。
- 卸载程序不会删除上面的数据文件夹；如需彻底删除，请手动删除该文件夹。

## 更新和备份

- **更新**：在"设置 → 关于"中点"检查更新"，或从本页下载新安装包直接覆盖安装。更新不会影响已有数据和已下载的模型。
- **备份**：程序每天自动备份一次数据库。重要数据建议在"设置 → 数据和备份"中做一次完整备份，存放到另一块硬盘或网盘文件夹。
- **换电脑**：在新电脑上安装后，在首次启动向导中选择"从备份恢复"。

## 问题反馈

遇到问题或有建议，请在本仓库的 [Issues](../../issues) 中提交。为便于定位问题，请写明：

- 程序版本（"设置 → 关于"中可以看到）和操作系统版本；
- 操作步骤、看到的结果和期望的结果；
- 如有报错，附上截图。

**请不要上传含有个人信息或商业信息的单据。** 需要示例时，请先遮盖敏感内容。

## 许可

JacarandaParse 由 Ryan Camus 开发，以 [Apache License 2.0](LICENSE) 发布，可以免费使用。程序附带的第三方组件的许可，
见程序内"设置 → 关于 → 第三方许可"。

识别使用的模型 [Qwen3.5-9B](https://huggingface.co/Qwen/Qwen3.5-9B)（GGUF 格式由
[unsloth](https://huggingface.co/unsloth/Qwen3.5-9B-GGUF) 转换）以 Apache License 2.0 发布，在首次启动时单独下载。

---

## English

JacarandaParse reads receipts, invoices and similar documents on your own computer and turns the fields you need into a
spreadsheet. Everything runs locally; your documents are never uploaded.

- **Import** photos, scans and PDFs, several at a time.
- **Extract** dates, amounts, merchants and line items with templates.
- **Review** each field next to its location on the original; fields without evidence or failing a check are flagged.
  Every change is recorded and the original result is kept.
- **Templates** for common documents of many countries and regions; create your own or import them from Excel.
- **Export** to Excel, one document or a summary.

**Download**: [latest release](../../releases/latest) — macOS (Apple silicon) `.dmg`, Windows 10/11 (64-bit)
`-setup.exe`. Intel Macs and Linux are not supported yet.

**First launch**: the app is not signed with Apple or Microsoft certificates yet.

- macOS: open the app once, then go to System Settings → Privacy & Security and click "Open Anyway".
- Windows: in the "Windows protected your PC" dialog, click "More info", then "Run anyway".

The setup assistant then downloads the recognition model (about 6.6 GB). After that the app works fully offline.

**Privacy**: documents, results and settings stay on your computer. The app goes online only when you click to download
the model or to check for updates, and never collects usage data.

**Feedback**: please open an [issue](../../issues) with the app version (Settings → About), your operating system, the
steps and a screenshot of any error. Do not upload documents containing personal or business information.

**License**: developed by Ryan Camus, released under the [Apache License 2.0](LICENSE). The Qwen3.5-9B model is
released under the Apache License 2.0 and downloaded separately on first launch.
