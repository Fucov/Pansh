# Pansh GitHub 自动化接入与运维指南

本文供 `Fucov/Pansh` 维护者完成仓库外配置。请按顺序上线：Actions 白名单和 GitHub App 凭据必须在首次合并前就绪；必需检查则要等首次 CI 注册名称后才能加入 Ruleset。

## 1. 可执行的上线顺序

1. **合并前预配置并回读**：完成第 2 节 Actions 策略及强制 read-back gate，再完成第 3 节 GitHub App 创建、安装、`AUTOMERGE_APP_CLIENT_ID` variable 和 `AUTOMERGE_APP_PRIVATE_KEY` secret。任何回读值不匹配时都不要合并；App 凭据缺失则首次 `main` push 会让 Release Please 的 token 步骤失败。
2. **首次合并和 CI 注册**：人工审阅并合并自动化 PR，等待 `main` 上 CI 完整运行一次，确认五个检查名称已经出现。首次 release workflow 即使没有可发布版本也应能完成 Release Please 步骤；若 App 配置仍有误，修正后在该 workflow run 中选择 **Re-run failed jobs**。
3. **建立合并闸门**：完成合并方式、手工标签和第 6 节 `main` Ruleset。不要在检查名称注册前把它们设为 required，否则可能把 `main` 锁住。
4. **首个实际发布前配置 PyPI**：完成第 8 节 `pypi` Environment 和 PyPI Trusted Publisher，再合并第一个 Release PR。若 publish 因外部配置缺失失败，配置完成后按第 10 节恢复，不要重打 tag。

五个固定检查名称为：

- `test (py3.10)`
- `test (py3.11)`
- `test (py3.12)`
- `test (py3.13)`
- `package`

## 2. 合并前配置 GitHub Actions

进入仓库 **Settings → Actions → General**：

1. 在 **Actions permissions** 选择 **Allow OWNER, and select non-OWNER, actions and reusable workflows**（页面会把 `OWNER` 显示为当前 owner）。
2. 关闭宽泛的 **Allow actions created by GitHub** 和 **Allow actions by Marketplace verified creators**，只在指定 action/reusable workflow 的输入框填写以下支持的 pattern，以英文逗号分隔：

   ```text
   actions/checkout@*, actions/setup-python@*, actions/upload-artifact@*, actions/download-artifact@*, actions/github-script@*, actions/create-github-app-token@*, googleapis/release-please-action@*, pypa/gh-action-pypi-publish@*
   ```

3. 开启 **Require actions to be pinned to a full-length commit SHA**。仓库 workflow 中所有 action 都固定到 40 位 commit SHA；行尾版本注释仅供人阅读。
4. 将默认 **Workflow permissions** 设为 **Read repository contents and packages permissions**。每个 workflow/job 显式声明例外权限，GitHub App token 也按用途请求更窄权限。
5. **Allow GitHub Actions to create and approve pull requests** 不需要开启。Release Please PR 由专用 GitHub App token 创建和更新，不使用 `GITHUB_TOKEN` 的这项能力。

### 强制 read-back gate（合并前必须通过）

保存设置后重新加载 **Settings → Actions → General**，不要只相信未保存的表单状态。也可以使用 GitHub 官方 Actions permissions API 读取仓库当前策略。将 UI/API 结果规范化后逐项核对：

- `allowed_actions=selected`
- `sha_pinning_required=true`
- GitHub-owned 宽泛允许项关闭，即 `github_owned_allowed=false`
- verified-creator 宽泛允许项关闭，即 `verified_allowed=false`
- `patterns_allowed` **精确等于**以下八项，不多也不少：

  ```text
  actions/checkout@*, actions/setup-python@*, actions/upload-artifact@*, actions/download-artifact@*, actions/github-script@*, actions/create-github-app-token@*, googleapis/release-please-action@*, pypa/gh-action-pypi-publish@*
  ```

把该回读结果作为首次自动化 PR 的合并前检查记录。只要任一布尔值、模式内容或模式数量不一致，就先修正仓库或上级 policy 并再次回读；**read-back 完全匹配前不得合并**。

Dependabot 会更新 workflow 中的完整 SHA，但 action 标识不变，因此上述 allowlist 无需随版本升级修改。审阅 Dependabot PR 后合并，不要把 SHA 改回可移动 tag。

