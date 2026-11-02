#!/bin/sh
# Rebuild Linchan from this folder and install it for your user as a Flatpak.
# Run it again after you change the code. Needs the GNOME 51 runtime and org.flatpak.Builder.
set -e
cd "$(dirname "$0")"
flatpak run org.flatpak.Builder --user --install --force-clean build-flatpak io.github.songanane.Linchan.yml
echo "Installed. Start it from the app grid, or: flatpak run io.github.songanane.Linchan"
