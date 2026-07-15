# Plugins folder

This Fabric 26.2 server keeps a `plugins/` directory for Bukkit/Spigot/Paper-style plugins.

## Current status (Minecraft 26.2)

Hybrid bridges such as **Cardboard** / **Banner** (Bukkit API on Fabric) did **not** ship a stable 26.2 build at the time this project was set up.

- Drop Fabric **mods** into `../mods/` (`.jar`) — these work natively.
- Drop Bukkit **plugins** here only after you install a compatible hybrid bridge mod for 26.2 into `../mods/`.

## AI control (included)

Server control for the web AI panel does **not** require a Bukkit plugin. The web backend attaches to the server process:

- reads console stdout (logs)
- writes commands to console stdin

That bridge is always available after `./start.sh`.

## When Cardboard (or similar) supports 26.2

1. Download the hybrid mod jar for Minecraft 26.2.
2. Place it in `server/mods/`.
3. Place Bukkit plugins in this folder.
4. Restart the server.
