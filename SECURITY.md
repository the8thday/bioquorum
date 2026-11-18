# Security

BioQuorum lets a language model run code and call tools on your machine. This page says what protects you, and where the protection stops. It describes the current code, not the roadmap.

## Threats we design for

1. **Model-written code damages or leaks data**: deletes files, reads things it should not, sends data out.
2. **Prompt injection**: text returned by a tool (a web page, an abstract, a database record) tells the model to do something harmful.
3. **A project's data goes to a model or service that is not cleared for it.**

## What is in place

### Kernel sandbox (Linux)

Code the model runs with `python_execute` executes in a Jupyter kernel started inside [bubblewrap](https://github.com/containers/bubblewrap):

| | Inside the sandbox |
|---|---|
| Network | None. An empty network namespace: no DNS, no sockets to the internet or to localhost services. |
| Project directory | Read-write. |
| Data directories (`[project] data_dirs`) | Read-only. |
| Python environment, system libraries | Read-only. |
| Rest of your home directory and file system | Not visible. |
| `.bioquorum/` (event log, content store) | Appears empty; cannot be read or changed. |
| `bioquorum.toml`, `.git` | Read-only, so code cannot plant an MCP server command or a git hook that would later run outside the sandbox. |
| Environment variables | Cleared; a short fixed list is set. API keys are never visible to kernel code. |
| Processes | Own PID namespace; other processes are not visible. |

Every run records how it was isolated (`isolation` on the `run_started` event: `bwrap`, `bwrap+net` or `none`).

**When the sandbox is not available** (macOS, Windows, Linux without `bwrap` or without unprivileged user namespaces, some containers):

- `internal` and `confidential` projects refuse to run code. There is no silent downgrade.
- `public` projects run with a warning on every start. In this mode kernel code can read your files and reach the network. Environment variables whose names look like secrets (`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, ...) are still removed.

**Kernel network access** can be turned on with `[kernel] network = true` in `bioquorum.toml`, for `public` projects only. Files and environment stay sandboxed.

### Tool calls

Every tool call goes through one function (`tools.invoke`), which checks the project's data classification, checks permission (asking you when nothing has been granted), and writes the call and its result to an append-only event log that doubles as the audit trail. Refused calls are logged too.

### Downloads

`download_file` is how external data reaches an analysis now that the kernel has no network. It is a `network` permission on the target host, available to `public` projects only (the URL itself leaves your machine). It accepts http(s) URLs without embedded credentials, refuses any URL or redirect that resolves to a private, loopback or link-local address (so injected instructions cannot use it to reach localhost services or your intranet), enforces a size limit while streaming, and records the URL, size and SHA-256 in the event log with a copy of the file in the content store. Files land in `downloads/` inside the project.

### Data classification

A project is `public`, `internal` or `confidential`. Model providers and tools declare the highest level they may receive. A call that would send data above that level is refused and logged; it is never rerouted to something weaker without telling you. By default remote model providers accept `public` and `internal`; only endpoints on your own machine accept `confidential`.

### Untrusted content

Everything a tool returns is marked untrusted and wrapped in delimiters before the model sees it; the system prompt tells the model not to follow instructions inside them. **This lowers the risk of prompt injection; it does not remove it.** The sandbox and the permission checks are what limit the damage when an injection succeeds.

### Secrets and telemetry

API keys are read from environment variables, referenced in config as `env:NAME`, and redacted from events, logs and error messages. BioQuorum sends no telemetry.

## What is not protected yet

- **External MCP servers are not sandboxed.** A server you configure runs as a normal process with your permissions (or as a remote service). Only add servers you trust. BioQuorum controls which project classifications may use a server, not what the server does.
- **Queries to public databases leave your machine.** A gene name or compound sent to Open Targets or PubMed is visible to that service. Remote servers are limited to `public` projects unless you mark them otherwise.
- **No outbound allowlist.** Kernel network is all or nothing.
- **DNS rebinding.** `download_file` checks the addresses a host resolves to, then connects; a hostile DNS server could answer differently the second time. Treat this as a hurdle, not a guarantee.
- **Downloads are not available to `internal` / `confidential` projects yet.** Download public data in a `public` project, or place the files in a data directory yourself.
- **The project directory is writable.** Sandboxed code can delete or overwrite files in it, including scripts you may run later yourself. Keep raw data in `data_dirs` (read-only) and the project under version control.
- **No resource limits.** Kernel code can fill the disk of the project directory or use all memory and CPU.
- **bubblewrap is not a virtual machine.** A kernel-level vulnerability in Linux could let code escape. For hostile inputs at scale, run BioQuorum inside a VM or container as well.
- **No sandbox on macOS and Windows yet.**
- **Model providers see what you send them.** Classification controls which provider gets which project; it cannot make a remote provider private.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on the repository (Security → Report a vulnerability) rather than a public issue.
