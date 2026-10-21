# 项目清理与发布渠道检查

检查日期：2026-10-21。

## 已清理

以下均为未纳入 Git 的旧构建或缓存，删除前已核对具体路径，不含符号链接或 junction：

- `dist/project-upgrade-maintainer-18.0.0.skill`：已被本地 18.0.1 取代。
- `.codex-tmp/project-upgrade-maintainer-18.0.0.skill`：旧版临时构建。
- `.codex-tmp/release-notes.md`：仅对应旧版 18.0.0 发布说明。
- `.codex-tmp/unit-test.log`：旧的 220 项失败运行日志，已被后续全量验证取代。
- `cli/`、`commands/`、`core/`、`tests/`、`tools/` 下 5 个 `__pycache__` 目录：共 139 个文件、2,016,329 字节。
- 清空后的 `.codex-tmp/` 目录。

删除未经过回收站。缓存可重新生成；旧发布资产仍可从 GitHub 获取，但本地旧临时包与旧日志不保证可原样恢复。源码、测试、历史审查报告、有效治理记录和 `.git/` 均保留。未对未知用途文件执行推测性删除，未初始化本项目的 `.upgrade/`。生命周期 Skill 的清理 CLI 仅管辖 `.upgrade/`，因此本次对用户指定的仓库生成产物使用精确路径清理，没有扩大其 CLI 管辖范围。

## 当前 Skill 包

路径：`dist/project-upgrade-maintainer-18.0.1.skill`。

SHA-256：`72e77302e19db05ed2eddced7dc9e08be1550bc0391e3263c3e8547d980d5d6f`。

ZIP 完整性正常；88 个包内运行文件与当前运行源码逐字节一致，无缺失、额外或不同文件。Skill 格式校验通过。本次只清理生成产物及更新检查记录，未改运行源码；沿用上轮全量验证：运行 251 项，OK（10 项跳过），E2E 通过。未重复执行全量测试，也未重新构建内容相同的包。

## GitHub 渠道

- origin：`git@github.com:gome09/project-upgrade-maintainer.git`，fetch/push 地址均已配置。
- 发布页：https://github.com/gome09/project-upgrade-maintainer/releases
- latest-release 查询返回 `v18.0.0`，资产为 `project-upgrade-maintainer-18.0.0.skill`，摘要为 `e7d413ce996c16651b3f3b84bc426a1a6d75fbc1013aa8a84318e46bcd255043`。线上包不是当前 18.0.1。
- 公开 REST 查询的远程 main：`160e146d154f63a9f8d835d9ef65681c846b0039`，与当前本地 HEAD 一致；当前修复尚未提交。
- 最近查询到的 Actions 成功运行针对上述旧提交，不能证明当前修改已完成远程 CI。
- `.github/workflows/tests.yml` 仅构建并上传 Actions artifact；没有创建 GitHub Release / 上传 Release asset 的自动发布步骤。发布指南采用手动发布流程。
- GitHub CLI 部分接口返回 HTTP 401；SSH 检查因无法读取本机 known_hosts 而失败。另一次 HTTPS 查询发生连接超时。未更改凭证、SSH 配置或跳过主机身份校验，不能据此确认当前推送/发布权限。

结论：仓库与既有 Release 渠道已关联，但最新修复尚未同步发布，自动 Release 发布未配置。后续发布需要单独授权提交、推送、运行 CI、创建 18.0.1 标签与 Release 并上传当前包；本次没有执行这些外部写操作。
