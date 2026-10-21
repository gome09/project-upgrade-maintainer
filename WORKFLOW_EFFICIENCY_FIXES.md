# 调用流程冗余修复记录

日期：2026-10-21。沿用用户指定的本地版本 18.0.1；未执行公开发布。

## 已实施

1. 同阶段刷新：省略 next-action 时保留阶段文件原值，与 STATE 的可选参数语义一致。
2. 无变更事务：CLI 延迟到首个文件操作准备时才发布事务日志；所有实际文件操作仍先写入持久化 journal。显式 Transaction.start 保留立即登记契约。
3. 模块激活：统一由 activate_workspace_modules 检查或修复资源，移除 Decision/Risk/Exception/Delivery 紧随其后的重复资源检查；已激活模块的缺失资源仍可修复。
4. 交付依赖：绑定匹配 scope 的活动治理记录、匹配 subject/scope/environment 的最新验证，以及共享 blocker/drift/unverified 字段。仍校验完整记录链；相关或全局证据、策略、对象、恢复状态、阻塞及告警变化仍阻止旧审批执行。
5. 审计复用：git-audit --include-artifacts 在一次扫描中返回 Git 问题及完整产物分类；Skill 路由说明无需把所有审计串成固定前置流程。没有跨命令缓存扫描，也没有删除 apply 的重新校验。
6. 重试去重：validation-record --request-id 标记一次实际运行，同标识同证据重试不追加，冲突证据报错；新的运行必须使用新标识。不传标识继续按每次调用记录，避免合并两次真实测试。
7. 治理重试：Decision/Risk/Exception 使用相同有效计划和精确审批 ID 重试时，只有已验证且内容完全匹配的已持久化记录才能无写入返回。风险仅识别最新修订。新 review 仍代表独立决策，不能用语义相似推断用户意图。

## 回归与边界

新增 tests/test_workflow_efficiency.py，覆盖阶段无变更刷新、空日志、模块单次检查及修复、无关 scope 不失效、相关验证和全局风险失效、无关记录损坏阻断、验证请求重试/冲突、新运行记录、精确治理计划重试及单次合并扫描，共 10 项。

Skill 说明遵循 skill-creator 的聚焦原则：仅新增需要改变调用决策的说明，不要求额外固定审计流水线。

此前业务/工程审查提出的锁探测、首次初始化并发、子模块指纹、阶段验收策略及 CI 平台覆盖属于独立待办，本次未修改。

## 最终验证

本节记录调用冗余修复当时的构建。后续 PID 与初始化互斥修复已更新发布包；当前摘要和验证请以 RELEASE_PLAN.md 为准。

- 源码稳定后完整测试：230 项，成功，跳过 8 项。
- E2E acceptance：通过。
- skill-creator quick_validate：通过；git diff --check：通过。
- 重建 dist/project-upgrade-maintainer-18.0.1.skill，88 个运行文件与源码逐字节一致，ZIP 完整性及重复构建校验通过。
- 当前 SHA-256：ae2b1243294150b2f06c7aec8fed60f42b65220ea52f6dc51fcbfc9943f82184。

该包替换此前本地 18.0.1 构建，旧构建摘要 f80d2f59cefb1f0d5c6f6a5a00b1618d0455b0af617a461f09127b78a8ea6334 仅作为历史记录保留。没有提交、推送或公开发布。
