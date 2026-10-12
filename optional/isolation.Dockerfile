FROM python:3.12-slim
# Test-only image. The workflow resolves the built image to an immutable ID and
# archives both that identity and the exact installed package list.
RUN pip install --no-cache-dir nbclient==0.11.0 ipykernel==7.4.0
