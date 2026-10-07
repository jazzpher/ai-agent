FROM python:3.11.11-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends bubblewrap libseccomp2 util-linux bash ca-certificates && rm -rf /var/lib/apt/lists/*
RUN useradd --create-home --uid 1000 agent
WORKDIR /app
COPY requirements-render.txt .
RUN pip install --no-cache-dir -r requirements-render.txt
COPY --chown=agent:agent . .
RUN chown agent:agent /app
USER agent
ENV AGENT_HOST=0.0.0.0 AGENT_SANDBOX_MODE=bubblewrap GRADIO_ANALYTICS_ENABLED=False
CMD ["python", "app.py"]