组织或企业级 Actions policy 可以覆盖仓库设置。若已正确填写仍被拒绝，检查 Fucov 账号所属组织/企业的继承策略；上级策略需要管理员处理，仓库管理员无法绕过。

## 3. 合并前创建专用 GitHub App

在 **Fucov 个人账号 Settings → Developer settings → GitHub Apps → New GitHub App** 创建专用 App：

- GitHub App name：全局唯一名称，例如 `Pansh Automation`。
- Homepage URL：`https://github.com/Fucov/Pansh`。
- Webhook：取消 **Active**，无需填写 webhook secret。
- Where can this GitHub App be installed?：**Only on this account**。
- OAuth callback URL、Device Flow、Setup URL 和 event subscriptions：本自动化均不需要，不要启用或填写。

Repository permissions 设置为：

| 权限 | 级别 |
|---|---|
| Contents | Read and write |
| Issues | Read and write |
| Pull requests | Read and write |
| Metadata | Read-only（GitHub 隐式授予） |

创建后进入 App 的 **Install App → Fucov → Only select repositories → Pansh → Install**。不要选择 All repositories。

在 App 设置页生成 private key，下载 PEM。进入仓库 **Settings → Secrets and variables → Actions**：

- Repository variable：`AUTOMERGE_APP_CLIENT_ID=<GitHub App Client ID>`。必须使用 **Client ID**，不是数字 App ID。
- Repository secret：`AUTOMERGE_APP_PRIVATE_KEY=<完整 PEM private key>`。从 `-----BEGIN ... PRIVATE KEY-----` 到 `-----END ... PRIVATE KEY-----` 整段原样粘贴并保留换行。

在首次合并前完成两项配置。工作流通过固定 SHA 的 `actions/create-github-app-token` 读取 private key 并签发短期 token；PR 自动合并只申请 Contents、Pull requests 写权限，Release Please 另申请 Issues 写权限。secret 不会传给 PR 代码。

### PEM 保管与轮换

- 如需留存 PEM，放入团队认可的加密密码库；完成 secret 配置且无需离线备份时，应安全删除下载文件和系统废纸篓中的副本。
- 轮换时先生成新 private key，立即用新 PEM 更新 `AUTOMERGE_APP_PRIVATE_KEY`，验证一次 PR policy 和 release token 签发成功，再撤销旧 key，最后安全删除旧 PEM。不要先撤销唯一可用 key。
- 修改 variable 或 secret **不会自动触发 workflow**。修正失败的 release run 后选择 **Re-run failed jobs**；修正 PR policy 凭据后可重跑失败 job，或触发不改变 head 的安全 PR 事件。外部源码 PR 如需重新授权，由维护者在当前 head 上重新添加 `automerge`。

缺少或填错 App 凭据时，普通 CI 和 PR 风险分类仍运行，但策略无法启用 auto-merge，Release Please 也无法创建/更新 Release PR 或 GitHub Release。策略结果为 false 时，撤销已有 auto-merge 使用 workflow 的 `GITHUB_TOKEN`，不依赖 App 凭据。

## 4. 配置合并方式

进入 **Settings → General → Pull Requests**：

1. 开启 **Allow auto-merge**。
2. 开启 **Allow squash merging**；自动合并固定使用 squash。
3. 如团队不需要其他历史形态，可关闭 merge commits 和 rebase merging。

## 5. 创建手工标签并理解授权生命周期

在 **Issues → Labels** 手工创建：

- `automerge`：有 write/maintain/admin 权限的维护者为外部源码 PR 的**当前 head SHA**授权。
- `never-automerge`：强制转为人工处理。

`bot:automerge`、`bot:review-required`、`bot:blocked` 由策略 workflow 自动创建和维护。

有效的维护者授权会以当前 head SHA 记录在 bot marker comment 中。只要 head SHA 不变且 `automerge` 标签仍存在，labeled/unlabeled 其他标签、ready-for-review 等无关事件不会清除授权，失败的 App token 步骤也可安全重跑。以下任一情况会清除/移除人工授权标记和 `automerge` 标签，并撤销依赖该人工 head 授权的已有 native auto-merge：

- `synchronize` 产生新 head SHA；
- 维护者移除 `automerge`；
- 策略结果变为 false，包括 Draft、`never-automerge`、敏感路径、超过 12 文件/600 行、源码变化但无测试变化等硬阻断。

