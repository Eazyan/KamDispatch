FROM mcr.microsoft.com/playwright/python:v1.64.1-noble@sha256:330d401ae23c905896a0c26c66c36fc971b621519e7b11b435a8328d7bf6e031
RUN pip install --no-cache-dir playwright==1.64.1 numpy==2.2.6 Pillow==11.3.0
WORKDIR /app
COPY scripts/capture_traffic.py scripts/traffic_linux_worker.py scripts/prune_traffic_spool.py scripts/traffic_capture_plan.py /app/scripts/
COPY containers/traffic-worker/pilot.json /app/containers/traffic-worker/pilot.json
COPY monitoring/traffic_image.py /app/monitoring/
RUN mkdir -p /spool && chown pwuser:pwuser /spool
USER pwuser
ENV PYTHONUNBUFFERED=1
ENTRYPOINT ["python", "scripts/traffic_linux_worker.py"]
CMD ["smoke", "/spool/smoke"]
