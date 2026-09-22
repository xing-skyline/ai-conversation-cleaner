# AI 会话清理器

[English](README.md) · 简体中文

在 Windows 本机统一查看、搜索和删除 **Codex、Claude Code、Grok Build、Cursor、Google Antigravity（反重力）、DeepSeek Harness、OpenCode** 的本地会话。支持单选、多选、删除预览，以及默认备份、自选目录备份、不备份三种方式。

**公开预览版 · Windows x64 · 简体中文界面。** 本项目由社区独立开发，与上述应用厂商无隶属关系。

> 工具直接修改应用的本地数据。删除时必须关闭对应应用、终端和后台进程。建议先运行隔离演示模式，在确认适配自己的应用版本前保持备份开启。

## 下载与启动

从 [Releases](https://github.com/xing-skyline/ai-conversation-cleaner/releases) 下载 `AIConversationCleaner-Windows-x64.zip`，解压后双击 **`AIConversationCleaner.exe`**。不需要 Python、Node.js 或 CMD。自行编译的文件名为 `AI会话清理器.exe`。

程序打开仅监听 `127.0.0.1` 的浏览器页面，每次启动使用新的访问令牌。这是本机应用，不是在线网站。结束使用时点击 **“退出工具”**，或者直接关闭当前实例的最后一个页面，后台会在约 3 秒的刷新宽限期后退出。还有其他页面打开时不会退出；正在进行的删除、备份及其他请求会先完成。浏览器崩溃或连接丢失时，约 3 分钟后自动回收；启动后始终没有页面连入则在 2 分钟后退出。标签被浏览器长时间冻结/休眠也可能触发回收，此时重新双击 EXE 即可。`--no-browser` 的纯接口使用不会超时退出，除非已有浏览器页面连入。

EXE **没有数字签名**，可使用发布页面提供的 SHA-256 校验文件核对。如果 Windows 阻止运行，请不要关闭安全防护；可以审阅源码或自行构建。

## 使用步骤

1. 选择应用标签，搜索标题、项目路径或会话 ID。
2. 点击“详情”查看摘要，然后勾选需要删除的会话，或全选当前筛选结果。空白、已归档、已识别的残留记录也可选中。
3. 选择“删除前备份”：

   | 方式 | 行为 |
   | --- | --- |
   | 备份到默认目录 | 先备份，再删除，使用当前应用的默认位置。 |
   | 手动选择备份目录 | 点击“选择文件夹…”或输入完整路径。备份放到 `所选目录/AIConversationCleaner/应用名/时间戳`。 |
   | 不备份，直接删除 | 不复制会话或数据库。误删及中途失败后，已提交的修改不能自动恢复。 |

4. 退出对应应用、CLI 和后台进程。
5. 点击“删除所选任务”，核对标题、ID 和备份方式，直接点击 **“备份并删除”** 或 **“直接删除（不备份）”**，无需手动输入确认文字。
6. 查看结果，再重新打开原应用。

每次启动默认选择备份，不自动记住“不备份”。删除预览绑定当时的会话集合和备份设置，使用短时、一次性的确认令牌；数据变化后需要重新预览。每批最多 500 条会话。

## 支持范围

| 应用 | 已识别的本地记录 |
| --- | --- |
| Codex | 本地桌面目录、主状态、已适配消息/目标/队列/记忆记录、session index 和 JSONL。优先尝试已安装 CLI 的 `thread/delete`，再清理已识别残留；保留非本地主机目录条目。 |
| Claude Code | `.claude/projects` 转录、所属附属文件和子目录、会话索引、对应 history 条目及已识别辅助文件。 |
| Grok Build | `.grok/sessions` 会话目录、提示历史、活动索引、搜索数据库和 FTS 索引。 |
| Cursor | 已适配 composer、独立消息/检查点/差异键、工作区索引和独立转录；保留跨会话共享内容缓存。 |
| Antigravity | 已适配 conversation、annotation、brain、recording 文件、本地摘要库和 IDE 索引；不解码二进制正文。 |
| DeepSeek Harness | `.dsh/sessions`（含压缩日志）、摘要缓存、工作区归属和归档引用；显示缓存标题和首条提示摘要，不展开完整压缩正文。 |
| OpenCode | `opencode.db` 的会话、消息/正文、待办、分享元数据、输入/上下文投影及对应事件记录；已识别的旧版 JSON 和会话差异文件。保留项目、账号、凭据、共享工具输出及云端分享。删除父会话需明确勾选子会话。 |

这是**从已识别的当前本地存储中实际移除记录和独立文件**，不是简单归档；但**不是安全擦除**。已有备份、通用日志、共享缓存、数据库空闲页、云端历史和系统恢复机制仍可能保留数据。项目源码及文档、凭据、设置、Skills 和插件不在清理范围内。

支持默认用户级 Windows 数据目录；Codex 额外支持 `CODEX_HOME` / `--home`。OpenCode 默认读取 `%USERPROFILE%/.local/share/opencode`，支持 `XDG_DATA_HOME` 和 `OPENCODE_DB`（不含内存数据库），未指定时读取标准 `opencode.db`。其余应用的自定义目录、WSL、SSH/远端任务、云端 Agent、无关编辑器插件不在适配范围内。未安装 Codex 不影响其他应用标签启动。

OpenCode SQLite 适配已核对 v1.18.31 表结构，同时处理已识别的旧版 `storage/session`、`storage/message`、`storage/part` 和 `storage/session_diff` JSON 文件。发现未知会话关联表时会停止删除，不会静默跳过。OpenCode 数据库备份是整库副本，可能包含账号与凭据信息，请勿公开或上传。

应用内部格式会随升级变化，**不保证所有版本兼容**。目前识别 DeepSeek 分会话缓存版本 5/7、共享摘要版本 3、工作区版本 2。若干未知结构会阻止处理，但不保证识别所有上游变化。进程保护覆盖已知原生可执行文件和 Node/Bun 路径，不保证识别所有自定义启动器和插件，因此仍需手动退出原应用。

## 备份、恢复与隐私

默认位置：

- Codex：`%USERPROFILE%/.codex/backups/codex-thread-cleaner`，保留旧子目录名以兼容已有备份与操作锁。
- 其他应用：默认 Windows 用户布局下的 `%LOCALAPPDATA%/AIConversationCleaner/backups/应用名`。

启用备份时，每批包含 `result.json`、路径映射、数据库副本和所选文件。SQLite 使用在线备份接口；普通异常且原应用保持退出时，会尝试回滚。断电、强制关闭或并发写入仍可能需要人工恢复；未完成操作会阻止后续删除。**产生新会话后，不要直接用旧备份覆盖整库。** 当前没有一键整库恢复按钮。

所有模式在 `.operations` 保留少量 ID、状态和路径，不含会话标题、正文或数据库副本。不备份失败时显示 `failed_no_backup`，不会误称“已回滚”。已有备份不自动删除。

工具不包含统计上报、账号登录或主动调用云端会话接口的代码；可选 Codex 子进程遵循自己的配置。若主动选择网络共享目录，备份会写入该共享位置。备份、操作记录、截图和 `--inventory` 输出可能含隐私，**不要上传到 Issue 或提交进 Git**。详见 [SECURITY.md](SECURITY.md)。

## 源码运行与开发

需要 Windows 和 Python 3.11+。运行时使用标准库；文件夹选择和 Node/Bun 进程检查会调用 Windows PowerShell。

```powershell
git clone https://github.com/xing-skyline/ai-conversation-cleaner.git
cd ai-conversation-cleaner
python run.py
python run.py --demo                    # 临时、虚构的 Codex 示例
python run.py --app cursor --inventory  # 只读，但输出可能含隐私
python run.py --home C:\Example\CodexData
```

`源码启动.cmd` 仅供开发，EXE 不依赖它。

```powershell
python -m unittest discover -s tests -v
node --check web/app.js
python tools/check_public_files.py

# 用干净环境，避免打入个人 Python 环境中的其他包
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\build.ps1 -Python .\.venv\Scripts\python.exe
```

正常测试使用隔离虚构数据，不删除真实会话。可选探针见 [CONTRIBUTING.md](CONTRIBUTING.md)。CI 在 Windows 上测试、检查公开文件、于干净环境构建、用虚构数据验证 EXE，并打包校验和及依赖许可。提供可复现构建步骤，但**不宣称产物逐字节一致**。

## 许可证

本项目采用 [MIT 开源许可证](LICENSE)。允许使用、修改和再分发，包括商业用途；须保留版权声明和许可证。软件按现状提供，不附带担保。第三方组件保留各自许可，见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md)。
