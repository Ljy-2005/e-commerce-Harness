# 安全说明

本仓库是**公开**的。任何被推送进来的内容都应视为永久公开——即使随后删除，
它仍留在 git 历史、GitHub 缓存、以及可能的 fork 与搜索引擎快照里。

因此本项目对「密钥与个人信息」采用**四层防护**。

---

## 四层防护

| 层 | 位置 | 拦住什么 | 能否绕过 |
|---|---|---|---|
| 1 | `.gitignore` | 敏感文件的常规忽略 | 能（`git add -f`） |
| 2 | `pre-commit` 钩子 | 暂存区里的密钥 / 私人文件 | 能（`--no-verify`） |
| 3 | `pre-push` 钩子 | 推送前复查 + 未跟踪文件 | 能（`--no-verify`） |
| 4 | CI 扫描 + GitHub 推送保护 | 已离开本机的内容 | 基本不能 |

钩子由 `scripts/setup_hooks.py` 安装，并在 `pip install -e .` 后自动执行。
手动补装：

```bash
python scripts/setup_hooks.py            # 安装
python scripts/setup_hooks.py --check    # 检查（CI 用）
python scripts/setup_hooks.py --uninstall
```

安装时若 `.git/hooks/` 已有**非本工具**的钩子，会先备份为 `*.bak` 再覆盖，不会静默丢弃。

### 第 4 层需要手动开启

GitHub 仓库设置里打开（**公开仓库免费**）：

> `Settings` → `Code security` → 开启 **Secret protection** 与 **Push protection**

这是唯一能在服务端兜住 `--no-verify` 的防线。开启后，即使本地被绕过，
GitHub 也会在收到含密钥的推送时直接拒绝，并在提交页给出提示。

---

## 扫描规则

规则与豁免清单位于 `scripts/secret_rules.json`，**改规则不需要改代码**。

### 阻止级（HARD，命中即拒绝提交）

**敏感文件名** —— 即使 `git add -f` 强行暂存也会拦：

- 密钥/证书：`.env`、`*.pem`、`*.key`、`*.p12`、`*.pfx`、`id_rsa*`、`id_ed25519*`
- 本项目配置：`config/secrets.yaml`、`config/tenant_keys.yaml`、`config/providers.yaml`、
  `config/custom_providers.yaml`、`config/chat.yaml`、`config/output.yaml`、`config/pricing.yaml`
- 个人信息：`*.docx`、`*.doc`、`*.pdf`、`*简历*`、`*resume*`、`*证件*`、`*身份证*`、`*预览*`

**内容特征**：

| 规则 | 特征 |
|---|---|
| `openai-key` | `sk-` 开头的长串 |
| `anthropic-key` | `sk-ant-` |
| `ark-key` | `ark-` + UUID 形态（火山方舟） |
| `aws-akid` | `AKIA` + 16 位大写 |
| `github-token` | `ghp_` / `gho_` / `ghu_` / `ghs_` / `ghr_` |
| `slack-token` | `xoxb-` / `xoxp-` 等 |
| `private-key` | `-----BEGIN ... PRIVATE KEY-----` |
| `plaintext-password` | 配置文件中 `password: xxx` 明文（仅扫 yaml/yml/env/ini/cfg/conf/properties/toml） |
| `jdbc-url-password` | JDBC 连接串内嵌 `password=` |

### 警告级（WARN，只提示不阻断）

`jwt`（JWT 三段式）、`high-entropy-hex`（32+ 位十六进制）、
`credential-assignment`（引号内疑似凭据）、`cn-mobile`（中国大陆手机号）。

这类规则靠启发式判断，误报率天然高于阻止级，所以不拦——**但请认真看一眼**。

---

## 遇到误报怎么办

三选一，从轻到重：

**1. 行内豁免**（最推荐，影响面最小）

在命中行末尾加注释即可，脚本会跳过该行：

```yaml
example_key: sk-abcdefghijklmnopqrstuvwxyz012345   # secret-scan: allow
```

支持 `#` 注释的语言均可用（YAML / Python / JS / TS / TOML / Shell / INI）。
JSON 无注释，请用第 2 种。

**2. 路径豁免**（整个文件都是构造数据时）

编辑 `scripts/secret_rules.json`，在 `allowlist.paths` 里登记，**并写明理由**：

```json
{ "glob": "tests/fixtures/**", "reason": "测试夹具，全部为构造数据" }
```

`allowlist` **优先于**敏感文件名规则，也优先于内容规则。
已有豁免项：`.env.example`、`tests/**`、`frontend/src/**/*.test.js(x)`、两个 `package-lock.json`、
`docs/coverage_baseline.json`（含覆盖率哈希）。

**3. 本次强行通过**（仅限你已确认无害）

```bash
git commit --no-verify
git push --no-verify
```

会被第 4 层（GitHub 推送保护）再拦一次，这是预期行为。

> 判断原则：如果它在**真实运行**里会被当成凭据使用，那它就是真密钥，不要豁免。
> 只有"给测试/文档用、永远不会连真实服务"的构造值才该走豁免。

---

## 怀疑已经泄漏了怎么办

**第一步永远是轮换密钥**，不要在清理历史上花时间——只要密钥还有效，
清理历史的速度永远赶不上被抓取的速度。

1. **立即轮换**：去对应服务商控制台作废旧 Key、签发新 Key。
   - DeepSeek / 火山方舟 / OpenAI / Anthropic / 通义千问 / FLUX 各家控制台
2. **更新本地配置**：把新 Key 写进 `config/secrets.yaml`（该文件已被忽略，不会入库），
   或用环境变量注入。
3. **确认泄漏范围**：
   ```bash
   python scripts/check_secrets.py --history
   ```
4. **视情况清理历史**（仅当密钥已作废、且你确实需要抹掉痕迹时）：
   使用 `git filter-repo` 重写历史后强制推送。**注意**：
   - 这会改写所有提交 SHA，协作者必须重新克隆
   - GitHub 上的旧对象不会立即消失，需联系 GitHub Support 清理缓存
   - 已存在的 fork 不受影响
5. 若涉及个人证件/简历类文件，除清理历史外，还应考虑该信息已被索引的风险。

---

## 本地配置约定

真实凭据**只应存在于**下列被忽略的位置：

```
config/secrets.yaml          # 本项目 API Key 存放处
config/tenant_keys.yaml      # 多租户 Key
config/providers.yaml        # 私有服务商端点
config/custom_providers.yaml
config/chat.yaml
config/output.yaml
config/pricing.yaml
.env                         # 环境变量（从 .env.example 复制而来）
```

`.env.example` 是**唯一**入库的模板文件，里面所有值都是占位符
（`sk-...` 或空）。新增环境变量时，只往 `.env.example` 加**空值或占位符**。

---

## 报告问题

如果你在本仓库发现已泄漏的凭据或个人信息，请**不要**开公开 Issue，
直接联系仓库所有者 `@Ljy-2005`。
