# ADR-048 · 工作台模型连接与 ChatGPT 套餐

接受。研究者可在侧栏进入 Models & accounts，按服务商、模型 ID 和角色登记模型。自定义 OpenAI 兼容服务单独命名，环境变量与连接同名。现有连接的端点、凭据引用与项目分级策略不能由添加模型的操作覆盖。

ChatGPT 是独立远程提供商。使用 OpenAI 为本地开源应用提供的 Sign in with ChatGPT、动态注册、PKCE、OIDC 签名验证和用户同意，通过公开 Responses API 消耗适用套餐。模型清单取自当前账户。工具调用仍走 BioQuorum 自己的执行与审计路径。OAuth 凭据存于本机用户私有目录，项目事件只保存连接和角色。轮换刷新令牌的操作跨进程互斥。套餐连接失败时停止该角色的请求，避免悄悄转用付费 API Key。

本机注册的外部 host ID 按 OpenAI 指定格式生成 UUIDv4；BioQuorum 领域 ID 继续使用 ULID。模型适配复用锁文件内的 Pydantic AI，签名验证及文件锁将现有 PyJWT、cryptography 与 filelock 声明为直接依赖。
