#!/bin/sh
# Testet den Dienst und rollt ihn in einen Docker-Compose-Stack auf einem anderen Rechner aus.
#   DEPLOY_HOST  ssh-Ziel (Pflicht)
#   DEPLOY_DIR   Ordner des Stacks dort, relativ zu ~ (Pflicht); der Code landet in $DEPLOY_DIR/oeffi/app
#   DEPLOY_SERVICE  Name des Dienstes in der Compose-Datei (Standard: oeffi)
set -eu
cd "$(dirname "$0")"
HOST="${DEPLOY_HOST:?DEPLOY_HOST setzen}"
DIR="${DEPLOY_DIR:?DEPLOY_DIR setzen}"
SERVICE="${DEPLOY_SERVICE:-oeffi}"
node --disable-warning=ExperimentalWarning --test 'app/test/*.test.js'
python3 -m unittest discover -s app/server
rm -rf app/server/__pycache__
ssh "$HOST" "mkdir -p ~/$DIR/oeffi/app"
rsync -a --delete --exclude __pycache__ app/server app/web app/Dockerfile "$HOST:$DIR/oeffi/app/"
ssh "$HOST" "cd ~/$DIR && docker compose up -d --build $SERVICE"
echo "Fertig."
