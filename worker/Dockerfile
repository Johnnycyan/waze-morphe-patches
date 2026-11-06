FROM eclipse-temurin:21-jre-noble

ARG MORPHE_VERSION=2.0.0
ARG JADX_VERSION=1.5.6

# aapt reads package/version; apksigner checks Waze's signing certificate.
RUN apt-get update \
 && apt-get install -y --no-install-recommends python3 python3-venv aapt apksigner unzip curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

RUN mkdir -p /opt/morphe /opt/jadx \
 && curl -fsSL -o /opt/morphe/morphe.jar \
      "https://github.com/MorpheApp/morphe-cli/releases/download/v${MORPHE_VERSION}/morphe-desktop-${MORPHE_VERSION}-all.jar" \
 && curl -fsSL -o /tmp/jadx.zip "https://github.com/skylot/jadx/releases/download/v${JADX_VERSION}/jadx-${JADX_VERSION}.zip" \
 && unzip -q /tmp/jadx.zip -d /opt/jadx && rm /tmp/jadx.zip

WORKDIR /app
COPY requirements.txt .
RUN python3 -m venv /venv && /venv/bin/pip install --no-cache-dir -r requirements.txt
COPY app.py .
COPY templates templates
COPY static static

ENV DATA_DIR=/data MORPHE_VERSION=${MORPHE_VERSION} JAVA_TOOL_OPTIONS=-Xmx3g
VOLUME /data
EXPOSE 8080
CMD ["/venv/bin/python", "app.py"]
