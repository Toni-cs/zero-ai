<div align="center">

# ZeroAI

**English** | [简体中文](README.zh-CN.md)

### A Terminal AI Collaboration Platform for Research and Engineering

**Multi-expert collaboration · Academic literature search · National-standard document generation · Security audit · Cross-platform SSH operations · Local system operations**

[![Python](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/)
[![Platform](https://img.shields.io/badge/Platform-Windows%20%7C%20macOS%20%7C%20Linux-green.svg)]()
[![License](https://img.shields.io/badge/License-Proprietary-orange.svg)](LICENSE)
[![PyPI](https://img.shields.io/badge/PyPI-zero--ai--cli-1.1.3-blue.svg)](https://pypi.org/project/zero-ai-cli/1.1.3/)
[![GitHub](https://img.shields.io/badge/GitHub-zero--ai-black.svg)](https://github.com/gt17641001169-design/zero-ai)

</div>

---

## Abstract

ZeroAI is a terminal AI collaboration platform built for researchers and developers. It uses a multi-expert collaboration architecture that brings task planning, code generation, deep reasoning, academic writing, literature search, document generation, security auditing, and remote operations into a single terminal environment — cutting down the cost of switching between tools and improving research throughput.

The system is purpose-built for research workflows: it integrates the Semantic Scholar academic database (200M+ papers) with intelligent filtering by citation count, influence, and year; it ships a LaTeX formula rendering engine that converts fractions, radicals, matrices, limits, summations, and integrals into Unicode and high-resolution PDF images; it generates Word/PDF academic documents that follow the GB/T 7713.1-2025 national standard; it provides an offline speech recognition engine to keep research data private; and its proxy server architecture delivers zero API key exposure for secure team collaboration.

---

## Research Value

### 1. Academic Literature Search and Review Assistance

Integrates the Semantic Scholar academic database (200M+ peer-reviewed papers) and supports:

- Literature search by keyword, author, or DOI
- Intelligent ranking by citation count, influence, and publication year
- Automatic generation of a literature review draft, helping researchers grasp a field quickly
- Free, no API key required, suitable for research groups with limited funding

### 2. Academic Formula Derivation and Rendering

Built-in LaTeX formula rendering engine, supporting:

- Inline formulas `$...$` and display formulas `$$...$$` in dual mode
- Academic symbols including fractions, radicals, super/subscripts, Greek letters, matrices, limits, summations, integrals, and partial derivatives
- Display formulas rendered to high-resolution images via matplotlib mathtext, ready to embed in PDF/Word
- Unicode output compatible with both terminal display and document typesetting

### 3. National-Standard Academic Document Generation

Generated in accordance with GB/T 7713.1-2025, *Presentation of scientific and technical reports, dissertations and scientific papers*:

- Four-level heading hierarchy (`#` through `####`)
- Automatic reference formatting
- Mixed layout of academic formulas, tables, and images
- Dual-format output (.docx / .pdf) conforming to academic publishing conventions

### 4. Multi-Expert Collaborative Research

Seven experts collaborate on a single research question, coordinated by the project manager:

- Academic Research Expert: literature review, formula derivation, paper writing
- Reasoning Expert: mathematical proof, logical analysis, complexity analysis
- Coding Expert: algorithm implementation, experiment code, data processing
- Project Manager: task decomposition, result aggregation, multimodal analysis
- Well suited to cross-disciplinary research (e.g. computational linguistics, bioinformatics, computational social science)

### 5. Offline Speech Recognition and Data Privacy

- Offline speech recognition engine based on SenseVoice (Alibaba DAMO Academy)
- Support for five languages — Chinese, English, Japanese, Korean, and Cantonese — for international collaboration
- Runs fully offline; research data never leaves the machine, meeting data-security requirements of confidential research projects
- No API key, no cloud calls, no data-leak risk

### 6. Team Collaboration and API Key Isolation

The proxy server architecture achieves zero API key exposure:

- Real API keys live only in the server-side `.env` file
- Clients hold only an access token, fully separated from the upstream keys
- Per-user token assignment for multi-member research groups
- Rate limiting and audit logging to prevent abuse and enable traceability

### 7. Research Code Security Audit

For research code — especially data-processing and statistical-analysis code — the system provides:

- Hardcoded secret scanning (to prevent dataset access credential leaks)
- Path traversal detection (to prevent accidental deletion of experiment data)
- Known-vulnerability checks for dependency packages
- Non-intrusive scanning; no penetration testing is performed

---

## System Architecture

### Multi-Expert Collaboration Architecture

| Expert | Responsibility | Typical Use |
|------|------|---------|
| Project Manager | Task analysis, scheduling, multimodal | Cross-disciplinary decomposition, image-text analysis |
| Coding | Code generation, debugging, refactoring | Algorithm implementation, experiment code |
| Reasoning | Deep reasoning, mathematics, logic | Mathematical proof, complexity analysis |
| General Knowledge | General Q&A, translation, encyclopedia | Cross-language literature translation, concept lookup |
| Chinese Writing | Chinese writing, copywriting, reports | Chinese academic writing, research reports |
| Multimodal | Image understanding, image-text analysis | Chart analysis, experiment result visualization |
| Academic Research | Academic research, formula derivation, paper writing | Literature review, paper authoring |

### Hybrid Routing (Zero Latency + Semantic Precision)

ZeroAI uses **two-tier hybrid routing** that balances speed and precision:

| Tier | Trigger Condition | Latency | Implementation |
|------|---------|------|---------|
| **L1 Keyword fast routing** | Short messages (<10 characters) or an unambiguous expert keyword hit | 0 ms | Local keyword matching + LRU cache |
| **L2 GLM semantic routing** | L1 misses (falls back to `knowledge`) | 1-2 s | GLM-4V semantic classification + MD5 cache |

**Workflow**:
1. User input → keyword matching (clear domains such as coder/academic/chinese/reasoner)
2. Hit → route directly to the corresponding expert (zero latency; most scenarios take this path)
3. Miss → call GLM-4V for semantic classification (1-2 seconds), caching the result in an LRU (256 entries)
4. GLM failure → degrade to the General Knowledge expert (never blocks)

**Performance advantage**: questions with unambiguous keywords — code, papers, writing — are routed with zero latency; only vague questions (such as "take a look at this for me") go through semantic routing. The LRU cache avoids repeated classification, and MD5 digests are used as keys to prevent long-text prefix collisions.

**Circuit breaker**: an OpenRouter expert that fails three times in a row is automatically tripped, skipping that expert and degrading to GLM so users are not left stuck on "Thinking…".

### Automatic Routing Examples

- "Prove …" → Reasoning expert (keyword hit)
- "Implement …" → Coding expert (keyword hit)
- "Paper …" → Academic Research expert (keyword hit)
- "Translate …" → General Knowledge expert (keyword hit)
- "Take a look at whether this approach is any good" → GLM semantic routing (no keyword hit)

### Hybrid Thinking Mode (Multi-Expert Collaboration)

Multiple experts work on the same problem while the project manager consolidates their conclusions — useful for complex research questions such as "design an experiment to validate the hypothesis and analyze statistical significance". In hybrid mode, inter-expert communication runs over a MessageBus (publish/subscribe) plus a Blackboard (shared state), supporting three collaboration strategies: pipeline, role dependency graph, and consensus voting.

---

## Core Features

### SSH Remote Deployment (7 tools)

- Parallel connections to multiple servers, distinguished by `conn_id`, with password/key authentication
- Remote command execution, with second-confirmation for dangerous commands (11 categories including `rm -rf /`, `mkfs`, `dd`)
- SFTP file transfer — upload/download files with automatic permission handling
- One-click automated deployment with `ssh_deploy` (pre_check → mkdir → upload → install → restart → health_check → post_cmds)
- Audit log (up to 200 entries), fully traceable
- Host address validation, dangerous-command blacklist, optional internal-IP blocking, and output truncation protection

### AI Remote Operations (8 semantic tools, cross-platform)

Turns "AI stringing commands together" into "AI invoking semantic tools", reducing hallucination, unifying error handling, and automatically analyzing results. All 8 tools support **automatic adaptation across Linux and Windows Server**:

| Tool | Function |
|------|------|
| `ssh_service_manage` | systemd wrapper (status/start/stop/restart/enable) |
| `ssh_log_view` | journalctl wrapper with automatic error-density statistics |
| `ssh_process_check` | Top N sorted by CPU/memory |
| `ssh_disk_analyze` | `df` + `du` Top 10, with automatic critical/warning annotation |
| `ssh_network_diag` | Ports/connections/ping/statistics |
| `ssh_docker_manage` | Containers/images/logs/resources |
| `ssh_firewall_manage` | Automatic detection of ufw/firewalld/iptables |
| `ssh_health_check` | Comprehensive report + AI health analysis + anomaly annotation |

### AI Operations Decision Chain

- Vague problem handling: "the server is slow" → run a health check first → drill down → localize → give recommendations
- Explicit troubleshooting: "nginx is down" → check status → if failed → inspect the error log → isolate the root cause
- Proactive AI analysis: the health report automatically identifies anomalies in load, disk, swap, failed services, and error logs

### Cross-Platform SSH Operations (Linux + Windows Server)

All 8 AI remote operations tools support **automatic cross-platform adaptation**; on connecting they detect the remote operating system, so users never specify command syntax:

| Tool | Linux Implementation | Windows Implementation |
|------|-----------|-------------|
| `ssh_service_manage` | `systemctl` | `sc.exe` / `Get-Service` |
| `ssh_log_view` | `journalctl` | `Get-WinEvent` (event log) |
| `ssh_process_check` | `ps aux` | `Get-Process` |
| `ssh_disk_analyze` | `df` + `du` | `Get-CimInstance` + `Get-ChildItem` Top 10 |
| `ssh_network_diag` | `ss` / `netstat` | `Get-NetTCPConnection` |
| `ssh_docker_manage` | Native Docker CLI | Docker Desktop (`docker.exe`) |
| `ssh_firewall_manage` | `ufw` / `firewalld` / `iptables` | `netsh advfirewall` |
| `ssh_health_check` | Comprehensive health check (auto-detects OS) | Comprehensive health check (Windows Server metrics) |

**Why cross-platform matters**:
- The same natural-language instruction manages both Linux and Windows servers: "check nginx status" executes correctly on either side
- Operators do not need to memorize two sets of command syntax, lowering the barrier to cross-platform operations
- Command differences are handled inside the tools, so the AI never assembles commands by hand and hallucinates less

### Local Operations Tools (4 semantic tools, cross-platform)

Common local machine operations are wrapped as semantic tools that adapt automatically to Windows/Linux, and are **preferred over hand-assembled `run_command` calls**:

| Tool | Function | Typical Scenario |
|------|------|---------|
| `local_port_check` | Port/network check | "what ports are open", "is port 80 taken", "ping 192.168.1.1" |
| `local_process_check` | Process inspection/management | "is the machine bogged down", "find the chrome process", "kill PID 1234" |
| `local_disk_check` | Disk space analysis | "how much disk is left", "which directory is largest", "the C: drive is full" |
| `local_service_check` | Service management | "list running services", "MySQL status", "restart the docker service" |

**Security design**:
- Anti-injection whitelist: process names/service names may contain only letters, digits, `.`, `_`, and `-`, rejecting injections such as `nginx; rm -rf /`
- Port checks probe via socket connections rather than external commands
- Dangerous operations (kill/stop) require explicit parameters

### Cross-Platform Command Compatibility (automatic translation in `run_command`)

The `run_command` tool embeds a **Linux ↔ Windows command translation engine**, so users can type a command from any platform and the system adapts it to the current operating system:

```
User on Windows types 'ls -la'      -> automatically executes 'dir -la'
User on Windows types 'cat file'    -> automatically executes 'type file'
User on Windows types 'rm -rf /tmp' -> automatically executes 'rmdir /s /q /tmp'
User on Windows types 'grep x file' -> automatically executes 'findstr x file'
User on Windows types 'ps aux'      -> automatically executes 'tasklist aux'
```

**Translation characteristics**:
- **50+ command mappings** covering file operations, networking, services, processes, and package management
- **Longest-match first**: `rm -rf` matches before `rm`, preventing mistranslation
- **Smart skip**: when the original command is already in the target platform's format (e.g. `netstat -ano`), translation is skipped to avoid duplication
- **Translation notice**: after translating, the system prints a notice such as `[cross-platform] translated 'ls' to 'dir'`

### Voice Conversation

- Offline speech recognition: SenseVoice (Alibaba DAMO Academy), five languages (Chinese/English/Japanese/Korean/Cantonese), no API key required
- Speech synthesis: Edge TTS with male/female voice switching and adjustable speaking rate
- Live subtitles: real-time subtitles in conversation mode
- Shortcuts: `Ctrl+T` for single-shot voice input, `Ctrl+D` for continuous conversation mode

### Security Audit

- Vulnerability scanning: SQL injection, XSS, hardcoded secrets, path traversal, and more
- Dependency checks: scan dependency packages for known vulnerabilities
- Configuration audit: inspect configuration files for security issues
- Non-intrusive: only scans the project itself; no penetration testing

### ReAct Agent Loop (Reasoning-Action Cycle)

The agent framework introduced in Phase 1 goes beyond conventional single-shot tool calls:

- **Chain-of-thought visualization**: shows the full Thought → Action → Observation → Thought process
- **Plan-and-Execute planning**: complex tasks are decomposed into multi-step plans and executed incrementally
- **Reflexion self-critique**: when a tool call fails, the agent analyzes the cause and adjusts its strategy
- **Parallel tool calling**: independent subtasks can run in parallel for higher efficiency
- **Tool result summarization**: long outputs are compressed automatically to avoid context overflow
- **TUI commands**: `/react` enters Agent mode, `/index` builds the project index, `/memory` shows vector memory

### Vector Memory and RAG (Retrieval-Augmented Generation)

The long-term memory system introduced in Phase 2 supports semantic context recall:

- **GLM embedding-3 integration**: generates vectors with Zhipu's GLM embedding-3 model
- **Hybrid retrieval**: vector similarity (0.7 weight) fused with BM25 keywords (0.3 weight)
- **Conversation history vectorization**: historical conversations are chunked, vectorized, and stored automatically
- **Memory decay**: memories not accessed for a long time are automatically down-weighted to avoid noise
- **File background watching**: project files are re-indexed automatically when they change, keeping memory fresh
- **Zero-dependency fallback**: when no embedding API is available, it degrades to pure TF-IDF retrieval

### MCP Support (Model Context Protocol)

The bidirectional MCP support introduced in Phase 3 lets ZeroAI act either as an MCP Server exposing its tools to external clients, or as a Client connecting to external MCP servers:

- **JSON-RPC 2.0 core**: a complete implementation of the MCP specification
- **stdio / SSE dual transport**: supports both local subprocesses (stdio) and remote services (SSE)
- **Automatic tool registration**: after connecting to an external MCP Server, its tools are registered into the ZeroAI tool table automatically
- **58 tools exposed**: as an MCP Server, it exposes all 58 built-in tools to clients such as Claude Desktop
- **Claude Desktop configuration example**: a ready-to-use configuration is provided at `zeroai/mcp/examples/claude_desktop_config.json`
- **Launching**: start the MCP Server with `python -m zeroai.mcp`

### C/Zig Acceleration Layer (high-performance terminal rendering)

The mixed-language acceleration layer introduced in Phase D provides performance headroom for TUI rendering:

- **Three-tier fallback**: Zig shared library → C extension → pure Python, automatically selecting the fastest available path
- **Cross-platform builds**: `scripts/build_extensions.py` supports Windows / macOS / Linux
- **ABI consistency**: the 8-byte `StyleStruct` has an identical layout across C, Zig, and Python
- **ctypes loading**: the Zig library is loaded via ctypes, usable without compiling a Python extension
- **Multi-path search**: environment variable → inside the package → project root → zig-out → site-packages → system libraries
- **Diagnostic function**: `_diagnose_zig_load_failure()` gives a detailed analysis of load failures
- **ABI test suite**: `tests/test_abi.py` verifies field offsets, color mapping, and large-buffer stress tests

---

## Installation

### Option 1: pip install (recommended)

```bash
pip install zero-ai-cli
```

### Option 2: Install from source

```bash
git clone https://github.com/gt17641001169-design/zero-ai.git
cd zero-ai-cli
pip install -e .
```

### Optional: install voice support

```bash
pip install zero-ai-cli[voice]
```

Voice support includes sherpa-onnx (speech recognition), faster-whisper (fallback recognition), and av (audio processing).

On first use, the SenseVoice model (~220 MB) is downloaded automatically from the HuggingFace mirror; after that it runs offline.

---

## Usage

After installation, type this in any terminal:

```bash
zeroai
```

Or use the Python module entry point (recommended):

```bash
python -m zeroai                    # default Textual UI (recommended)
python -m zeroai --ui textual       # explicitly select the Textual UI
python -m zeroai --ui zeroai-tui    # C/Zig-accelerated TUI
python -m zeroai --expert coder     # specify an expert directly
python -m zeroai --version          # print the version
```

> **Architecture change note**: as of v1.1.3 the project was refactored from the single-file `tui_agent.py` into the modular `zeroai` package.
> `python -m zeroai` is the recommended entry point; `python tui_agent.py` still works (backward compatible, with a deprecation notice).

### First-Time Configuration

**Option A: Direct mode (personal use)**

1. Get a GLM API key (free): sign up at https://open.bigmodel.cn/ and create a key
2. After launching ZeroAI, press `Ctrl+P` to open the settings panel
3. Enter your GLM API key and save
4. Alternatively, set the environment variable: `ZEROAI_API_KEY_GLM=your_key`

**Option B: Proxy mode (team collaboration, recommended)**

Access AI models through a proxy server, with zero API key exposure on the client:

1. Deploy the proxy server (see "Proxy Server Deployment" below)
2. After launching ZeroAI, press `Ctrl+P` to open the settings panel
3. In the "Proxy Server" section, configure:
   - Proxy address: `http://<server-ip>:8000/v1`
   - Access token: assigned by the administrator
   - Proxy mode: enabled
4. All requests are forwarded through the proxy automatically; the real key stays on the server

### Command Reference

Every command also accepts a Chinese alias, and both aliases work at any time. The table below lists the English form; the Chinese aliases are documented in the [Chinese README](README.zh-CN.md).

#### Basic Commands

| Command | Description |
|------|------|
| `/help` | Show help |
| `/clear` | Clear the screen |
| `/new` | Start a new conversation |
| `/exit`, `/quit` | Exit the program |

#### Mode Switching

| Command | Description |
|------|------|
| `/expert` | Switch to expert mode (automatic routing) |
| `/hybrid` | Switch to hybrid thinking (multi-expert collaboration) |
| `/manual` | Switch to manual mode (specify a model) |
| `/model` | Show the current model and expert team |
| `/model glm` | Switch to Zhipu GLM |
| `/model glm-v` | Switch to Zhipu GLM-4V (multimodal) |
| `/model openrouter` | Switch to OpenRouter |
| `/model ollama` | Switch to Ollama (local model) |

#### Agent, Index and Memory

| Command | Description |
|------|------|
| `/react` | Switch to ReAct Agent mode |
| `/init` | Analyze the project structure and generate `AGENTS.md` |
| `/index` | Build the project vector index (enables RAG retrieval) |
| `/memory` | Show vector memory statistics |
| `/mcp` | MCP protocol management (list/install/connect/tools) |

#### Voice Interaction

| Command | Description |
|------|------|
| `/voice` | Toggle automatic reading of AI replies |
| `/dialog` | Enter voice conversation mode |
| `/stop` | Stop voice conversation mode |
| `/voice_female` | Switch to a female voice (Xiaoxiao) |
| `/voice_male` | Switch to a male voice (Yunxi) |
| `/voice_rate +10%` | Set the speaking rate |
| `Ctrl+T` | Single-shot voice input |
| `Ctrl+D` | Voice conversation mode |

#### Other Features

| Command | Description |
|------|------|
| `/image`, `/img` | Paste an image from the clipboard |
| `/copy` | Copy the most recent reply |
| `/copy N` | Copy the Nth code block from the most recent reply |
| `/audit` | Security audit |
| `/ssh` | Show the SSH remote deployment + AI operations toolset (15 tools) |
| `Ctrl+G` | Paste-image shortcut |
| `Ctrl+N` | New conversation |
| `Ctrl+P` | Settings panel |
| `Ctrl+W` | Companion mode |
| `Ctrl+Y` | Copy |

> **Language note**: the CLI ships both English and Chinese command aliases, and both work at any time. The AI's replies follow the language configuration of the selected expert (Chinese by default). The Chinese command aliases are listed in the [Chinese README](README.zh-CN.md).

---

## Proxy Server Deployment (Team Collaboration)

The proxy server achieves zero API key exposure and is designed for research groups, laboratories, and enterprise teams.

### Architecture

```
Client (zeroai)  --token auth-->  Proxy server  --real key-->  Upstream AI (GLM/OpenRouter)
                                       |
                                       +- Rate limiting (30 req/min/IP)
                                       +- Model allowlist
                                       +- Streaming SSE pass-through
                                       +- Audit logging
```

### Deployment Steps

1. Upload the `zeroai-proxy/` directory to the server
2. Install dependencies: `pip install -r requirements.txt`
3. Configure `.env` (copy from `.env.example`, which lists every available option):
   ```bash
   GLM_API_KEY=your_zhipu_key
   OR_API_KEY=your_openrouter_key
   CLIENT_TOKENS=access_tokens_generated_for_each_member
   ALLOWED_MODELS=glm-4.7-flash,glm-4-flash,glm-4v-flash
   ```
4. Start the service (choose Linux systemd / Windows NSSM / Docker)
5. Open port 8000 in the firewall (internal network only)
6. Configure the proxy address and token on the client and it is ready to use

### Security Features

| Feature | Description |
|------|------|
| API key isolation | The real key lives only in the server `.env`; clients can never obtain it |
| Token authentication | Clients use independent tokens, fully separated from upstream keys |
| Rate limiting | Sliding window of 30 requests/minute per IP (configurable) |
| Model allowlist | Prevents clients from invoking expensive models |
| Streaming pass-through | Full SSE support; streaming output is unaffected |
| OpenAI compatibility | No SDK changes on the client; only the base URL changes |
| Log auditing | Records IP/token/model/status for traceability |

### IP Security Design (v1.2.0 security hardening)

In response to the security threats a proxy server faces in team/internal deployments, v1.2.0 introduces systematic IP and token hardening:

#### 1. Token Ownership and Lifecycle Management

| Capability | Description |
|------|------|
| Token ownership | Each token is bound to a user name, team, and note for audit traceability |
| Usage statistics | Automatically records call count and last-used time |
| Expiry | Supports permanent tokens or a specified expiry (ISO 8601) |
| Instant revocation | Editing `tokens.json` takes effect via **hot reload**, with no service restart |
| State restoration | A revoked token can be reinstated |
| Statistics reset | A token's usage counters can be reset |
| Management endpoints | `/admin/tokens` to view, `/admin/revoke` to revoke (requires `ADMIN_TOKEN`) |

Token file format (`tokens.json`):
```json
{
  "abc123def456...": {
    "user": "alice",
    "team": "dev-team",
    "revoked": false,
    "expires": null,
    "usage_count": 0,
    "last_used": null,
    "created_at": "2026-07-25T10:00:00"
  }
}
```

#### 2. Brute-Force Protection

| Parameter | Default | Description |
|------|--------|------|
| `MAX_FAILURES` | 5 | Consecutive failures from the same IP before a ban is triggered |
| `BAN_MINUTES` | 30 | Ban duration (minutes) |
| `BAN_WINDOW_MINUTES` | 10 | Failure-counting window (minutes) |

- An IP that fails 5 times within a 10-minute window is banned automatically for 30 minutes
- A successful verification clears that IP's failure record
- The ban list can be viewed at `/admin/banned` (requires `ADMIN_TOKEN`)

#### 3. HTTPS Encrypted Transport (self-signed certificate)

- Generate a self-signed certificate: `python generate_cert.py` (based on the `cryptography` library)
- Once enabled, tokens are transported encrypted, preventing man-in-the-middle sniffing
- Clients set `verify_ssl=false` to accept the self-signed certificate (internal deployment scenario)
- Certificate files (`cert.pem` / `cert.key`) are excluded via `.gitignore` and never enter version control

#### 4. API Documentation Endpoints Disabled

| Endpoint | Status | Description |
|------|------|------|
| `/docs` | Disabled | Swagger UI is not exposed |
| `/redoc` | Disabled | ReDoc is not exposed |
| `/openapi.json` | Disabled | The OpenAPI schema does not leak the interface structure |

This prevents attackers from probing system interfaces and parameters through the API documentation.

#### 5. IP-Masked Audit Logging

All IP addresses in logs are masked automatically, keeping only the first two octets:
```
Original IP: 192.168.10.66  ->  Log: 192.168.xxx.xxx
```
- Protects user privacy while preserving internal auditability
- Ban/unban log entries are masked as well

#### 6. Management Endpoint Authentication

`/admin/*` endpoints require `ADMIN_TOKEN` authentication (independent of client tokens):
- `/admin/tokens`: list all tokens (masked)
- `/admin/revoke`: revoke a specified token
- `/admin/reinstate`: reinstate a revoked token
- `/admin/banned`: view the currently banned IP list
- `/admin/unban`: unban a specified IP

#### 7. .gitignore Security Exclusions

```gitignore
# Security-related (certificates and token files — never commit)
*.pem
*.key
*.crt
tokens.json
cert.pem
cert.key
*.bak.*
```

This ensures sensitive files are never committed to version control by accident.

For detailed deployment documentation, see [zeroai-proxy/README.md](zeroai-proxy/README.md).

---

## System Requirements

- Python >= 3.10
- Operating system: Windows / macOS / Linux
- Network: access to the Zhipu GLM API (`open.bigmodel.cn`) or a proxy server
- Voice features: a microphone (voice conversation mode only)
- Multimodal: supports png/jpg/jpeg/gif/bmp/webp images

---

## Configuration

### Configuration File Location

- Windows: `%USERPROFILE%\.zeroai\config.json`
- macOS/Linux: `~/.zeroai/config.json`

### Environment Variables

| Environment Variable | Description |
|---------|------|
| `ZEROAI_API_KEY_GLM` | Zhipu GLM API key (direct mode) |
| `ZEROAI_API_KEY_OPENROUTER` | OpenRouter API key (direct mode) |
| `ZEROAI_HOME` | ZeroAI resource directory (where `libs`/`models` live) |

### Resource Directory Lookup Order

1. The directory containing the script (development mode)
2. The directory specified by the `ZEROAI_HOME` environment variable
3. The user home directory `~/.zeroai/` (pip install mode)

---

## Tech Stack

| Component | Technology |
|------|------|
| UI framework | Textual TUI |
| AI interface | OpenAI SDK (compatible with the GLM API) |
| SSH remote | asyncssh (pure-Python asynchronous SSH) |
| Speech recognition | sherpa-onnx + SenseVoice (offline) |
| Speech synthesis | Edge TTS |
| Document generation | python-docx + reportlab + matplotlib |
| Academic formulas | LaTeX → Unicode + matplotlib mathtext |
| Literature search | Semantic Scholar API |
| Proxy service | FastAPI + httpx |

---

## Feature Demos

> The transcripts below are illustrative and written in English for readability. The built-in interface labels and the default expert replies are in Chinese (several expert system prompts explicitly instruct "answer in Chinese").

### Multi-Expert Collaboration

```
User: Write a Python function that computes the Fibonacci sequence, and analyze its time complexity

-> Project Manager · GLM-4.7 analyzes the task
-> Coding · GLM-4.7 generates the code
-> Reasoning · GLM-4.7 analyzes the complexity
-> Project Manager · GLM-4.7 aggregates the results
```

### Academic Literature Search

```
User: Search for papers on Transformer acceleration from the last three years, ranked by citations

-> Academic Research expert calls the Semantic Scholar API
-> Returns 10 highly cited papers (title/author/year/citations/abstract)
-> Automatically generates a literature review draft
```

### Academic Document Generation

```
User: Generate an academic report on machine learning in Word format, including formulas

-> Academic Research · GLM-4.7 generates the content
-> LaTeX formula rendering: $$E = mc^2$$ -> high-resolution image embedded
-> Automatically typeset per the GB/T 7713.1-2025 format
-> Saved as a .docx file
```

### SSH Remote Deployment

```
User: Connect to 192.168.10.20 as root with password xxx, and deploy D:/projects/myapp to /opt/myapp

-> ssh_connect establishes the connection (conn_id=default)
-> ssh_deploy one-click deployment:
  1. pre_check    -> check disk space / Python version
  2. mkdir        -> create the /opt/myapp directory
  3. upload       -> SFTP the project files
  4. install      -> pip install -r requirements.txt
  5. restart      -> systemctl restart myapp
  6. health_check -> curl localhost:8080/health
  7. post_cmds    -> clean up temporary files
-> Generates a deployment report
```

### AI Remote Operations

```
User: The server is slow, take a look at what is going on

-> ssh_health_check one-click health check
  -> system info, CPU, memory, disk, network, load, failed services, error logs
  -> AI analysis: 3 issues found
    1. 1-minute load 5.2 is high
    2. Disk usage 92% (critical)
    3. 15 error log entries in the last hour
  Recommends drilling down

User: How is mysql doing
-> ssh_service_manage(action=status, service=mysql)
-> Automatic status interpretation: running / not running / exited abnormally

User: Check the nginx error log
-> ssh_log_view(service=nginx, keyword=error)
-> Returns the log + automatic statistics: high error density, recommends drilling down

User: Open port 8080
-> ssh_firewall_manage(action=open, port=8080)
-> Automatically detects the firewall type (ufw/firewalld/iptables) and executes
```

---

## Development

### Development Mode Installation

```bash
git clone https://github.com/gt17641001169-design/zero-ai.git
cd zero-ai-cli
pip install -e .           # main package (development mode)
pip install -e ".[dev]"    # development dependencies (build, pyinstaller)
pip install -e ".[voice]"  # optional: voice dependencies
```

In development mode, changes to the `zeroai/` package or `tui_agent.py` take effect immediately with no reinstall.

### Building

```bash
pip install build
python -m build
```

The generated packages appear in `dist/` (`.whl` and `.tar.gz`).

### Building the C/Zig Acceleration Layer (optional)

```bash
cd zeroai-tui
python setup.py build_ext --inplace            # build both the Zig and C extensions
python setup.py build_ext --inplace --skip-zig # build only the C extension (skip Zig)
```

Acceleration layer architecture: Python → C → Zig (falling back automatically to the C scalar implementation on failure).

### Running Tests

```bash
# Phase 3 switch-block regression test
python test_phase3_regression.py

# v1.1.3 release integration test
python test_v1_1_3_release.py

# C/Zig acceleration layer test suite
python -m pytest zeroai-tui/test_zeroai_tui.py zeroai-tui/tests/ -v -p no:xonsh
```

### Project Structure

```
zero-ai-cli/
├── zeroai/                   # Modular package (recommended entry point, v1.1.3+)
│   ├── core/                 # Core layer (8 submodules)
│   │   ├── paths.py          # Path management
│   │   ├── runtime.py        # Runtime cache and interrupt control
│   │   ├── secrets.py        # Secret and configuration persistence
│   │   ├── constants.py      # Constants and expert team
│   │   ├── expert_route.py   # Expert routing (with LRUCache)
│   │   ├── context_compress.py # Context compression
│   │   ├── model_manager.py  # Model management
│   │   └── response_utils.py # Response handling utilities
│   ├── tools/                # Tool layer (10 submodules, 56 tools)
│   │   ├── file_manager.py   # File operations
│   │   ├── command_exec.py   # Command execution
│   │   ├── network.py        # Network operations
│   │   ├── system_check.py   # System checks
│   │   ├── security.py       # Security audit
│   │   ├── doc_gen.py        # Document generation
│   │   ├── academic.py       # Academic research
│   │   ├── window_mgr.py     # Window management
│   │   ├── ssh_ops.py        # SSH remote operations
│   │   └── registry.py       # Tool registry (TOOLS + TOOL_MAP)
│   ├── tui/                  # TUI wrapper layer (7 submodules)
│   │   ├── colors.py         # Color constants
│   │   ├── markdown.py       # Markdown/LaTeX rendering
│   │   ├── identity.py       # Identity-leak filtering
│   │   ├── widgets.py        # Custom widgets
│   │   ├── screens.py        # Modal dialogs
│   │   ├── app.py            # ZeroAI main application class
│   │   └── icons.py          # Icon loading
│   ├── main.py               # Unified entry point
│   └── __main__.py           # Module entry point (supports python -m zeroai)
├── tui_agent.py              # Original implementation (kept as backup, backward compatible, deprecated)
├── zeroai-tui/               # C/Zig-accelerated TUI framework
│   ├── zeroai_tui/           # TUI component package
│   │   ├── src/_renderer.c   # C rendering core (dynamically loads Zig)
│   │   ├── src/_terminal.c   # C terminal control
│   │   └── components.py     # TUI component framework
│   ├── src/zig_render.zig    # Zig rendering acceleration
│   ├── build.zig             # Zig build script
│   ├── setup.py              # C/Zig extension build
│   └── tests/                # Test suite
├── zeroai-proxy/             # Proxy server (API key protection)
│   ├── main.py               # FastAPI proxy entry point
│   ├── requirements.txt      # Dependency list
│   ├── .env.example          # Configuration template
│   ├── Dockerfile            # Docker deployment
│   ├── docker-compose.yml
│   ├── start.sh              # systemd deployment script
│   └── README.md             # Deployment documentation
├── assets/icons/             # Icon assets
├── pyproject.toml            # Package configuration
├── README.md                 # This documentation
├── CHANGELOG.md              # Changelog
├── CONTRIBUTING.md           # Contribution guide
├── LICENSE                   # Proprietary license
├── AUTHORS                   # Author list
├── install.bat               # Windows one-click installer
└── libs/ models/             # Voice dependencies (development mode, not versioned)
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for details.

---

## FAQ

### Q: It says "please configure the GLM API key" on startup?

A: Press `Ctrl+P` to open the settings panel and enter your Zhipu GLM API key. Get one free at https://open.bigmodel.cn/

### Q: Voice features are not working?

A:
1. Make sure the voice dependencies are installed: `pip install zero-ai-cli[voice]`
2. On first use the model is downloaded automatically (~220 MB)
3. Make sure microphone permission is granted

### Q: I get a RateLimitError (rate limiting)?

A: The GLM free tier is limited, and the system degrades to other models automatically. If rate limiting happens frequently, you can:
- Upgrade your quota on the Zhipu platform
- Configure OpenRouter as a fallback
- Use your own GLM API key
- Deploy a proxy server to manage quota centrally

### Q: Are macOS / Linux supported?

A: The core functionality is. Voice features may need additional audio driver configuration on macOS/Linux.

### Q: How do I use SSH remote operations?

A:
1. Simply tell the AI in natural language: "connect to 192.168.10.20 as root with password xxx"
2. The AI calls `ssh_connect` to establish the connection
3. From there you can say things like "check nginx status", "the server is slow, run a health check", "check the mysql error log", "open port 8080", "restart the web container"
4. The AI automatically calls the corresponding semantic operations tool (`ssh_service_manage` / `ssh_health_check` / `ssh_log_view` / `ssh_firewall_manage` / `ssh_docker_manage`, etc.)
5. Type `/ssh` to see the full list of 15 SSH/operations tools

### Q: Are SSH operations safe?

A: Multiple safeguards are in place:
- **Dangerous command blacklist**: 11 categories including `rm -rf /`, `mkfs`, `dd`, and `shutdown` require `confirm_dangerous=true` as a second confirmation
- **Injection protection**: service name/container name whitelist validation rejects command injections such as `nginx; rm -rf /`
- **Audit log**: all SSH operations are recorded automatically (up to 200 entries), queryable via `ssh_list`
- **Host validation**: IP/domain format validation with optional internal-IP blocking
- **Output truncation**: command output longer than 8000 characters is truncated automatically to prevent screen flooding

### Q: How do I use one-click deployment (`ssh_deploy`)?

A: Just tell the AI what you need deployed and it constructs the `deploy_config` automatically:

```
Deploy D:/projects/myapp to /opt/myapp on 192.168.10.20,
install dependencies with pip install -r requirements.txt,
restart with systemctl restart myapp,
health check with curl localhost:8080/health
```

The AI executes the 7 steps automatically and produces a deployment report.

### Q: How does the proxy server protect API keys?

A: Under the proxy server architecture:
- The real API key lives only in the server-side `.env` file (never committed to Git, never present on the client)
- The client holds only an access token, fully separated from the upstream key
- A packet capture on the client reveals only the token, never the real key
- Per-user token assignment supports team collaboration
- Tokens can be revoked at any time without affecting the real key

---

## License

Proprietary software. Commercial use without authorization is prohibited. See [LICENSE](LICENSE).

## Author

ZeroAI Team. See [AUTHORS](AUTHORS).

## Changelog

See [CHANGELOG.md](CHANGELOG.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

## Acknowledgements

- [Zhipu AI](https://open.bigmodel.cn/) - GLM family models
- [Textual](https://textual.textualize.io/) - TUI framework
- [asyncssh](https://asyncssh.readthedocs.io/) - asynchronous SSH client
- [sherpa-onnx](https://github.com/k2-fsa/sherpa-onnx) - speech recognition
- [Semantic Scholar](https://www.semanticscholar.org/) - academic literature data
- [FastAPI](https://fastapi.tiangolo.com/) - proxy service framework

---

<div align="center">

**ZeroAI — Research collaboration, made efficient**

</div>