因此新提交绝不会沿用旧 SHA 的人工授权，但会基于新 head 独立重新分类：docs-only、可信作者，或显式开启 `ALLOW_EXTERNAL_CODE_AUTOMERGE=true` 的路线仍可自行重新满足 auto-merge 条件；只有依赖维护者人工授权的外部源码路线才必须重新审阅并加 `automerge`。该标签不能绕过任何硬阻断。

## 6. 创建精确的 Ruleset

五个 CI 名称注册后，进入 **Settings → Rules → Rulesets → New ruleset → New branch ruleset**：

1. Enforcement status 设为 **Active**。
2. Target branches 选择 **Include default branch**（当前即 `main`；也可精确 Include `main`）。
3. Bypass list 保持为空。
4. 开启 **Require a pull request before merging**，Required approvals 设为 `0`；关闭 **Require review from Code Owners** 和 **Require approval of the most recent reviewable push**。
5. 开启 **Require status checks to pass**，但关闭 **Require branches to be up to date before merging**（即 required checks 使用 loose 模式）。逐项添加以下检查，并在可选 source 中选择 **GitHub Actions**：
   - `test (py3.10)`
   - `test (py3.11)`
   - `test (py3.12)`
   - `test (py3.13)`
   - `package`
6. 开启 **Require conversation resolution before merging**。
7. 开启阻止 force push 和 branch deletion 的规则。

Ruleset 是技术闸门；auto-merge 只排队，不绕过必需检查或对话解决。高风险、敏感配置和 Release PR 的“人工处理”是团队流程要求，而不是无人值守模式下由 Ruleset 强制的 required approval；若把 approvals 改为大于 0，低风险无人值守合并也会停止。

loose 模式是本项目默认的兼容方案：`main` 在 PR 检查通过后继续前进时，GitHub 不会要求维护者人工执行 Update branch，因此低风险 auto-merge 可以保持无人值守。代价是检查结果可能没有覆盖该 PR 与最新 `main` 的最终组合，存在遗漏集成问题的可能；GitHub 仍会阻止存在实际 merge conflict 的 PR。本策略还会把敏感配置和依赖文件转人工，只自动处理 docs/tests，以及满足规模、测试和作者/授权限制的小型源码改动，从而缩小该风险面。

如果 `Fucov/Pansh` 的仓库所有者类型和 GitHub 套餐支持 merge queue，可以在单独验证后改用 **Require merge queue**；CI 已监听 `merge_group`，这是比 loose checks 更强的集成保证。它不是当前默认值：启用前必须单独端到端验证专用 App 与 `gh pr merge --auto` 的实际行为，不能仅凭 CI 已支持 `merge_group` 就直接切换。

可选 tag Ruleset：进入 **New ruleset → New tag ruleset**，设为 **Active**，Target tags Include `v*`，Bypass list 为空；限制 tag updates 和 deletions，但不要限制 tag creation，以便 GitHub App 创建新 release tag。先在测试 release 中确认账号级策略没有额外阻止 App。

## 7. 外部源码自动合并开关

Actions repository variable `ALLOW_EXTERNAL_CODE_AUTOMERGE` 默认保持**未设置**。未设置或任何非 `true` 值都采用安全默认。

只有明确接受风险时才设为大小写不敏感的 `true`：没有仓库写权限且未获当前 head 人工授权的外部源码 PR，也可在满足小规模、非敏感、非 Draft、未禁止、源码同步改测试等其余条件后进入 auto-merge。CI 和 Ruleset 仍生效，但真人来源审查层会被移除。

## 8. 配置 PyPI Trusted Publishing

### GitHub Environment

进入仓库 **Settings → Environments → New environment**，创建 `pypi`，然后进入 **pypi → Deployment branches and tags → Selected branches and tags → Add deployment branch rule → Branch**，精确添加 `main`，不添加其他 branch/tag rule。

初次接入可以临时添加 required reviewer 观察 OIDC 请求；目标为完全无人值守发布时应移除 reviewer。若 publish 显示 Waiting，检查并批准 Environment，或按目标调整 reviewer；这不是构建失败。

### PyPI 现有项目 Publisher

