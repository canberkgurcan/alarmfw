FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl tar gzip \
  && rm -rf /var/lib/apt/lists/*

# ---- install oc client ----
ARG OC_VERSION=4.16.0
RUN curl -fsSL -o /tmp/openshift-client.tgz \
      "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/${OC_VERSION}/openshift-client-linux-${OC_VERSION}.tar.gz" \
 && curl -fsSL -o /tmp/oc-sha256.txt \
      "https://mirror.openshift.com/pub/openshift-v4/clients/ocp/${OC_VERSION}/sha256sum.txt" \
 && grep "openshift-client-linux-${OC_VERSION}.tar.gz" /tmp/oc-sha256.txt | sha256sum -c - \
 && tar -xzf /tmp/openshift-client.tgz -C /usr/local/bin oc kubectl \
 && chmod +x /usr/local/bin/oc /usr/local/bin/kubectl \
 && rm -f /tmp/openshift-client.tgz /tmp/oc-sha256.txt \
 && oc version --client || true

COPY pyproject.toml /app/pyproject.toml
COPY src/ /app/src/

RUN pip install --no-cache-dir -U pip setuptools wheel \
 && pip install --no-cache-dir .

RUN mkdir -p /config /state

ENTRYPOINT ["alarmfw"]
CMD ["run","--config","/config/run_local.yaml"]