# 网站剪报技能

这是一个给 Codex 使用的本地网站剪报归档技能。它适合把公开网页按作者、用户名或分散主题整理成可暂停、可续跑的离线项目。

## 适用范围

- 先判断网页和目标的关系，再决定是否保存和继续追踪。
- 用 SingleFile 保存已经渲染的页面，用猫抓补齐 SingleFile 没有收下来的动态资源。
- 用 Markdown 记录候选、关系证据、处理状态和断点。
- 用离线审计脚本生成本地入口和完整性报告。

技能不会绕过登录、付费墙、验证码或其他访问控制，也不会建立无人照看的无限爬虫。只归档公开内容或用户有权访问的内容。

## 安装

把本仓库目录放到 Codex 技能目录，并保持目录名为 `archive-web-clippings`：

```text
$CODEX_HOME/skills/archive-web-clippings/
```

至少需要保留以下文件和目录：

```text
SKILL.md
agents/openai.yaml
references/project-layout.md
scripts/audit_offline_archive.py
```

安装后重新打开 Codex，或者让 Codex 重新扫描技能目录。

## 使用

直接告诉 Codex 入口网址、收集范围和本地保存位置，例如：

> 使用 `$archive-web-clippings`，从这些入口建立一个可续跑的网站剪报项目：……

首次运行会确认保存位置、入口网址、收集范围和会改变边界的必要规则。完成一批页面后可以说“继续”，从项目记录的断点恢复。

## 目录里的文件

### `SKILL.md`

技能入口和网页关系判断、页面保存、续跑与收尾规则。

### `references/project-layout.md`

剪报项目的目录、表格字段和离线入口约定；只有实际建立或恢复项目时才需要读取。

### `scripts/audit_offline_archive.py`

离线完整性测试脚本。运行：

```powershell
python -X utf8 scripts/audit_offline_archive.py --project <剪报项目目录>
```

它会检查关系表中的本地页面、把已收录页面链接改为本地相对路径，并生成 `index.html` 和 `03-完整性测试.md`。

## 分享与隐私

这是公开的技能代码仓库，任何人都可以查看和安装。不要把实际剪报项目、登录信息、Cookie、访问令牌或私人网页内容提交到这个技能仓库；剪报项目应保存在本地或另一个访问受限的仓库中。
