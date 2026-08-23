FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app/ ./app/
COPY knowledge_docs/ ./knowledge_docs/
COPY eval/datasets/ ./eval/datasets/
COPY release-policy.yaml .

# No local index is baked into this image, and there is nothing to build
# here -- there is no local retrieval path left in this project at all
# (see app/retrieval/retrieval.py). The real Azure AI Search index is
# created and populated by scripts/build_search_index.py, run once
# automatically by the azd postprovision hook (see azure.yaml) against the
# live Search service, and again any time the index needs to be repaired.
# This container only ever QUERIES that index at request time; it never
# builds or owns it.

EXPOSE 8000
ENV PYTHONUNBUFFERED=1

CMD ["uvicorn", "app.api.main:app", "--host", "0.0.0.0", "--port", "8000"]