登录 PyPI 后进入 **Your projects → pansh → Manage → Publishing → Add a new publisher → GitHub Actions**。项目已由页面上下文确定为 `pansh`，表单只需填写：

| 字段 | 值 |
|---|---|
| GitHub owner | `Fucov` |
| Repository | `Pansh` |
| Workflow name | `release.yml` |
| Environment name | `pypi` |

发布使用 OIDC，不需要 `PYPI_TOKEN`。首次成功后，确认无其他 workflow、外部脚本或维护流程使用，再删除遗留的 `PYPI_TOKEN`、`TWINE_PASSWORD`、`PYPI_API_TOKEN`。

## 9. PR 与 Release Please 流程

准备 squash 合入 `main` 的 PR 使用 Conventional Commit 标题：

- `fix:`：patch；`feat:`：minor。
- `feat!:` 或正文 `BREAKING CHANGE:`：major。
- 纯维护改动使用 `docs:`、`test:`、`chore:`、`ci:` 等合适类型。

每次 `main` push 会触发 `release.yml`，不提供手动触发。Release Please 用 App token 创建/更新 Release PR；该敏感 PR 由维护者人工审阅并合并。合并后的 `main` push 创建 GitHub Release，build job 检出 Release Please 输出的精确 SHA，核对 checkout/tag/源码版本，使用固定工具链构建、Twine 检查，先以 `overwrite: true` 上传 artifact，再从 wheel 做隔离冒烟。全部成功后 publish job 下载同一 artifact，经 `pypi` Environment、OIDC 和 attestations 发布。

## 10. 端到端验证与恢复

### 两个独立 PR 的验证

1. 创建一个小型 docs-only PR。确认 Ruleset 的 branch up-to-date 选项为关闭，策略评论、`bot:automerge`、五个检查及 squash auto-merge 正常；即使测试期间 `main` 有其他提交，也不应出现要求人工 Update branch 的门槛。
2. 另从外部 fork 创建一个同时修改 `src/pansh/` 与 `tests/` 的小型 PR，并有意让一项 CI 失败，以免测试期间真的合并。
3. 维护者在该外部 PR 当前 head 上添加 `automerge`。确认 `bot:automerge` 出现且 native auto-merge 已排队，但被失败检查拦住；添加或移除一个与策略无关的其他标签，确认 head 未变时授权仍保留。
4. 外部作者 push 一个仍未修复失败的新提交。确认 `synchronize` 后 `automerge`、SHA 授权和 native auto-merge 均被清除。
5. 外部作者再 push 修复提交；维护者审阅新 head 后重新添加 `automerge`。确认全部检查通过并 squash 合并。
6. 合入正常 Conventional Commit PR，确认 Release PR 创建/更新。完成第 8 节后人工合并 Release PR，确认 GitHub Release、artifact、冒烟、OIDC 发布和 attestations。

### Release 恢复原则

- **App/Release Please 或临时网络失败**：修复凭据或等待服务恢复后，在原 run 选择 **Re-run failed jobs**。更改 secret 本身不会触发新 run。
- **build/smoke 临时失败**：artifact 上传使用 `overwrite: true`，因此可安全 **Re-run failed jobs**，同一 run 的同名 artifact 会被替换，不会因已存在而卡住。
- **只有 publish 因外部配置失败**：只重跑失败的 publish job；只要原 run 的 `release-distributions` artifact 仍在保留期内，它会下载并发布相同已验证产物，无需重新构建或重打 tag。
- **Environment 等待批准**：Waiting 表示部署保护规则正在排队，不要创建新 tag；由 reviewer 批准，或在确认无人值守目标后移除 reviewer。
- **OIDC claim 无效**：逐字核对 owner `Fucov`、repository `Pansh`、workflow `release.yml`、environment `pypi`，以及 GitHub Environment 是否仅允许当前 `main` ref。修正 PyPI Publisher/Environment 后重跑 publish。
- **版本已存在**：PyPI 文件名和版本不可覆盖。先确认 PyPI 上现有文件 hash、attestation/provenance 与本次 artifact 是否一致；不要反复上传或尝试替换文件。若不是同一次可信发布，停止并发布更高的新版本。
- **源码或包缺陷在 release tag 创建后才发现**：不得移动、删除并复用 tag/版本；提交 `fix:`，由 Release Please 生成新版本。
- **PyPI 部分上传**：立即停止重试，比较已上传文件与本地 artifact 的 hash 和 provenance；不要删除或复用任何文件名。yank 受影响版本，并发布更高的新版本。
- **组织/企业策略覆盖**：若 action、App 安装、Ruleset 或 Environment 在仓库设置正确仍被拒绝，检查上级 policy/audit log，并由对应管理员修正；不要扩大仓库 token 权限规避。

