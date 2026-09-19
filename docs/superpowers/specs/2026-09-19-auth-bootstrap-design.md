# Issue #24：动态 OAuth2 客户端与 RSA 公钥发现设计

## 文档目的

本文是 [Issue #24](https://github.com/Fucov/Pansh/issues/24) 的实现指导文件，供维护者和外部贡献者共同评审、拆分任务和验收。目标是在不破坏北航现有登录的前提下，让新的 AnyShare 部署只配置 `host` 即可完成密码登录，不再要求复制其他学校的 OAuth2 客户端凭据或手工填写 RSA 公钥。

本文只覆盖 Pansh 已有的“用户名、密码、无浏览器登录”能力。CAS 自动化、短信或图形验证码、本地 HTTP 回调监听、浏览器登录和 refresh token 生命周期不在本次范围内。

## 结论与可行性

Issue #24 提出的两项能力均可实现，并且与现有 `httpx`、`rsa`、profile 和 credential store 架构兼容：

- `POST /oauth2/clients` 可以把 `client_id` 和 `client_secret` 从全局硬编码改为每个 profile 的运行时注册结果。
- `/oauth2/auth` 登录页引用的 JavaScript 可以用于发现 `publicKeyFromPem(...)` 中的 RSA 公钥；必须解析实际 RSA 模数，而不是用 PEM 字符串长度猜测用途。
- 当前 `FileCredentialStore` 已使用原子替换、文件锁和 POSIX `0600` 权限，适合保存动态注册产生的 `client_secret`。
- 当前实现会在 OAuth 请求之前加密密码，因此不能直接插入公钥发现。实现需要把客户端解析、公钥解析、密码加密和 token 交换收拢为一次认证编排。

风险主要来自不同 AnyShare 版本的接口差异、重复注册产生的服务端垃圾客户端、密钥轮换导致已保存密文失效，以及从登录页抓取脚本带来的重定向与资源边界问题。下文对这些风险给出确定行为。

## 方案选择

采用“动态优先、host 限定兼容回退”的混合方案：

1. persistent profile 复用已缓存且 host 匹配的动态客户端和公钥。
2. 无缓存时先动态注册 OAuth2 客户端，再用该客户端请求授权页并发现公钥。
3. 只有注册端点明确表示不支持，并且 host 精确命中内置兼容表时，才使用该 host 的旧客户端和公钥。
4. 未列入兼容表的 host 绝不接收北航客户端 secret 或北航公钥。

不采用“每次密码重试或 token 刷新都重新注册”：persistent profile 跨进程复用，ephemeral profile 至多在每个进程注册一次并在该进程的全部重试中复用。ephemeral 为了兑现“不读写磁盘认证状态”的承诺，无法跨进程复用 secret；这是选择临时模式时明确接受的服务端注册成本。也不采用“每校一个适配器”，因为它会把新增学校重新变成必须修改 Pansh 源码和发版的流程。

## 安全边界与配置归属

`profile.yaml` 继续只保存非敏感、用户可审阅的连接配置：

```yaml
revision: 2
host: pan.example.edu.cn
pubkey: null                  # 可选人工固定；null 表示自动发现
store_password: true
verify_tls: true
```

动态 `client_secret`、登录密码密文和 token 只能进入当前 profile 的 `AuthRecord`。persistent 模式写入 `auth.json`；ephemeral 模式只写入 `MemoryCredentialStore`，进程退出后消失。

自动发现的公钥本身不是秘密，但和动态客户端、密文必须作为一个一致的认证快照更新。因此它也放入 `AuthRecord.bootstrap`，而不是登录过程中单独改写 `profile.yaml`。

用户显式配置的 `pubkey` 始终优先于自动发现。该能力用于旧部署、受限网络和故障恢复，但 README 不再要求新学校默认手填公钥。

## 数据模型

在 `src/pansh/models.py` 中新增以下模型。字段名和语义是实现契约；贡献者不应把 `client_secret` 移入 `ProfileConfig`。

```python
class OAuthClientCredentials(BaseModel):
    host: str
    client_id: str
    client_secret: str = Field(repr=False)
    redirect_uri: str
    source: Literal["dynamic", "compatibility"]


class PublicKeyMaterial(BaseModel):
    host: str
    pem: str
    fingerprint: str
    modulus_bits: int
    source: Literal["profile", "discovered", "compatibility"]


class StoredPassword(BaseModel):
    ciphertext: str = Field(repr=False)
    key_fingerprint: str


class AuthBootstrapState(BaseModel):
    oauth_client: OAuthClientCredentials | None = None
    public_key: PublicKeyMaterial | None = None


class AuthRecord(BaseModel):
    revision: int = 2
    username: str | None = None
    password: StoredPassword | None = Field(default=None, repr=False)
    cached_token: CachedToken = Field(default_factory=CachedToken)
    bootstrap: AuthBootstrapState = Field(default_factory=AuthBootstrapState)
```

`ProfileConfig.pubkey` 改为 `str | None`。旧 `encrypted` 字段读取时迁移为 `StoredPassword`：只有旧 profile 的 host 为 `bhpan.buaa.edu.cn` 且密钥仍是原内置北航公钥时，才补上该公钥的指纹；无法证明密钥来源时丢弃旧密文并在下一次登录重新询问密码。迁移不能猜测其他 host 的密钥。

序列化、`repr`、doctor 和 `whoami` 输出必须继续隐藏 `client_secret`、密文和 token。

## 模块与职责

### `src/pansh/oauth.py`

只实现 OAuth2 线路，不负责 profile、credential store 或交互输入：

```python
REDIRECT_URI = "http://127.0.0.1:8899/callback"

def register_client(
    base_url: str,
    client: httpx.Client,
) -> OAuthClientCredentials: ...

def build_authorize_url(
    base_url: str,
    credentials: OAuthClientCredentials,
    *,
    state: str,
) -> str: ...

def exchange_authorization_code(
    base_url: str,
    credentials: OAuthClientCredentials,
    code: str,
    client: httpx.Client,
) -> str: ...
```

注册请求固定声明 `authorization_code`、`refresh_token` 和 `implicit` grant，scope 为 `offline openid all`，device `client_type` 为 `windows`，与现有无浏览器密码表单保持一致。token 请求使用 `client_secret_basic`：先分别按 OAuth2 规则 form-encode client id 和 secret，再以 `id:secret` 做 Base64；secret 不放入请求 body 或日志。

### `src/pansh/key_discovery.py`

只负责从已经取得的授权页解析资源和选择 signin 公钥：

```python
@dataclass(frozen=True)
class DiscoveryLimits:
    max_scripts: int = 32
    max_html_bytes: int = 2 * 1024 * 1024
    max_script_bytes: int = 4 * 1024 * 1024
    max_total_script_bytes: int = 16 * 1024 * 1024


def discover_signin_key(
    page: httpx.Response,
    client: httpx.Client,
    *,
    limits: DiscoveryLimits = DiscoveryLimits(),
) -> PublicKeyMaterial: ...
```

实现必须：

- 从 `<script src>` 提取并去重 JavaScript URL，正确解析相对路径。
- 默认只下载与最终登录页同源的 HTTPS 脚本；拒绝 `file:`、`data:`、loopback 和跨源脚本。
- 在下载前后执行大小限制，避免依赖异常响应耗尽内存。
- 解码 JavaScript 字符串中的 `\\n`，提取完整 PEM 后使用 `rsa.PublicKey.load_pkcs1_openssl_pem` 验证。
- 以 `n.bit_length()` 选择最大模数；signin 密钥必须至少 2048 位，若并列则要求 PEM 完全一致，否则报告歧义。
- 指纹为 `rsa.PublicKey.save_pkcs1(format="DER")` 生成的规范化 PKCS#1 DER 的 SHA-256 十六进制值；不使用不稳定的文本格式作为指纹，也不为此新增密码学依赖。

### `src/pansh/auth.py`

保留为认证门面，负责单次同步 HTTP 会话中的完整协议编排：

```python
@dataclass(frozen=True)
class AuthRequest:
    base_url: str
    username: str
    password: str | None
    stored_password: StoredPassword | None
    profile_pubkey: str | None
    bootstrap: AuthBootstrapState
    verify_tls: bool = True


@dataclass(frozen=True)
class AuthResult:
    access_token: str
    stored_password: StoredPassword
    bootstrap: AuthBootstrapState


def authenticate(request: AuthRequest) -> AuthResult: ...
```

`authenticate()` 创建一个保留 cookie 的 `httpx.Client`，依次解析客户端、请求授权页、解析 challenge/CSRF、公钥、密码密文、signin、authorization code 和 access token。所有异常出口都关闭客户端。

### `src/pansh/compatibility.py`

兼容表使用精确、规范化 host 查询，不能使用后缀匹配：

```python
@dataclass(frozen=True)
class LegacyAuthPreset:
    oauth_client: OAuthClientCredentials
    public_key: PublicKeyMaterial


def preset_for_host(host: str) -> LegacyAuthPreset | None: ...
```

首版只允许 `bhpan.buaa.edu.cn`。旧 `_CLIENT_ID`、`_BASIC_AUTH` 和 `DEFAULT_PUBKEY` 从通用认证路径移入该模块，并标记为兼容回退。这样不会把北航凭据发送到用户输入的其他服务器。

### `src/pansh/api.py` 与 `src/pansh/session.py`

`AsyncApiManager` 仍负责 API client 和 token 刷新，但不再自行选择公钥或直接持有散落的 OAuth 常量。它接收一个 `AuthRequest` 模板，在线程中调用 `auth.authenticate()`，并公开只读 `auth_result`。

`SessionController` 是 credential store 的唯一协调者：

- 有效 cached token 存在时不触发注册或公钥发现。
- 需要重新认证时把 profile、密码输入和当前 `AuthRecord` 组装为 `AuthRequest`。
- manager 初始化成功后，将 token、`StoredPassword` 和 bootstrap 快照一次性保存。
- 只有 `StoredPassword.key_fingerprint` 与本次公钥指纹一致时才复用密文；不一致且没有明文密码时重新提示密码。
- 密码错误重试只替换密码相关状态，不重复注册客户端；公钥解密错误允许强制重新发现一次，但最多一次。

不得在 `AsyncApiManager` 的异步 client 和同步 OAuth client 之间共享连接对象；OAuth 流程继续通过 `asyncio.to_thread()` 执行，维持当前单 event-loop 约束。

## 认证数据流

新 token 登录按以下顺序执行：

1. 规范化 profile host，并丢弃 host 不匹配的 bootstrap 缓存。
2. 如果有可复用的动态客户端，直接使用；否则调用 `/oauth2/clients` 注册。
3. 注册端点返回 `404`、`405` 或 `501` 时查询精确 host 兼容表；其他 HTTP/协议错误直接报告，不静默回退。
4. 使用解析后的 client id 和 redirect URI 请求 `/oauth2/auth`，让同一个 client 保留 CSRF cookie。
5. 公钥优先级为 profile 显式值、host 匹配的缓存值、登录页发现值、精确 host 兼容值。
6. 公钥指纹与保存密文一致时复用密文；否则使用本次内存中的明文密码重新加密。
7. 使用授权页中的 challenge、CSRF 和 cookie 调用 `/oauth2/signin`。
8. 最多跟随 10 次同源 HTTP 重定向；每一步先检查 `Location` 中的 code。当目标为注册的 loopback callback 时提取 code，绝不真的请求 `127.0.0.1:8899`。
9. 使用动态 Basic Auth 换取 access token。
10. API 入口文档验证成功后，原子保存完整 `AuthRecord`；之前任何失败都不能写入半成品 bootstrap 状态。

注册成功但后续登录失败时，本次注册可以保留在内存中用于密码重试，但只有完整登录成功后才持久化。这会牺牲一次失败流程中的注册结果，以换取 credential store 的事务一致性。

## 错误模型

新增可区分的内部异常，CLI 最终转换成不含 secret 的中文提示：

- `OAuthRegistrationUnsupported`：端点明确不支持，允许检查 host 兼容表。
- `OAuthRegistrationError`：注册返回无效状态或缺少 client id/secret。
- `PublicKeyDiscoveryError`：未找到、格式无效、位数不足或密钥歧义。
- `UnsafeAuthResourceError`：脚本跨源、不安全 scheme、超出大小或重定向限制。
- `AuthenticationProtocolError`：缺少 challenge、CSRF、redirect、code 或 access token。

网络超时、TLS 和现有 `network.ApiException` 保留原始异常链。日志可以包含 host、状态码、脚本数量、密钥位数和指纹前 12 位，但禁止记录 client secret、完整 Basic 头、密码、密文、token、challenge 或 CSRF 值。

## 兼容与迁移

- 现有北航 default profile 无需编辑即可继续登录。第一次升级登录会迁移认证记录；动态注册可用时转为动态客户端，不可用时使用 host 限定回退。
- 其他学校的旧 profile 若显式配置了 `pubkey`，继续优先使用该 key，同时动态注册 OAuth 客户端。
- 旧 profile 的 `pubkey` 等于历史默认值但 host 不是北航时，将其视为隐式默认而非用户固定值，迁移为 `null` 以触发发现。
- `AuthRecord.revision` 从 1 升至 2。未知的更高 revision 必须拒绝读取，避免旧客户端覆盖新格式。
- `logout` 继续清除整个当前 profile 的认证记录，包括动态客户端；后续登录会重新注册。单独清 token 但保留客户端不在本次范围。

## 测试契约

所有 CI 测试离线运行，使用 `httpx.MockTransport` 或已有 fake manager，不访问真实学校服务。

### 新测试文件

- `tests/test_oauth_registration.py`
  - 注册 payload、201 解析、缺字段、unsupported 状态、Basic Auth 编码和 secret 日志脱敏。
- `tests/test_key_discovery.py`
  - 相对脚本 URL、转义换行、1024/2048 位选择、无效 PEM、同位密钥歧义、跨源与大小限制。
- `tests/test_auth_flow.py`
  - cookie/CSRF 保持、完整动态流程、redirect code 截获、不请求 loopback、host 限定回退、失败不返回半成品状态。

### 更新现有测试

- `tests/test_credentials.py`：revision 2 round trip、secret 不出现在 repr、persistent/ephemeral 隔离。
- `tests/test_runtime.py`：revision 1 迁移、北航密文指纹补全、非北航隐式默认 key 清理、未知 revision 拒绝。
- `tests/test_session.py` 与 `tests/test_session_modes.py`：有效 token 不 bootstrap、密钥轮换重新询问密码、错误重试不重复注册、ephemeral 不落盘。
- `tests/test_profile_connection.py`：`verify_tls` 传递到注册、发现、signin 和 token 全链路。
- `tests/test_doctor.py` 与 `tests/test_cli_auth.py`：任何输出都不包含新增敏感字段。

验收仍要求：

```bash
ruff check src tests
python -m pytest -o pythonpath=src tests -q
python -m build
python -m twine check dist/*
```

## 人工互操作验证

真实服务验证不进入自动 CI。维护者发布前至少验证：

1. 北航现有 persistent profile 可复用旧 token并可重新登录。
2. 北航删除本地 auth record 后可以动态注册，或在端点不支持时进入 host 限定回退。
3. 山东大学仅配置 host 后能够注册客户端、发现 2048 位 signin key 并换取 token。
4. ephemeral 登录结束后磁盘上没有 client secret、密文或 token。
5. 模拟服务端 RSA key 轮换后不会复用旧密文。

真实验证日志和 Issue 回帖不得包含 client id/secret、完整公钥以外的认证数据、token、账号或密码。

## 实施顺序

1. 引入 revision 2 模型与迁移测试，不改变认证行为。
2. 实现并测试 `oauth.py` 的注册、授权 URL 和 token 交换。
3. 实现并测试 `key_discovery.py` 的资源边界和 RSA 选择。
4. 用 `AuthRequest`/`AuthResult` 重构 `auth.py`，先保持北航兼容 preset 通过。
5. 接入动态客户端与自动发现，完成端到端 mock 测试。
6. 将新认证结果接入 manager、session 和 credential store。
7. 更新 README 的 profile 示例、迁移说明、跨校登录和故障诊断。
8. 完成离线全套验证与两校人工互操作验证。

每一步必须保持已有 persistent、ephemeral、event-loop 生命周期和传输测试通过。动态注册与 RSA 发现应作为同一功能发布，因为只实现其中一项仍无法让新 host 达到零配置登录。

## 完成标准

- 新 profile 只填写 host 即可进入动态密码登录流程。
- Pansh 不再向任意 host 发送全局硬编码的北航 OAuth secret。
- 2048 位 signin 公钥由真实模数选择，密钥变化会使旧密文失效。
- persistent profile 复用动态客户端；ephemeral profile 不写磁盘。
- 所有失败路径关闭 HTTP client，不请求 loopback callback，不持久化半成品认证状态。
- 现有北航用户升级后无需手工迁移配置。
- 自动测试、构建检查和人工互操作矩阵全部通过。
