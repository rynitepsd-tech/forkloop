# syntax=docker/dockerfile:1.7
# kanboard-v1: Kanboard v1.2.54 (the official image's application tree, MIT licence) on the same XFCE +
# Google Chrome desktop as claims-ops-v1 (Xvfb :0 at 1280x720x24, session user `desktop`), driven through
# the same in-container agent (worlds/claims_ops_v1/docker/agent.py) by forkloop/backends/docker.py.
# Build it with build_image.sh (next to this file): this Dockerfile makes the *base* image (PHP, Kanboard
# migrated, base population seeded); build_image.sh boots it once, logs Chrome in (bake.sh) and commits
# the golden image.
#
# Context = projects/forkloop (Dockerfile.dockerignore next to this file):
#   docker build -f worlds/kanboard_v1/docker/Dockerfile -t forkloop/kanboard-v1-base:1 .

ARG UBUNTU=22.04
# --- the desktop: kept instruction-for-instruction identical to the `desktop` stage of
# worlds/claims_ops_v1/docker/Dockerfile, so on a host that built that world BuildKit reuses the layer.
FROM ubuntu:${UBUNTU} AS desktop
ENV DEBIAN_FRONTEND=noninteractive LANG=en_US.UTF-8 LC_ALL=en_US.UTF-8 TZ=UTC
# The desktop the Solari `default` template ships (Ubuntu 22.04 + XFCE + Chrome), minus what the world
# never uses (VS Code, LibreOffice, which build.sh purges there anyway).
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      ca-certificates curl gnupg locales tzdata sudo procps psmisc less tini lsb-release \
      xvfb x11-utils x11-xserver-utils xdotool wmctrl dbus dbus-x11 at-spi2-core \
      xfce4-session xfwm4 xfce4-panel xfce4-settings xfconf xfdesktop4 xfce4-pulseaudio-plugin \
      greybird-gtk-theme elementary-xfce-icon-theme adwaita-icon-theme hicolor-icon-theme \
      fonts-liberation fonts-dejavu-core fonts-ubuntu fonts-noto-color-emoji \
      python3 python3-venv python3-pip sqlite3 software-properties-common \
 && locale-gen en_US.UTF-8 \
 && curl -fsSL -o /tmp/google-chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb \
 && apt-get install -y --no-install-recommends /tmp/google-chrome.deb \
 && rm -f /tmp/google-chrome.deb \
 && useradd -m -s /bin/bash -u 1000 -c desktop desktop \
 && rm -rf /var/lib/apt/lists/*

# --- the application, from the official image (pinned by digest); only /var/www/app is used
FROM kanboard/kanboard:v1.2.54@sha256:8df6c4339134b6c196da9a262daa42ab8ce395f528a48d4a3dd7038c296f34c1 AS kanboard

FROM desktop AS world
# PHP 8.1 is Ubuntu 22.04's own (Kanboard 1.2.54 requires >= 8.1.0: app/check_setup.php). Served like the
# official image (nginx + php-fpm, its server block in nginx.conf). PHP's built-in server was tried first and
# hung pages: its single-threaded workers block on Chrome's idle preconnect sockets (measured 2026-09-29).
RUN apt-get update \
 && apt-get install -y --no-install-recommends php8.1-cli php8.1-fpm php8.1-sqlite3 php8.1-gd php8.1-mbstring \
      php8.1-xml php8.1-curl php8.1-zip nginx yaru-theme-gtk yaru-theme-icon \
 && rm -rf /var/lib/apt/lists/* \
 && rm -f /etc/nginx/sites-enabled/default /etc/php/8.1/fpm/pool.d/www.conf
COPY worlds/kanboard_v1/docker/nginx.conf /etc/nginx/sites-enabled/kanboard.conf
COPY worlds/kanboard_v1/docker/php-fpm-pool.conf /etc/php/8.1/fpm/pool.d/kanboard.conf
COPY --from=kanboard /var/www/app /var/www/app
COPY worlds/kanboard_v1/docker/config.php /var/www/app/config.php
# Only what the build step reads: the base population and the installer (the world's Python code
# stays on the controller; editing it does not change the image).
COPY worlds/__init__.py /opt/forkloop/worlds/__init__.py
COPY worlds/kanboard_v1/__init__.py worlds/kanboard_v1/base_data.py /opt/forkloop/worlds/kanboard_v1/
COPY worlds/kanboard_v1/docker/install_world.py /opt/forkloop/install_world.py
RUN rm -f /var/www/app/data/db.sqlite \
 && python3 /opt/forkloop/install_world.py \
 && touch /var/log/kanboard.log

COPY worlds/kanboard_v1/docker/chrome_policy.json /etc/opt/chrome/policies/managed/forkloop.json
# The in-container agent and the desktop session defaults are the claims-ops-v1 image's (world-agnostic).
COPY worlds/claims_ops_v1/docker/agent.py /usr/local/forkloop/agent.py
COPY --chown=desktop:desktop worlds/claims_ops_v1/docker/xfce/ /home/desktop/.config/xfce4/
COPY worlds/kanboard_v1/docker/entrypoint.sh worlds/kanboard_v1/docker/bake.sh /usr/local/forkloop/
RUN chmod 755 /usr/local/forkloop/*.sh /usr/local/forkloop/agent.py \
 && mkdir -p /home/desktop/Downloads /home/desktop/.config/autostart /run/forkloop \
 && for f in /etc/xdg/autostart/*.desktop; do \
      [ -e "$f" ] || continue; \
      printf '[Desktop Entry]\nHidden=true\n' > "/home/desktop/.config/autostart/$(basename "$f")"; \
    done \
 && chown -R desktop:desktop /home/desktop

LABEL forkloop=1 forkloop.world=kanboard-v1 forkloop.kind=base forkloop.kanboard=v1.2.54
# No volumes: `docker commit` captures the whole world (database, sessions, browser profile).
EXPOSE 80
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/forkloop/entrypoint.sh"]