### 常见非发布问题

- **App token 失败**：确认是 Client ID 而非 App ID、PEM 完整且 key 未撤销、App 安装在 `Fucov/Pansh` 并具备三项权限。
- **无法 auto-merge**：确认 Allow auto-merge/squash、策略评论和 Ruleset；若外部源码 PR 依赖人工路线，新 head 必须由维护者重新授权，其他路线按新 head 独立重新判定。
- **必需检查 Pending**：确认检查名称和 source 精确，且 workflow 覆盖当前 PR。若 auto-merge 额外要求人工 Update branch，说明误开了 **Require branches to be up to date before merging**；关闭它恢复默认 loose 模式，或在仓库符合条件并完成 E2E 后另行采用 merge queue。

## 11. 自托管 Runner 安全边界

当前 workflow 全部使用 GitHub-hosted runner。未来接入 AnyShare 或内网资源时，自托管 runner **绝不能运行 fork PR 或不受信任的 PR head 代码**。应创建独立 integration workflow，只允许可信 `main` push、`schedule`，或由维护者控制输入且固定到可信 ref 的手动运行；不要把现有 PR CI 改到自托管 runner，也不要让 `pull_request_target` 检出或执行 PR 内容。

## 需要手动填写的位置

| 位置 | 键/设置 | 精确期望值或来源 |
|---|---|---|
| Actions permissions | Allowed patterns | `actions/checkout@*`, `actions/setup-python@*`, `actions/upload-artifact@*`, `actions/download-artifact@*`, `actions/github-script@*`, `actions/create-github-app-token@*`, `googleapis/release-please-action@*`, `pypa/gh-action-pypi-publish@*` |
| Actions permissions | Pinning / defaults | Require full-length SHA；默认 workflow permissions 为 read；宽泛 GitHub/verified creator 选项关闭 |
| Actions permissions read-back | Merge gate | `allowed_actions=selected`；`sha_pinning_required=true`；`github_owned_allowed=false`；`verified_allowed=false`；`patterns_allowed` 精确为上述八项 |
| GitHub App | Owner / name / homepage | `Fucov` / 全局唯一名称（如 `Pansh Automation`）/ `https://github.com/Fucov/Pansh` |
| GitHub App | Installation availability / webhook | `Only on this account` / inactive |
| GitHub App installation | Repository | `Install App → Fucov → Only select repositories → Pansh` |
| GitHub App repository permissions | Contents / Issues / Pull requests | 三项 `Read and write`；Metadata 隐式 `Read-only` |
| Actions variable | `AUTOMERGE_APP_CLIENT_ID` | GitHub App **Client ID**，不是 App ID |
| Actions secret | `AUTOMERGE_APP_PRIVATE_KEY` | 完整 PEM private key，保留首尾行和换行 |
| Actions variable（可选） | `ALLOW_EXTERNAL_CODE_AUTOMERGE` | 默认不创建；明确接受风险时才设为 `true` |
| Branch Ruleset | Target / bypass / state | default branch (`main`) / empty / Active |
| Branch Ruleset | Reviews / checks | approvals `0`；CODEOWNER/last-pusher off；五项检查 source 为 GitHub Actions；**Require branches to be up to date before merging = off** |
| Branch Ruleset（可选增强） | Merge queue | 默认关闭；仅在仓库条件支持并验证 App/`gh` 端到端行为后开启 Require merge queue |
| Branch Ruleset | Protection | conversation resolution；阻止 force push/delete |
| Tag Ruleset（可选） | Target / bypass / state | `v*` / empty / Active；限制 update/delete，允许 creation |
| GitHub Environment | Name / deployment rule | `pypi` / Selected branches and tags → Branch `main` only |
| PyPI Publisher | Owner / repo / workflow / environment | `Fucov` / `Pansh` / `release.yml` / `pypi` |
| GitHub labels | Manual inputs | `automerge`、`never-automerge`；`bot:*` 由 workflow 创建 |
