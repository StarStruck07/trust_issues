"""Owner: Prachi (P3). STUB from Arth — see PRACHI_product.md section 5."""
from fastapi import FastAPI

app = FastAPI(title="Counterparty Risk Desk")


@app.get("/health")
def health():
    return {"ok": True}
