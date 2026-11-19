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

Every run records how it was isolated (`isolation` on the `run_started` event: `bwrap`, `bwrap+net`, `seatbelt`, or `none`).

**When the sandbox is not available** (Windows, Linux without usable `bwrap`, or macOS without an enabled and usable native profile):

- `internal` and `confidential` projects refuse to run code. There is no silent downgrade.
- `public` projects run with a warning on every start. In this mode kernel code can read your files and reach the network. Environment variables whose names look like secrets (`*_KEY`, `*_TOKEN`, `*_SECRET`, `*_PASSWORD`, ...) are still removed.

An explicit `[kernel] macos_sandbox = true` uses the host's `sandbox-exec` facility. It confines ordinary file content access to the project, declared data, Python environment, and system paths. The profile allows metadata probes and private Jupyter Unix sockets. It blocks IP networking, protects the project configuration, and denies access to `.bioquorum/`. The native profile and configured proxy require working isolation, including for public projects.

**Kernel network access** can be turned on with `[kernel] network = true` in `bioquorum.toml`, for `public` projects only. Files and environment stay sandboxed.

### Tool calls

Every tool call goes through one function (`tools.invoke`), which checks the project's data classification, checks permission (asking you when nothing has been granted), and writes the call and its result to an append-only event log that doubles as the audit trail. Refused calls are logged too.

The workbench **Audit log** displays session-local metadata from that event stream. It provides read-only pages with classification, model calls, permissions, tool outcomes, and recorded isolation. A literature lookup is represented by its parent start and finish entries.

Configured stdio MCP commands can opt into offline isolation with `isolated = true`. They run in a separate working directory with no project mount, with explicit read-only inputs and their runtime. The connector owner controls the executable and read paths. Hosted MCP endpoints remain outside BioQuorum's process boundary.

### Downloads

`download_file` is how external data reaches an analysis now that the kernel has no network. It is a `network` permission on the target host, available to `public` projects only (the URL itself leaves your machine). It accepts http(s) URLs without embedded credentials, refuses any URL or redirect that resolves to a private, loopback or link-local address (so injected instructions cannot use it to reach localhost services or your intranet), enforces a size limit while streaming, and records the URL, size and SHA-256 in the event log with a copy of the file in the content store. Files land in `downloads/` inside the project.

### Data classification

A project is `public`, `internal` or `confidential`. Model providers and tools declare the highest level they may receive. A call that would send data above that level is refused and logged; it is never rerouted to something weaker without telling you. By default remote model providers accept `public` and `internal`; only endpoints on your own machine accept `confidential`.

### HTTPS egress proxy

`bq egress` can listen on IPv4 loopback with an exact HTTPS host allowlist. It accepts CONNECT requests for port 443, checks the complete DNS answer, and connects directly to a checked public IP. Clients retain their normal TLS verification. `[security] outbound_proxy` sends downloads, online citation lookups, remote model calls, and HTTP MCP through that listener. Loopback model providers run directly and must be trusted. A configured proxy requires offline isolation for stdio MCP and disables kernel networking. The proxy governs its own listener and checked destination addresses. Use the host's network policy for a deployment-wide egress boundary. Access to the loopback listener is available to local processes.

### Untrusted content

Everything a tool returns is marked untrusted and wrapped in delimiters before the model sees it; the system prompt tells the model not to follow instructions inside them. **This lowers the risk of prompt injection; it does not remove it.** The sandbox and the permission checks are what limit the damage when an injection succeeds.

### Secrets and telemetry

API keys are read from environment variables, referenced in config as `env:NAME`, and redacted from events, logs and error messages. BioQuorum sends no telemetry.

## What is not protected yet

- **Unisolated stdio MCP servers use your process privileges.** Set `isolated = true` for offline process isolation, or use endpoints you trust.
- **Queries to public databases leave your machine.** A gene name or compound sent to Open Targets or PubMed is visible to that service. Remote servers are limited to `public` projects unless you mark them otherwise.
- **Direct downloads and DNS changes.** Without the optional proxy, `download_file` checks DNS before its HTTP client resolves and connects. The HTTPS proxy connects to an IP already checked by its allowlist path.
- **Downloads are not available to `internal` / `confidential` projects yet.** Download public data in a `public` project, or place the files in a data directory yourself.
- **The project directory is writable.** Sandboxed code can delete or overwrite files in it, including scripts you may run later yourself. Keep raw data in `data_dirs` (read-only) and the project under version control.
- **No resource limits.** Kernel code can fill the disk of the project directory or use all memory and CPU.
- **bubblewrap is not a virtual machine.** A kernel-level vulnerability in Linux could let code escape. For hostile inputs at scale, run BioQuorum inside a VM or container as well.
- **Platform limits.** Windows has no kernel sandbox. The macOS backend relies on an OS sandbox facility whose availability can vary across operating system versions.
- **Model providers see what you send them.** Classification controls which provider gets which project; it cannot make a remote provider private.

## Reporting a vulnerability

Please use GitHub's private vulnerability reporting on the repository (Security → Report a vulnerability) rather than a public issue.

## Local workbench

The workbench binds to an IPv4 loopback address on an ephemeral port by
default. It checks the exact Host and requires the matching Origin for
write requests. Do not expose the local server through a tunnel or reverse
proxy. It has no shared-service authentication and assumes all processes
and users with local access to the project are trusted. Tool text and
model output are displayed as text, and recorded figures are scoped to a
session and served with a sandbox policy. Project classifications and
tool permissions apply in the same execution path as the CLI.

Stopping a turn cancels the current request and tears down its kernel. A
tool or provider might already have performed its action. An interrupted
turn and a session fork do not roll back files, provider usage or external
effects. Forks read the same project files; they copy neither kernel
memory nor permissions. The bundled offline example uses a FakeKernel
and canned model responses, with no external provider calls.

For a single-user server, use a dedicated ordinary account and SSH loopback forwarding. Containerized use and network boundaries are covered in the [deployment guide](docs/demo-v0.6.md).
