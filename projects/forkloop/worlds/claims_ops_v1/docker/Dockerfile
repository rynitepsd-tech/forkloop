# syntax=docker/dockerfile:1.7
# claims-ops-v1 as a Docker image: the same world the Solari golden snapshot holds (OpenEMR 8.3.0 +
# the synthetic payer portal + Google Chrome on an XFCE desktop, Xvfb :0 at 1280x720x24, user
# `desktop`). Build it with build_image.sh (next to this file): this Dockerfile makes the *base*
# image (services installed, both databases seeded); build_image.sh then boots it once, runs
# browser_setup.sh (Chrome logs into both apps) and commits the result as the golden image.
#
# Context = projects/forkloop (Dockerfile.dockerignore next to this file keeps it to worlds/ + forkloop/):
#   docker build -f worlds/claims_ops_v1/docker/Dockerfile -t forkloop/claims-ops-v1-base:1 .
#
# The world is installed by the *unchanged* Solari scripts (build.sh -> openemr/install.sh). They call
# systemctl; there is no systemd in a container, so /usr/local/bin/systemctl is a small shim that
# drives docker/svc.sh instead (the Solari path never sees the shim).

ARG UBUNTU=22.04
# libfaketime for the world clock (entrypoint.sh), upstream v0.9.10, built twice: the full library for every
# process (MariaDB needs FAKE_PTHREAD: without it its 1 s realtime waits return at once and InnoDB aborts),
# and one without FAKE_PTHREAD for Chrome only (with it, headed Chrome segfaulted at start, even with a zero
# offset — Ubuntu 22.04's 0.9.8 package and upstream 0.9.10 alike; bisected 2026-09-29).
FROM ubuntu:22.04 AS faketime
ARG LIBFAKETIME_VERSION=0.9.10
ARG LIBFAKETIME_SHA256=729ad33b9c750a50d9c68e97b90499680a74afd1568d859c574c0fe56fe7947f
RUN apt-get update && apt-get install -y --no-install-recommends gcc make libc6-dev curl ca-certificates \
 && curl -fsSL -o /tmp/lft.tar.gz "https://github.com/wolfcw/libfaketime/archive/refs/tags/v${LIBFAKETIME_VERSION}.tar.gz" \
 && echo "${LIBFAKETIME_SHA256}  /tmp/lft.tar.gz" | sha256sum -c - \
 && tar -xzf /tmp/lft.tar.gz -C /tmp \
 && cd "/tmp/libfaketime-${LIBFAKETIME_VERSION}/src" \
 && make libfaketime.so.1 && install -D -m 755 libfaketime.so.1 /out/libfaketime.so.1 \
 && make clean && make libfaketime.so.1 FAKETIME_COMPILE_CFLAGS="-UFAKE_PTHREAD" \
 && install -D -m 755 libfaketime.so.1 /out/libfaketime-nopthread.so.1

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

