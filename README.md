# 逐字输入器 · TypeWriter IME

把剪贴板里的文字一个字符一个字符"敲"进当前焦点窗口——接收方看到的每一次按键都和真人打字一样，而不是一次粘贴。

仅依赖 Python 标准库，Windows 专用。设计原理见 [设计文档.md](设计文档.md)。

## 下载 exe（无需装 Python）

到 [Releases](https://github.com/panxian-AI/typewriter-ime/releases/latest) 下载：

| 文件 | 说明 |
| --- | --- |
| `typewriter-ime-v1.0.0-win.zip` | **推荐**，两个 exe + 使用说明，解压即用 |
| `TypeWriterIME.exe` | 图形界面版，双击即用 |
| `TypeWriterIME-cli.exe` | 命令行版 |

> PyInstaller 单文件包无数字签名，杀毒软件可能误报，添加信任即可。

## 快速开始（源码运行）

```bash
# 图形界面（推荐）
python typewriter_ime.py

# 命令行：读剪贴板，倒计时 3 秒后逐字敲进焦点窗口
python typewriter_ime.py --clipboard
```

## 图形界面用法

1. 把要输入的内容粘贴进文本框（或点"读取剪贴板"）；
2. 调整速度 / 启动延迟 / 换行方式（Enter、Shift+Enter、跳过换行）；
   - **括号补全**：默认「删除自动补全」，打完 `( [ { " '` 立刻删掉编辑器补的那个，源码里的 `) ] } "` 原样打 → 不会出现 `}}`；
   - **缩进对齐**：默认开启，回车后 `Home`×2 + `Shift+End` 选中编辑器自动缩进的那串空格，再用缩进覆盖它 → 缩进不会翻倍；
   - **压缩注释前的对齐空格**（默认开启）：`code;      // 注释` 这种对齐用的长空格串，部分在线代码编辑器（实测超星）会因此把整块内容吞掉——压缩成单个空格后恢复正常。字符串里的空格、行首缩进都不动；
   - **缩进来源**（默认自动）：源码里有一半以上非空行带行首空白就信源码；否则按**大括号层级推导**——专门治"从网页/课件复制的代码复制时丢缩进"。`public:` / `private:` / `case:` 标签自动退一级，注释和字符串里的 `{ }` 不计数。「重排缩进」按钮可以先在文本框里看推导结果；
3. 点"开始输入"或按 **F8**，在倒计时内切到目标窗口；
4. 随时 **F8** 暂停/继续，**F9** 停止（全局热键，目标窗口有焦点也有效）。

## 命令行用法

```bash
python typewriter_ime.py --clipboard              # 读剪贴板
python typewriter_ime.py --text "你好，世界"       # 直接指定文本
python typewriter_ime.py --file letter.txt        # 读 UTF-8 文件
python typewriter_ime.py --clipboard --speed 80 --delay 5 --newline shift
python typewriter_ime.py --clipboard --no-human   # 匀速机械输入
python typewriter_ime.py --clipboard --code       # 代码模式（删自动补全 + 缩进对齐）
python typewriter_ime.py --file main.cpp --code --speed 45   # 打 C++ 源码
python typewriter_ime.py --clipboard --indent-from braces    # 强制按大括号推导缩进
python typewriter_ime.py --selftest               # 自检
```

| 参数 | 默认 | 说明 |
|---|---|---|
| `--text / --file / --clipboard` | — | 文本来源（三选一） |
| `--speed` | 60 | 基础速度（毫秒/字符） |
| `--delay` | 3 | 开始前倒计时秒数 |
| `--newline` | enter | 换行处理：`enter` / `shift`（聊天框换行不发消息）/ `skip` |
| `--no-human` | 关 | 关闭拟人节奏（抖动、标点停顿、"想一下"） |
| `--code` | 关 | 代码模式 = `--brackets delete --indent` |
| `--brackets` | off | `delete` 删掉编辑器补的闭合符（推荐）/ `skip` 用 → 跨过 / `off` 不处理 |
| `--indent` | 关 | 缩进对齐：回车后选中自动缩进，再用源码缩进覆盖 |
| `--no-tidy-comments` | 关 | 保留 `//` 前的对齐空格（默认会压成一个） |
| `--indent-from` | auto | 缩进来源：`auto` 有缩进用源码、没有则按大括号推导 / `braces` 一律推导 / `source` 只用源码 |

## 特性

- **不触碰剪贴板**：Win32 `SendInput + KEYEVENTF_UNICODE` 逐字符注入，网页/程序里表现为逐个受信任的键盘事件；
- **中文、全角、emoji 通吃**：超出 BMP 的字符自动拆 UTF-16 代理对；
- **拟人节奏**：随机抖动 + 句读停顿 + 偶发"想一下"，500 字约 40 秒；
- **安全控制**：倒计时、焦点守护（不会敲进自己）、全局热键、Esc 中止、UIPI 拒绝时明确报错；
- **代码模式**：与 IDE 的自动补全/自动缩进共存 —— 打完括号删掉编辑器补的那个，缩进用「选中后覆盖」还原，`}}` 和缩进翻倍都不会出现。

## 打包为 exe

```bash
# 图形界面版（无控制台窗口）
python -m PyInstaller --onefile --windowed --clean --noconfirm --name TypeWriterIME typewriter_ime.py
# 命令行版
python -m PyInstaller --onefile --console  --clean --noconfirm --name TypeWriterIME-cli typewriter_ime.py
```

产物在 `dist/`：`逐字输入器.exe`（双击即用）、`逐字输入器-命令行.exe`，外加一份打包好的 `逐字输入器.zip`（含使用说明）。
提示：PyInstaller 单文件包可能被杀毒软件误报，属常见现象。

## 打完自检

输入完成后（GUI 默认开启「打完自检」/ 命令行 `--verify`），工具会自动发一次 `Ctrl+A`、`Ctrl+C`，把目标里**实际的内容**读回来，与原文逐行比对：

- `自检通过：N 行逐字一致` —— 内容确实进去了；
- `行数不对 —— 期望 N 行，实际 M 行` 并列出差异 —— 当场就丢了；
- `自检跳过：焦点已不在目标窗口` —— 焦点跑掉了，不算数。

用途：区分「输入时丢的」和「打完被编辑器/平台改掉的」。焦点不在目标窗口时会自动跳过，避免读到别处内容误报。

> 注意：自检会占用一次剪贴板（原内容不保证恢复），并把编辑框整体选中后取消选中（光标落在末尾）。

## 自测

```bash
python typewriter_ime.py --selftest        # 注入结构 / 步骤编译 / 节奏参数
python tests/test_code_mode.py             # 编辑器模型：多行块、空块、空行、三层嵌套
python tests/test_code_mode.py --live      # 额外做一次真实注入（会弹窗抢焦点）
```

## 注意

- 第一次在某编辑器里用，建议先拿两行代码试：`{` 后是否只出现一个 `}`、缩进是否和源码一致。若发现字符被吃掉（编辑器其实不自动补全），把「括号补全」切到「不处理」；
- 目标窗口若以管理员权限运行，请同样以管理员身份运行本工具，否则系统会拒绝注入；
- 请用于正当用途（无障碍输入、旧系统录入、远程协助等），遵守目标系统使用条款。
