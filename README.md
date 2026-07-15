# Minecraft AI Chat

Fabric **Minecraft Java 26.2** dedicated server + web control panel.  
An OpenAI-compatible AI can run console commands and read live logs through tools.

[![Minecraft](https://img.shields.io/badge/Minecraft-26.2-green)](https://www.minecraft.net/)
[![Fabric](https://img.shields.io/badge/Loader-Fabric-blue)](https://fabricmc.net/)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

## Features

- **Fabric 26.2** server (`mods/` for Fabric mods, `plugins/` ready for hybrids)
- **Web UI** — API settings, AI chat, live console
- **AI tools** — `run_server_command`, `get_console_output`, `get_server_status`, `wait_for_console`
- **One-shot start** — `./start.sh` installs Python deps, starts MC + web, prints URLs
- System prompt with Java **26.2** command reference

## Requirements

| | |
|---|---|
| OS | Linux |
| Java | 21+ (tested on OpenJDK 25) |
| Python | 3.10+ (3.14 OK with recent wheels) |
| Network | First run downloads Minecraft + pip packages |

## Quick start

```bash
git clone https://github.com/qememe/minecraft_ai_chat.git
cd minecraft_ai_chat
chmod +x start.sh
./start.sh
```

First launch downloads Fabric/Minecraft (can take a few minutes). Wait for:

```text
Done (...)! For help
[bridge] Server is READY
```

Open the Web UI (prefer localhost if you use a system SOCKS proxy):

```text
http://127.0.0.1:8080
```

(`start.sh` picks another free port if `8080` is busy.)

### AI settings

In the UI → **Settings**:

| Field | Example |
|---|---|
| Base URL | `https://api.x.ai/v1` or any OpenAI-compatible endpoint |
| Model | `grok-4.5` / your model id |
| API key | your key |

Or via environment:

```bash
export OPENAI_API_KEY=...
export OPENAI_BASE_URL=https://api.x.ai/v1
export OPENAI_MODEL=grok-4.5
./start.sh
```

Keys are stored only in local `config/settings.json` (gitignored).

## Connect to Minecraft

| | |
|---|---|
| Address (this PC) | `127.0.0.1:25565` |
| Address (LAN) | `<your-lan-ip>:25565` |
| Client | Fabric **26.2** + same mods as the server |

Multiplayer → **Direct Connection**.

## Ports

| Service | Default | Override |
|---|---|---|
| Web UI | `8080` | `WEB_PORT` |
| Minecraft | `25565` | `server/server.properties` → `server-port` |
| RCON | `25575` | `server.properties` |

## Project layout

```text
minecraft_ai_chat/
├── start.sh                 # entrypoint
├── web/                     # FastAPI backend + static UI
│   ├── app.py
│   ├── mc_server.py         # process / console bridge
│   ├── ai_agent.py          # OpenAI-compatible tool loop
│   ├── system_prompt.py
│   └── static/
├── server/                  # Fabric dedicated server (runtime data gitignored)
│   ├── mods/                # drop Fabric mods here
│   ├── plugins/             # Bukkit plugins (needs hybrid mod for 26.2)
│   └── server.properties.example
├── config/
│   └── settings.example.json
└── plugin/README.md         # notes on the console bridge
```

## Example prompts

- «Кто онлайн?»
- «Сделай день и выключи дождь»
- «Дай игроку Steve 64 яблока»
- «Включи keepInventory»
- «Напиши всем сообщение в чат»

## Environment variables

| Variable | Default | Meaning |
|---|---|---|
| `WEB_PORT` | `8080` | Web UI port |
| `MC_MEMORY` | `2G` | JVM heap |
| `AUTO_START_MC` | `1` | Auto-start Minecraft with the web app |
| `JAVA_BIN` | `java` | Java binary |
| `XAI_API_KEY` / `OPENAI_API_KEY` | — | API key fallback |
| `OPENAI_BASE_URL` | — | OpenAI-compatible base URL |
| `OPENAI_MODEL` | — | Model id |
| `AI_HTTP_PROXY` | — | Optional proxy for AI HTTP only (`socks5://…`) |

## Notes

- AI console commands must **not** use a leading `/` (server console syntax).
- System `ALL_PROXY=socks://…` is ignored for AI HTTP (use `socks5://` via `AI_HTTP_PROXY` if needed).
- Bukkit plugins on Fabric need a hybrid bridge (Cardboard/Banner); none was stable for 26.2 at packaging time — see `server/plugins/README.md`.
- `eula.txt` is set to `true` for automation; you must agree with the [Minecraft EULA](https://aka.ms/MinecraftEULA).

## License

MIT — see [LICENSE](LICENSE).  
Minecraft is a trademark of Mojang/Microsoft. This project is not affiliated with them.
