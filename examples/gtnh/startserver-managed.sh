#!/bin/sh
set -eu

# Run Java as the container process. Docker owns automatic restart behavior.
exec java \
  -Xms8G \
  -Xmx8G \
  -Dfml.readTimeout=180 \
  @java9args.txt \
  -jar lwjgl3ify-forgePatches.jar \
  nogui
