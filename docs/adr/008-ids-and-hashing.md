# ADR-008 ULID 作 ID，SHA-256 作哈希，Run 身份三要素

状态：已接受（2026-11-13）

## 背景
研究包、重跑比对、内容寻址存储都依赖稳定的身份规则。规则不定，后期补不回来。

## 决定
- ID：ULID 字符串。
- 哈希：SHA-256 十六进制。文件哈希字节；内存对象先规范序列化（DataFrame→parquet、AnnData→h5ad）再哈希；代码去行尾空白统一换行后哈希。
- `env_lock_hash = sha256(pixi.lock)`。
- `run_key = sha256(code_hash + env_lock_hash + sorted(input hashes))`。同 run_key 输出不同即复现失败，数值型可设容差。

## 后果
- 规范序列化的参数（parquet 压缩、列排序）一旦定下不可改，否则历史 hash 失效。
- 无法规范序列化的对象标记为弱哈希，不参与复现判定。
