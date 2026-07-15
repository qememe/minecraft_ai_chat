# AI Console Bridge (server control plugin)

This project uses a **process-level console bridge** instead of a Bukkit jar:

| Capability | How it works |
|---|---|
| Run commands | Web backend writes to Minecraft process **stdin** |
| Read console | Web backend streams process **stdout** into the UI + AI tools |
| AI tools | `run_server_command`, `get_console_output`, `get_server_status`, `wait_for_console` |

Implementation lives in:

- `web/mc_server.py` — process manager
- `web/ai_agent.py` — OpenAI-compatible tool loop
- `web/system_prompt.py` — Minecraft 26.2 command knowledge for the model

## Why not a classic Bukkit plugin?

On pure Fabric, Bukkit plugins need a hybrid layer (Cardboard/Banner). Those were not available for 26.2 when this stack was built. The stdin/stdout bridge works on any dedicated server (vanilla, Fabric, Paper, etc.) and is more reliable for AI console control.

## Optional: add your own Fabric mod

If you want in-game commands (e.g. `/aichat`), create a Fabric Loom project targeting:

- Minecraft `26.2`
- Fabric Loader `0.19.3+`
- Loom `1.17+`
- Fabric API `0.154.x+26.2`

Then put the built jar into `server/mods/`.