# The LAMP packages openemr/install.sh installs, pre-installed in their own layer so that editing the
# world sources does not re-download them (install.sh's own apt-get install then finds them present).
FROM desktop AS lamp
RUN add-apt-repository -y ppa:ondrej/php \
 && apt-get update \
 && apt-get install -y -q tar apache2 mariadb-server mariadb-client \
      php8.3-fpm php8.3-cli php8.3-common php8.3-mysql php8.3-gd php8.3-curl php8.3-xml php8.3-mbstring \
      php8.3-zip php8.3-soap php8.3-intl php8.3-ldap php8.3-bcmath php8.3-imagick \
 && rm -rf /var/lib/apt/lists/*

# The portal's systemd unit is defined in world.py (PORTAL_SYSTEMD, written by ClaimsOpsWorld.build on
# Solari). Extract it in a throwaway stage: BuildKit caches the COPY below by content, so edits to the
# rest of world.py do not invalidate the (slow) world build.
FROM desktop AS unit
COPY worlds/claims_ops_v1/world.py /tmp/world.py
RUN python3 - <<'EOF'
import re
src = open("/tmp/world.py", encoding="utf-8").read()
m = re.search(r'PORTAL_SYSTEMD = """(.*?)"""', src, re.S)
assert m, "PORTAL_SYSTEMD not found in world.py"
open("/tmp/forkloop-portal.service", "w").write(m.group(1))
EOF

FROM lamp AS world
COPY worlds/claims_ops_v1/docker/svc.sh /usr/local/forkloop/svc.sh
COPY worlds/claims_ops_v1/docker/systemctl /usr/local/bin/systemctl
COPY --from=unit /tmp/forkloop-portal.service /etc/systemd/system/forkloop-portal.service
# What build.sh reads (the full trees are copied again below, as the Solari build uploads them).
COPY worlds/__init__.py /opt/forkloop/worlds/__init__.py
COPY worlds/claims_ops_v1/__init__.py worlds/claims_ops_v1/build.sh worlds/claims_ops_v1/browser_setup.sh \
     worlds/claims_ops_v1/chrome_policy.json /opt/forkloop/worlds/claims_ops_v1/
COPY worlds/claims_ops_v1/portal /opt/forkloop/worlds/claims_ops_v1/portal
COPY worlds/claims_ops_v1/openemr /opt/forkloop/worlds/claims_ops_v1/openemr
# --headless skips only the Solari-desktop steps build.sh cannot do inside `docker build` (purging the
# template's VS Code, and the Chrome login, which needs a running X session: build_image.sh does it).
RUN chmod 755 /usr/local/forkloop/svc.sh /usr/local/bin/systemctl \
 && bash /opt/forkloop/worlds/claims_ops_v1/build.sh --build-dir /opt/forkloop --headless \
 && mkdir -p /etc/opt/chrome/policies/managed \
 && install -m 644 /opt/forkloop/worlds/claims_ops_v1/chrome_policy.json /etc/opt/chrome/policies/managed/forkloop.json \
 && /usr/local/forkloop/svc.sh stop-all

# build.sh's disk-saving `apt-get purge gcc g++ cpp` (for the 4 GB Solari disk) also purges xfce4-session and
# x11-xserver-utils on Ubuntu 22.04 (both depend on cpp; apt history, 2026-09-28). A running Solari VM keeps
# its session in memory; a container boots, so put them back. Yaru (Ubuntu's theme) is what the Solari
# desktop's panel shows (orange active-task underline, grey pager).
RUN apt-get update \
 && apt-get install -y --no-install-recommends xfce4-session x11-xserver-utils yaru-theme-gtk yaru-theme-icon \
 && rm -rf /var/lib/apt/lists/*

# Full source trees, as ClaimsOpsWorld.build uploads them to /opt/forkloop on Solari.
COPY worlds /opt/forkloop/worlds
COPY forkloop /opt/forkloop/forkloop
# Desktop session defaults for the `desktop` user (top panel like the Solari desktop, no screensaver).
COPY --chown=desktop:desktop worlds/claims_ops_v1/docker/xfce/ /home/desktop/.config/xfce4/
COPY worlds/claims_ops_v1/docker/entrypoint.sh worlds/claims_ops_v1/docker/agent.py \
     worlds/claims_ops_v1/docker/bake.sh /usr/local/forkloop/
RUN chmod 755 /usr/local/forkloop/*.sh /usr/local/forkloop/agent.py \
 && mkdir -p /home/desktop/Downloads /home/desktop/.config/autostart \
 && for f in /etc/xdg/autostart/*.desktop; do \
      [ -e "$f" ] || continue; \
      printf '[Desktop Entry]\nHidden=true\n' > "/home/desktop/.config/autostart/$(basename "$f")"; \
    done \
 && chown -R desktop:desktop /home/desktop

# World clock (entrypoint.sh): libfaketime in every process, one relative offset per boot. Written last so
# no build step runs preloaded.
COPY --from=faketime /out/ /usr/local/lib/faketime/
COPY worlds/claims_ops_v1/docker/chrome-wrapper.sh /usr/local/forkloop/chrome-wrapper.sh
RUN rm -f /etc/ld.so.preload /usr/bin/google-chrome \
 && install -m 755 /usr/local/forkloop/chrome-wrapper.sh /usr/bin/google-chrome
ARG WORLD_CLOCK=2026-09-07T09:00:00Z
ENV LD_PRELOAD=/usr/local/lib/faketime/libfaketime.so.1 FAKETIME_DONT_FAKE_MONOTONIC=1 \
    FORKLOOP_WORLD_CLOCK=${WORLD_CLOCK}

LABEL forkloop=1 forkloop.world=claims-ops-v1 forkloop.kind=base forkloop.world_clock=${WORLD_CLOCK}
# Everything the agent can touch lives in the container filesystem (the Solari rule "inside the VM
# snapshot"): no volumes, so `docker commit` captures the whole world.
EXPOSE 80 8080
ENTRYPOINT ["/usr/bin/tini", "--", "/usr/local/forkloop/entrypoint.sh"]
